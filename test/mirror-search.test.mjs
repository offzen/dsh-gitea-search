import { test } from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import {
  defaultBranch, listRefs, matches, mirrorInfo, pickRepos, readBlob, readLastSync, scanRepos,
} from '../lib/mirror.mjs';
import { formatJson, formatText, search } from '../lib/search.mjs';
import { makeMirror } from './helpers/fixture.mjs';

const spec = [
  { owner: 'team', name: 'app' },
  { owner: 'team', name: 'libs' },
  { owner: 'ops', name: 'svc' },
  { owner: '', name: 'flatrepo' },
];

test('scanRepos understands owner and flat layouts', async (t) => {
  const fx = await makeMirror(spec);
  t.after(fx.cleanup);
  const repos = scanRepos(fx.config);
  assert.deepEqual([...repos.keys()].sort(), ['flatrepo', 'ops/svc', 'team/app', 'team/libs']);
  assert.ok(repos.get('team/app').dir.endsWith(path.join('team', 'app.git')));
  assert.equal(readLastSync(fx.config), '2026-01-01T00:00:00Z');
  assert.equal(mirrorInfo(fx.config, repos).repoCount, 4);
});

test('matches and pickRepos implement glob and substring semantics', async (t) => {
  const fx = await makeMirror(spec);
  t.after(fx.cleanup);
  assert.equal(matches('team/app', 'team/*'), true);
  assert.equal(matches('team/app', 'app'), true, 'substring matches the name inside the id');
  assert.equal(matches('apps', 'app?'), true);
  assert.equal(matches('team/app', 'app?'), false, 'the glob is anchored, it must not match the id');
  assert.equal(matches('team/app', 'libs'), false);
  const repos = scanRepos(fx.config);
  assert.deepEqual(pickRepos(repos, ['team/*']).map((r) => r.id), ['team/app', 'team/libs']);
  assert.deepEqual(pickRepos(repos, ['app', 'svc']).map((r) => r.id).sort(), ['ops/svc', 'team/app']);
  assert.equal(pickRepos(repos, []).length, 4);
});

test('refs, default branch and CJK reads over a fixture mirror', async (t) => {
  const fx = await makeMirror(spec);
  t.after(fx.cleanup);
  const repos = scanRepos(fx.config);
  const repo = repos.get('team/app');

  assert.deepEqual((await listRefs(repo.dir)).sort(), ['dev', 'main']);
  assert.equal(await defaultBranch(repo.dir), 'main');

  const read = await readBlob(fx.config, repo, { filePath: 'cjk.txt' });
  assert.equal(read.ok, true);
  assert.ok(!read.body.includes('\uFFFD'), 'CJK read produced U+FFFD');
  assert.ok(read.body.includes('护栏'));

  const big = await readBlob(fx.config, repo, { filePath: 'big.txt' });
  assert.equal(big.ok, true);
  assert.equal(big.truncated, true, '5000-byte file must be truncated at maxReadBytes=2048');
  assert.ok(Buffer.byteLength(big.body, 'utf8') <= fx.config.maxReadBytes);

  const missing = await readBlob(fx.config, repo, { filePath: 'no/such.md' });
  assert.equal(missing.ok, false);
});

test('search covers content, path, message modes and every filter', async (t) => {
  const fx = await makeMirror(spec);
  t.after(fx.cleanup);
  const repos = [...scanRepos(fx.config).values()];

  const content = await search(fx.config, repos, { pattern: 'needle', max: 200 });
  assert.ok(content.total >= 5, `expected hits, got ${content.total}`);
  const hit = content.results.find((r) => r.path === 'src/app.js');
  assert.ok(hit, 'expected a hit in src/app.js');
  assert.ok(hit.line && hit.text.includes('needle'));

  const devOnly = await search(fx.config, repos, { pattern: 'needle in branch', max: 200 });
  assert.ok(devOnly.total >= 1);
  assert.ok(devOnly.results.every((r) => r.ref === 'dev'), 'this text exists only on the dev branch');

  const noDev = await search(fx.config, repos, { pattern: 'needle in branch', branch: 'main', max: 200 });
  assert.equal(noDev.total, 0, 'branch filter must exclude the dev-only hit');

  const heads = await search(fx.config, repos, { pattern: 'needle in branch', branch: 'HEAD', max: 200 });
  assert.equal(heads.total, 0, 'HEAD means the default branch (main)');

  const upper = await search(fx.config, repos, { pattern: 'NEEDLE', caseSensitive: true, max: 200 });
  assert.equal(upper.total, 0, 'case sensitive must miss the lowercase needle');

  const regex = await search(fx.config, repos, { pattern: 'need[a-z]e', regex: true, max: 200 });
  assert.ok(regex.total >= 5, 'regex should match needle');

  const words = await search(fx.config, repos, { pattern: 'needle', word: true, max: 200 });
  assert.ok(words.total >= 5, 'whole-word still matches "needle marker"');

  const pathMode = await search(fx.config, repos, { pattern: 'docs/note', mode: 'path', max: 200 });
  assert.ok(pathMode.total >= 1);
  assert.equal(pathMode.results[0].path, 'docs/note.txt');

  const msgMode = await search(fx.config, repos, { pattern: 'branch work', mode: 'message', max: 200 });
  assert.ok(msgMode.total >= 1);
  assert.ok(msgMode.results.every((r) => r.commit && r.subject.includes('branch work')));

  const filesOnly = await search(fx.config, repos, { pattern: 'needle', filesOnly: true, max: 200 });
  assert.ok(filesOnly.results.every((r) => !r.line && !r.text));

  const counted = await search(fx.config, repos, { pattern: 'needle', count: true, max: 200 });
  assert.ok(counted.results.length >= 1 && Number(counted.results[0].text) > 0);

  const truncated = await search(fx.config, repos, { pattern: 'needle', max: 1 });
  assert.equal(truncated.results.length, 1);
  assert.equal(truncated.truncated, true);

  const zero = await search(fx.config, repos, { pattern: 'zzz-no-such-token', max: 200 });
  assert.equal(zero.total, 0);
  assert.deepEqual(zero.results, []);

  const text = formatText(content);
  assert.ok(text.includes('team/app@dev:cjk.txt:1:'), 'text output carries repo@ref:path:line:text');
  assert.ok(/-- \d+ lines in \d+ repositor/.test(text), `unexpected footer: ${text.slice(-120)}`);
  const json = JSON.parse(formatJson(content));
  assert.equal(json.pattern, 'needle');
  assert.ok(Array.isArray(json.results));
});