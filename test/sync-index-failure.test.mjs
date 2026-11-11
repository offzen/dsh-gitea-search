import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const POSIX = process.platform !== 'win32';

function writeExecutable(file, content) {
  fs.writeFileSync(file, content, { mode: 0o755 });
}

test('R1 sync.sh exposes indexer rc=42 without refreshing last_ok', { skip: !POSIX }, (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gts-r1-index-failure-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));

  const app = path.join(dir, 'app');
  const mirror = path.join(dir, 'mirror');
  const state = path.join(mirror, 'state');
  const fakeBin = path.join(dir, 'bin');
  const gitCalls = path.join(dir, 'git.calls');
  const pythonCalls = path.join(dir, 'python3.calls');
  fs.mkdirSync(path.join(app, 'scripts'), { recursive: true });
  fs.mkdirSync(path.join(app, 'lib'));
  fs.mkdirSync(path.join(mirror, 'fixture', 'demo.git'), { recursive: true });
  fs.mkdirSync(state);
  fs.mkdirSync(fakeBin);
  fs.copyFileSync(path.join(ROOT, 'scripts', 'sync.sh'), path.join(app, 'scripts', 'sync.sh'));
  fs.writeFileSync(path.join(app, 'lib', 'indexer.py'), '# R1 fixture: existence selects the indexer path\n');

  writeExecutable(path.join(fakeBin, 'git'), `#!/bin/sh
printf '%s\\n' "$*" >> "$R1_GIT_CALLS"
exit 0
`);
  writeExecutable(path.join(fakeBin, 'python3'), `#!/bin/sh
printf '%s\\n' "$*" >> "$R1_PYTHON_CALLS"
exit "${'$'}{R1_INDEX_RC:-42}"
`);

  const lastOk = path.join(state, 'last_ok');
  fs.writeFileSync(lastOk, '2000-01-01 00:00:00\n');
  const oldTime = new Date('2000-01-02T03:04:05Z');
  fs.utimesSync(lastOk, oldTime, oldTime);
  const before = { content: fs.readFileSync(lastOk, 'utf8'), mtimeMs: fs.statSync(lastOk).mtimeMs };

  const env = {
    ...process.env,
    HOME: path.join(dir, 'home'),
    GITEA_MIRROR_ROOT: mirror,
    GITEA_OWNERS: 'fixture',
    GITQ_JOBS: '1',
    PATH: `${fakeBin}${path.delimiter}${process.env.PATH}`,
    R1_GIT_CALLS: gitCalls,
    R1_PYTHON_CALLS: pythonCalls,
    R1_INDEX_RC: '42',
  };
  for (const key of Object.keys(env)) {
    if (/^(GITEA_|GITQ_|GTS_)/.test(key)
        && !['GITEA_MIRROR_ROOT', 'GITEA_OWNERS', 'GITQ_JOBS'].includes(key)) delete env[key];
  }

  const result = spawnSync('/bin/bash', [path.join(app, 'scripts', 'sync.sh'), '-q', '-r', 'demo'], {
    cwd: dir, env, encoding: 'utf8', timeout: 15_000,
  });

  assert.equal(result.status, 1, `stdout:\n${result.stdout}\nstderr:\n${result.stderr}`);
  assert.match(result.stderr, /INDEX_UPDATE_FAILED:.*rc=42/);
  assert.equal(result.stdout, '');
  assert.match(fs.readFileSync(gitCalls, 'utf8'), /remote update --prune/);
  assert.equal(
    fs.readFileSync(pythonCalls, 'utf8').trim(),
    `${path.join(app, 'scripts')}/../lib/indexer.py --build`,
  );

  const status = fs.readFileSync(path.join(state, 'last_status'), 'utf8');
  assert.match(status, /(?:^|\s)ok=1(?:\s|$)/);
  assert.match(status, /(?:^|\s)fail=0(?:\s|$)/);
  assert.match(status, /(?:^|\s)index_status=failed(?:\s|$)/);
  assert.match(status, /(?:^|\s)index_rc=42(?:\s|$)/);
  assert.equal(fs.readFileSync(lastOk, 'utf8'), before.content);
  assert.equal(fs.statSync(lastOk).mtimeMs, before.mtimeMs);

  env.R1_INDEX_RC = '0';
  const control = spawnSync('/bin/bash', [path.join(app, 'scripts', 'sync.sh'), '-q', '-r', 'demo'], {
    cwd: dir, env, encoding: 'utf8', timeout: 15_000,
  });
  assert.equal(control.status, 0, `stdout:\n${control.stdout}\nstderr:\n${control.stderr}`);
  assert.equal(control.stdout, '');
  assert.equal(control.stderr, '');
  const controlStatus = fs.readFileSync(path.join(state, 'last_status'), 'utf8');
  assert.match(controlStatus, /(?:^|\s)index_status=ok(?:\s|$)/);
  assert.match(controlStatus, /(?:^|\s)index_rc=0(?:\s|$)/);
  assert.notEqual(fs.readFileSync(lastOk, 'utf8'), before.content);
  assert.notEqual(fs.statSync(lastOk).mtimeMs, before.mtimeMs);
});
