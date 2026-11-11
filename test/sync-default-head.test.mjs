import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const POSIX = process.platform !== 'win32';
const REAL_GIT = POSIX
  ? spawnSync('/bin/sh', ['-c', 'command -v git'], { encoding: 'utf8' }).stdout.trim()
  : '';

function runGit(args, env) {
  const result = spawnSync(REAL_GIT, args, { env, encoding: 'utf8' });
  assert.equal(
    result.status,
    0,
    `git ${args.join(' ')}\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}`,
  );
  return result.stdout.trim();
}

function makeFixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gts-c4-default-head-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));

  const app = path.join(dir, 'app');
  const mirror = path.join(dir, 'mirror');
  const upstream = path.join(dir, 'upstream.git');
  const work = path.join(dir, 'work');
  const bare = path.join(mirror, 'fixture', 'demo.git');
  const home = path.join(dir, 'home');
  fs.mkdirSync(path.join(app, 'scripts'), { recursive: true });
  fs.mkdirSync(path.dirname(bare), { recursive: true });
  fs.mkdirSync(home);
  fs.copyFileSync(path.join(ROOT, 'scripts', 'sync.sh'), path.join(app, 'scripts', 'sync.sh'));

  const gitEnv = {
    ...process.env,
    HOME: home,
    GIT_CONFIG_NOSYSTEM: '1',
    GIT_CONFIG_GLOBAL: '/dev/null',
    GIT_AUTHOR_NAME: 'C4 Fixture',
    GIT_AUTHOR_EMAIL: 'c4@example.invalid',
    GIT_COMMITTER_NAME: 'C4 Fixture',
    GIT_COMMITTER_EMAIL: 'c4@example.invalid',
  };

  runGit(['init', '-q', work], gitEnv);
  runGit(['-C', work, 'checkout', '-q', '-b', 'main'], gitEnv);
  fs.writeFileSync(path.join(work, 'README.md'), 'main\n');
  runGit(['-C', work, 'add', 'README.md'], gitEnv);
  runGit(['-C', work, 'commit', '-q', '-m', 'seed main'], gitEnv);
  runGit(['init', '-q', '--bare', upstream], gitEnv);
  runGit(['--git-dir', upstream, 'symbolic-ref', 'HEAD', 'refs/heads/main'], gitEnv);
  runGit(['-C', work, 'remote', 'add', 'origin', upstream], gitEnv);
  runGit(['-C', work, 'push', '-q', 'origin', 'main'], gitEnv);
  runGit(['clone', '-q', '--mirror', upstream, bare], gitEnv);

  const env = {
    ...gitEnv,
    GITEA_MIRROR_ROOT: mirror,
    GITEA_OWNERS: 'fixture',
    GITQ_JOBS: '1',
  };
  for (const key of Object.keys(env)) {
    if (/^(GITEA_|GITQ_|GTS_)/.test(key)
        && !['GITEA_MIRROR_ROOT', 'GITEA_OWNERS', 'GITQ_JOBS'].includes(key)) delete env[key];
  }

  return {
    dir,
    app,
    mirror,
    upstream,
    work,
    bare,
    env,
    gitEnv,
    sync: path.join(app, 'scripts', 'sync.sh'),
  };
}

function runSync(fx, env = fx.env) {
  return spawnSync('/bin/bash', [fx.sync, '-q', '-r', 'demo'], {
    cwd: fx.dir,
    env,
    encoding: 'utf8',
    timeout: 15_000,
  });
}

function installCloneWrapper(fx) {
  const fakeBin = path.join(fx.dir, 'clone-bin');
  fs.mkdirSync(fakeBin);
  fs.writeFileSync(path.join(fakeBin, 'git'), `#!/bin/sh
is_clone=0
dest=
for arg in "$@"; do
  [ "$arg" = clone ] && is_clone=1
  dest=$arg
done
if [ "$is_clone" -eq 1 ]; then
  exec "$C4_REAL_GIT" clone --mirror -q "$C4_UPSTREAM" "$dest"
fi
exec "$C4_REAL_GIT" "$@"
`, { mode: 0o755 });
  return {
    ...fx.env,
    PATH: `${fakeBin}${path.delimiter}${process.env.PATH}`,
    C4_REAL_GIT: REAL_GIT,
    C4_UPSTREAM: fx.upstream,
    GITEA_REMOTE: 'fixture-remote',
  };
}

function assertFailedClosed(fx, result, marker, originalHead = 'refs/heads/main') {
  assert.equal(result.status, 1, `stdout:\n${result.stdout}\nstderr:\n${result.stderr}`);
  assert.equal(result.stdout, '');
  assert.match(result.stderr, new RegExp(`^FAIL head demo :: ${marker}$`, 'm'));
  assert.equal(runGit(['--git-dir', fx.bare, 'symbolic-ref', 'HEAD'], fx.gitEnv), originalHead);
  const status = fs.readFileSync(path.join(fx.mirror, 'state', 'last_status'), 'utf8');
  assert.match(status, /(?:^|\s)ok=0(?:\s|$)/);
  assert.match(status, /(?:^|\s)fail=1(?:\s|$)/);
  assert.equal(fs.existsSync(path.join(fx.mirror, 'state', 'last_ok')), false);
}

