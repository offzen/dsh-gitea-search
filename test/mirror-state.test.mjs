import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { readSyncState, normalizeTimestamp } from '../lib/mirror-state.mjs';
import { staleNotice } from '../lib/next-actions.mjs';

const lib = fileURLToPath(new URL('../lib', import.meta.url));
const py = process.platform === 'win32' ? 'python' : 'python3';
for (const [name, files, expected] of [
  ['manual mirror', {}, 'unrecorded'],
  ['attempt is not success', { last_sync: '2026-11-01 12:00:00' }, 'unknown'],
  ['failed attempt after old success', { last_sync: '2026-11-05T12:00:00Z', 'state/last_ok': '2026-11-01 12:00:00', 'state/last_status': 'ok=1 fail=2 total=3' }, 'failed'],
  ['successful sync', { last_sync: '2026-11-05T12:00:00+08:00', 'state/last_ok': '2026-11-05T12:00:00+08:00', 'state/last_status': 'fail=0' }, 'success'],
  ['corrupt date', { last_sync: '2026-02-30 12:00:00' }, 'invalid'],
  ['corrupt record', { 'state/last_status': 'nonsense' }, 'invalid'],
  ['empty timestamp', { last_sync: '' }, 'invalid'],
]) {
  test(`sync state parity: ${name}`, (t) => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'gts-state-'));
    t.after(() => fs.rmSync(root, { recursive: true, force: true }));
    for (const [filename, data] of Object.entries(files)) {
      const target = path.join(root, filename);
      fs.mkdirSync(path.dirname(target), { recursive: true }); fs.writeFileSync(target, data);
    }
    const result = spawnSync(py, ['-c', 'import sys,json;sys.path.insert(0,sys.argv[1]);from mirror_state import read_sync_state;print(json.dumps(read_sync_state(sys.argv[2])))', lib, root], { encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr);
    const state = readSyncState(root);
    assert.deepEqual(state, JSON.parse(result.stdout));
    assert.equal(state.status, expected);
  });
}
test('freshness uses only successful sync; invalid/future dates do not warn', () => {
  const now = Date.parse('2026-11-06T12:00:00Z');
  assert.equal(staleNotice({ status: 'unknown', last_attempt: '2026-11-01T00:00:00Z' }, now), '');
  assert.match(staleNotice({ status: 'failed', last_success: '2026-11-01T00:00:00Z' }, now), /MIRROR_STALE/);
  assert.equal(staleNotice({ status: 'invalid', last_success: '2026-11-01T00:00:00Z' }, now), '');
  assert.equal(staleNotice({ status: 'success', last_success: '2026-11-07T00:00:00Z' }, now), '');
});
test('fractional seconds normalize identically for Python and Node', (t) => {
  assert.equal(normalizeTimestamp('2026-11-06T10:00:00.5Z'), '2026-11-06T10:00:00.500000+00:00');
  assert.equal(normalizeTimestamp('2026-11-06T10:00:00.12345+08:00'), '2026-11-06T10:00:00.123450+08:00');
  assert.equal(normalizeTimestamp('2026-11-06T10:00:00.1234567Z'), '2026-11-06T10:00:00.123456+00:00');
  const result = spawnSync(py, ['-c', 'import sys;sys.path.insert(0,sys.argv[1]);from mirror_state import _normalize_timestamp;print(_normalize_timestamp("2026-11-06T10:00:00.5Z"))', lib], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), normalizeTimestamp('2026-11-06T10:00:00.5Z'));
});

// ── rc7 B1：bin/gts 的 mirror_state import 必须在 sys.path 注入之后 ─────────────────────────────
const GTS = fileURLToPath(new URL('../bin/gts', import.meta.url));
function failedMirror(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'gts-b1-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, 'state'), { recursive: true });
  fs.mkdirSync(path.join(root, 'o'), { recursive: true });
  fs.writeFileSync(path.join(root, 'last_sync'), '2026-11-01 10:00:00');
  fs.writeFileSync(path.join(root, 'state/last_status'), 'ok=0 fail=1 total=1');
  return root;
}
function gtsEnv(root) {
  const env = { ...process.env, GITEA_MIRROR_ROOT: root, GITEA_OWNERS: 'o' };
  delete env.PYTHONPATH;
  return env;
}

test('B1 bin/gts --repos 在失败的同步记录上必须给出「同步失败」提示（mirror_state 只能靠 sys.path 注入导入）', (t) => {
  const root = failedMirror(t);
  // cwd 与 PYTHONPATH 都不含 lib/：mirror_state 只有在 bin/gts 自己注入 ../lib 之后才能 import。
  // 旧实现把 import 写在注入之前，ImportError 被吞，这里完全没有提示。
  const r = spawnSync(py, [GTS, '--repos'], { encoding: 'utf8', env: gtsEnv(root), cwd: os.tmpdir() });
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stderr, /镜像同步状态: 最近同步失败/);
  assert.doesNotMatch(r.stderr, /无法导入 mirror_state/);
  assert.match(r.stdout, /共 0 个仓/);
});

test('B1 mirror_state 仍然导入失败时，--repos 必须在 stderr 明确说明而不是静默丢失提示', (t) => {
  const root = failedMirror(t);
  const sandbox = fs.mkdtempSync(path.join(os.tmpdir(), 'gts-b1-bin-'));
  t.after(() => fs.rmSync(sandbox, { recursive: true, force: true }));
  fs.mkdirSync(path.join(sandbox, 'bin'));
  fs.mkdirSync(path.join(sandbox, 'lib'));
  fs.copyFileSync(GTS, path.join(sandbox, 'bin', 'gts'));
  fs.copyFileSync(path.join(lib, 'config.py'), path.join(sandbox, 'lib', 'config.py')); // 故意不带 mirror_state.py
  const r = spawnSync(py, [path.join(sandbox, 'bin', 'gts'), '--repos'], { encoding: 'utf8', env: gtsEnv(root), cwd: os.tmpdir() });
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stderr, /警告：无法导入 mirror_state 模块（.*mirror_state.*）/);
  assert.match(r.stderr, /无法判定镜像同步状态/);
  assert.match(r.stdout, /共 0 个仓/, '--repos 本身仍应走到真实输出分支');
});

test('B1 只在 --repos 时才报告 mirror_state 缺失（其他模式不额外噪声）', (t) => {
  const root = failedMirror(t);
  const sandbox = fs.mkdtempSync(path.join(os.tmpdir(), 'gts-b1-bin-'));
  t.after(() => fs.rmSync(sandbox, { recursive: true, force: true }));
  fs.mkdirSync(path.join(sandbox, 'bin'));
  fs.mkdirSync(path.join(sandbox, 'lib'));
  fs.copyFileSync(GTS, path.join(sandbox, 'bin', 'gts'));
  fs.copyFileSync(path.join(lib, 'config.py'), path.join(sandbox, 'lib', 'config.py'));
  const r = spawnSync(py, [path.join(sandbox, 'bin', 'gts'), '--show-effective-config'], { encoding: 'utf8', env: gtsEnv(root), cwd: os.tmpdir() });
  assert.equal(r.status, 0, r.stderr);
  assert.doesNotMatch(r.stderr, /mirror_state/);
});
