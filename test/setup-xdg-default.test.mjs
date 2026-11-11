#!/usr/bin/env node
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const POSIX = process.platform !== 'win32';
const REAL_PYTHON = POSIX ? spawnSync('/bin/sh', ['-c', 'command -v python3'], { encoding: 'utf8' }).stdout.trim() : '';
const REAL_NODE = process.execPath;

function writeExe(file, content) {
  fs.writeFileSync(file, content, { mode: 0o755 });
}

function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gts-d4-xdg-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const app = path.join(dir, 'app');
  const home = path.join(dir, 'home');
  const xdg = path.join(dir, 'xdg-data');
  const bin = path.join(dir, 'stub-bin');
  const profiles = path.join(dir, 'profiles');
  for (const p of [path.join(app, 'scripts'), path.join(app, 'bin'), path.join(app, 'lib'), path.join(app, 'node_modules', '@modelcontextprotocol', 'sdk'), home, xdg, bin, path.join(profiles, 'headless')]) fs.mkdirSync(p, { recursive: true });
  for (const rel of ['scripts/setup.sh', 'scripts/profile-patch.py']) fs.copyFileSync(path.join(ROOT, rel), path.join(app, rel));
  for (const rel of ['scripts/dsh-session-health.mjs', 'bin/gts', 'bin/mcp-server', 'lib/indexer.py', 'lib/server.mjs']) fs.writeFileSync(path.join(app, rel), '# fixture\n');
  fs.writeFileSync(path.join(profiles, 'headless', 'cordis.patch.yml'), '[]\n');
  writeExe(path.join(bin, 'python3'), `#!/bin/sh
[ "$1" = "-c" ] && exit 0
exec ${JSON.stringify(REAL_PYTHON)} "$@"
`);
  writeExe(path.join(bin, 'node'), `#!/bin/sh
[ "$1" = "-e" ] && exit 0
case "$1" in *dsh-session-health.mjs) exit 0 ;; esac
exec ${JSON.stringify(REAL_NODE)} "$@"
`);
  writeExe(path.join(bin, 'git'), '#!/bin/sh\necho "git version 2.45.0"\n');
  return { dir, app, home, xdg, bin, profiles };
}

function runSetup(fx, extra = {}) {
  const env = {
    ...process.env,
    HOME: fx.home,
    XDG_DATA_HOME: fx.xdg,
    PATH: `${fx.bin}:/usr/bin:/bin`,
    GITEA_OWNERS: 'fixture-owner',
    DSH_PROFILES_ROOT: fx.profiles,
    SKIP_GLOBAL_LINKS: '1',
    ...extra,
  };
  for (const key of ['GITEA_MIRROR_ROOT', 'GITEA_MIRROR_PATH', 'GITQ_MIRROR_ROOT']) {
    if (!(key in extra)) delete env[key];
  }
  return spawnSync('/bin/bash', [path.join(fx.app, 'scripts', 'setup.sh'), '--install-dsh', '--profile', 'headless'], {
    cwd: fx.dir, env, encoding: 'utf8', timeout: 15_000,
  });
}

test('D4 Linux setup profile uses the shared XDG mirror default', { skip: !POSIX }, (t) => {
  const fx = fixture(t);
  const result = runSetup(fx);
  assert.equal(result.status, 0, `stdout:\n${result.stdout}\nstderr:\n${result.stderr}`);
  const profile = fs.readFileSync(path.join(fx.profiles, 'headless', 'cordis.patch.yml'), 'utf8');
  const expected = path.join(fx.xdg, 'dsh-gitea-search', 'mirror');
  assert.ok(profile.includes(expected), `expected mirror ${expected}\n${profile}`);
  assert.ok(!profile.includes(path.join(fx.home, '.local', 'share', 'dsh-gitea-search', 'mirror')));
});
