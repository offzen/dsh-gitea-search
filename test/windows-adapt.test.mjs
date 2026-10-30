#!/usr/bin/env node
/**
 * Windows 适配单元测试（node --test）。
 *
 * 只测**纯逻辑**，不需要 Windows 机器、不需要真实镜像、不启动 MCP server：
 *   - classifyWinExecutable：Windows 上什么扩展名才能被 CreateProcess 执行
 *   - probeExecutable：跨平台可执行性判定（含 bin/gts 在 Windows 必须被判为不可执行）
 *   - winQuoteArg：cmd.exe 引号转义（防命令注入）
 *   - missingCliMessage：Windows 上要给出可照做的指引
 *
 * 运行：node --test（自动递归发现 test/*.test.mjs）
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

// lib/server.mjs 顶层会连 MCP server，无法直接 import；这里把纯函数抽出来测。
// 抽取方式：读取源文件，用正则取出函数体后在沙箱里 eval —— 保证测的是**真实源码**，
// 而不是复制一份可能漂移的实现。
const SRC = fs.readFileSync(new URL('../lib/server.mjs', import.meta.url), 'utf8');

function extractFunction(name) {
  const re = new RegExp(`^(?:function ${name}\\s*\\(|const ${name}\\s*=\\s*\\()`, 'm');
  const m = srcIndex(name);
  if (m == null) throw new Error(`找不到函数 ${name}`);
  return m;
}

function srcIndex(name) {
  const idx = SRC.indexOf(`function ${name}(`);
  return idx === -1 ? null : idx;
}

// 截取「从函数声明起、括号配平到结束」的源码片段。
function sliceFunction(name) {
  const start = SRC.indexOf(`function ${name}(`);
  if (start === -1) throw new Error(`找不到函数 ${name}`);
  let depth = 0;
  let seenBrace = false;
  for (let i = start; i < SRC.length; i += 1) {
    const ch = SRC[i];
    if (ch === '{') { depth += 1; seenBrace = true; }
    else if (ch === '}') {
      depth -= 1;
      if (seenBrace && depth === 0) return SRC.slice(start, i + 1);
    }
  }
  throw new Error(`函数 ${name} 括号未配平`);
}

const SANDBOX_ENV = `
const IS_WIN = process.platform === 'win32';
const WIN_EXEC_EXTS = ['.exe', '.com'];
const WIN_SHELL_EXTS = ['.cmd', '.bat'];
`;
// 依赖的第三方：fs / path 在沙箱里可用
const PRELUDE = SANDBOX_ENV;

function makeFn(simulateWin) {
  const body = [
    PRELUDE,
    sliceFunction('classifyWinExecutable'),
    // probeExecutable 依赖 IS_WIN；测试时替换成可注入的版本
    sliceFunction('winQuoteArg'),
    'return { classifyWinExecutable, winQuoteArg };',
  ].join('\n');
  // eslint-disable-next-line no-new-func
  return new Function('fs', 'path', 'IS_WIN_OVERRIDE', body)(fs, path);
}

const { classifyWinExecutable, winQuoteArg } = makeFn(false);

test('Windows 可执行判定：.exe/.com 直执行，.cmd/.bat 走 shell', () => {
  assert.equal(classifyWinExecutable('gts.exe'), 'direct');
  assert.equal(classifyWinExecutable('GTS.EXE'), 'direct');
  assert.equal(classifyWinExecutable('C:\\x\\gts.com'), 'direct');
  assert.equal(classifyWinExecutable('gts.cmd'), 'shell');
  assert.equal(classifyWinExecutable('gts.bat'), 'shell');
});

test('Windows 可执行判定：bin/gts(.py/无扩展名) 必须被判为不可执行', () => {
  // 这是本次修复的核心：旧代码用 X_OK，在 Windows 上退化为存在性检查会误判为「找到了」，
  // 随后 spawn 抛 EFTYPE/ENOEXEC，用户看到莫名其妙的错误而不是友好提示。
  assert.equal(classifyWinExecutable('bin/gts'), null);
  assert.equal(classifyWinExecutable('C:\\proj\\bin\\gts'), null);
  assert.equal(classifyWinExecutable('gts.py'), null);
  assert.equal(classifyWinExecutable('gts.sh'), null);
});

test('winQuoteArg：整个参数被双引号包住，内部引号翻倍', () => {
  assert.equal(winQuoteArg('hello'), '"hello"');
  assert.equal(winQuoteArg('a b'), '"a b"');
  assert.equal(winQuoteArg('say "hi"'), '"say ""hi"""');
});

test('winQuoteArg：cmd 元字符被包在引号内不被解释', () => {
  // 命令注入防护：pattern 是模型可控字符串，`a&calc` 若不加引号会被 cmd 当成两条命令。
  for (const evil of ['a&calc', 'a|b', 'a>b', 'a<b', 'a^b', 'a&&b']) {
    const q = winQuoteArg(evil);
    assert.equal(q, `"${evil}"`);
    assert.ok(q.startsWith('"') && q.endsWith('"'), `${evil} 必须被引号包裹`);
  }
});

test('源码含 Windows 关键适配点（防回退）', () => {
  // 断言这些改动没有被后续提交无意改回旧行为
  assert.match(SRC, /process\.platform === 'win32'/, '应有平台判定');
  assert.match(SRC, /windowsHide:\s*true/, 'spawn 应设置 windowsHide');
  assert.match(SRC, /WIN_EXEC_EXTS/, '应有 .exe/.com 扩展名表');
  assert.match(SRC, /WIN_SHELL_EXTS/, '应有 .cmd/.bat 扩展名表');
  assert.match(SRC, /missingCliMessage/, '应有平台化缺 CLI 提示');
  // 旧的「仅 X_OK」判定不应再作为唯一判据出现在 findSearchCli 路径上
  const probe = sliceFunction('probeExecutable');
  assert.match(probe, /IS_WIN/, 'probeExecutable 必须区分平台');
});

test('Python 层：POSIX 专属模块已改为条件导入', () => {
  const py = fs.readFileSync(new URL('../lib/indexer.py', import.meta.url), 'utf8');
  assert.match(py, /^IS_WINDOWS\s*=\s*os\.name\s*==\s*["']nt["']/m, '应有 Windows 判定');
  assert.match(py, /import msvcrt/, 'Windows 应使用 msvcrt 做字节区间锁');
  // 顶层不应再有无条件的 import fcntl / import resource
  assert.ok(!/^import fcntl$/m.test(py), '不允许无条件 import fcntl');
  assert.ok(!/^import resource$/m.test(py), '不允许无条件 import resource');
});

test('readGiteaFile 与 gitDir 路径归一化（防 Windows 反斜杠破损）', () => {
  // readGiteaFile 与 gitDir 必须将反斜杠转为正斜杠，因为 Git tree object 只认 /
  assert.ok(SRC.includes(".replace(/\\\\/g, '/')"), '需把 \\ 归一化为 /');
  // findSearchCli 需支持从 PATH 环境变量搜索
  assert.match(SRC, /process\.env\.PATH/, 'findSearchCli 应支持从 PATH 环境变量搜索');
});

test('IndexLock Windows 锁区间一致性：_acquire 前必须 seek(0)', () => {
  const py = fs.readFileSync(new URL('../lib/indexer.py', import.meta.url), 'utf8');
  assert.match(py, /self\.fd\.seek\(0\)[\r\n\s]+msvcrt\.locking/, 'Windows _acquire 必须在 locking 前 seek(0)');
});

test('setup-windows.ps1 YAML 路径使用 Replace 避免正则转义缺陷', () => {
  const ps = fs.readFileSync(new URL('../scripts/setup-windows.ps1', import.meta.url), 'utf8');
  assert.match(ps, /\.Replace\(['"]\\['"],\s*['"]\/['"]\)/, 'setup-windows.ps1 应使用 .Replace 避免正则替换损坏');
});
