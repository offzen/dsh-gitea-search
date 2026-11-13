#!/usr/bin/env node
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

async function callDocs(t, relation, hasMore) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gts-r3-mcp-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const mirror = path.join(dir, 'mirror');
  fs.mkdirSync(path.join(mirror, 'fixture'), { recursive: true });
  const payloadFile = path.join(dir, 'payload.json');
  fs.writeFileSync(payloadFile, JSON.stringify({
    total: 1,
    returned: 1,
    has_more: hasMore,
    total_relation: relation,
    results: [{ repo: 'fixture/demo', ref: 'main', path: 'README.md', title: 'README', snippet: 'hit' }],
    perf: { status: 'OK', elapsed_ms: 1 },
  }) + '\n');
  const isWin = process.platform === 'win32';
  const gts = path.join(dir, isWin ? 'gts.cmd' : 'gts');
  if (isWin) fs.writeFileSync(gts, '@echo off\r\ntype "%~dp0payload.json"\r\n');
  else fs.writeFileSync(gts, '#!/bin/sh\ncat "$(dirname "$0")/payload.json"\n', { mode: 0o755 });
  const env = {
    ...process.env,
    HOME: path.join(dir, 'home'),
    USERPROFILE: path.join(dir, 'home'),
    GTS_BIN: gts,
    GITEA_MIRROR_ROOT: mirror,
    GITEA_OWNERS: 'fixture',
  };
  fs.mkdirSync(env.HOME);
  const { Client } = await import('@modelcontextprotocol/sdk/client/index.js');
  const { StdioClientTransport } = await import('@modelcontextprotocol/sdk/client/stdio.js');
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [path.join(ROOT, 'lib', 'server.mjs')],
    env,
    stderr: 'ignore',
  });
  const client = new Client({ name: 'r3-pagination-test', version: '0' }, { capabilities: {} });
  try {
    await client.connect(transport);
    const result = await client.callTool({ name: 'search_gitea_docs', arguments: { query: 'hit', max_results: 1 } });
    assert.notEqual(result.isError, true, result.content?.[0]?.text);
    return result.content[0].text;
  } finally {
    await client.close().catch(() => {});
  }
}

test('R3 MCP distinguishes observed-more from saturated uncertainty', async (t) => {
  const observed = await callDocs(t, 'gt', true);
  assert.match(observed, /返回 1 篇相关文档（还有更多）/);
  assert.doesNotMatch(observed, /全库命中/);

  const uncertain = await callDocs(t, 'gte', null);
  assert.match(uncertain, /返回 1 篇相关文档（总数可能更多）/);
  assert.doesNotMatch(uncertain, /全库命中/);
});
