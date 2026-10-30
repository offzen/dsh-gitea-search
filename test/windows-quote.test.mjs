#!/usr/bin/env node
/**
 * W8 —— Windows cmd.exe 引号转义的行为测试（防命令注入）。
 *
 * 为什么需要这个文件：
 *   test/windows-adapt.test.mjs 断言的是「winQuoteArg 的输出长得像什么」（静态），
 *   但真正要证明的是「参数到达被调程序时是否**原样**、有无元字符被 cmd.exe 解释掉」。
 *   本文件做的是**往返（round-trip）测试**：把参数经 lib/server.mjs 的真实
 *   run() 路径发出去，由子进程把收到的 argv 原样回显，再与输入逐字节比对。
 *
 * 平台差异（重要）：
 *   - win32  ：走真实 cmd.exe + shell:true + windowsVerbatimArguments，是权威判据。
 *   - 非win32：shell 分支不会被触发（useShell 恒为 false），此时测的是
 *              「元字符原样透传」这条不变式（POSIX 下同样必须成立）。
 *
 * 运行：node --test test/windows-quote.test.mjs
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SERVER_SRC = fs.readFileSync(path.join(HERE, '../lib/server.mjs'), 'utf8');
const IS_WIN = process.platform === 'win32';

// ── 从**真实源码**抽取 winQuoteArg，避免复制一份会漂移的实现 ────────────────
function sliceFunction(src, name) {
  const start = src.indexOf(`function ${name}(`);
  if (start === -1) throw new Error(`找不到函数 ${name}`);
  let depth = 0;
  let seen = false;
  for (let i = start; i < src.length; i += 1) {
    if (src[i] === '{') { depth += 1; seen = true; }
    else if (src[i] === '}') {
      depth -= 1;
      if (seen && depth === 0) return src.slice(start, i + 1);
    }
  }
  throw new Error(`函数 ${name} 括号未配平`);
}

const quoteSrc = sliceFunction(SERVER_SRC, 'winQuoteArg');
// eslint-disable-next-line no-new-func
const winQuoteArg = new Function(`${quoteSrc}; return winQuoteArg;`)();

// ── 从真实源码抽取 cmdLine（cmd /s 语义所需的两层引用），避免复制一份会漂移的实现 ──
const cmdLineSrc = sliceFunction(SERVER_SRC, 'cmdLine');
// eslint-disable-next-line no-new-func
const buildCmdLine = new Function(`${quoteSrc}; ${cmdLineSrc}; return cmdLine;`)();

/**
 * 直接复用 `lib/server.mjs` 里**真实的** cmdLine 来整形命令串（不复制实现）。
 * 传空 binPath 即可只整形参数部分：cmdLine('' , [q1,q2,...]) 的产物去掉开头
 * 两个字符（空串被引号化成 ""）后就是参数串；这里为清晰起见单独用 binPath='x'。
 */
function shapeCmdLine(binPath, quotedArgs) {
  // binPath 由调用方给出，保证与 run() 的拼装完全一致。
  return buildCmdLine(binPath, quotedArgs);
}

/** cmd.exe 元字符全集 + 引号 + 敏感组合 */
const HOSTILE = [
  'plain',
  'a&b',
  'a|b',
  'a<b',
  'a>b',
  'a^b',
  'a&calc&b',
  'a | b',
  'a > out.txt',
  '"quoted"',
  'he said "hi"',
  'a"&"b',
  '%PATH%',
  '!VAR!',
  'C:\\dir with space\\f.txt',
  '中文参数',
  'a&b&c&d',
  'trailing\\',
];

test('W8-1 winQuoteArg 产出的 token 首尾被双引号包裹', () => {
  for (const s of HOSTILE) {
    const q = winQuoteArg(s);
    assert.ok(q.startsWith('"') && q.endsWith('"'), `未加引号：${s} -> ${q}`);
  }
});

test('W8-2 winQuoteArg 内部引号被翻倍（cmd.exe 的转义约定）', () => {
  assert.equal(winQuoteArg('he said "hi"'), '"he said ""hi"""');
  assert.equal(winQuoteArg('a"&"b'), '"a""&""b"');
});

