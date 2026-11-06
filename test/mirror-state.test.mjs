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
