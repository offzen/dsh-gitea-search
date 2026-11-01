#!/usr/bin/env node
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PY = process.platform === 'win32' ? 'python' : 'python3';
const NODE_CONFIG = new URL('../lib/config.mjs', import.meta.url);

function resolveWith(envOverrides, args = {}) {
  const code = [
    'import json,sys',
    `sys.path.insert(0, ${JSON.stringify(path.join(ROOT, 'lib'))})`,
    'from config import resolve_config',
    `print(json.dumps(resolve_config(**json.loads(${JSON.stringify(JSON.stringify(args))}))))`,
  ].join(';');
  const env = { ...process.env, ...envOverrides };
  for (const key of envOverrides.__unset || []) delete env[key];
  delete env.__unset;
  const p = spawnSync(PY, ['-c', code], { encoding: 'utf8', env });
  assert.equal(p.status, 0, p.stderr);
  return JSON.parse(p.stdout);
}

function cleanEnv(extra = {}) {
  return {
    ...extra,
    __unset: ['GITEA_MIRROR_ROOT', 'GITEA_MIRROR_PATH', 'GITQ_MIRROR_ROOT', 'GITEA_OWNERS', 'GITEA_OWNER', 'GITQ_OWNER', 'GITEA_SEARCH_CONFIG'],
  };
}

test('effective config priority is CLI > env > explicit config', () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'gts-config-'));
  const configPath = path.join(dir, 'config.json');
  writeFileSync(configPath, JSON.stringify({ storage: { mirror_root: path.join(dir, 'file') }, gitea: { owners: ['file-owner'] } }));
  const env = cleanEnv();
  env.__unset = env.__unset.filter((key) => !['GITEA_MIRROR_ROOT', 'GITEA_OWNERS'].includes(key));
  env.GITEA_MIRROR_ROOT = path.join(dir, 'env');
  env.GITEA_OWNERS = 'env-owner';
  const cfg = resolveWith(env, { cli_root: path.join(dir, 'cli'), cli_owners: 'cli-owner', config_path: configPath });
  assert.equal(cfg.mirror_root, path.join(dir, 'cli'));
  assert.deepEqual(cfg.owners, ['cli-owner']);
  assert.deepEqual(cfg.sources, { mirror_root: 'cli', owners: 'cli' });
  assert.equal(cfg.valid, true);
});

test('explicit config is shared and reports its source', () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'gts-config-'));
  const root = path.join(dir, 'mirror');
  mkdirSync(root);
  const configPath = path.join(dir, 'config.json');
  writeFileSync(configPath, JSON.stringify({ storage: { mirror_root: root }, gitea: { owners: ['team-a', 'team-b'] } }));
  const cfg = resolveWith(cleanEnv(), { config_path: configPath });
  assert.equal(cfg.mirror_root, root);
  assert.deepEqual(cfg.owners, ['team-a', 'team-b']);
  assert.deepEqual(cfg.sources, { mirror_root: 'explicit-config', owners: 'explicit-config' });
  assert.equal(cfg.valid, true);
});

test('Node and Python resolve the same explicit config fixture', () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'gts-config-'));
  const root = path.join(dir, 'mirror');
  const configPath = path.join(dir, 'config.json');
  writeFileSync(configPath, JSON.stringify({ storage: { mirror_root: root }, gitea: { owners: ['shared-team'] } }));
  const env = cleanEnv();
  const py = resolveWith(env, { config_path: configPath });
  const code = [
    `import { resolveConfig } from ${JSON.stringify(NODE_CONFIG.href)}`,
    `console.log(JSON.stringify(resolveConfig({ configPath: ${JSON.stringify(configPath)}, projectDir: ${JSON.stringify(ROOT)} })))`,
  ].join(';');
  const nodeEnv = { ...process.env };
  for (const key of env.__unset) delete nodeEnv[key];
  const node = spawnSync(process.execPath, ['--input-type=module', '-e', code], { encoding: 'utf8', env: nodeEnv });
  assert.equal(node.status, 0, node.stderr);
  const js = JSON.parse(node.stdout);
  assert.equal(js.mirror_root, py.mirror_root);
  assert.deepEqual(js.owners, py.owners);
  assert.deepEqual(js.sources, py.sources);
  assert.equal(js.valid, py.valid);
});

test('missing and placeholder owners fail closed', () => {
  const missing = resolveWith(cleanEnv());
  assert.equal(missing.valid, false);
  assert.match(missing.errors.join(' '), /owners are not configured/);
  const placeholderEnv = cleanEnv();
  placeholderEnv.__unset = placeholderEnv.__unset.filter((key) => key !== 'GITEA_OWNERS');
  placeholderEnv.GITEA_OWNERS = 'myorg';
  const placeholder = resolveWith(placeholderEnv);
  assert.equal(placeholder.valid, false);
  assert.match(placeholder.errors.join(' '), /placeholder/);
});
