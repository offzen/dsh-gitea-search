#!/usr/bin/env node
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const SRC = fs.readFileSync(new URL('../lib/server.mjs', import.meta.url), 'utf8');

function sliceFunction(name) {
  const start = SRC.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`missing ${name}`);
  let depth = 0;
  let seen = false;
  for (let i = start; i < SRC.length; i += 1) {
    if (SRC[i] === '{') { depth += 1; seen = true; }
    else if (SRC[i] === '}') {
      depth -= 1;
      if (seen && depth === 0) return SRC.slice(start, i + 1);
    }
  }
  throw new Error(`unbalanced ${name}`);
}

function resolver(root, simulateWin = false) {
  const body = `${sliceFunction('resolveRepo')}\n${sliceFunction('gitDir')}\nreturn { resolveRepo, gitDir };`;
  return new Function('fs', 'path', 'MIRROR_ROOT', 'OWNERS', 'IS_WIN', body)(
    fs, path, root, ['team', 'other'], simulateWin,
  );
}

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'a1-mcp-owner-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  for (const owner of ['team', 'other']) fs.mkdirSync(path.join(root, owner, 'demo.git'), { recursive: true });
  return root;
}

test('MCP exact owner/repo resolves the requested mirror, not the first owner', (t) => {
  const root = fixture(t);
  const r = resolver(root).resolveRepo('other/demo');
  assert.equal(r.repo, 'other/demo');
  assert.equal(r.dir, path.join(root, 'other', 'demo.git'));
});

test('MCP bare duplicate repo fails closed with all qualified candidates', (t) => {
  const root = fixture(t);
  const r = resolver(root).resolveRepo('demo');
  assert.match(r.error, /^AMBIGUOUS_REPO:/);
  assert.match(r.error, /team\/demo/);
  assert.match(r.error, /other\/demo/);
  assert.equal(resolver(root).gitDir('demo'), null);
});

test('MCP Windows owner\\repo normalizes to canonical owner/repo', (t) => {
  const root = fixture(t);
  const r = resolver(root, true).resolveRepo('other\\demo');
  assert.equal(r.repo, 'other/demo');
  assert.equal(r.dir, path.join(root, 'other', 'demo.git'));
});
