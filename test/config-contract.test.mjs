#!/usr/bin/env node
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
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

test('R5 example config contains only resolver-effective keys', () => {
  const example = JSON.parse(readFileSync(path.join(ROOT, 'config.example.json'), 'utf8'));
  assert.deepEqual(Object.keys(example).sort(), ['gitea', 'storage']);
  assert.deepEqual(Object.keys(example.gitea).sort(), ['owners']);
  assert.deepEqual(Object.keys(example.storage).sort(), ['mirror_root']);
});

test('R5 Node and Python report ignored key paths without leaking values', () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'gts-config-r5-'));
  const home = path.join(dir, 'home');
  mkdirSync(home);
  const configPath = path.join(dir, 'config.json');
  const canary = ['R5', 'VALUE', 'CANARY'].join('-');
  writeFileSync(configPath, JSON.stringify({
    gitea: { owners: ['fixture'], url: `https://${canary}.invalid`, token: canary },
    storage: { mirror_root: path.join(dir, 'mirror'), db_path: `${canary}/db` },
    search: { max_results: 5 },
    extra: canary,
  }));
  const env = cleanEnv({ HOME: home, USERPROFILE: home });
  const py = resolveWith(env, { config_path: configPath });
  assert.equal(py.valid, true);
  assert.deepEqual(py.warnings, [
    'config: key "extra" is not supported in v1.2.3 and is ignored',
    'config: key "gitea.token" is not supported in v1.2.3 and is ignored',
    'config: key "gitea.url" is not supported in v1.2.3 and is ignored',
    'config: key "search" is not supported in v1.2.3 and is ignored',
    'config: key "storage.db_path" is not supported in v1.2.3 and is ignored',
  ]);
  assert.doesNotMatch(py.warnings.join('\n'), new RegExp(canary));

  const nodeCode = [
    `import { resolveConfig } from ${JSON.stringify(NODE_CONFIG.href)}`,
    `console.log(JSON.stringify(resolveConfig({ configPath: ${JSON.stringify(configPath)} })))`,
  ].join(';');
  const node = spawnSync(process.execPath, ['--input-type=module', '-e', nodeCode], { encoding: 'utf8', env: { ...process.env, HOME: home, USERPROFILE: home } });
  assert.equal(node.status, 0, node.stderr);
  assert.deepEqual(JSON.parse(node.stdout).warnings, py.warnings);

  const gts = spawnSync(PY, [path.join(ROOT, 'bin', 'gts'), '--config', configPath, '--show-effective-config'], {
    encoding: 'utf8', env: { ...process.env, HOME: home, USERPROFILE: home },
  });
  assert.equal(gts.status, 0, gts.stderr);
  assert.doesNotThrow(() => JSON.parse(gts.stdout));
  assert.match(gts.stderr, /gitea\.token/);
  assert.doesNotMatch(`${gts.stdout}\n${gts.stderr}`, new RegExp(canary));
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

// ── rc7 B2/B3/B4：显式配置不回落、跨语言单一契约、畸形配置结构化报错 ─────────────────────────
//
// 单一契约（lib/config.mjs 与 lib/config.py 必须同判）：
//   1. owners 层「已设置」当且仅当规范化后（split/trim/去空）非空；[] / "" / [""] / " , " 等价于未设置，
//      交给下一层（env → gitea.owners → 顶层 owners → missing）。
//   2. 显式指定（--config / GITEA_SEARCH_CONFIG）的文件不存在/不是普通文件 ⇒ 错误，绝不回落到 user/project 配置。
//   3. 配置文件内容畸形（非 JSON、顶层非对象、storage/gitea 非对象、mirror_root/owners 类型错）⇒ 结构化错误
//      （valid:false + errors），两侧都不抛异常、报错文本一致。
const HAVE_PY = spawnSync(PY, ['--version'], { encoding: 'utf8' }).status === 0;
const SERVER = path.join(ROOT, 'lib', 'server.mjs');

function isolatedEnv(home, extra = {}) {
  const env = { ...process.env, HOME: home, USERPROFILE: home, ...extra };
  for (const key of ['GITEA_MIRROR_ROOT', 'GITEA_MIRROR_PATH', 'GITQ_MIRROR_ROOT', 'GITEA_OWNERS', 'GITEA_OWNER', 'GITQ_OWNER', 'GITEA_SEARCH_CONFIG', 'XDG_DATA_HOME']) {
    if (!(key in extra)) delete env[key];
  }
  return env;
}

function jsSide(args, env) {
  const code = [
    `import { resolveConfig } from ${JSON.stringify(NODE_CONFIG.href)}`,
    `console.log(JSON.stringify(resolveConfig(${JSON.stringify(args)})))`,
  ].join(';');
  const r = spawnSync(process.execPath, ['--input-type=module', '-e', code], { encoding: 'utf8', env });
  assert.equal(r.status, 0, `Node 侧不得抛异常: ${r.stderr}`);
  return JSON.parse(r.stdout);
}