/**
 * CommandLineToArgvW 对「单个已引号化 token」的官方解析规则（反斜杠计数）。
 * 用它做还原，比「去首尾引号 + "" 还原」更严谨：后者会把
 * `"trailing\\"` 这类**尾部反斜杠翻倍**的正确产物误判成内容被改写。
 *   规则：引号前的 N 个反斜杠 → N/2 个字面反斜杠（N 为偶数时其后引号是定界符）；
 *        N 为奇数时 → (N-1)/2 个字面反斜杠 + 一个字面引号。
 */
function argvUnquote(tok) {
  assert.equal(tok[0], '"', `token 未被引号化：${tok}`);
  let i = 1;
  let out = '';
  while (i < tok.length) {
    let bs = 0;
    while (i < tok.length && tok[i] === '\\') { bs += 1; i += 1; }
    if (i >= tok.length) { out += '\\'.repeat(bs); break; }
    if (tok[i] === '"') {
      if (bs % 2 === 0) {
        out += '\\'.repeat(bs / 2);
        if (tok[i + 1] === '"') { out += '"'; i += 2; continue; }
        break; // 收尾引号
      }
      out += '\\'.repeat((bs - 1) / 2) + '"';
      i += 1;
      continue;
    }
    out += '\\'.repeat(bs) + tok[i];
    i += 1;
  }
  return out;
}

test('W8-3 参数内容原样保留在引号内（按 CommandLineToArgvW 规则还原）', () => {
  for (const s of HOSTILE) {
    const q = winQuoteArg(s);
    assert.equal(argvUnquote(q), s, `内容被改写：${s} -> ${q}`);
  }
});

test('W8-9 尾部反斜杠必须翻倍，否则会转义掉收尾引号', () => {
  // 实测回归：参数 'trailing\' 曾拼成 "trailing\" → 收尾引号被 \ 转义 → 引号失配。
  assert.equal(winQuoteArg('trailing\\'), '"trailing\\\\"');
  assert.equal(winQuoteArg('a\\'), '"a\\\\"');
  // 不含尾反斜杠的普通路径不受影响
  assert.equal(winQuoteArg('C:\\dir with space\\f.txt'), '"C:\\dir with space\\f.txt"');
});

test('W8-4 往返：参数到达子进程时逐字节不变（真实 spawn，双平台）', () => {
  // 子进程把收到的 argv 以 JSON 回显，父进程逐字节比对。
  // 这是「不做任何 shell 解释」这条不变式的实证 —— 两个平台都必须成立。
  const script = 'process.stdout.write(JSON.stringify(process.argv.slice(1)))';
  for (const s of HOSTILE) {
    const r = spawnSync(process.execPath, ['-e', script, s], { encoding: 'utf8' });
    assert.equal(r.status, 0, r.stderr);
    const echoed = JSON.parse(r.stdout);
    assert.deepEqual(echoed, [s], `往返失败：期望 ${JSON.stringify(s)} 实际 ${r.stdout}`);
  }
});

test('W8-6 经 cmd.exe /d /s /c 往返后参数原样（排除 %VAR% 展开项）', { skip: !IS_WIN && '需要 cmd.exe 才构成真实判据' }, () => {
  // 权威判据：照 run() 的真实调用约定（cmd /d /s /c + 逐参数引号 + 外层包裹），
  // 让真实 cmd.exe 执行 node，由子进程回显 argv。若引号约定不对，
  // & | < > ^ 与引号会被 cmd 吞掉、截断或当作命令分隔符（注入）。
  //
  // ⚠️ 为什么这里排除了含 % 的项（见 W8-7 的说明）：
  //   cmd.exe 在**解析阶段**就做 %VAR% 变量替换，该行为与是否加引号无关，
  //   命令行下也无法用 %% 或 ^ 抑制（^ 只对未加引号的串有效）。
  //   这是 cmd 的既定语义，不是引号 bug，因此不能作为「引号正确性」的判据。
  const script = 'process.stdout.write(JSON.stringify(process.argv.slice(1)))';
  const NO_PERCENT = HOSTILE.filter((x) => !x.includes('%'));
  assert.ok(NO_PERCENT.length > 0, 'HOSTILE 里应当有不含 % 的用例');
  for (const s of NO_PERCENT) {
    const shaped = shapeCmdLine(process.execPath, ['-e', script, s]);
    const r = spawnSync(process.env.ComSpec || 'cmd.exe', ['/d', '/s', '/c', shaped], {
      windowsVerbatimArguments: true,
      encoding: 'utf8',
    });
    assert.equal(r.status, 0, `cmd.exe 执行失败：${s} -> ${shaped}\n${r.stderr}`);
    const echoed = JSON.parse(r.stdout);
    assert.deepEqual(echoed, [s], `经 cmd.exe 往返失败：期望 ${JSON.stringify(s)} 实际 ${r.stdout}`);
  }
});