test('C4 sync follows a validated upstream default branch change from main to trunk', { skip: !POSIX }, (t) => {
  const fx = makeFixture(t);
  const oid = runGit(['-C', fx.work, 'rev-parse', 'main'], fx.gitEnv);
  runGit(['--git-dir', fx.upstream, 'update-ref', 'refs/heads/aaa-keep', oid], fx.gitEnv);
  runGit(['--git-dir', fx.upstream, 'update-ref', 'refs/heads/trunk', oid], fx.gitEnv);
  runGit(['--git-dir', fx.upstream, 'update-ref', 'refs/heads/zzz-keep', oid], fx.gitEnv);
  runGit(['--git-dir', fx.upstream, 'symbolic-ref', 'HEAD', 'refs/heads/trunk'], fx.gitEnv);
  runGit(['--git-dir', fx.upstream, 'update-ref', '-d', 'refs/heads/main'], fx.gitEnv);

  const result = runSync(fx);

  assert.equal(result.status, 0, `stdout:\n${result.stdout}\nstderr:\n${result.stderr}`);
  assert.equal(result.stdout, '');
  assert.equal(result.stderr, '');
  assert.equal(runGit(['--git-dir', fx.bare, 'symbolic-ref', 'HEAD'], fx.gitEnv), 'refs/heads/trunk');
  assert.equal(runGit(['--git-dir', fx.bare, 'rev-parse', '--verify', 'HEAD'], fx.gitEnv), oid);
  const missingMain = spawnSync(REAL_GIT, ['--git-dir', fx.bare, 'show-ref', '--verify', '--quiet', 'refs/heads/main'], { env: fx.gitEnv });
  assert.equal(missingMain.status, 1);
  assert.equal(fs.existsSync(path.join(fx.bare, 'HEAD.lock')), false);
  assert.equal(fs.existsSync(path.join(fx.mirror, 'state', 'last_ok')), false,
    'successful -r validates the selected repo but cannot claim global mirror freshness');
  assert.match(fs.readFileSync(path.join(fx.mirror, 'state', 'last_status'), 'utf8'), /(?:^|\s)sync_scope=partial(?:\s|$)/);
});

test('C4 sync fails closed when remote HEAD discovery becomes unreachable after fetch', { skip: !POSIX }, (t) => {
  const fx = makeFixture(t);
  const fakeBin = path.join(fx.dir, 'bin');
  const gitCalls = path.join(fx.dir, 'git.calls');
  fs.mkdirSync(fakeBin);
  fs.writeFileSync(path.join(fakeBin, 'git'), `#!/bin/sh
printf '%s\\n' "$*" >> "$C4_GIT_CALLS"
for arg in "$@"; do
  if [ "$arg" = "ls-remote" ]; then
    printf '%s\\n' 'fatal: unable to access https://example.invalid/private/C4_CREDENTIAL_CANARY_93AF' >&2
    exit 128
  fi
done
exec "$C4_REAL_GIT" "$@"
`, { mode: 0o755 });
  const env = {
    ...fx.env,
    PATH: `${fakeBin}${path.delimiter}${process.env.PATH}`,
    C4_REAL_GIT: REAL_GIT,
    C4_GIT_CALLS: gitCalls,
  };

  const result = runSync(fx, env);

  assertFailedClosed(fx, result, 'HEAD_UNVERIFIED_REMOTE_UNREACHABLE');
  const calls = fs.readFileSync(gitCalls, 'utf8').trim().split('\n');
  assert.match(calls[0], /remote update --prune/);
  assert.ok(calls.slice(1).some((line) => /ls-remote --symref/.test(line)), calls.join('\n'));
  assert.doesNotMatch(`${result.stdout}\n${result.stderr}`, /C4_CREDENTIAL_CANARY_93AF/);
});

test('C4 sync fails closed when the remote is empty and preserves the prior mirror HEAD', { skip: !POSIX }, (t) => {
  const fx = makeFixture(t);
  runGit(['--git-dir', fx.upstream, 'update-ref', '-d', 'refs/heads/main'], fx.gitEnv);

  const result = runSync(fx);

  assertFailedClosed(fx, result, 'HEAD_UNVERIFIED_REMOTE_HEAD');
});

test('C4 validates a fresh clone whose upstream HEAD is a non-main branch', { skip: !POSIX }, (t) => {
  const fx = makeFixture(t);
  const oid = runGit(['-C', fx.work, 'rev-parse', 'main'], fx.gitEnv);
  runGit(['--git-dir', fx.upstream, 'update-ref', 'refs/heads/trunk', oid], fx.gitEnv);
  runGit(['--git-dir', fx.upstream, 'symbolic-ref', 'HEAD', 'refs/heads/trunk'], fx.gitEnv);
  fs.rmSync(fx.bare, { recursive: true, force: true });

  const result = runSync(fx, installCloneWrapper(fx));

  assert.equal(result.status, 0, `stdout:\n${result.stdout}\nstderr:\n${result.stderr}`);
  assert.equal(result.stdout, '');
  assert.equal(result.stderr, '');
  assert.equal(runGit(['--git-dir', fx.bare, 'symbolic-ref', 'HEAD'], fx.gitEnv), 'refs/heads/trunk');
  assert.equal(runGit(['--git-dir', fx.bare, 'rev-parse', '--verify', 'HEAD'], fx.gitEnv), oid);
});

test('C4 fails closed after cloning an empty upstream', { skip: !POSIX }, (t) => {
  const fx = makeFixture(t);
  runGit(['--git-dir', fx.upstream, 'update-ref', '-d', 'refs/heads/main'], fx.gitEnv);
  fs.rmSync(fx.bare, { recursive: true, force: true });

  const result = runSync(fx, installCloneWrapper(fx));

  assertFailedClosed(fx, result, 'HEAD_UNVERIFIED_REMOTE_HEAD');
});
