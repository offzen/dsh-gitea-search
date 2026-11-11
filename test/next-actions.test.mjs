import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { EventEmitter } from 'node:events';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { failureResult } from '../lib/next-actions.mjs';

for (const [raw, context, expected] of [
  [{ code: 2, err: 'CONFIG_ERROR: owners missing' }, 'search', 'CONFIG_ERROR'],
  [{ code: 2, err: 'GIT_SEARCH_FAILED: fixture/demo grep rc=128' }, 'search', 'GIT_SEARCH_FAILED'],
  [{ code: 1, err: 'INDEX_UPDATE_FAILED: rc=42; SYNC_FAILED: exit=1' }, 'search', 'INDEX_UPDATE_FAILED'],
  [{ code: -1, err: 'spawn ENOENT' }, 'repos', 'CLI_START_FAILED'],
  [{ code: -1, err: 'EACCES' }, 'search', 'CLI_START_FAILED'],
  [{ code: null, timedOut: true }, 'search', 'PROCESS_TIMEOUT'],
  [{ code: 2, err: 'SYNC_FAILED: exit=1' }, 'search', 'SYNC_FAILED'],
  [{ code: 2, err: 'SYNC_UNAVAILABLE' }, 'search', 'SYNC_UNAVAILABLE'],
  [{ code: 2, err: 'MIRROR_MISSING' }, 'repos', 'MIRROR_MISSING'],
  [{ code: 2, err: '索引库不存在' }, 'docs', 'INDEX_UNAVAILABLE'],
  [{ code: 1, err: 'ModuleNotFoundError: jieba' }, 'docs', 'DEPENDENCY_MISSING'],
  [{ code: 0, out: 'wrong tool' }, 'search', 'INVALID_CLI_OUTPUT'],
  [{ code: 2, err: 'unclassified marker' }, 'search', 'UNKNOWN_FAILURE'],
]) {
  test(`failure attribution: ${expected}`, () => {
    const result = failureResult(raw, context);
    assert.equal(result.isError, true);
    assert.ok(result.text.startsWith(expected + ':'));
    if (raw.err) assert.ok(result.text.includes(raw.err));
    assert.match(result.text, /POST-INSTALL\.md/);
    assert.doesNotMatch(result.text, /\/C:\//);
  });
}

// ── rc7 B8：run()/carry() 必须真的报告超时，并归因到 PROCESS_TIMEOUT ───────────────────────────
// lib/server.mjs 顶层会连 MCP server，无法 import：从真实源码切出 carry()（run() 的两条 spawn
// 路径都经它收尾），用桩子进程驱动「超时」「普通失败」等路径。
const SERVER_SRC = fs.readFileSync(new URL('../lib/server.mjs', import.meta.url), 'utf8');
function sliceFn(name) {
  const start = SERVER_SRC.indexOf(`function ${name}(`);
  let depth = 0;
  let seen = false;
  for (let i = start; i < SERVER_SRC.length; i += 1) {
    if (SERVER_SRC[i] === '{') { depth += 1; seen = true; }
    else if (SERVER_SRC[i] === '}') {
      depth -= 1;
      if (seen && depth === 0) return SERVER_SRC.slice(start, i + 1);
    }
  }
  throw new Error(`找不到函数 ${name}`);
}

// ── v1.2.3 C1：MCP 传给 argparse 的查询必须与控制选项机械分隔 ─────────────────────
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PY = process.platform === 'win32' ? 'python' : 'python3';

function loadArgBuilders() {
  const body = [
    sliceFn('buildSearchArgs'),
    sliceFn('buildListReposArgs'),
    sliceFn('buildDocsArgs'),
    'return { buildSearchArgs, buildListReposArgs, buildDocsArgs };',
  ].join('\n');
  // eslint-disable-next-line no-new-func
  return new Function(body)();
}

test('C1 MCP argv builders bind option values and terminate options before queries', () => {
  const { buildSearchArgs, buildListReposArgs, buildDocsArgs } = loadArgBuilders();
  const search = buildSearchArgs({
    query: '--reindex', mode: 'message', regex: true, case_sensitive: true,
    files_only: true, repo: '--sync', branch: '--doctor', sync: true,
  }, 7);
  assert.deepEqual(search, [
    '--json', '-n', '7', '-m', '-E', '-s', '-l',
    '--repo=--sync', '--branch=--doctor', '--sync', '--', '--reindex',
  ]);
  assert.equal(search.filter((v) => v === '--sync').length, 1);
  assert.deepEqual(
    buildDocsArgs({ query: '--reindex', repo: '--sync', kind: '--doctor' }, 4),
    ['-D', '--json', '-n', '4', '--repo=--sync', '--kind=--doctor', '--', '--reindex'],
  );
  assert.deepEqual(
    buildListReposArgs({ filter: '--sync' }),
    ['--repos', '--json', '--repo=--sync'],
  );
});

function c1Fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gts-c1-argv-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const app = path.join(dir, 'app');
  fs.mkdirSync(path.join(app, 'bin'), { recursive: true });
  fs.mkdirSync(path.join(app, 'scripts'));
  fs.copyFileSync(path.join(ROOT, 'bin', 'gts'), path.join(app, 'bin', 'gts'));
  fs.cpSync(path.join(ROOT, 'lib'), path.join(app, 'lib'), { recursive: true });
  const marker = path.join(dir, 'SYNC-RAN');
  fs.writeFileSync(
    path.join(app, 'scripts', 'sync.sh'),
    '#!/bin/sh\nprintf "sync\\n" >> "$C1_SYNC_MARKER"\n',
  );
  const mirror = path.join(dir, 'mirror');
  const bare = path.join(mirror, 'fixture', 'demo.git');
  fs.mkdirSync(path.dirname(bare), { recursive: true });
  const init = spawnSync('git', ['init', '--bare', bare], { encoding: 'utf8' });
  assert.equal(init.status, 0, init.stderr);
  const home = path.join(dir, 'home');
  fs.mkdirSync(home);
  const env = { ...process.env, HOME: home, USERPROFILE: home, C1_SYNC_MARKER: marker };
  for (const key of Object.keys(env)) {
    if (/^(GITEA_|GITQ_|GTS_)/.test(key)) delete env[key];
  }
  return {
    gts: path.join(app, 'bin', 'gts'), marker, mirror, env,
    state: path.join(mirror, 'state'),
  };
}

