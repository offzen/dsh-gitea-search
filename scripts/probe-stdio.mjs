#!/usr/bin/env node
/**
 * probe-stdio.mjs —— 不依赖 DSH 的 MCP stdio 冒烟探针。
 *
 * 用途：验证某一份 server.mjs 副本本身能否正常启动并完成端到端检索。
 * 它做的事：按 MCP stdio 协议直接和 server 对话 —— initialize → tools/list → 调一次
 * search_gitea，然后断言：① 工具在 ② 有命中 ③ 输出无 U+FFFD（传输层按块解码损坏）
 * ④ 输出字节数在上限内（护栏生效）。收块用 Buffer 累积、按行分帧。
 *
 * 用法：
 *   node probe-stdio.mjs                                   # 默认探本仓 bin/mcp-server 入口
 *   node probe-stdio.mjs /srv/gitea-tool/bin/mcp-server-gitea-search
 *   node probe-stdio.mjs <入口> <查询词>                    # 默认查询词 gitea
 * 退出码：0=全部断言通过；1=有断言失败；2=用法/启动错误。
 */
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

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
const MAX_OUT_BYTES = 131072;          // GITQ_MAX_SEARCH_BYTES 默认值

if (!existsSync(CMD)) { console.error(`入口不存在：${CMD}`); process.exit(2); }

const p = spawn(CMD, [], { stdio: ['pipe', 'pipe', 'pipe'], env: { ...process.env } });
p.on('error', (e) => { console.error(`启动失败：${e.message}`); process.exit(2); });

// —— 收输出：累积 Buffer，按 0x0A 切**完整行**再解码（绝不按块 toString）——
let acc = Buffer.alloc(0);
const waiters = new Map();
let stderrText = '';
p.stderr.on('data', (d) => { stderrText += d.toString('utf8'); });
p.stdout.on('data', (d) => {
  acc = Buffer.concat([acc, d]);
  let nl;
  while ((nl = acc.indexOf(0x0a)) >= 0) {
    const line = acc.subarray(0, nl).toString('utf8');
    acc = acc.subarray(nl + 1);
    if (!line.trim()) continue;
    let msg;
    try { msg = JSON.parse(line); } catch { continue; }
    const w = waiters.get(msg.id);
    if (w) { waiters.delete(msg.id); w(msg); }
  }
});

const send = (obj) => p.stdin.write(JSON.stringify(obj) + '\n');
p.on('close', (code) => {
  for (const [id, w] of waiters) {
    waiters.delete(id);
    w({ __closed: true, code });
  }
});
function call(id, method, params, ms = 90_000) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => { waiters.delete(id); reject(new Error(`${method} 超时 ${ms}ms`)); }, ms);
    waiters.set(id, (msg) => {
      clearTimeout(t);
      if (msg && msg.__closed) reject(new Error(`${method} 子进程提前退出 (code=${msg.code})`));
      else resolve(msg);
    });
    send({ jsonrpc: '2.0', id, method, ...(params ? { params } : {}) });
  });
}

let pass = 0, fail = 0;
const ok = (c, m) => { if (c) { pass += 1; console.log(`  ✓ ${m}`); } else { fail += 1; console.log(`  ✗ ${m}`); } };

try {
  console.log(`探针目标：${CMD}`);
  console.log(`cwd=${process.cwd()}  mirror=${process.env.GITQ_MIRROR_ROOT || '(未设)'}\n`);

  const init = await call(1, 'initialize', {
    protocolVersion: '2024-11-05',
    capabilities: {},
    clientInfo: { name: 'probe-stdio', version: '1.0.0' },
  });
  ok(!!init.result, `initialize 有响应（server=${init.result?.serverInfo?.name ?? '?'} v${init.result?.serverInfo?.version ?? '?'}）`);
  send({ jsonrpc: '2.0', method: 'notifications/initialized' });

  const list = await call(2, 'tools/list');
  const names = (list.result?.tools ?? []).map((t) => t.name).sort();
  ok(names.includes('search_gitea') && names.includes('read_gitea_file'), `tools/list 工具齐全：${names.join(', ') || '(空)'}`);

  const res = await call(3, 'tools/call', { name: 'search_gitea', arguments: { query: QUERY } });
  const text = res.result?.content?.[0]?.text ?? '';
  const bytes = Buffer.byteLength(text, 'utf8');
  const fffd = (text.match(/\uFFFD/g) ?? []).length;
  const firstLine = text.split('\n')[0] ?? '';
  console.log(`      查询 "${QUERY}" ⇒ ${bytes}B / ${text.split('\n').length} 行；首行：${firstLine.slice(0, 120)}`);
  ok(text.length > 0, 'search_gitea 返回了内容');
  ok(/命中\s*\d+\s*条/.test(firstLine), '首行是「命中 N 条」统计（走的是真实检索路径，不是空壳）');
  ok(fffd === 0, `输出无 U+FFFD（本次 ${fffd} 个）`);
  ok(bytes <= MAX_OUT_BYTES, `输出 ${bytes}B 在上限 ${MAX_OUT_BYTES}B 内（护栏生效）`);
} catch (e) {
  fail += 1;
  console.log(`  ✗ 探针异常：${e.message}`);
  if (stderrText) console.log(`      stderr: ${stderrText.slice(0, 500)}`);
} finally {
  try { p.kill('SIGTERM'); } catch { /* 已退出 */ }
}

console.log(`\n结果：${pass} 绿 / ${fail} 红`);
process.exit(fail ? 1 : 0);
