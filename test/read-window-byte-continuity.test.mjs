#!/usr/bin/env node
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function git(args, options = {}) {
  const result = spawnSync('git', args, { encoding: 'utf8', ...options });
  assert.equal(result.status, 0, `git ${args.join(' ')}\n${result.stderr}`);
  return result;
}

function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gts-c5-read-window-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));

  const work = path.join(dir, 'work');
  git(['init', '-b', 'main', work]);
  git(['-C', work, 'config', 'user.name', 'C5 Fixture']);
  git(['-C', work, 'config', 'user.email', 'c5@example.invalid']);

  const sourceLines = Array.from(
    { length: 520 },
    (_, index) => `LINE-${String(index + 1).padStart(4, '0')}-${'x'.repeat(190)}-END`,
  );
  fs.writeFileSync(path.join(work, 'large.txt'), `${sourceLines.join('\n')}\n`);
  fs.writeFileSync(
    path.join(work, 'oversized.txt'),
    `BEFORE-END\nOVERSIZED-${'界'.repeat(24_000)}-END\nAFTER-END\n`,
  );
  git(['-C', work, 'add', 'large.txt', 'oversized.txt']);
  git(['-C', work, 'commit', '-m', 'large line fixture']);

  const mirror = path.join(dir, 'mirror');
  fs.mkdirSync(path.join(mirror, 'fixture'), { recursive: true });
  git(['clone', '--bare', work, path.join(mirror, 'fixture', 'demo.git')]);

  const home = path.join(dir, 'home');
  fs.mkdirSync(home);
  const env = {
    ...process.env,
    HOME: home,
    USERPROFILE: home,
    GITEA_MIRROR_ROOT: mirror,
    GITEA_OWNERS: 'fixture',
    GITQ_MAX_READ_BYTES: '60000',
    GITQ_READ_DEFAULT_LINES: '400',
  };
  for (const key of Object.keys(env)) {
    if (/^(GITEA_|GITQ_|GTS_)/.test(key) && ![
      'GITEA_MIRROR_ROOT', 'GITEA_OWNERS', 'GITQ_MAX_READ_BYTES', 'GITQ_READ_DEFAULT_LINES',
    ].includes(key)) delete env[key];
  }
  return { env, sourceLines };
}

async function callReadTool(env, args) {
  const { Client } = await import('@modelcontextprotocol/sdk/client/index.js');
  const { StdioClientTransport } = await import('@modelcontextprotocol/sdk/client/stdio.js');
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [path.join(ROOT, 'lib', 'server.mjs')],
    env,
    stderr: 'ignore',
  });
  const client = new Client({ name: 'c5-read-window-test', version: '0' }, { capabilities: {} });
  try {
    await client.connect(transport);
    const result = await client.callTool({ name: 'read_gitea_file', arguments: args });
    assert.notEqual(result.isError, true, result.content?.[0]?.text);
    return result.content[0].text;
  } finally {
    await client.close().catch(() => {});
  }
}

function bodyFrom(text) {
  const bodyStart = text.indexOf('\n\n') + 2;
  const noteStart = text.indexOf('\n\n…（', bodyStart);
  return text.slice(bodyStart, noteStart >= 0 ? noteStart : text.length);
}

function completeLineNumbers(text) {
  return [...text.matchAll(/^LINE-(\d{4})-x+-END$/gm)].map((match) => Number(match[1]));
}

function continuationOffset(text, required = true) {
  const matches = [...text.matchAll(/续读：offset=(\d+)/g)];
  assert.ok(matches.length <= 1, `multiple continuation offsets:\n${text.slice(-500)}`);
  if (!matches.length) {
    if (required) assert.fail(`missing continuation offset:\n${text.slice(-500)}`);
    return null;
  }
  return Number(matches[0][1]);
}

async function readAllWindows(env, limit) {
  const numbers = [];
  let offset = 0;
  for (let attempt = 0; attempt < 10; attempt += 1) {
    const text = await callReadTool(env, {
      repo: 'fixture/demo', path: 'large.txt', offset, limit,
    });
    const page = completeLineNumbers(text);
    assert.ok(page.length > 0);
    assert.ok(Buffer.byteLength(bodyFrom(text)) <= 60000);
    numbers.push(...page);
    const next = continuationOffset(text, false);
    if (next === null) return numbers;
    assert.equal(next, offset + page.length);
    offset = next;
  }
  assert.fail('continuation did not finish within 10 reads');
}

test('C5 byte-capped default read continuation emits only complete contiguous lines', { timeout: 30_000 }, async (t) => {
  const fx = fixture(t);
  const first = await callReadTool(fx.env, { repo: 'fixture/demo', path: 'large.txt' });
  const firstNumbers = completeLineNumbers(first);
  assert.ok(firstNumbers.length > 290, 'implementation must pack close to the byte budget');
  assert.ok(firstNumbers.length < 400, 'fixture must hit the byte cap before the default line cap');
  assert.deepEqual(firstNumbers, Array.from({ length: firstNumbers.length }, (_, index) => index + 1));
  const bodyLines = bodyFrom(first).split('\n');
  assert.ok(bodyLines.every((line) => /^LINE-\d{4}-x+-END$/.test(line)), 'partial line must not be emitted');

  const offset = continuationOffset(first);
  assert.equal(offset, firstNumbers.at(-1));
  assert.match(first, new RegExp(`已显示第 1-${firstNumbers.at(-1)} 行`));
  assert.doesNotMatch(first, /已显示第 1-400 行|offset=400/);

  const allNumbers = await readAllWindows(fx.env, 400);
  assert.deepEqual(allNumbers, Array.from({ length: 520 }, (_, index) => index + 1));
});

test('C5 limit=0 remains byte-bounded and provides a truthful continuation', { timeout: 30_000 }, async (t) => {
  const fx = fixture(t);
  const first = await callReadTool(fx.env, { repo: 'fixture/demo', path: 'large.txt', limit: 0 });
  const numbers = completeLineNumbers(first);
  assert.ok(numbers.length > 290 && numbers.length < 520);
  assert.ok(Buffer.byteLength(bodyFrom(first)) <= 60000);
  assert.equal(continuationOffset(first), numbers.at(-1));
});

test('C5 oversized single line is marked partial and continuation still progresses', { timeout: 30_000 }, async (t) => {
  const fx = fixture(t);
  const first = await callReadTool(fx.env, { repo: 'fixture/demo', path: 'oversized.txt', limit: 0 });
  assert.equal(bodyFrom(first), 'BEFORE-END');
  assert.equal(continuationOffset(first), 1);

  const partial = await callReadTool(fx.env, {
    repo: 'fixture/demo', path: 'oversized.txt', offset: 1, limit: 0,
  });
  assert.match(partial, /^OVERSIZED-/m);
  assert.doesNotMatch(partial, /OVERSIZED-.*-END/m, 'oversized line must not be claimed complete');
  assert.match(partial, /partial/i);
  const offset = continuationOffset(partial);
  assert.equal(offset, 2, 'continuation must advance past the partial second line');

  const last = await callReadTool(fx.env, {
    repo: 'fixture/demo', path: 'oversized.txt', offset, limit: 0,
  });
  assert.match(last, /^AFTER-END$/m);
});
