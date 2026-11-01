#!/usr/bin/env node
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SETUP = path.join(ROOT, 'scripts', 'setup-macos.sh');
const SETUP_LINUX = path.join(ROOT, 'scripts', 'setup.sh');
const IS_POSIX = process.platform !== 'win32';
const PYTHON = IS_POSIX ? spawnSync('sh', ['-c', 'command -v python3'], { encoding: 'utf8' }).stdout.trim() : '';

function makeStub(dir, name, body) {
  const file = path.join(dir, name);
  fs.writeFileSync(file, `#!/usr/bin/env bash\n${body}\n`, { mode: 0o755 });
}

function run(profile) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gts-macos-setup-'));
  const home = path.join(dir, 'home');
  const bin = path.join(dir, 'bin');
  const profiles = path.join(home, '.dsh', 'profiles');
  fs.mkdirSync(bin, { recursive: true });
  for (const name of ['desktop', 'web']) {
    const target = path.join(profiles, name);
    fs.mkdirSync(target, { recursive: true });
    fs.writeFileSync(path.join(target, 'cordis.patch.yml'), '[]\n');
  }
  makeStub(bin, 'python3', `
if [[ "$1" == "--version" ]]; then echo 'Python 3.12.0'; exit 0; fi
if [[ "$1" == "-c" ]]; then exit 0; fi
exec ${JSON.stringify(PYTHON)} "$@"
`);
  makeStub(bin, 'node', `
if [[ "$1" == "-e" ]]; then exit 0; fi
exec ${JSON.stringify(process.execPath)} "$@"
`);
  makeStub(bin, 'git', "if [[ \"$1\" == '--version' ]]; then echo 'git version 2.45.0'; fi; exit 0");
  for (const name of ['jq', 'zstd', 'npm']) makeStub(bin, name, 'exit 0');
  const result = spawnSync('bash', [SETUP, '--install-dsh', '--profile', profile], {
    cwd: ROOT,
    encoding: 'utf8',
    env: {
      ...process.env,
      HOME: home,
      PATH: `${bin}:/usr/bin:/bin`,
      DSH_PROFILES_ROOT: profiles,
      GITEA_OWNERS: 'ci-owner',
      GITEA_MIRROR_ROOT: path.join(dir, 'mirror'),
      GITQ_VENV_DIR: path.join(dir, 'venv'),
    },
  });
  return { dir, profiles, result };
}

test('POSIX installers share explicit profile selection and the patcher', () => {
  for (const file of [SETUP_LINUX, SETUP]) {
    const setup = fs.readFileSync(file, 'utf8');
    assert.match(setup, /desktop\|web\|headless/);
    assert.match(setup, /\ball\)/);
    assert.match(setup, /\bAuto\)/);
    assert.match(setup, /profile-patch\.py/);
    assert.doesNotMatch(setup, /\bmapfile\b/);
  }
});

test('setup-macos requires explicit profile when Auto finds multiple profiles', { skip: !IS_POSIX && 'requires a POSIX shell runtime' }, () => {
  const { result } = run('Auto');
  assert.equal(result.status, 2, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout + result.stderr, /Auto.*恰好一个 profile/);
});

test('setup-macos updates only the selected profile', { skip: !IS_POSIX && 'requires a POSIX shell runtime' }, () => {
  const { profiles, result } = run('desktop');
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  const desktop = fs.readFileSync(path.join(profiles, 'desktop', 'cordis.patch.yml'), 'utf8');
  const web = fs.readFileSync(path.join(profiles, 'web', 'cordis.patch.yml'), 'utf8');
  assert.equal((desktop.match(/id: mcp-gitea-search/g) || []).length, 1);
  assert.equal(web, '[]\n');
});
