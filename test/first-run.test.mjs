import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PY = process.platform === 'win32' ? 'python' : 'python3';

function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gts-first-run-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const home = path.join(dir, 'home');
  fs.mkdirSync(home);
  const env = { ...process.env, HOME: home, USERPROFILE: home };
  for (const key of Object.keys(env)) {
    if (/^(GITEA_|GITQ_|GTS_)/.test(key)) delete env[key];
  }
  return { dir, env };
}

function syncCli(dir, env, args, syncExit = 0) {
  const capture = path.join(dir, 'sync-env.json');
  const code = `
import json, pathlib, runpy, subprocess, sys
from unittest.mock import patch
def sync(command, **kwargs):
    pathlib.Path(${JSON.stringify(capture)}).write_text(json.dumps({'command': command, 'env': kwargs.get('env')}), encoding='utf-8')
    return subprocess.CompletedProcess(command, ${syncExit})
sys.argv = [${JSON.stringify(path.join(ROOT, 'bin', 'gts'))}, *json.loads(${JSON.stringify(JSON.stringify(args))})]
with patch('subprocess.run', side_effect=sync):
    runpy.run_path(sys.argv[0], run_name='__main__')
`;
  const result = spawnSync(PY, ['-c', code], { encoding: 'utf8', env, timeout: 15000 });
  return { result, captured: fs.existsSync(capture) ? JSON.parse(fs.readFileSync(capture, 'utf8')) : null };
}

for (const source of ['json', 'config-option', 'cli']) {
  test(`--sync passes effective ${source} mirror and owner to the child`, (t) => {
    const { dir, env } = fixture(t);
    const mirror = path.join(dir, 'mirror with spaces');
    fs.mkdirSync(path.join(mirror, 'selected', 'demo.git'), { recursive: true });
    const config = path.join(dir, 'config.json');
    fs.writeFileSync(config, JSON.stringify({ mirror_root: mirror, owners: ['selected'] }));
    const args = ['--sync', '--repos', '--json'];
    if (source === 'json') env.GITEA_SEARCH_CONFIG = config;
    else if (source === 'config-option') args.push('--config', config);
    else {
      env.GITEA_MIRROR_ROOT = path.join(dir, 'ambient');
      env.GITEA_OWNERS = 'ambient';
      args.push('--mirror-root', mirror, '--owners', 'selected');
    }
    env.GITEA_TOKEN = 'fixture-token';
    const { result, captured } = syncCli(dir, env, args);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(captured.env.GITEA_MIRROR_ROOT, mirror);
    assert.equal(captured.env.GITEA_OWNERS, 'selected');
    assert.equal(captured.env.GITEA_TOKEN, 'fixture-token');
    assert.deepEqual(JSON.parse(result.stdout).repos, ['demo']);
  });
}

test('--sync preserves a failed child exit and stops the search', (t) => {
  const { dir, env } = fixture(t);
  const { result, captured } = syncCli(dir, env, ['--owners', 'selected', '--sync', '--repos', '--json'], 7);
  assert.equal(result.status, 7, result.stderr);
  assert.ok(captured);
  assert.match(result.stderr, /SYNC_FAILED/);
  assert.equal(result.stdout, '');
});

test('--sync rejects multiple owners before a partial or wrong-target sync', (t) => {
  const { dir, env } = fixture(t);
  const { result, captured } = syncCli(dir, env, ['--owners', 'one,two', '--sync', '--repos']);
  assert.equal(result.status, 2);
  assert.equal(captured, null);
  assert.match(result.stderr, /exactly one owner/);
});

test('--sync rejects invalid explicit configuration before starting a child', (t) => {
  const { dir, env } = fixture(t);
  const { result, captured } = syncCli(dir, env, ['--config', path.join(dir, 'missing.json'), '--owners', 'team', '--sync']);
  assert.equal(result.status, 2);
  assert.equal(captured, null);
  assert.match(result.stderr, /CONFIG_ERROR/);
});

const POSIX = process.platform !== 'win32';
function onboarding(t, status, valid = true) {
  const { dir, env } = fixture(t);
  const app = path.join(dir, '安装目录 with spaces');
  fs.mkdirSync(path.join(app, 'bin'), { recursive: true });
  fs.mkdirSync(path.join(app, 'lib'));
  fs.mkdirSync(path.join(app, 'scripts'));
  fs.copyFileSync(path.join(ROOT, 'bin', 'gts'), path.join(app, 'bin', 'gts'));
  fs.chmodSync(path.join(app, 'bin', 'gts'), 0o755);
  for (const name of ['config.py', 'indexer.py', 'mirror_state.py']) {
    fs.copyFileSync(path.join(ROOT, 'lib', name), path.join(app, 'lib', name));
  }
  fs.copyFileSync(path.join(ROOT, 'scripts', 'onboarding.sh'), path.join(app, 'scripts', 'onboarding.sh'));
  const mirror = path.join(dir, 'mirror');
  fs.mkdirSync(path.join(mirror, 'team', 'demo.git'), { recursive: true });
  if (status === 'failed') {
    fs.mkdirSync(path.join(mirror, 'state'));
    fs.writeFileSync(path.join(mirror, 'state', 'last_status'), 'ok=0 fail=1 total=1\n');
  }
  const config = path.join(dir, 'config.json');
  fs.writeFileSync(config, JSON.stringify({ mirror_root: mirror, owners: valid ? ['team'] : [] }));
  env.GITEA_SEARCH_CONFIG = config;
  env.GTS_BIN = path.join(app, 'bin', 'gts');
  return spawnSync('bash', [path.join(app, 'scripts', 'onboarding.sh'), '--verify'], { encoding: 'utf8', env, timeout: 15000 });
}

for (const status of ['unrecorded', 'failed']) {
  test(`onboarding parses real JSON despite ${status} sync warnings and a spaced Unicode CLI path`, { skip: !POSIX }, (t) => {
    const result = onboarding(t, status);
    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
    assert.match(result.stdout, /Mirrored repositories: 1/);
    assert.match(result.stdout, /FTS index: missing/);
    assert.match(result.stderr, /镜像同步状态/);
    assert.doesNotMatch(result.stderr, /JSONDecodeError|No such file/);
  });
}

test('onboarding reports invalid configuration without a JSON traceback', { skip: !POSIX }, (t) => {
  const result = onboarding(t, 'unrecorded', false);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /FAIL: effective config is invalid/);
  assert.doesNotMatch(result.stderr, /JSONDecodeError|Traceback/);
});
