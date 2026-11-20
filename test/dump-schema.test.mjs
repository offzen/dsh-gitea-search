#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
/**
 * test/dump-schema.test.mjs
 * Verifies static Function Calling JSON Schema export via dumpSchema() and
 * `bin/mcp-server --dump-schema [--compact]`, ensuring zero runtime side effects
 * (no config/mirror reads, no child process spawns, no stderr noise).
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dumpSchema, SERVER_NAME, SERVER_VERSION, TOOLS } from '../lib/server.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const BIN = path.join(ROOT, 'bin', 'mcp-server');
const PKG = JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8'));
const EXPECTED_NAMES = ['list_repositories', 'read_file', 'search_code', 'search_docs'];

test('dumpSchema() returns consistent gitea-search-mcp metadata and 4 neutral tools', () => {
  const schema = dumpSchema();
  assert.equal(schema.service, 'gitea-search-mcp');
  assert.equal(schema.service, SERVER_NAME);
  assert.equal(schema.version, PKG.version);
  assert.equal(schema.version, SERVER_VERSION);
  assert.equal(schema.transport, 'stdio');
  assert.ok(Array.isArray(schema.functions));
  assert.equal(schema.functions.length, 4);

  const fnNames = schema.functions.map((f) => f.name).sort();
  assert.deepEqual(fnNames, EXPECTED_NAMES);
  for (const legacy of ['search_gitea', 'search_gitea_docs', 'read_gitea_file', 'list_gitea_repos', 'search_gitorca']) {
    assert.ok(!fnNames.includes(legacy), `legacy name ${legacy} must not appear in dumpSchema()`);
  }
});

test('dumpSchema() matches TOOLS inputSchema and valid JSON-Schema contract', () => {
  const schema = dumpSchema();
  assert.deepEqual(
    schema.functions.map((f) => f.name).sort(),
    TOOLS.map((t) => t.name).sort(),
  );
  for (const fn of schema.functions) {
    assert.equal(typeof fn.name, 'string');
    assert.ok(fn.name.length > 0);
    assert.equal(typeof fn.description, 'string');
    assert.ok(fn.description.length > 0);
    assert.equal(fn.parameters?.type, 'object');
    assert.equal(typeof fn.parameters?.properties, 'object');
    for (const req of fn.parameters.required || []) {
      assert.ok(fn.parameters.properties[req], `required "${req}" must exist in properties of ${fn.name}`);
    }
    const tool = TOOLS.find((t) => t.name === fn.name);
    assert.deepEqual(fn.parameters, tool.inputSchema);
  }
});

test('dumpSchema({ compact: true }) returns only functions', () => {
  const compact = dumpSchema({ compact: true });
  assert.deepEqual(Object.keys(compact), ['functions']);
  assert.equal(compact.functions.length, 4);
  assert.deepEqual(compact.functions.map((f) => f.name).sort(), EXPECTED_NAMES);
});

test('CLI bin/mcp-server --dump-schema is pure static with zero side effects and no stderr noise', () => {
  // Supply broken explicit config and broken CLI path: static dump MUST NOT touch them.
  const env = {
    ...process.env,
    GITEA_SEARCH_CONFIG: '/nonexistent/path/that/must/not/be/read.json',
    GITEA_SEARCH_CLI: '/nonexistent/gts-binary',
    GITQ_MIRROR_ROOT: '/nonexistent/legacy-mirror',
  };
  const full = spawnSync(process.execPath, [BIN, '--dump-schema'], { encoding: 'utf8', env, timeout: 10000 });
  assert.equal(full.status, 0, full.stderr);
  assert.equal(full.stderr, '', 'static --dump-schema must not trigger config/env warnings or startup logs');
  const parsed = JSON.parse(full.stdout);
  assert.equal(parsed.service, 'gitea-search-mcp');
  assert.equal(parsed.version, PKG.version);
  assert.equal(parsed.transport, 'stdio');
  assert.deepEqual(parsed.functions.map((f) => f.name).sort(), EXPECTED_NAMES);

  const compact = spawnSync(process.execPath, [BIN, '--dump-schema', '--compact'], { encoding: 'utf8', env, timeout: 10000 });
  assert.equal(compact.status, 0, compact.stderr);
  assert.equal(compact.stderr, '');
  const parsedCompact = JSON.parse(compact.stdout);
  assert.deepEqual(Object.keys(parsedCompact), ['functions']);
  assert.equal(parsedCompact.functions.length, 4);
});