function runC1(fx, args) {
  return spawnSync(PY, [
    fx.gts, '--mirror-root', fx.mirror, '--owners', 'fixture', ...args,
  ], { encoding: 'utf8', env: fx.env, timeout: 15_000 });
}

test('C1 real gts treats option-looking MCP queries only as data', { skip: process.platform === 'win32' }, (t) => {
  const fx = c1Fixture(t);
  const { buildSearchArgs } = loadArgBuilders();
  for (const query of [
    '--sync', '--reindex', '--help', '--doctor', '--read', '-D', '--repos',
    '-x', '--', '--mirror-root=/x',
  ]) {
    const result = runC1(fx, buildSearchArgs({ query }, 5));
    assert.equal(result.status, 1, `${query}: ${result.stderr}`); // clean zero-hit search
    assert.equal(JSON.parse(result.stdout).pattern, query);
    assert.equal(fs.existsSync(fx.marker), false, `${query} must not run sync.sh`);
    assert.equal(fs.existsSync(fx.state), false, `${query} must not create sync state`);
  }
});

test('C1 explicit sync remains control, while the legacy argv shape proves the regression', { skip: process.platform === 'win32' }, (t) => {
  const fx = c1Fixture(t);
  const { buildSearchArgs } = loadArgBuilders();
  const explicit = runC1(fx, buildSearchArgs({ query: 'needle', sync: true }, 5));
  assert.equal(explicit.status, 1, explicit.stderr);
  assert.equal(fs.readFileSync(fx.marker, 'utf8'), 'sync\n');

  fs.rmSync(fx.marker);
  const legacy = runC1(fx, ['--json', '-n', '5', '--sync']);
  assert.equal(legacy.status, 2, legacy.stderr);
  assert.equal(fs.readFileSync(fx.marker, 'utf8'), 'sync\n');
});

// eslint-disable-next-line no-new-func
const carry = new Function(`${sliceFn('carry')}; return carry;`)();
const runCarry = (p, timeoutMs) => new Promise((resolve) => carry(p, timeoutMs, resolve));

