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

function writeExecutable(file, content) {
  fs.writeFileSync(file, content, { mode: 0o755 });
}

function baseFixture(t, label) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), `gts-${label}-`));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const app = path.join(dir, 'app');
  const mirror = path.join(dir, 'mirror');
  const state = path.join(mirror, 'state');
  const fakeBin = path.join(dir, 'bin');
  fs.mkdirSync(path.join(app, 'scripts'), { recursive: true });
  fs.mkdirSync(path.join(app, 'lib'));
  fs.mkdirSync(path.join(mirror, 'fixture'), { recursive: true });
  fs.mkdirSync(state);
  fs.mkdirSync(fakeBin);
  fs.copyFileSync(path.join(ROOT, 'scripts', 'sync.sh'), path.join(app, 'scripts', 'sync.sh'));
  fs.writeFileSync(path.join(app, 'lib', 'indexer.py'), '# Stage 6 fixture\n');
  writeExecutable(path.join(fakeBin, 'python3'), '#!/bin/sh\nexit 0\n');
  const env = {
    ...process.env,
    HOME: path.join(dir, 'home'),
    USERPROFILE: path.join(dir, 'home'),
    GITEA_MIRROR_ROOT: mirror,
    GITEA_OWNERS: 'fixture',
    GITQ_JOBS: '1',
    PATH: `${fakeBin}${path.delimiter}${process.env.PATH}`,
  };
  fs.mkdirSync(env.HOME);
  for (const key of Object.keys(env)) {
    if (/^(GITEA_|GITQ_|GTS_)/.test(key)
        && !['GITEA_MIRROR_ROOT', 'GITEA_OWNERS', 'GITQ_JOBS'].includes(key)) delete env[key];
  }
  return {
    dir, app, mirror, state, fakeBin, env,
    sync: path.join(app, 'scripts', 'sync.sh'),
  };
}

test('D1 sync fails closed when the executor runs zero repository jobs', { skip: !POSIX }, (t) => {
  const fx = baseFixture(t, 'd1-zero-jobs');
  writeExecutable(path.join(fx.fakeBin, 'xargs'), '#!/bin/sh\ncat >/dev/null\nexit 0\n');

  const lastOk = path.join(fx.state, 'last_ok');
  fs.writeFileSync(lastOk, 'D1-OLD-LAST-OK\n');
  const oldTime = new Date('2001-02-03T04:05:06Z');
  fs.utimesSync(lastOk, oldTime, oldTime);
  const before = { content: fs.readFileSync(lastOk, 'utf8'), mtimeMs: fs.statSync(lastOk).mtimeMs };

  const result = spawnSync('/bin/bash', [fx.sync, '-q', '-r', 'demo'], {
    cwd: fx.dir, env: fx.env, encoding: 'utf8', timeout: 15_000,
  });
  const status = fs.readFileSync(path.join(fx.state, 'last_status'), 'utf8');

  assert.match(status, /(?:^|\s)ok=0(?:\s|$)/);
  assert.match(status, /(?:^|\s)total=1(?:\s|$)/);
  assert.notEqual(result.status, 0, `stdout:\n${result.stdout}\nstderr:\n${result.stderr}`);
  assert.match(status, /(?:^|\s)fail=1(?:\s|$)/);
  assert.match(status, /(?:^|\s)sync_status=unaccounted(?:\s|$)/);
  assert.equal(fs.readFileSync(lastOk, 'utf8'), before.content);
  assert.equal(fs.statSync(lastOk).mtimeMs, before.mtimeMs);
});

