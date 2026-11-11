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
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gts-c8-message-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const work = path.join(dir, 'work');
  git(['init', '-b', 'main', work]);
  git(['-C', work, 'config', 'user.name', 'C8 Fixture']);
  git(['-C', work, 'config', 'user.email', 'c8@example.invalid']);
  fs.writeFileSync(path.join(work, 'base.txt'), 'base\n');
  git(['-C', work, 'add', 'base.txt']);
  git(['-C', work, 'commit', '-m', 'main baseline']);
  git(['-C', work, 'checkout', '-b', 'side']);
  fs.writeFileSync(path.join(work, 'side.txt'), 'side\n');
  git(['-C', work, 'add', 'side.txt']);
  git(['-C', work, 'commit', '-m', 'C8SideOnlySentinel']);
  git(['-C', work, 'checkout', 'main']);

  const mirror = path.join(dir, 'mirror');
  fs.mkdirSync(path.join(mirror, 'fixture'), { recursive: true });
  git(['clone', '--bare', work, path.join(mirror, 'fixture', 'demo.git')]);
  const env = { ...process.env, HOME: path.join(dir, 'home'), USERPROFILE: path.join(dir, 'home') };
  fs.mkdirSync(env.HOME);
  for (const key of Object.keys(env)) if (/^(GITEA_|GITQ_|GTS_)/.test(key)) delete env[key];
  return { mirror, env };
}

function search(fx, extra) {
  return spawnSync(PY, [
    path.join(ROOT, 'bin', 'gts'), '--mirror-root', fx.mirror, '--owners', 'fixture',
    '--json', '-m', ...extra, '--', 'C8SideOnlySentinel',
  ], { encoding: 'utf8', env: fx.env, timeout: 15_000 });
}

function payload(result) {
  assert.doesNotThrow(() => JSON.parse(result.stdout), result.stderr || result.stdout);
  return JSON.parse(result.stdout);
}

test('C8 message mode honors an explicit branch scope', { skip: process.platform === 'win32' }, (t) => {
  const fx = fixture(t);
  const main = search(fx, ['-b', 'main']);
  assert.equal(main.status, 1, main.stderr);
  assert.equal(payload(main).total, 0, '-b main must exclude a side-only commit');

  const side = search(fx, ['-b', 'side']);
  assert.equal(side.status, 0, side.stderr);
  assert.equal(payload(side).total, 1);
  assert.equal(payload(side).results[0].subject, 'C8SideOnlySentinel');

  const unfiltered = search(fx, []);
  assert.equal(unfiltered.status, 0, unfiltered.stderr);
  assert.equal(payload(unfiltered).total, 1, 'unfiltered message search must retain all-branches behavior');
});