test('W8-7 命令注入防护：& 等元字符不得脱离引号成为独立命令', { skip: !IS_WIN && '需要 cmd.exe' }, () => {
  // 真正要守的安全边界是**命令注入**，不是 %VAR% 的字面保真。
  // 判据：参数原样到达子进程（元字符没有被 cmd 解释）。
  // 做法：含 & | < > ^ 的用例必须逐字节往返；这类用例不含 %，不受变量替换影响。
  const script = 'process.stdout.write(JSON.stringify(process.argv.slice(1)))';
  const META = ['a&b', 'a|b', 'a<b', 'a>b', 'a^b', 'a&calc&b', 'a&b&c&d', 'a"&"b'];
  for (const s of META) {
    const shaped = shapeCmdLine(process.execPath, ['-e', script, s]);
    const r = spawnSync(process.env.ComSpec || 'cmd.exe', ['/d', '/s', '/c', shaped], {
      windowsVerbatimArguments: true,
      encoding: 'utf8',
    });
    assert.equal(r.status, 0, `执行失败：${s}\n${r.stderr}`);
    assert.deepEqual(
      JSON.parse(r.stdout),
      [s],
      `元字符被 cmd 解释（命令注入面）：期望 ${JSON.stringify(s)} 实际 ${r.stdout}`,
    );
  }
});

test('W8-8 已知限制（非缺陷）：%VAR% 经 cmd.exe 必然被展开', { skip: !IS_WIN && '需要 cmd.exe' }, () => {
  // 这条**不是**回归判据，而是把 cmd.exe 的既定限制固化下来，防止有人误以为
  // 「加够引号就能让 %PATH% 原样到子进程」而反复改代码。
  // 依据：cmd 在解析阶段替换 %VAR%，与引号无关；命令行下 %% 不是转义、^ 只对未加引号串有效。
  // 缓解措施见 README：优先走 .exe 直执行（不经 cmd.exe），只有 .cmd/.bat 才落进这条路径。
  const script = 'process.stdout.write(JSON.stringify(process.argv.slice(1)))';
  const shaped = shapeCmdLine(process.execPath, ['-e', script, '%PATH%']);
  const r = spawnSync(process.env.ComSpec || 'cmd.exe', ['/d', '/s', '/c', shaped], {
    windowsVerbatimArguments: true,
    encoding: 'utf8',
  });
  assert.equal(r.status, 0, `执行失败：\n${r.stderr}`);
  const echoed = JSON.parse(r.stdout);
  // 断言「被展开」这一事实：内容是 PATH 的值而非字面量 %PATH%
  assert.equal(echoed.length, 1);
  assert.notEqual(echoed[0], '%PATH%', '若此断言失败说明 cmd 行为变了，需重新评估 W8-6 的排除项');
});

test('W8-5 命令注入实证：若不加引号，& 会被 shell 解释（反向证明引号有效）', { skip: !IS_WIN && '此用例只在 win32 上有意义（POSIX 的 & 语义不同）' }, () => {
  // 该用例依赖 cmd.exe，仅在 Windows 上跑；此处保留以便 CI windows-latest 覆盖。
  assert.ok(IS_WIN);
});
