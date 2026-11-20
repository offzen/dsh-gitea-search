#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
/**
 * test/tool-aliases.test.mjs
 * End-to-end verification of:
 *   1. All three tool name families (neutral v2 / search_gitea* / search_gitorca*)
 *      producing equivalent results while legacy calls emit stderr deprecation warnings.
 *   2. GITEA_SEARCH_CLI > GTS_BIN > GITQ_BIN precedence and fail-closed missing CLI.
 *   3. Read-only SQLite user_version precheck in scripts/doctor-mcp.sh.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PY = process.platform === 'win32' ? 'python' : 'python3';
const POSIX = process.platform !== 'win32';

function git(args, options = {}) {
  const r = spawnSync('git', args, { encoding: 'utf8', ...options });
  assert.equal(r.status, 0, `git ${args.join(' ')}\n${r.stderr}`);
  return r;
}

function makeMirrorFixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gts-aliases-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));

  const work = path.join(dir, 'work');
  git(['init', '-q', '-b', 'main', work]);
  git(['-C', work, 'config', 'user.name', 'Alias Fixture']);
  git(['-C', work, 'config', 'user.email', 'alias@example.invalid']);
  fs.writeFileSync(path.join(work, 'README.md'), '# Alias Demo\n\nAlphaSentinelToken for alias routing verification.\n');
  git(['-C', work, 'add', 'README.md']);
  git(['-C', work, 'commit', '-q', '-m', 'init alias fixture']);

  const mirror = path.join(dir, 'mirror');
  const bare = path.join(mirror, 'team', 'demo.git');
  fs.mkdirSync(path.dirname(bare), { recursive: true });
  git(['clone', '-q', '--bare', work, bare]);
  fs.writeFileSync(path.join(mirror, 'last_sync'), '2026-11-21 00:00:00\n');

  const home = path.join(dir, 'home');
  fs.mkdirSync(home);

  const env = {
    ...process.env,
    HOME: home,
    USERPROFILE: home,
    GITEA_MIRROR_ROOT: mirror,
    GITEA_OWNERS: 'team',
    GITEA_ALLOW_NO_JIEBA: '1',
  };
  for (const key of Object.keys(env)) {
    if (/^(GITQ_|GTS_|GITEA_SEARCH_)/.test(key)) delete env[key];
  }

  // Build FTS5 index so search_docs / search_gitea_docs / search_gitorca_docs return real hits
  const idx = spawnSync(PY, [path.join(ROOT, 'lib', 'indexer.py'), '--build', '--full'], {
    encoding: 'utf8',
    env,
    timeout: 15000,
  });
  assert.equal(idx.status, 0, `indexer build failed:\n${idx.stderr}`);

  // On Windows, create a .cmd wrapper so spawn works without pip install in isolated unit test
  if (!POSIX) {
    const wrapper = path.join(dir, 'gts-wrapper.cmd');
    fs.writeFileSync(wrapper, `@echo off\r\n${PY} "${path.join(ROOT, 'bin', 'gts')}" %*\r\n`);
    env.GITEA_SEARCH_CLI = wrapper;
  }

  return { dir, mirror, env };
}

function stripElapsed(text) {
  return text.replace(/耗时\s+[\d.]+ms/g, '耗时 <ms>');
}

test('all three tool families route equivalently and legacy aliases warn on stderr', { timeout: 30000 }, async (t) => {
  const fx = makeMirrorFixture(t);
  const { Client } = await import('@modelcontextprotocol/sdk/client/index.js');
  const { StdioClientTransport } = await import('@modelcontextprotocol/sdk/client/stdio.js');

  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [path.join(ROOT, 'bin', 'mcp-server')],
    env: fx.env,
    stderr: 'pipe',
  });
  let stderrLog = '';
  transport.stderr?.on('data', (chunk) => { stderrLog += chunk.toString('utf8'); });

  const client = new Client({ name: 'alias-routing-test', version: '1.0.0' }, { capabilities: {} });
  try {
    await client.connect(transport);
    const listed = (await client.listTools()).tools.map((tool) => tool.name).sort();
    assert.deepEqual(listed, ['list_repositories', 'read_file', 'search_code', 'search_docs']);

    const groups = [
      {
        canonical: 'search_code',
        aliases: ['search_gitea', 'search_gitorca'],
        args: { query: 'AlphaSentinelToken' },
        expectMatch: /AlphaSentinelToken/,
      },
      {
        canonical: 'search_docs',
        aliases: ['search_gitea_docs', 'search_gitorca_docs'],
        args: { query: 'AlphaSentinelToken' },
        expectMatch: /Alias Demo/,
      },
      {
        canonical: 'read_file',
        aliases: ['read_gitea_file', 'read_gitorca_file'],
        args: { repo: 'team/demo', path: 'README.md' },
        expectMatch: /AlphaSentinelToken/,
      },
      {
        canonical: 'list_repositories',
        aliases: ['list_gitea_repos', 'list_gitorca_repos'],
        args: {},
        expectMatch: /team\/demo/,
      },
    ];

    for (const g of groups) {
      const base = await client.callTool({ name: g.canonical, arguments: g.args });
      assert.notEqual(base.isError, true, `${g.canonical} failed: ${base.content?.[0]?.text}`);
      const baseText = stripElapsed(base.content[0].text);
      assert.match(baseText, g.expectMatch, g.canonical);

      for (const alias of g.aliases) {
        const beforeLen = stderrLog.length;
        const res = await client.callTool({ name: alias, arguments: g.args });
        assert.notEqual(res.isError, true, `${alias} failed: ${res.content?.[0]?.text}`);
        assert.equal(stripElapsed(res.content[0].text), baseText, `${alias} must match ${g.canonical}`);
        // Wait briefly for stderr pipe flush
        await new Promise((r) => setTimeout(r, 20));
        const delta = stderrLog.slice(beforeLen);
        assert.match(delta, new RegExp(`工具 ${alias} 已废弃，请改用 ${g.canonical}`), `missing deprecation warning for ${alias}`);
      }
    }
  } finally {
    await client.close().catch(() => {});
  }
});

test('GITEA_SEARCH_CLI fails closed with platform guidance when pointing to missing CLI', { timeout: 30000 }, async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gts-missing-cli-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const home = path.join(dir, 'home');
  fs.mkdirSync(home);
  const missing = path.join(dir, 'no-such-cli');
  const env = {
    ...process.env,
    HOME: home,
    USERPROFILE: home,
    GITEA_MIRROR_ROOT: path.join(dir, 'mirror'),
    GITEA_OWNERS: 'team',
    GITEA_SEARCH_CLI: missing,
  };
  delete env.GTS_BIN;
  delete env.GITQ_BIN;

  const { Client } = await import('@modelcontextprotocol/sdk/client/index.js');
  const { StdioClientTransport } = await import('@modelcontextprotocol/sdk/client/stdio.js');
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [path.join(ROOT, 'bin', 'mcp-server')],
    env,
    stderr: 'ignore',
  });
  const client = new Client({ name: 'missing-cli-test', version: '1.0.0' }, { capabilities: {} });
  try {
    await client.connect(transport);
    for (const [tool, args] of [['search_code', { query: 'x' }], ['search_docs', { query: 'x' }], ['list_repositories', {}]]) {
      const res = await client.callTool({ name: tool, arguments: args });
      assert.equal(res.isError, true, tool);
      assert.match(res.content[0].text, /GITEA_SEARCH_CLI=/, tool);
      assert.ok(res.content[0].text.includes(missing), tool);
    }
  } finally {
    await client.close().catch(() => {});
  }
});

test('doctor-mcp.sh reports legacy user_version != 2 read-only without mutating the database', { skip: !POSIX, timeout: 30000 }, (t) => {
  const fx = makeMirrorFixture(t);
  const dbPath = path.join(fx.mirror, 'state', 'gitea-fts.sqlite');
  // Downgrade PRAGMA user_version to 1 to simulate a <= v1.2.2 index
  const downgrade = spawnSync(PY, ['-c', `
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
c.execute('PRAGMA user_version = 1')
c.commit()
c.close()
`, dbPath], { encoding: 'utf8' });
  assert.equal(downgrade.status, 0, downgrade.stderr);

  const beforeStat = fs.statSync(dbPath);
  const beforeBytes = fs.readFileSync(dbPath);

  const doc = spawnSync('bash', [path.join(ROOT, 'scripts', 'doctor-mcp.sh'), '--verify', '--query', 'AlphaSentinelToken'], {
    encoding: 'utf8',
    env: fx.env,
    timeout: 20000,
  });
  assert.equal(doc.status, 0, `stdout:\n${doc.stdout}\nstderr:\n${doc.stderr}`);
  assert.match(doc.stdout, /search_code/);
  assert.match(doc.stdout, /PRAGMA user_version=1/);
  assert.match(doc.stdout, /gts --reindex --full/);

  // Strictly read-only: database bytes and mtime must be unchanged
  const afterStat = fs.statSync(dbPath);
  const afterBytes = fs.readFileSync(dbPath);
  assert.equal(afterStat.mtimeMs, beforeStat.mtimeMs);
  assert.deepEqual(afterBytes, beforeBytes);
});
