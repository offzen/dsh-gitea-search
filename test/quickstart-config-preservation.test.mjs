import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const QUICKSTART = process.env.QUICKSTART_SCRIPT || path.join(ROOT, 'scripts/quickstart-local.sh');
const E2E = process.env.E2E_SCRIPT || path.join(ROOT, 'scripts/run-e2e-test.sh');
const CONFIG_MODULE = new URL('../lib/config.mjs', import.meta.url).href;
const POSIX = { skip: process.platform === 'win32' };
const SENTINEL = '{"mirror_root":"caller-mirror","owners":["caller"]}\n';
const quote = (value) => `'${value.replace(/'/g, "'\\''")}'`;

function fixture(t, indexerExit = 0) {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'gts-c9-'));
  t.after(() => fs.rmSync(base, { recursive: true, force: true }));
  const project = path.join(base, 'project with spaces');
  const home = path.join(base, 'home');
  fs.mkdirSync(path.join(project, 'scripts'), { recursive: true });
  fs.mkdirSync(path.join(project, 'lib'));
  fs.mkdirSync(home);
  fs.copyFileSync(QUICKSTART, path.join(project, 'scripts/quickstart-local.sh'));
  const calls = path.join(base, 'indexer-calls.jsonl');
  fs.writeFileSync(path.join(project, 'lib/indexer.py'), [
    'import json, os, sys',
    `with open(${JSON.stringify(calls)}, 'a', encoding='utf-8') as output:`,
    "    output.write(json.dumps({'argv': sys.argv[1:], 'mirror': os.environ.get('GITEA_MIRROR_ROOT'), 'config': os.environ.get('GITEA_SEARCH_CONFIG')}) + '\\n')",
    `sys.exit(${indexerExit})`, '',
  ].join('\n'));
  return {
    base, project, home, calls,
    mirror: path.join(base, 'mirror'), config: path.join(project, 'config.json'),
    explicit: path.join(base, 'explicit-config.json'),
    target: path.join(base, 'caller-target.json'), missing: path.join(base, 'missing-target.json'),
    env: { PATH: process.env.PATH, HOME: home, USERPROFILE: home },
  };
}

function snapshot(file) {
  let stat;
  try { stat = fs.lstatSync(file, { bigint: true }); }
  catch (error) { if (error.code === 'ENOENT') return { kind: 'absent' }; throw error; }
  const common = { ino: stat.ino, mode: stat.mode, mtimeNs: stat.mtimeNs };
  if (stat.isSymbolicLink()) return { ...common, kind: 'symlink', target: fs.readlinkSync(file) };
  if (stat.isDirectory()) return { ...common, kind: 'directory' };
  return { ...common, kind: 'file', content: fs.readFileSync(file) };
}
const watched = (p) => [p.config, p.explicit, p.target, p.missing].map(snapshot);
const states = {
  regular: (p, file) => fs.writeFileSync(file, SENTINEL),
  symlink: (p, file) => { fs.writeFileSync(p.target, SENTINEL); fs.symlinkSync(p.target, file); },
  dangling: (p, file) => fs.symlinkSync(p.missing, file),
};

function runQuickstart(p, env = {}, mirror = p.mirror) {
  const result = spawnSync('bash', [path.join(p.project, 'scripts/quickstart-local.sh'), mirror], {
    cwd: p.base, encoding: 'utf8', timeout: 15000, env: { ...p.env, ...env },
  });
  assert.equal(result.error, undefined, String(result.error));
  return result;
}

for (const location of ['default', 'explicit']) {
  for (const state of Object.keys(states)) {
    test(`C9 quickstart refuses an existing ${location} ${state} before side effects`, POSIX, (t) => {
      const p = fixture(t);
      const file = location === 'default' ? p.config : p.explicit;
      states[state](p, file);
      const before = watched(p);
      const result = runQuickstart(p, location === 'explicit' ? { GITEA_SEARCH_CONFIG: file } : {});
      assert.equal(result.status, 2, result.stdout + result.stderr);
      assert.match(result.stderr, /refusing to overwrite/);
      assert.deepEqual(watched(p), before);
      assert.equal(fs.existsSync(p.mirror), false);
      assert.equal(fs.existsSync(p.calls), false);
    });
  }
}

