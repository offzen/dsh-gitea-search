#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
/**
 * test/protocol-mock.test.mjs
 * Verifies MCP protocol handlers via in-memory createServer() and stdio handshake
 * through both `bin/mcp-server` and direct-run `lib/server.mjs` entry points.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer, SERVER_NAME, SERVER_VERSION, TOOL_ALIASES } from '../lib/server.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const EXPECTED_TOOLS = ['list_repositories', 'read_file', 'search_code', 'search_docs'];

test('createServer() exposes only the 4 neutral tool names in tools/list', async () => {
  const server = createServer();
  const listHandler = server._requestHandlers?.get('tools/list');
  assert.ok(listHandler, 'tools/list handler must be registered');

  const res = await listHandler({ method: 'tools/list', params: {} });
  const names = res.tools.map((t) => t.name).sort();
  assert.deepEqual(names, EXPECTED_TOOLS);
  for (const legacy of Object.keys(TOOL_ALIASES)) {
    assert.ok(!names.includes(legacy), `legacy alias ${legacy} must not appear in tools/list`);
  }
});

test('createServer() rejects unknown tool names with structured error', async () => {
  const server = createServer();
  const callHandler = server._requestHandlers?.get('tools/call');
  assert.ok(callHandler, 'tools/call handler must be registered');

  const res = await callHandler({ method: 'tools/call', params: { name: 'non_existent_tool', arguments: {} } });
  assert.equal(res.isError, true);
  assert.match(res.content[0].text, /未知工具/);
});

test('both bin/mcp-server and direct-run lib/server.mjs complete MCP stdio handshake cleanly', { timeout: 30000 }, async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gts-proto-handshake-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const home = path.join(dir, 'home');
  fs.mkdirSync(home);
  const env = {
    ...process.env,
    HOME: home,
    USERPROFILE: home,
    GITEA_MIRROR_ROOT: path.join(dir, 'mirror'),
    GITEA_OWNERS: 'fixture',
  };
  for (const key of Object.keys(env)) {
    if (/^(GITQ_|GTS_|GITEA_SEARCH_)/.test(key)) delete env[key];
  }

  const { Client } = await import('@modelcontextprotocol/sdk/client/index.js');
  const { StdioClientTransport } = await import('@modelcontextprotocol/sdk/client/stdio.js');

  for (const entry of [path.join(ROOT, 'bin', 'mcp-server'), path.join(ROOT, 'lib', 'server.mjs')]) {
    const transport = new StdioClientTransport({
      command: process.execPath,
      args: [entry],
      env,
      stderr: 'pipe',
    });
    const client = new Client({ name: 'protocol-mock-test', version: '1.0.0' }, { capabilities: {} });
    try {
      await client.connect(transport);
      const serverVersion = client.getServerVersion();
      assert.equal(serverVersion?.name, SERVER_NAME, entry);
      assert.equal(serverVersion?.version, SERVER_VERSION, entry);
      const list = await client.listTools();
      assert.deepEqual(list.tools.map((tool) => tool.name).sort(), EXPECTED_TOOLS, entry);
    } finally {
      await client.close().catch(() => {});
    }
  }
});
