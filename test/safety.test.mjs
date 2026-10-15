import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { assertRef, assertRelPath, readBlob, scanRepos } from '../lib/mirror.mjs';
import { run } from '../lib/git.mjs';
import { makeMirror } from './helpers/fixture.mjs';

test('assertRef rejects option-shaped, range and surprising refs', () => {
  for (const bad of ['--output=/tmp/x', '-c', 'main..other', 'main:x', 'a b', '..']) {
    assert.throws(() => assertRef(bad), undefined, `should reject ${JSON.stringify(bad)}`);
  }
  for (const good of ['HEAD', 'main', 'v1.0.0', 'refs/tags/v1', 'feature/foo_bar-1', '0.2.0-rc.2']) {
    assert.equal(assertRef(good), good);
  }
});

test('assertRelPath rejects traversal and empty segments', () => {
  for (const bad of ['../../etc/passwd', 'a//b', './a', 'a/./b', 'a/../b', '']) {
    assert.throws(() => assertRelPath(bad), undefined, `should reject ${JSON.stringify(bad)}`);
  }
  assert.throws(() => assertRelPath('a\0b'), /NUL/);
  assert.equal(assertRelPath('/etc/passwd'), 'etc/passwd', 'a leading slash is stripped');
  assert.equal(assertRelPath('deploy/compose.yml'), 'deploy/compose.yml');
});

test('git show --end-of-options blocks the --output write primitive', async (t) => {
  const fx = await makeMirror([{ owner: 'team', name: 'app' }]);
  t.after(fx.cleanup);
  const repo = [...scanRepos(fx.config).values()][0];

  // Control: without the guard git really writes an arbitrary file.
  const target = path.join(os.tmpdir(), `gitea-search-pwn-${process.pid}.txt`);
  fs.rmSync(target, { force: true });
  const res = await run('git', ['-c', 'core.quotePath=false', `--git-dir=${repo.dir}`, 'show', `--output=${target}`, 'HEAD']);
  assert.equal(res.code, 0);
  assert.ok(fs.existsSync(target), 'control: --output must be able to write a file');
  fs.rmSync(target, { force: true });

  // Guard: the option is rejected before anything is written.
  const blocked = await run('git', ['-c', 'core.quotePath=false', `--git-dir=${repo.dir}`, 'show', '--end-of-options', `--output=${target}`]);
  assert.notEqual(blocked.code, 0);
  assert.ok(!fs.existsSync(target), 'with --end-of-options nothing is written');
});

test('readBlob never reaches git with a dangerous ref or path', async (t) => {
  const fx = await makeMirror([{ owner: 'team', name: 'app' }]);
  t.after(fx.cleanup);
  const repo = [...scanRepos(fx.config).values()][0];

  await assert.rejects(() => readBlob(fx.config, repo, { filePath: 'README.md', ref: '--output=/tmp/x' }), /ref/);
  await assert.rejects(() => readBlob(fx.config, repo, { filePath: '../../etc/passwd' }), /\.\./);

  const missing = await readBlob(fx.config, repo, { filePath: 'no/such/file.md' });
  assert.equal(missing.ok, false);
  const badRepo = await readBlob(fx.config, { id: 'nope', owner: '', name: 'nope', dir: '/nonexistent' }, { filePath: 'x' });
  assert.equal(badRepo.ok, false);
});