for (const indexerExit of [0, 7]) {
  test(`C9 direct first run and identical rerun preserve the created file (indexer rc=${indexerExit})`, POSIX, (t) => {
    const p = fixture(t, indexerExit);
    assert.equal(runQuickstart(p).status, indexerExit);
    assert.deepEqual(JSON.parse(fs.readFileSync(p.config, 'utf8')), { mirror_root: p.mirror, owners: ['local'] });
    const before = snapshot(p.config);
    assert.equal(runQuickstart(p).status, indexerExit);
    assert.deepEqual(snapshot(p.config), before);
  });
  for (const state of ['absent', ...Object.keys(states)]) {
    test(`C9 explicit fixture leaves project config ${state} unchanged (indexer rc=${indexerExit})`, POSIX, (t) => {
      const p = fixture(t, indexerExit);
      if (state !== 'absent') states[state](p, p.config);
      const before = [p.config, p.target, p.missing].map(snapshot);
      const result = runQuickstart(p, { GITEA_SEARCH_CONFIG: p.explicit });
      assert.equal(result.status, indexerExit, result.stdout + result.stderr);
      assert.deepEqual([p.config, p.target, p.missing].map(snapshot), before);
      assert.deepEqual(JSON.parse(fs.readFileSync(p.explicit, 'utf8')), { mirror_root: p.mirror, owners: ['local'] });
      const call = JSON.parse(fs.readFileSync(p.calls, 'utf8').trim().split('\n')[0]);
      assert.deepEqual(call, { argv: ['--build'], mirror: p.mirror, config: p.explicit });
    });
  }
}

test('C9 config directory and missing parent are refused without mirror writes', POSIX, (t) => {
  const p = fixture(t);
  fs.mkdirSync(p.explicit);
  const before = snapshot(p.explicit);
  assert.equal(runQuickstart(p, { GITEA_SEARCH_CONFIG: p.explicit }).status, 2);
  assert.deepEqual(snapshot(p.explicit), before);
  assert.notEqual(runQuickstart(p, { GITEA_SEARCH_CONFIG: path.join(p.base, 'missing-parent/config.json') }).status, 0);
  assert.equal(fs.existsSync(p.mirror), false);
  assert.equal(fs.existsSync(p.calls), false);
  assert.equal(snapshot(p.config).kind, 'absent');
});

test('C9 fixture config JSON escapes quoted, Unicode and backslash paths', POSIX, (t) => {
  const p = fixture(t);
  const mirror = path.join(p.base, 'mirror "quoted" \\ 中文');
  const result = runQuickstart(p, { GITEA_SEARCH_CONFIG: p.explicit }, mirror);
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.deepEqual(JSON.parse(fs.readFileSync(p.explicit, 'utf8')), { mirror_root: mirror, owners: ['local'] });
  const printed = result.stdout.split('MCP Configuration:\n')[1].split('\n--------------------------------------------------')[0];
  const client = JSON.parse(printed).mcpServers['gitea-search'];
  assert.equal(client.env.GITEA_SEARCH_CONFIG, p.explicit);
  assert.equal(client.env.GITEA_MIRROR_ROOT, mirror);
});

for (const state of ['regular', 'symlink', 'dangling']) {
  test(`C9 exclusive create rejects a raced ${state} without touching its contents`, POSIX, (t) => {
    const p = fixture(t);
    const realGit = spawnSync('bash', ['-c', 'command -v git'], { encoding: 'utf8', env: p.env }).stdout.trim();
    assert.ok(realGit);
    const bin = path.join(p.base, 'bin');
    fs.mkdirSync(bin);
    if (state === 'symlink') fs.writeFileSync(p.target, SENTINEL);
    const beforeTarget = [p.target, p.missing].map(snapshot);
    fs.writeFileSync(path.join(bin, 'git'), [
      '#!/usr/bin/env bash',
      'if [ "$3" = "init" ] && [ ! -e "$RACE_CONFIG" ] && [ ! -L "$RACE_CONFIG" ]; then',
      state === 'regular' ? '  printf "%s" "$RACE_BODY" > "$RACE_CONFIG"' : '  ln -s "$RACE_TARGET" "$RACE_CONFIG"',
      'fi', 'exec "$REAL_GIT" "$@"', '',
    ].join('\n'), { mode: 0o755 });
    const result = runQuickstart(p, {
      PATH: `${bin}${path.delimiter}${p.env.PATH}`, REAL_GIT: realGit,
      RACE_CONFIG: p.config, RACE_BODY: SENTINEL,
      RACE_TARGET: state === 'dangling' ? p.missing : p.target,
    });
    assert.equal(result.status, 2, result.stdout + result.stderr);
    assert.deepEqual([p.target, p.missing].map(snapshot), beforeTarget);
    if (state === 'regular') assert.equal(fs.readFileSync(p.config, 'utf8'), SENTINEL);
    else assert.equal(snapshot(p.config).kind, 'symlink');
    assert.equal(fs.existsSync(p.calls), false);
  });
}