/** 桩子进程：kill() 之后异步 emit close(null, signal)，与真实被信号杀死的进程一致。 */
function stubProc() {
  const p = new EventEmitter();
  p.stdout = new EventEmitter();
  p.stderr = new EventEmitter();
  p.kills = [];
  p.kill = (sig) => { p.kills.push(sig); setImmediate(() => p.emit('close', null, sig)); return true; };
  return p;
}

test('B8 超时：挂住的子进程被 kill，run() 结果带 timedOut=true，归因为 PROCESS_TIMEOUT', async () => {
  const p = stubProc();
  const pending = runCarry(p, 20);
  p.stdout.emit('data', Buffer.from('partial'));
  const result = await pending;
  assert.equal(result.timedOut, true);
  assert.equal(result.code, null);
  assert.deepEqual(p.kills, ['SIGKILL']);
  assert.equal(result.out, 'partial');
  const failure = failureResult(result, 'search');
  assert.equal(failure.isError, true);
  assert.ok(failure.text.startsWith('PROCESS_TIMEOUT:'), failure.text);
  assert.doesNotMatch(failure.text, /UNKNOWN_FAILURE/);
});

test('B8 普通失败：非零退出码 timedOut=false，仍归因 UNKNOWN_FAILURE，且没有 kill', async () => {
  const p = stubProc();
  const pending = runCarry(p, 5000);
  p.stderr.emit('data', Buffer.from('boom'));
  p.emit('close', 2, null);
  const result = await pending;
  assert.equal(result.timedOut, false);
  assert.equal(result.code, 2);
  assert.deepEqual(p.kills, []);
  const failure = failureResult(result, 'search');
  assert.ok(failure.text.startsWith('UNKNOWN_FAILURE:'), failure.text);
  assert.ok(failure.text.includes('boom'));
});

test('B8 外部信号终止（非本进程的超时 kill）不得被误判为超时', async () => {
  const p = stubProc();
  const pending = runCarry(p, 5000);
  p.emit('close', null, 'SIGTERM');
  const result = await pending;
  assert.equal(result.timedOut, false);
  assert.ok(failureResult(result, 'search').text.startsWith('UNKNOWN_FAILURE:'));
});

test('B8 子进程在超时前正常结束：不会被事后 kill，timedOut=false', async () => {
  const p = stubProc();
  const pending = runCarry(p, 30);
  p.stdout.emit('data', Buffer.from('{"ok":true}'));
  p.emit('close', 0, null);
  const result = await pending;
  await new Promise((r) => setTimeout(r, 80));
  assert.equal(result.timedOut, false);
  assert.deepEqual(p.kills, []);
});

test('B8 spawn error 路径同样带 timedOut 字段（false）', async () => {
  const p = stubProc();
  const pending = runCarry(p, 5000);
  p.emit('error', new Error('spawn ENOENT'));
  const result = await pending;
  assert.equal(result.timedOut, false);
  assert.equal(result.code, -1);
  assert.ok(failureResult(result, 'search').text.startsWith('CLI_START_FAILED:'));
});

test('B8 真实子进程：sleep 超过 timeout 被杀并报告 timedOut', async () => {
  const child = spawn(process.execPath, ['-e', 'setTimeout(() => {}, 60000)']);
  const started = Date.now();
  const result = await runCarry(child, 300);
  assert.equal(result.timedOut, true);
  assert.ok(Date.now() - started < 10000, '必须在超时后及时返回，而不是等 60s');
  assert.ok(failureResult(result, 'docs').text.startsWith('PROCESS_TIMEOUT:'));
});

test('B8 server.mjs 各调用点把 timedOut 传给 failureResult（超时不会被当成「输出无法解析」）', () => {
  const normalized = SERVER_SRC.replace(/\r\n/g, '\n');
  const calls = normalized.match(/NEXT\.failureResult\(\{[^}]*\}/g) || [];
  assert.equal(calls.length, 4);
  for (const call of calls) assert.match(call, /timedOut/, call);
});

test('B5 failure attribution: ARG_REJECTED 被单独归因并给出可执行的下一步', () => {
  const result = failureResult({ code: -1, err: 'ARG_REJECTED: 参数 #2 "a&b" 含 cmd 元字符 &' }, 'search');
  assert.ok(result.text.startsWith('ARG_REJECTED:'), result.text);
  assert.match(result.text, /gts\.exe/);
});