test('D2 a failed later API page never becomes an authoritative partial enumeration', { skip: !POSIX }, (t) => {
  const fx = baseFixture(t, 'd2-partial-page');
  const page1 = path.join(fx.dir, 'page1.json');
  const curlCalls = path.join(fx.dir, 'curl.calls');
  fs.writeFileSync(page1, JSON.stringify(Array.from({ length: 50 }, (_, index) => ({
    name: `partial-${String(index + 1).padStart(2, '0')}`,
    has_wiki: false,
  }))));
  writeExecutable(path.join(fx.fakeBin, 'curl'), `#!/bin/sh
url=
for arg in "$@"; do
  case "$arg" in http://*|https://*) url=$arg ;; esac
done
printf '%s\n' "$url" >> "$D2_CURL_CALLS"
case "$url" in
  *users/fixture/repos*page=1*) cat "$D2_PAGE1"; exit 0 ;;
  *users/fixture/repos*page=2*) exit 22 ;;
  *orgs/fixture/repos*) exit 22 ;;
esac
exit 22
`);

  const cache = path.join(fx.state, 'repos.cache');
  const cacheBefore = '# D2 pre-existing complete cache\ncache-a\n';
  fs.writeFileSync(cache, cacheBefore);
  const env = {
    ...fx.env,
    GITQ_ENUM: 'api',
    GITEA_TOKEN: 'D2_TOKEN_CANARY_NOT_SECRET',
    D2_PAGE1: page1,
    D2_CURL_CALLS: curlCalls,
  };
  const result = spawnSync('/bin/bash', [fx.sync, '--list'], {
    cwd: fx.dir, env, encoding: 'utf8', timeout: 15_000,
  });

  assert.equal(result.status, 0, `stdout:\n${result.stdout}\nstderr:\n${result.stderr}`);
  assert.match(result.stdout, /(?:^|\n)cache-a\n$/, 'complete cache fallback must supply the repository');
  assert.equal(fs.readFileSync(cache, 'utf8'), cacheBefore, 'partial API data must not overwrite cache');
  assert.doesNotMatch(result.stdout, /partial-/, 'partial API names must not escape');
  const calls = fs.readFileSync(curlCalls, 'utf8');
  assert.match(calls, /users\/fixture\/repos.*page=2/);
  assert.match(calls, /orgs\/fixture\/repos/);
});

test('D5 partial repository sync does not refresh global last_ok', { skip: !POSIX }, (t) => {
  const fx = baseFixture(t, 'd5-partial-freshness');
  const pythonCalls = path.join(fx.dir, 'python.calls');
  fs.mkdirSync(path.join(fx.mirror, 'fixture', 'demo.git'));
  writeExecutable(path.join(fx.fakeBin, 'python3'), `#!/bin/sh
printf '%s\n' "$*" >> "$D5_PYTHON_CALLS"
exit 0
`);
  writeExecutable(path.join(fx.fakeBin, 'git'), `#!/bin/sh
case " $* " in
  *" remote update --prune "*) exit 0 ;;
  *" remote ") printf '%s\n' origin; exit 0 ;;
  *" ls-remote --symref -- origin HEAD "*)
    printf 'ref: refs/heads/main\\tHEAD\n'
    printf '1111111111111111111111111111111111111111\\tHEAD\n'
    exit 0
    ;;
  *" check-ref-format refs/heads/main "*) exit 0 ;;
  *" show-ref --verify --hash refs/heads/main "*)
    printf '%s\n' 1111111111111111111111111111111111111111
    exit 0
    ;;
  *" symbolic-ref HEAD refs/heads/main "*) exit 0 ;;
esac
exit 1
`);
  const lastOk = path.join(fx.state, 'last_ok');
  fs.writeFileSync(lastOk, 'D5-GLOBAL-SENTINEL\n');
  const oldTime = new Date('2002-03-04T05:06:07Z');
  fs.utimesSync(lastOk, oldTime, oldTime);
  const before = { content: fs.readFileSync(lastOk, 'utf8'), mtimeMs: fs.statSync(lastOk).mtimeMs };
  const env = { ...fx.env, D5_PYTHON_CALLS: pythonCalls };

  const result = spawnSync('/bin/bash', [fx.sync, '-q', '-r', 'demo'], {
    cwd: fx.dir, env, encoding: 'utf8', timeout: 15_000,
  });
  assert.equal(result.status, 0, `stdout:\n${result.stdout}\nstderr:\n${result.stderr}`);
  assert.equal(fs.readFileSync(lastOk, 'utf8'), before.content);
  assert.equal(fs.statSync(lastOk).mtimeMs, before.mtimeMs);
  assert.match(fs.readFileSync(pythonCalls, 'utf8'), /--build --no-global-prune/);
  assert.match(fs.readFileSync(path.join(fx.state, 'last_status'), 'utf8'), /(?:^|\s)sync_scope=partial(?:\s|$)/);
});