function actualE2eBlock() {
  const source = fs.readFileSync(E2E, 'utf8');
  const start = source.indexOf('# 回归验证：config.json 自动解析');
  const end = source.indexOf('\necho ""\n', start);
  assert.ok(start >= 0 && end > start, 'actual E2E quickstart block not found');
  return source.slice(start, end);
}

for (const state of ['absent', ...Object.keys(states)]) {
  for (const failure of ['none', 'indexer', 'probe']) {
    test(`C9 actual E2E block preserves root ${state} on ${failure} path`, POSIX, (t) => {
      const p = fixture(t, failure === 'indexer' ? 7 : 0);
      if (state !== 'absent') states[state](p, p.config);
      const before = watched(p);
      const mirror = path.join(p.base, 'e2e-mirror');
      fs.mkdirSync(mirror);
      // A competing HOME config must not shadow the explicit E2E fixture config.
      const userConfig = path.join(p.home, '.config/dsh-gitea-search/config.json');
      fs.mkdirSync(path.dirname(userConfig), { recursive: true });
      fs.writeFileSync(userConfig, SENTINEL);
      const userBefore = snapshot(userConfig);
      const captured = path.join(p.base, 'probe-config.json');
      const mcp = path.join(p.base, 'mock-mcp.mjs');
      fs.writeFileSync(mcp, `
import fs from 'node:fs';
import readline from 'node:readline';
import { resolveConfig } from ${JSON.stringify(CONFIG_MODULE)};
const config = resolveConfig({ projectDir: ${JSON.stringify(p.project)} });
fs.writeFileSync(${JSON.stringify(captured)}, JSON.stringify(config));
const input = readline.createInterface({ input: process.stdin });
input.on('line', (line) => {
  const request = JSON.parse(line);
  let result = {};
  if (request.method === 'initialize') result = { protocolVersion: '2024-11-05', capabilities: {}, serverInfo: { name: 'c9-fixture', version: '1' } };
  if (request.method === 'tools/list') result = { tools: [{ name: 'search_fixture_docs', inputSchema: { type: 'object' } }] };
  if (request.method === 'tools/call') result = { content: [{ type: 'text', text: ${JSON.stringify(failure)} === 'probe' || !config.valid || config.owners[0] !== 'local' ? 'injected failure' : 'Demo Project' }] };
  process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: request.id, result }) + '\\n');
});
`);
      const harness = path.join(p.base, 'e2e-block.sh');
      fs.writeFileSync(harness, [
        'set -euo pipefail',
        'PASS=0; FAIL=0',
        'assert_ok() { if eval "$2"; then PASS=$((PASS + 1)); else FAIL=$((FAIL + 1)); fi; }',
        `ROOT_DIR=${quote(p.project)}`, `MIRROR_ROOT=${quote(mirror)}`, `MCP_BIN=${quote(mcp)}`,
        'export GITEA_MIRROR_ROOT="$MIRROR_ROOT" GITEA_OWNERS=test-owner',
        actualE2eBlock(), '[ "$FAIL" -eq 0 ]', '',
      ].join('\n'));
      const result = spawnSync('bash', [harness], { cwd: p.base, encoding: 'utf8', env: p.env, timeout: 15000 });
      assert.equal(result.error, undefined, String(result.error));
      assert.deepEqual(watched(p), before, result.stdout + result.stderr);
      assert.deepEqual(snapshot(userConfig), userBefore);
      assert.equal(result.status, failure === 'indexer' ? 7 : failure === 'probe' ? 1 : 0, result.stdout + result.stderr);
      if (failure !== 'indexer') {
        const resolved = JSON.parse(fs.readFileSync(captured, 'utf8'));
        assert.equal(resolved.sources.mirror_root, 'env-config');
        assert.equal(resolved.sources.owners, 'env-config');
        assert.equal(resolved.valid, true);
        assert.ok(resolved.config_path.startsWith(mirror + path.sep));
        assert.equal(resolved.mirror_root, path.join(mirror, 'mock-quickstart-verify'));
      }
    });
  }
}
