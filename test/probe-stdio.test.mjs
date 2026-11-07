#!/usr/bin/env node
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PROBE = path.join(ROOT, 'scripts', 'probe-stdio.mjs');

function makeServer(dir, source) {
  const file = path.join(dir, 'fake-mcp-server.mjs');
  fs.writeFileSync(file, source);
  return file;
}

function runProbe(server) {
  return spawnSync(process.execPath, [PROBE, server, 'gitea'], {
    encoding: 'utf8',
    timeout: 5000,
    env: { ...process.env, GITQ_MIRROR_ROOT: '/tmp/test-mirror' },
  });
}

test('probe reports child stderr and request phase when MCP child exits before replying', (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'probe-stdio-test-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const server = makeServer(dir, `
    process.stderr.write('fixture child failure\\n');
    process.exit(23);
  `);

  const result = runProbe(server);
  assert.equal(result.status, 1, result.stderr);
  assert.match(result.stderr, /阶段=initialize/);
  assert.match(result.stderr, /fixture child failure/);
  assert.match(result.stderr, /code=23/);
  assert.match(result.stderr, /子进程退出：code=23/);
});

test('Node reports spawn ENOENT via the error event without an unhandled exception', () => {
  const result = spawnSync(process.execPath, ['-e', `
    const { spawn } = require('node:child_process');
    const child = spawn('definitely-not-a-real-executable-rc5', []);
    child.once('error', (error) => {
      console.error('spawn error: ' + error.code);
      process.exitCode = 2;
    });
    child.once('close', () => {});
  `], { encoding: 'utf8', timeout: 5000 });
  assert.equal(result.status, 2, result.stderr);
  assert.match(result.stderr, /spawn error: ENOENT/);
  assert.doesNotMatch(result.stderr, /Unhandled 'error' event/);
});
