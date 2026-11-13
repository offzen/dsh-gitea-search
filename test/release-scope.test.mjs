#!/usr/bin/env node
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const server = fs.readFileSync(new URL('../lib/server.mjs', import.meta.url), 'utf8');
const readme = fs.readFileSync(new URL('../README.md', import.meta.url), 'utf8');
const naming = fs.readFileSync(new URL('../docs/TOOL-NAMING.md', import.meta.url), 'utf8');
const pkg = JSON.parse(fs.readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
const lock = JSON.parse(fs.readFileSync(new URL('../package-lock.json', import.meta.url), 'utf8'));
const pyproject = fs.readFileSync(new URL('../pyproject.toml', import.meta.url), 'utf8');
const setupLinux = fs.readFileSync(new URL('../scripts/setup.sh', import.meta.url), 'utf8');
const setupMac = fs.readFileSync(new URL('../scripts/setup-macos.sh', import.meta.url), 'utf8');
const setupWindows = fs.readFileSync(new URL('../scripts/setup-windows.ps1', import.meta.url), 'utf8');
const windowsCi = fs.readFileSync(new URL('../.github/workflows/windows-ci.yml', import.meta.url), 'utf8');
const packageGate = fs.readFileSync(new URL('../scripts/test-python-package.py', import.meta.url), 'utf8');
const provenance = JSON.parse(fs.readFileSync(new URL('../PROVENANCE.json', import.meta.url), 'utf8'));

test('1.2.3 candidate metadata is aligned without claiming release', () => {
  assert.equal(pkg.version, '1.2.3');
  assert.equal(lock.version, '1.2.3');
  assert.equal(lock.packages[''].version, '1.2.3');
  assert.match(pyproject, /version = "1.2.3"/);
  assert.match(server, /version: '1.2.3'/);
  assert.match(readme, /Version-v1.2.3%20candidate-yellow/);
});

test('candidate provenance is frozen to an exact content commit', () => {
  assert.match(provenance.source_commit, /^[0-9a-f]{40}$/);
  assert.notEqual(provenance.source_commit, 'pending-candidate-freeze');
});

test('document search declares default-branch scope and cold-start boundary', () => {
  assert.match(server, /scope: default-branches/);
  assert.match(server, /Python\/jieba.*cold|Python\/jieba.*冷启动/);
  assert.match(server, /search_gitea.*全部 heads/);
});

test('tool naming uses the three-layer host-portable model', () => {
  assert.match(naming, /MCP server 裸名/);
  assert.match(naming, /宿主注册\/函数名/);
  assert.match(naming, /GUI 友好显示名/);
  assert.match(naming, /实际名称必须以当前宿主工具注册表为准/);
});

test('optional skill exists and is not auto-installed', () => {
  const skillPath = 'extras/dsh-skill/gitea-search/SKILL.md';
  const skill = fs.readFileSync(new URL(`../${skillPath}`, import.meta.url), 'utf8');
  assert.match(skill, /default branch/i);
  assert.match(skill, /Install it explicitly/);
  for (const installer of [setupLinux, setupMac, setupWindows]) {
    assert.doesNotMatch(installer, /extras[\\/]dsh-skill|gitea-search[\\/]SKILL\.md/);
  }
});

test('Python artifacts include every module used by installed gts', () => {
  assert.match(pyproject, /"share\/dsh-gitea-search\/bin"\s*=\s*\["bin\/gts"\]/);
  assert.match(pyproject, /"share\/dsh-gitea-search\/lib"\s*=\s*\[[^\]]*"lib\/indexer\.py"[^\]]*"lib\/config\.py"[^\]]*\]/s);
  assert.equal(fs.existsSync(new URL('../scripts/test-python-package.py', import.meta.url)), true);
});

test('Windows hosted gates preserve UTF-8 output and native fallback prerequisites', () => {
  assert.match(packageGate, /encoding="utf-8"/);
  assert.match(packageGate, /errors="replace"/);
  assert.match(packageGate, /Path\(config\["mirror_root"\]\)\.mkdir/);
  assert.match(windowsCi, /foreach \(\$isolatedHome in/);
  assert.doesNotMatch(windowsCi, /foreach \(\$home in/);
  assert.match(windowsCi, /choco install sqlite/);
  assert.match(windowsCi, /GTS_BIN=.*command -v/);
  assert.match(windowsCi, /export GTS_BIN/);
  assert.match(setupWindows, /@\('python', @\(\)\), @\('py', @\('-3'\)\)/);
  assert.match(setupWindows, /pathlib\.Path\(sys\.executable\)\.resolve\(\)\.parent/);
});