function pySide(args, env) {
  const pyArgs = { cli_root: args.mirrorRoot, cli_owners: args.owners, config_path: args.configPath, project_dir: args.projectDir };
  for (const k of Object.keys(pyArgs)) if (pyArgs[k] === undefined) delete pyArgs[k];
  const code = [
    'import json,sys',
    `sys.path.insert(0, ${JSON.stringify(path.join(ROOT, 'lib'))})`,
    'from config import resolve_config',
    `print(json.dumps(resolve_config(**json.loads(${JSON.stringify(JSON.stringify(pyArgs))}))))`,
  ].join(';');
  const r = spawnSync(PY, ['-c', code], { encoding: 'utf8', env });
  assert.equal(r.status, 0, `Python 侧不得抛异常（AttributeError/TypeError 等）: ${r.stderr}`);
  return JSON.parse(r.stdout);
}

function bothSides(t, args, env) {
  if (!HAVE_PY) { t.skip('python3 不可用'); return null; }
  const js = jsSide(args, env);
  const py = pySide(args, env);
  assert.deepEqual(js, py, '同一 fixture，MCP(JS) 与 CLI(Python) 裁决必须完全一致');
  return js;
}

function fixtureDir() {
  const dir = mkdtempSync(path.join(tmpdir(), 'gts-config-rc7-'));
  const home = path.join(dir, 'home');
  mkdirSync(path.join(home, '.config', 'dsh-gitea-search'), { recursive: true });
  return { dir, home };
}

function writeConfig(dir, content, name = 'config.json') {
  const file = path.join(dir, name);
  writeFileSync(file, typeof content === 'string' ? content : JSON.stringify(content));
  return file;
}

test('B3 contract: empty owners array is an empty set (== unset), identically in Node and Python', (t) => {
  const { dir, home } = fixtureDir();
  const env = isolatedEnv(home);
  for (const owners of [[], [''], ['  ', ' ']]) {
    const file = writeConfig(dir, { owners });
    const r = bothSides(t, { configPath: file }, env);
    if (!r) return;
    assert.equal(r.valid, false);
    assert.deepEqual(r.owners, []);
    assert.equal(r.sources.owners, 'missing');
    assert.deepEqual(r.errors, ['owners are not configured']);
  }
  // 空 gitea.owners 不得遮蔽顶层 owners（旧 JS：[] 为 truthy ⇒ 遮蔽；旧 Python：falsy ⇒ 不遮蔽）。
  const shadow = writeConfig(dir, { gitea: { owners: [] }, owners: ['top-owner'] }, 'shadow.json');
  const r = bothSides(t, { configPath: shadow }, env);
  assert.deepEqual(r.owners, ['top-owner']);
  assert.equal(r.sources.owners, 'explicit-config');
  assert.equal(r.valid, true);
});

test('B3 contract: empty CLI owners falls through to env, identically in Node and Python', (t) => {
  const { home } = fixtureDir();
  const env = isolatedEnv(home, { GITEA_OWNERS: 'env-owner' });
  for (const owners of [[], '', ' , ']) {
    const r = bothSides(t, { owners }, env);
    if (!r) return;
    assert.deepEqual(r.owners, ['env-owner']);
    assert.equal(r.sources.owners, 'env');
  }
});

for (const [label, content, expectError] of [
  ['top-level null', 'null', /top-level value must be a JSON object, got null/],
  ['top-level array', '[]', /top-level value must be a JSON object, got array/],
  ['top-level scalar', '5', /top-level value must be a JSON object, got number/],
  ['top-level string', '"owners"', /top-level value must be a JSON object, got string/],
  ['storage is a string', { storage: '/mirror' }, /"storage" must be an object, got string/],
  ['gitea is an array', { gitea: [] }, /"gitea" must be an object, got array/],
  ['storage.mirror_root is a number', { storage: { mirror_root: 5 } }, /"storage.mirror_root" must be a string, got number/],
  ['owners is an object', { owners: { a: 1 } }, /"owners" must be a string or an array of strings, got object/],
  ['owners item is not a string', { gitea: { owners: ['ok', 1] } }, /"gitea.owners" items must be strings, item 1 is number/],
]) {
  test(`B4 malformed config (${label}) yields a structured error on both sides, never throws`, (t) => {
    const { dir, home } = fixtureDir();
    const file = writeConfig(dir, content);
    const r = bothSides(t, { configPath: file }, isolatedEnv(home));
    if (!r) return;
    assert.equal(r.valid, false);
    assert.equal(r.errors.length, 1);
    assert.match(r.errors[0], expectError);
    assert.ok(r.errors[0].includes(file), '错误必须点明是哪个配置文件');
  });
}

test('B4 invalid JSON is a structured error on both sides (texts differ per JSON parser)', (t) => {
  if (!HAVE_PY) return t.skip('python3 不可用');
  const { dir, home } = fixtureDir();
  const file = writeConfig(dir, '{bad');
  const env = isolatedEnv(home);
  for (const r of [jsSide({ configPath: file }, env), pySide({ configPath: file }, env)]) {
    assert.equal(r.valid, false);
    assert.match(r.errors[0], new RegExp(`^invalid config file ${file.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}: `));
    assert.equal(r.config_path, file);
  }
});

