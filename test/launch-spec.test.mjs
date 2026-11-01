#!/usr/bin/env node
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { launchSpec } from '../scripts/launch-spec.mjs';

test('Node scripts are launched through process.execPath', () => {
  for (const file of ['C:\\Program Files\\gts\\server.mjs', '/tmp/含 空格/server.js', '/x/server.cjs']) {
    const spec = launchSpec(file, ['a&b']);
    assert.equal(spec.command, process.execPath);
    assert.deepEqual(spec.args, [file, 'a&b']);
    assert.equal(spec.kind, 'node-script');
  }
});

test('extensionless MCP Node wrappers are portable on Windows', () => {
  const file = 'C:\\gts\\bin\\mcp-server';
  const spec = launchSpec(file, ['x']);
  if (process.platform === 'win32') {
    assert.equal(spec.command, process.execPath);
    assert.deepEqual(spec.args, [file, 'x']);
    assert.equal(spec.kind, 'node-script');
  } else {
    assert.equal(spec.command, file);
    assert.deepEqual(spec.args, ['x']);
    assert.equal(spec.kind, 'executable');
  }
});

test('executable wrappers are launched directly', () => {
  const spec = launchSpec('C:\\gts\\mcp-server.exe', ['x']);
  assert.equal(spec.command, 'C:\\gts\\mcp-server.exe');
  assert.deepEqual(spec.args, ['x']);
  assert.equal(spec.kind, 'executable');
});
