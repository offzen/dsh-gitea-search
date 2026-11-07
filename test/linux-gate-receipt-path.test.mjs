#!/usr/bin/env node
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const GATE = path.join(ROOT, 'scripts', 'ci', 'linux-gate.sh');
const GATE_SOURCE = fs.readFileSync(GATE, 'utf8');
const GUARD_START = GATE_SOURCE.indexOf('if [[ -z "$RECEIPT_PATH" ]]; then', GATE_SOURCE.indexOf('WORKFLOW_REF='));
const GUARD_END = GATE_SOURCE.indexOf('RECEIPT_TMP=', GUARD_START);
const GUARD = GATE_SOURCE.slice(GUARD_START, GUARD_END);
assert.notEqual(GUARD_START, -1);
assert.notEqual(GUARD_END, -1);

function runGuard(receipt) {
  const fixture = fs.mkdtempSync(path.join(os.tmpdir(), 'linux-gate-guard-'));
  const script = path.join(fixture, 'guard.sh');
  fs.writeFileSync(script, `#!/usr/bin/env bash\nset -euo pipefail\nROOT_DIR=/repo\nRECEIPT_PATH=${JSON.stringify(receipt)}\n${GUARD}`);
  const result = spawnSync('bash', [script], { encoding: 'utf8', timeout: 5000 });
  fs.rmSync(fixture, { recursive: true, force: true });
  return result;
}

test('Linux gate refuses to reuse an existing receipt', (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'linux-gate-receipt-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const receipt = path.join(dir, 'receipt.json');
  const stale = '{"status":"success","commit":"old"}\n';
  fs.writeFileSync(receipt, stale);

  const result = runGuard(receipt);
  assert.equal(result.status, 67, result.stderr);
  assert.match(result.stderr, /receipt path already exists/);
  assert.equal(fs.readFileSync(receipt, 'utf8'), stale);
});

test('Linux gate rejects a symlink receipt path', { skip: process.platform === 'win32' && 'Windows symlink creation requires elevated privilege' }, (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'linux-gate-receipt-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const target = path.join(dir, 'target.json');
  const receipt = path.join(dir, 'receipt.json');
  fs.writeFileSync(target, '{"status":"success"}\n');
  fs.symlinkSync(target, receipt);

  const result = runGuard(receipt);
  assert.equal(result.status, 67, result.stderr);
  assert.match(result.stderr, /receipt path already exists/);
  assert.equal(fs.existsSync(target), true);
});
