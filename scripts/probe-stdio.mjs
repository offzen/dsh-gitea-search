#!/usr/bin/env node
/**
 * probe-stdio.mjs —— 不依赖 DSH 的 MCP stdio 冒烟探针。
 *
 * 用途：验证某一份 MCP server 副本能正常启动并完成真实检索。
 * 退出码：0=断言通过；1=协议/断言失败；2=用法或进程启动错误。
 */
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { launchSpec } from './launch-spec.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
function resolveDefaultCmd() {
  const cands = [
    process.argv[2],
    join(__dirname, '..', 'bin', 'mcp-server'),
    join(__dirname, 'bin', 'mcp-server-gitea-search'),
    join(homedir(), 'gitea-search', 'bin', 'mcp-server-gitea-search'),
  ].filter(Boolean);
  for (const c of cands) {
    if (existsSync(c)) return c;
  }
  return process.argv[2] || cands[0];
}

const CMD = resolveDefaultCmd();
const QUERY = process.argv[3] || 'gitea';
const MAX_OUT_BYTES = 131072;
const CALL_TIMEOUT_MS = 90_000;

if (!existsSync(CMD)) {
  console.error(`入口不存在：${CMD}`);
  process.exit(2);
}

const spec = launchSpec(CMD);
const p = spawn(spec.command, spec.args, { stdio: ['pipe', 'pipe', 'pipe'], env: { ...process.env }, windowsHide: true });
let acc = Buffer.alloc(0);
let stderrText = '';
let spawnError;
let closeInfo;
let activeMethod = '启动';
let pass = 0;
let fail = 0;
const waiters = new Map();
const closed = new Promise((resolve) => p.once('close', (code, signal) => {
  closeInfo = { code, signal };
  resolve(closeInfo);
  for (const [id, resolveWaiter] of waiters) {
    waiters.delete(id);
    resolveWaiter({ __closed: true, code, signal });
  }
}));
const started = new Promise((resolve, reject) => {
  p.once('spawn', resolve);
  p.once('error', reject);
});
p.on('error', (error) => {
  spawnError = error;
  for (const [id, resolve] of waiters) {
    waiters.delete(id);
    resolve({ __spawnError: error });
  }
});
p.stderr.on('data', (chunk) => {
  stderrText = (stderrText + chunk.toString('utf8')).slice(-8192);
});
p.stdout.on('data', (chunk) => {
  acc = Buffer.concat([acc, chunk]);
  let newline;
  while ((newline = acc.indexOf(0x0a)) >= 0) {
    const line = acc.subarray(0, newline).toString('utf8');
    acc = acc.subarray(newline + 1);
    if (!line.trim()) continue;
    let message;
    try { message = JSON.parse(line); } catch { continue; }
    const resolve = waiters.get(message.id);
    if (resolve) {
      waiters.delete(message.id);
      resolve(message);
    }
  }
});

function call(id, method, params) {
  activeMethod = method;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      waiters.delete(id);
      reject(new Error(`${method} 超时 ${CALL_TIMEOUT_MS}ms`));
    }, CALL_TIMEOUT_MS);
    waiters.set(id, (message) => {
      clearTimeout(timer);
      if (message?.__spawnError) reject(message.__spawnError);
      else if (message?.__closed) reject(new Error(`${method} 子进程提前退出 (code=${message.code}, signal=${message.signal || 'none'})`));
      else resolve(message);
    });
    if (closeInfo) {
      waiters.delete(id);
      clearTimeout(timer);
      reject(new Error(`${method} 子进程已关闭`));
      return;
    }
    p.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, ...(params ? { params } : {}) }) + '\n', (error) => {
      if (error && waiters.has(id)) {
        waiters.delete(id);
        clearTimeout(timer);
        reject(error);
      }
    });
  });
}

const ok = (condition, message) => {
  if (condition) {
    pass += 1;
    console.log(`  ✓ ${message}`);
  } else {
    fail += 1;
    console.log(`  ✗ ${message}`);
  }
};

try {
  await started;
  console.log(`探针目标：${CMD}`);
  console.log(`cwd=${process.cwd()}  mirror=${process.env.GITQ_MIRROR_ROOT || '(未设)'}\n`);

  const init = await call(1, 'initialize', {
    protocolVersion: '2024-11-05',
    capabilities: {},
    clientInfo: { name: 'probe-stdio', version: '1.0.0' },
  });
  ok(!!init.result, `initialize 有响应（server=${init.result?.serverInfo?.name ?? '?'} v${init.result?.serverInfo?.version ?? '?'}）`);
  p.stdin.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) + '\n');

  const list = await call(2, 'tools/list');
  const names = (list.result?.tools ?? []).map((tool) => tool.name).sort();
  ok(names.includes('search_gitea') && names.includes('read_gitea_file'), `tools/list 工具齐全：${names.join(', ') || '(空)'}`);

  const result = await call(3, 'tools/call', { name: 'search_gitea', arguments: { query: QUERY } });
  const text = result.result?.content?.[0]?.text ?? '';
  const bytes = Buffer.byteLength(text, 'utf8');
  const replacements = (text.match(/\uFFFD/g) ?? []).length;
  const firstLine = text.split('\n')[0] ?? '';
  console.log(`      查询 "${QUERY}" ⇒ ${bytes}B / ${text.split('\n').length} 行；首行：${firstLine.slice(0, 120)}`);
  ok(text.length > 0, 'search_gitea 返回了内容');
  ok(/命中\s*\d+\s*条/.test(firstLine), '首行是「命中 N 条」统计（走的是真实检索路径，不是空壳）');
  ok(replacements === 0, `输出无 U+FFFD（本次 ${replacements} 个）`);
  ok(bytes <= MAX_OUT_BYTES, `输出 ${bytes}B 在上限 ${MAX_OUT_BYTES}B 内（护栏生效）`);
} catch (error) {
  fail += 1;
  if (spawnError) activeMethod = '启动';
  console.error(`  ✗ 探针异常（阶段=${activeMethod}）：${error.message}`);
  if (spawnError) console.error(`      spawn error: ${spawnError.code || ''} ${spawnError.message}`);
  if (stderrText) console.error(`      子进程 stderr（尾部最多 8KB）：\n${stderrText}`);
} finally {
  if (p.exitCode === null && p.signalCode === null) {
    p.kill('SIGTERM');
    const terminated = await Promise.race([
      closed.then(() => true),
      new Promise((resolve) => setTimeout(() => resolve(false), 1000)),
    ]);
    if (!terminated) p.kill('SIGKILL');
  }
}

if (closeInfo && (closeInfo.code !== 0 || closeInfo.signal)) {
  console.error(`      子进程退出：code=${closeInfo.code} signal=${closeInfo.signal || '(none)'}`);
}
console.log(`\n结果：${pass} 绿 / ${fail} 红`);
process.exitCode = spawnError ? 2 : fail ? 1 : 0;
