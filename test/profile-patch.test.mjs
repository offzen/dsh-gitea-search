#!/usr/bin/env node
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SCRIPT = path.join(ROOT, 'scripts', 'profile-patch.py');
const PYTHON = process.platform === 'win32' ? 'python' : 'python3';

function run(file, check = false) {
  const args = [SCRIPT, '--file', file, '--node', '/node', '--server', '/app/lib/server.mjs', '--gts', '/python/gts', '--mirror', '/mirror', '--owners', 'team'];
  if (check) args.push('--check');
  return spawnSync(PYTHON, args, { encoding: 'utf8' });
}

test('profile patch replaces the same id and stays idempotent', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gts-profile-'));
  const file = path.join(dir, 'cordis.patch.yml');
  fs.writeFileSync(file, "- insert:\n    - id: keep-me\n      name: keep\n- insert:\n    - id: mcp-gitea-search\n      name: stale\n");
  const first = run(file);
  assert.equal(first.status, 0, first.stderr);
  const once = fs.readFileSync(file, 'utf8');
  assert.equal((once.match(/id: mcp-gitea-search/g) || []).length, 1);
  assert.match(once, /id: keep-me/);
  assert.match(once, /GITEA_OWNERS: 'team'/);
  const second = run(file);
  assert.equal(second.status, 0, second.stderr);
  assert.equal(fs.readFileSync(file, 'utf8'), once);
  assert.equal(fs.readdirSync(dir).filter((name) => name.includes('.bak-')).length, 2);
});

test('profile patch check mode has no write side effect', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gts-profile-'));
  const file = path.join(dir, 'cordis.patch.yml');
  fs.writeFileSync(file, '[]\n');
  const before = fs.readFileSync(file);
  const result = run(file, true);
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(fs.readFileSync(file), before);
  assert.match(result.stdout, /id: mcp-gitea-search/);
  assert.equal(fs.readdirSync(dir).length, 1);
});