test('D3 HTTPS credentials stay out of argv, origin config, files, and output', { skip: !POSIX }, (t) => {
  const fx = baseFixture(t, 'd3-https-credential');
  const gitCalls = path.join(fx.dir, 'git.calls');
  const authCalls = path.join(fx.dir, 'auth.calls');
  const canary = ['D3', 'FAKE', 'TOKEN', 'CANARY'].join('-');
  writeExecutable(path.join(fx.fakeBin, 'git'), `#!/bin/bash
printf '%s\n' "$*" >> "$D3_GIT_CALLS"
auth() {
  user=$(${'$'}GIT_ASKPASS 'Username for fixture') || exit 91
  pass=$(${'$'}GIT_ASKPASS 'Password for fixture') || exit 92
  [ "$user" = fixture-user ] || exit 93
  [ "$pass" = "$D3_EXPECT_TOKEN" ] || exit 94
  printf '%s\n' "$1" >> "$D3_AUTH_CALLS"
}
case " $* " in
  *" clone --mirror -q "*)
    prev=
    for arg in "$@"; do prev2=$prev; prev=$arg; done
    url=$prev2; dest=$prev
    case "$url" in *":$D3_EXPECT_TOKEN@"*) ;; *) auth clone ;; esac
    mkdir -p "$dest"
    printf '[remote "origin"]\n\turl = %s\n' "$url" > "$dest/config"
    exit 0
    ;;
  *" remote update --prune "*) [ -z "${'$'}{GIT_ASKPASS:-}" ] || auth fetch; exit 0 ;;
  *" remote ") printf '%s\n' origin; exit 0 ;;
  *" ls-remote --symref -- origin HEAD "*)
    [ -z "${'$'}{GIT_ASKPASS:-}" ] || auth head
    printf 'ref: refs/heads/main\\tHEAD\n'
    printf '1111111111111111111111111111111111111111\\tHEAD\n'
    exit 0
    ;;
  *" check-ref-format refs/heads/main "*) exit 0 ;;
  *" show-ref --verify --hash refs/heads/main "*)
    printf '%s\n' 1111111111111111111111111111111111111111
    exit 0
    ;;
  *" symbolic-ref HEAD refs/heads/main "*) exit 0 ;;
esac
exit 1
`);
  const env = {
    ...fx.env,
    GITQ_TRANSPORT: 'https',
    GITEA_URL: 'https://forge.invalid',
    GITEA_TOKEN: canary,
    GITQ_GIT_USER: 'fixture-user',
    D3_EXPECT_TOKEN: canary,
    D3_GIT_CALLS: gitCalls,
    D3_AUTH_CALLS: authCalls,
  };
  const run = () => spawnSync('/bin/bash', [fx.sync, '-q', '-r', 'demo'], {
    cwd: fx.dir, env, encoding: 'utf8', timeout: 15_000,
  });

  const first = run();
  assert.equal(first.status, 0, `stdout:\n${first.stdout}\nstderr:\n${first.stderr}`);
  const second = run();
  assert.equal(second.status, 0, `stdout:\n${second.stdout}\nstderr:\n${second.stderr}`);
  const authText = fs.existsSync(authCalls) ? fs.readFileSync(authCalls, 'utf8') : '';
  const observed = [
    fs.readFileSync(gitCalls, 'utf8'),
    fs.readFileSync(path.join(fx.mirror, 'fixture', 'demo.git', 'config'), 'utf8'),
    fs.existsSync(path.join(fx.mirror, 'sync.log')) ? fs.readFileSync(path.join(fx.mirror, 'sync.log'), 'utf8') : '',
    first.stdout, first.stderr, second.stdout, second.stderr,
  ].join('\n');
  assert.match(authText, /clone[\s\S]*fetch[\s\S]*head/, 'HTTPS clone and later fetch/head must use askpass');
  assert.doesNotMatch(observed, new RegExp(canary), 'credential canary must never be observable or persisted');
  assert.match(observed, /https:\/\/fixture-user@forge\.invalid\/fixture\/demo\.git/);
});
