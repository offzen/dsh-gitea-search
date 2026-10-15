import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { makeMirror } from './helpers/fixture.mjs';

const serverPath = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'bin', 'mcp-server.mjs');

test('MCP stdio server: handshake, three tools, search/read, errors, no U+FFFD', async (t) => {
  const fx = await makeMirror([
    { owner: 'team', name: 'app' },
    { owner: 'team', name: 'libs' },
    { owner: 'ops', name: 'svc' },
    { owner: '', name: 'flatrepo' },
  ]);
  t.after(fx.cleanup);

  const child = spawn(process.execPath, [serverPath], {
    env: { ...process.env, GITEA_MIRROR_DIR: fx.mirror },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  t.after(() => { child.kill(); });

  const pending = new Map();
  let buffer = '';
  let allStdout = '';
  const responses = [];
  child.stdout.on('data', (chunk) => {
    allStdout += chunk.toString('utf8');
    buffer += chunk.toString('utf8');
    let index = buffer.indexOf('\n');
    while (index !== -1) {
      const line = buffer.slice(0, index).trim();
      buffer = buffer.slice(index + 1);
      if (!line) { index = buffer.indexOf('\n'); continue; }
      const message = JSON.parse(line);
      responses.push(message);
      if (message.id && pending.has(message.id)) {
        pending.get(message.id)(message);
        pending.delete(message.id);
      }
      index = buffer.indexOf('\n');
    }
  });

  const send = (message) => child.stdin.write(`${JSON.stringify(message)}\n`);
  const call = (id, method, params) => new Promise((resolve, reject) => {
    pending.set(id, resolve);
    send({ jsonrpc: '2.0', id, method, params });
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`timeout waiting for ${method}`));
    }, 60000);
    timer.unref();
  });

  const init = await call(1, 'initialize', {
    protocolVersion: '2024-11-05',
    capabilities: {},
    clientInfo: { name: 'test', version: '0' },
  });
  assert.equal(init.result.serverInfo.name, 'dsh-gitea-search');
  send({ jsonrpc: '2.0', method: 'notifications/initialized' });

  const tools = await call(2, 'tools/list', {});
  assert.deepEqual(
    tools.result.tools.map((tool) => tool.name).sort(),
    ['list_gitea_repos', 'read_gitea_file', 'search_gitea'],
  );

  const listed = await call(3, 'tools/call', { name: 'list_gitea_repos', arguments: {} });
  assert.ok(listed.result.content[0].text.includes('4 of 4 repositories'));

  const searched = await call(4, 'tools/call', { name: 'search_gitea', arguments: { query: 'needle', max_results: 3 } });
  assert.ok(searched.result.content[0].text.includes('hit(s)'));
  assert.ok(!allStdout.includes('\uFFFD'), 'search output must not contain U+FFFD');

  const read = await call(5, 'tools/call', { name: 'read_gitea_file', arguments: { repo: 'team/app', path: 'cjk.txt' } });
  assert.ok(read.result.content[0].text.includes('护栏'));
  assert.ok(!allStdout.includes('\uFFFD'), 'CJK read must not contain U+FFFD');

  const injected = await call(6, 'tools/call', {
    name: 'read_gitea_file',
    arguments: { repo: 'team/app', path: 'README.md', ref: '--output=/tmp/pwn' },
  });
  assert.equal(injected.result.isError, true);
  assert.ok(injected.result.content[0].text.includes('ref'), 'the error should name the ref problem');

  const missingArg = await call(7, 'tools/call', { name: 'search_gitea', arguments: {} });
  assert.equal(missingArg.result.isError, true);

  const unknown = await call(8, 'tools/call', { name: 'no_such_tool', arguments: {} });
  assert.equal(unknown.result.isError, true);

  assert.equal(responses.length, 8, 'one response per request');
  assert.ok(!allStdout.includes('\uFFFD'), 'the whole session stays free of replacement characters');
});