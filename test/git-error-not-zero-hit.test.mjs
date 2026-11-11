#!/usr/bin/env node
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PY = process.platform === 'win32' ? 'python' : 'python3';

function git(args, options = {}) {
  const result = spawnSync('git', args, { encoding: 'utf8', ...options });
  assert.equal(result.status, 0, `git ${args.join(' ')}\n${result.stderr}`);
  return result;
}

function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gts-c2-errors-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));

  const app = path.join(dir, 'app');
  fs.mkdirSync(path.join(app, 'bin'), { recursive: true });
  fs.copyFileSync(path.join(ROOT, 'bin', 'gts'), path.join(app, 'bin', 'gts'));
  fs.chmodSync(path.join(app, 'bin', 'gts'), 0o755);
  fs.cpSync(path.join(ROOT, 'lib'), path.join(app, 'lib'), { recursive: true });

  const work = path.join(dir, 'work');
  git(['init', '-b', 'main', work]);
  git(['-C', work, 'config', 'user.name', 'C2 Fixture']);
  git(['-C', work, 'config', 'user.email', 'c2@example.invalid']);
  fs.writeFileSync(path.join(work, 'sample.txt'), 'C2UniqueHit\nordinary text\n');
  git(['-C', work, 'add', 'sample.txt']);
  git(['-C', work, 'commit', '-m', 'fixture']);

  const mirror = path.join(dir, 'mirror');
  const ownerDir = path.join(mirror, 'fixture');
  fs.mkdirSync(ownerDir, { recursive: true });
  git(['clone', '--bare', work, path.join(ownerDir, 'good.git')]);

  // A directory ending in .git is discovered as a mirror, but it is intentionally
  // not a Git repository. This fails deterministically even as root; chmod fixtures do not.
  fs.mkdirSync(path.join(ownerDir, 'broken.git'));

  const home = path.join(dir, 'home');
  fs.mkdirSync(home);
  const env = { ...process.env, HOME: home, USERPROFILE: home, LC_ALL: 'C' };
  for (const key of Object.keys(env)) {
    if (/^(GITEA_|GITQ_|GTS_)/.test(key)) delete env[key];
  }
  return { gts: path.join(app, 'bin', 'gts'), mirror, env };
}

function runGts(fx, args) {
  return spawnSync(PY, [
    fx.gts, '--mirror-root', fx.mirror, '--owners', 'fixture', ...args,
  ], { encoding: 'utf8', env: fx.env, timeout: 15_000 });
}

function parseJson(result) {
  assert.doesNotThrow(() => JSON.parse(result.stdout), result.stderr || result.stdout);
  return JSON.parse(result.stdout);
}

test('C2 true no-hit remains a complete clean result', { skip: process.platform === 'win32' }, (t) => {
  const fx = fixture(t);
  const result = runGts(fx, ['--json', '--repo=fixture/good', '--', 'DefinitelyAbsentC2']);
  assert.equal(result.status, 1, result.stderr);
  const payload = parseJson(result);
  assert.equal(payload.total, 0);
  assert.deepEqual(payload.results, []);
  assert.equal(payload.complete, true);
  assert.equal(payload.repos_failed, 0);
  assert.deepEqual(payload.errors, []);
  assert.equal(result.stderr, '');
});

test('C2 invalid ERE is a structured error, not a zero-hit result', { skip: process.platform === 'win32' }, (t) => {
  const fx = fixture(t);
  const result = runGts(fx, ['--json', '--repo=fixture/good', '-E', '--', '(']);
  assert.equal(result.status, 2, result.stderr);
  const payload = parseJson(result);
  assert.equal(payload.complete, false);
  assert.equal(payload.repos_failed, 1);
  assert.equal(payload.total, 0);
  assert.deepEqual(payload.results, []);
  assert.equal(payload.errors.length, 1);
  assert.equal(payload.errors[0].repo, 'fixture/good');
  assert.equal(payload.errors[0].stage, 'grep');
  assert.ok(Number(payload.errors[0].rc) >= 2, JSON.stringify(payload.errors[0]));
  assert.match(result.stderr, /GIT_SEARCH_FAILED/);
});

test('C2 one broken repo makes partial hits incomplete and nonzero', { skip: process.platform === 'win32' }, (t) => {
  const fx = fixture(t);
  const result = runGts(fx, ['--json', '--', 'C2UniqueHit']);
  assert.equal(result.status, 2, result.stderr);
  const payload = parseJson(result);
  assert.equal(payload.complete, false);
  assert.equal(payload.repos_failed, 1);
  assert.equal(payload.total, 1);
  assert.equal(payload.results[0].repo, 'fixture/good');
  assert.deepEqual(payload.errors.map((error) => error.repo), ['fixture/broken']);
  assert.equal(payload.errors[0].stage, 'refs');
  assert.ok(Number(payload.errors[0].rc) >= 2, JSON.stringify(payload.errors[0]));
  assert.match(result.stderr, /GIT_SEARCH_FAILED/);
});

async function callServerTool(fx, args) {
  const { Client } = await import('@modelcontextprotocol/sdk/client/index.js');
  const { StdioClientTransport } = await import('@modelcontextprotocol/sdk/client/stdio.js');
  const env = {
    ...fx.env,
    GITEA_MIRROR_ROOT: fx.mirror,
    GITEA_OWNERS: 'fixture',
    GTS_BIN: fx.gts,
  };
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [path.join(ROOT, 'lib', 'server.mjs')],
    env,
    stderr: 'ignore',
  });
  const client = new Client({ name: 'c2-test', version: '0' }, { capabilities: {} });
  try {
    await client.connect(transport);
    return await client.callTool({ name: 'search_gitea', arguments: args });
  } finally {
    await client.close().catch(() => {});
  }
}

test('C2 MCP propagates invalid ERE as GIT_SEARCH_FAILED', { skip: process.platform === 'win32', timeout: 30_000 }, async (t) => {
  const fx = fixture(t);
  const result = await callServerTool(fx, { query: '(', regex: true, repo: 'fixture/good' });
  assert.equal(result.isError, true);
  assert.match(result.content[0].text, /^GIT_SEARCH_FAILED:/);
  assert.match(result.content[0].text, /fixture\/good/);
  assert.doesNotMatch(result.content[0].text, /（无命中/);
});