test('B2 explicit --config pointing at a missing file is an error and never falls back to user config', (t) => {
  const { dir, home } = fixtureDir();
  // 用户配置里有完全可用的 owners：旧实现会静默回落到它。
  writeConfig(path.join(home, '.config', 'dsh-gitea-search'), { owners: ['home-owner'] });
  const env = isolatedEnv(home);
  const missing = path.join(dir, 'does-not-exist.json');
  const r = bothSides(t, { configPath: missing }, env);
  if (!r) return;
  assert.equal(r.valid, false);
  assert.deepEqual(r.owners, []);
  assert.equal(r.config_path, null);
  assert.match(r.errors[0], /--config points to a config file that does not exist/);
  assert.ok(r.errors[0].includes(missing));
  // 对照：没有显式指定时，回落到 user 配置仍然允许。
  const fallback = bothSides(t, {}, env);
  assert.deepEqual(fallback.owners, ['home-owner']);
  assert.equal(fallback.sources.owners, 'user-config');
});

test('B2 GITEA_SEARCH_CONFIG pointing at a missing file is an error and never falls back', (t) => {
  const { dir, home } = fixtureDir();
  writeConfig(path.join(home, '.config', 'dsh-gitea-search'), { owners: ['home-owner'] });
  const missing = path.join(dir, 'nope.json');
  const r = bothSides(t, {}, isolatedEnv(home, { GITEA_SEARCH_CONFIG: missing }));
  if (!r) return;
  assert.equal(r.valid, false);
  assert.deepEqual(r.owners, []);
  assert.match(r.errors[0], /GITEA_SEARCH_CONFIG points to a config file that does not exist/);
  assert.ok(r.errors[0].includes(missing));
});

test('B2 explicit config that is a directory is rejected as "not a regular file"', (t) => {
  const { dir, home } = fixtureDir();
  const r = bothSides(t, { configPath: dir }, isolatedEnv(home));
  if (!r) return;
  assert.equal(r.valid, false);
  assert.match(r.errors[0], /--config points to a config file that is not a regular file/);
});

test('B2 bin/gts --config <missing> exits non-zero with CONFIG_ERROR on stderr', () => {
  if (!HAVE_PY) return;
  const { dir, home } = fixtureDir();
  const missing = path.join(dir, 'absent.json');
  const r = spawnSync(PY, [path.join(ROOT, 'bin', 'gts'), '--config', missing, '--repos'], { encoding: 'utf8', env: isolatedEnv(home, { GITEA_OWNERS: 'env-owner' }) });
  assert.notEqual(r.status, 0);
  assert.match(r.stderr, /CONFIG_ERROR: --config points to a config file that does not exist/);
});

async function withClient(env, fn) {
  const { Client } = await import('@modelcontextprotocol/sdk/client/index.js');
  const { StdioClientTransport } = await import('@modelcontextprotocol/sdk/client/stdio.js');
  const transport = new StdioClientTransport({ command: process.execPath, args: [SERVER], env, stderr: 'ignore' });
  const client = new Client({ name: 'rc7-test', version: '0' }, { capabilities: {} });
  try {
    await client.connect(transport);
    return await fn(client);
  } finally {
    await client.close().catch(() => {});
  }
}

test('B4 MCP server survives a malformed config: handshake succeeds, tools report CONFIG_ERROR', { timeout: 30000 }, async () => {
  const { dir, home } = fixtureDir();
  for (const content of ['[]', 'null', '{"storage":"x"}', '{bad']) {
    const file = writeConfig(dir, content);
    const env = isolatedEnv(home, { GITEA_SEARCH_CONFIG: file });
    // 旧实现：server.mjs 在模块加载期 throw / TypeError ⇒ 握手前进程退出，connect 失败。
    const res = await withClient(env, (client) => client.callTool({ name: 'list_gitea_repos', arguments: {} }));
    assert.equal(res.isError, true, content);
    assert.match(res.content[0].text, /CONFIG_ERROR: invalid config file/, content);
  }
});

test('B2 MCP server reports an explicit-but-missing GITEA_SEARCH_CONFIG instead of using user config', { timeout: 30000 }, async () => {
  const { dir, home } = fixtureDir();
  writeConfig(path.join(home, '.config', 'dsh-gitea-search'), { owners: ['home-owner'] });
  const env = isolatedEnv(home, { GITEA_SEARCH_CONFIG: path.join(dir, 'missing.json') });
  const res = await withClient(env, (client) => client.callTool({ name: 'list_gitea_repos', arguments: {} }));
  assert.equal(res.isError, true);
  assert.match(res.content[0].text, /CONFIG_ERROR: GITEA_SEARCH_CONFIG points to a config file that does not exist/);
});
