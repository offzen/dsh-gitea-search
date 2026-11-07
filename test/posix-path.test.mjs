/**
 * POSIX 路径语义回归测试
 *
 * 背景（v1.2.2-rc 引入的缺陷）：
 *   为支持 Windows，共享代码里加入了无条件的 `path.replace(/\\/g, '/')`。
 *   但反斜杠在 POSIX (ext4/APFS) 上是**合法文件名字符**——"a\b.md" 是一个真实
 *   存在的单文件名，Git 会如实保留它。无脑转换会让 `git show HEAD:a\b.md`
 *   变成 `git show HEAD:a/b.md`，从而**静默返回另一个文件的内容且不报错**。
 *
 *   同一处转换还会让 `classify_kind("foo\.md")` 把路径变成 "foo/.md"，
 *   splitext 将其视为隐藏文件而返回空扩展名，kind 落入 code 而非 doc，造成漏召回。
 *
 * 本文件锁定这些语义，防止后续"Windows 适配"再次踩坏 POSIX 侧。
 *
 * Windows 上运行会自动跳过（NTFS 上反斜杠是保留分隔符，构造不出这类文件名）。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const IS_WIN = process.platform === 'win32';
const SKIP_ON_WIN = IS_WIN ? 'NTFS 上反斜杠是保留分隔符，无法构造含反斜杠的文件名' : false;

function git(args, cwd) {
  return spawnSync('git', args, { cwd, encoding: 'utf8' });
}

/**
 * 造一个镜像仓 fixture，其中同时存在三个不同的东西：
 *   - "a\b.md"  (POSIX 上是**单个文件名**，内含反斜杠)
 *   - "a/b.md"  (子目录 a 下的 b.md)
 *   - "a.md"    (普通文件)
 * 三者内容各不相同，一旦归一化逻辑出错，读到的内容就会张冠李戴。
 */
function makeFixture() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gts-posix-path-'));
  const src = path.join(dir, 'src');
  fs.mkdirSync(src, { recursive: true });
  fs.writeFileSync(path.join(src, 'a\\b.md'), 'MARK_BACKSLASH\n');
  fs.mkdirSync(path.join(src, 'a'), { recursive: true });
  fs.writeFileSync(path.join(src, 'a', 'b.md'), 'MARK_SUB\n');
  fs.writeFileSync(path.join(src, 'a.md'), 'MARK_PLAIN\n');

  const ident = ['-c', 'user.email=t@e.st', '-c', 'user.name=test'];
  git(['init', '-q', '-b', 'master'], src);
  git([...ident, 'add', '-A'], src);
  git([...ident, 'commit', '-qm', 'init'], src);

  const owners = path.join(dir, 'test-owner');
  fs.mkdirSync(owners, { recursive: true });
  git(['clone', '-q', '--bare', src, path.join(owners, 'repo.git')], dir);
  return dir;
}

test('CLI: POSIX 下 --read 必须区分 "a\\b.md" 与 "a/b.md"', { skip: SKIP_ON_WIN }, () => {
  const dir = makeFixture();
  try {
    const env = { ...process.env, GITEA_MIRROR_ROOT: dir, GITEA_OWNERS: 'test-owner' };
    const cli = path.join(ROOT, 'bin', 'gts');
    const read = (p) =>
      spawnSync('python3', [cli, '-r', 'repo', '--read', p], { env, encoding: 'utf8' });

    const bs = read('a\\b.md');
    assert.equal(bs.status, 0, `读 a\\b.md 应成功，实际 stderr=${bs.stderr}`);
    assert.match(
      bs.stdout,
      /MARK_BACKSLASH/,
      '读 "a\\b.md" 必须返回该文件自身内容；若返回 MARK_SUB 说明反斜杠被误当分隔符',
    );
    assert.doesNotMatch(bs.stdout, /MARK_SUB/, '不应读到 a/b.md 的内容');

    const sep = read('a/b.md');
    assert.equal(sep.status, 0, '读 a/b.md 应成功');
    assert.match(sep.stdout, /MARK_SUB/, '子目录路径必须仍能正常读取');
    assert.doesNotMatch(sep.stdout, /MARK_BACKSLASH/, '不应读到 a\\b.md 的内容');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('indexer.classify_kind: 含反斜杠的文件名不得失去扩展名', { skip: SKIP_ON_WIN }, () => {
  const py = [
    'import sys',
    `sys.path.insert(0, ${JSON.stringify(path.join(ROOT, 'lib'))})`,
    'import indexer',
    // 用 raw 字面量避免 shell/JS 双重转义干扰
    'print(indexer.classify_kind("foo\\\\.md"))',
    'print(indexer.classify_kind("notes/readme.md"))',
  ].join('; ');
  const r = spawnSync('python3', ['-c', py], { encoding: 'utf8' });
  assert.equal(r.status, 0, `indexer 调用失败: ${r.stderr}`);
  const [withBackslash, normal] = r.stdout.trim().split('\n');
  assert.equal(withBackslash, 'doc', '"foo\\.md" 在 POSIX 上扩展名就是 .md，应分类为 doc');
  assert.equal(normal, 'doc', '常规路径分类不应受影响');
});

test('bin/gts: 行尾处理不得吞掉行内容尾部的 CR', () => {
  const src = fs.readFileSync(path.join(ROOT, 'bin', 'gts'), 'utf8');
  // 先统一换行为 LF：Windows 上 git 可能以 CRLF 检出，注释剥离用的 /^\s*#.*$/
  // 中 `.` 不匹配 `\r`，会把带 \r 结尾的注释行误判为代码，导致假失败。
  const normalized = src.replace(/\r\n/g, '\n');
  // 只检查代码：源码注释里会特意提到这个写法（解释为何不能用），需先剥掉注释
  const code = normalized
    .split('\n')
    .map((l) => l.replace(/^\s*#.*$/, ''))
    .join('\n');
  assert.doesNotMatch(
    code,
    /rstrip\(\s*["']\\r\\n["']\s*\)/,
    'rstrip("\\r\\n") 会把 "abc\\r\\r\\n" 削成 "abc"，连行内容里的 CR 一起吃掉；' +
      '应改为只剥除行终止符本身',
  );
});

test('server.mjs: 路径归一化必须按平台分流', () => {
  const src = fs.readFileSync(path.join(ROOT, 'lib', 'server.mjs'), 'utf8');
  assert.match(src, /function normalizeGitPath/, '应提供统一的平台感知归一化入口');

  const fn = src.match(/function normalizeGitPath\([\s\S]*?\n}/);
  assert.ok(fn, '应能定位到 normalizeGitPath 函数体');
  assert.match(
    fn[0],
    /IS_WIN\s*\?/,
    'normalizeGitPath 必须依据 IS_WIN 分流，POSIX 侧不得转换反斜杠',
  );

  // readGiteaFile 必须走归一化入口，而不是就地裸替换
  const readFn = src.match(/async function readGiteaFile\([\s\S]*?\n}/);
  assert.ok(readFn, '应能定位到 readGiteaFile');
  assert.match(readFn[0], /normalizeGitPath/, 'readGiteaFile 应使用 normalizeGitPath');
  assert.doesNotMatch(
    readFn[0],
    /\.replace\(\/\\\\\/g, '\/'\)/,
    'readGiteaFile 内不得再有裸的反斜杠替换',
  );
});

test('indexer.py: 归一化同样按平台分流', () => {
  const py = fs.readFileSync(path.join(ROOT, 'lib', 'indexer.py'), 'utf8');
  assert.match(py, /IS_WINDOWS\s*=\s*os\.name\s*==\s*["']nt["']/, '应有平台常量');
  // classify_kind 与 _normalize_repo_pattern 里的替换必须受 IS_WINDOWS 约束
  const kindFn = py.match(/def classify_kind\([\s\S]*?return ['"]code['"]/);
  assert.ok(kindFn, '应能定位 classify_kind');
  assert.match(kindFn[0], /IS_WINDOWS/, 'classify_kind 的归一化应受 IS_WINDOWS 约束');
});

// ── rc7 B6/B7：CLI 解析的机械约束（PATH 只认绝对目录 + realpath）与显式 GTS_BIN 不静默回落 ─────────
//
// lib/server.mjs 顶层会连 MCP server，无法直接 import：与 windows-adapt 同一做法，从**真实源码**
// 切出函数，在注入了 process/MODULE_DIR/IS_WIN/log 的沙箱里运行。
const SERVER_SRC = fs.readFileSync(path.join(ROOT, 'lib', 'server.mjs'), 'utf8');
const SKIP_WIN = IS_WIN ? '依赖 POSIX 可执行位/符号链接语义' : false;

function sliceFn(name) {
  const start = SERVER_SRC.indexOf(`function ${name}(`);
  if (start === -1) throw new Error(`找不到函数 ${name}`);
  let depth = 0;
  let seen = false;
  for (let i = start; i < SERVER_SRC.length; i += 1) {
    if (SERVER_SRC[i] === '{') { depth += 1; seen = true; }
    else if (SERVER_SRC[i] === '}') {
      depth -= 1;
      if (seen && depth === 0) return SERVER_SRC.slice(start, i + 1);
    }
  }
  throw new Error(`函数 ${name} 括号未配平`);
}

/** 在沙箱里构造 resolveSearchCli：moduleDir/home/env 可注入，log 被收集。 */
function loadResolver({ env, moduleDir, home }) {
  const logs = [];
  const body = [
    "const WIN_EXEC_EXTS = ['.exe', '.com']; const WIN_SHELL_EXTS = ['.cmd', '.bat'];",
    "const GTS_IDENTITY_RE = /gitea|gitq|dsh-search/i;",
    ...['expandHome', 'classifyWinExecutable', 'probeExecutable', 'looksLikeGtsCli', 'safePathDirs', 'explainUnusable', 'resolveSearchCli'].map(sliceFn),
    'return { resolveSearchCli, safePathDirs };',
  ].join('\n');
  // eslint-disable-next-line no-new-func
  const api = new Function('fs', 'os', 'path', 'spawnSync', 'process', 'MODULE_DIR', 'IS_WIN', 'log', body)(
    fs, { homedir: () => home }, path, spawnSync, { env, platform: process.platform }, moduleDir, false, (...a) => logs.push(a.join(' ')),
  );
  return { ...api, logs };
}

function writeExe(file, body = '#!/bin/sh\necho "gts (Gitea search) help"\n') {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, body);
  fs.chmodSync(file, 0o755);
  return file;
}

function resolverFixture() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gts-resolve-'));
  const home = path.join(dir, 'home');
  fs.mkdirSync(home, { recursive: true });
  const moduleDir = path.join(dir, 'proj', 'lib');
  fs.mkdirSync(moduleDir, { recursive: true });
  return { dir, home, moduleDir, projBin: path.join(dir, 'proj', 'bin', 'gts') };
}

const SYSTEM_GTS = fs.existsSync('/usr/local/bin/gts') || fs.existsSync('/usr/local/bin/dsh-search') || fs.existsSync('/usr/local/bin/gitq');

test('B7 GTS_BIN 显式设置但不存在 ⇒ 硬报错，且不回落到固定位（即使 bin/gts 可用）', { skip: SKIP_WIN }, () => {
  const { dir, home, moduleDir, projBin } = resolverFixture();
  writeExe(projBin); // 旧实现会静默回落到它
  const missing = path.join(dir, 'no-such-gts');
  const r = loadResolver({ env: { GTS_BIN: missing, PATH: '' }, moduleDir, home }).resolveSearchCli();
  assert.equal(r.bin, undefined, '不得返回任何可用 CLI');
  assert.match(r.error, /GTS_BIN=/);
  assert.ok(r.error.includes(missing), '错误必须点明用户指定的路径');
  assert.match(r.error, /文件不存在/);
  assert.match(r.error, /不会静默回落/);
});

test('B7 GTS_BIN 指向不可执行文件/目录 ⇒ 硬报错并说明原因', { skip: SKIP_WIN }, () => {
  const { dir, home, moduleDir, projBin } = resolverFixture();
  writeExe(projBin);
  const plain = path.join(dir, 'plain.txt');
  fs.writeFileSync(plain, 'not executable');
  fs.chmodSync(plain, 0o644);
  const noExec = loadResolver({ env: { GTS_BIN: plain, PATH: '' }, moduleDir, home }).resolveSearchCli();
  assert.match(noExec.error, /没有执行权限/);
  const asDir = loadResolver({ env: { GTS_BIN: dir, PATH: '' }, moduleDir, home }).resolveSearchCli();
  assert.match(asDir.error, /不是普通文件/);
});

test('B7 GITQ_BIN（别名）同样硬报错；GTS_BIN 优先于 GITQ_BIN；空串视为未设置', { skip: SKIP_WIN }, () => {
  const { dir, home, moduleDir, projBin } = resolverFixture();
  writeExe(projBin);
  const good = writeExe(path.join(dir, 'good', 'gts'));
  const missing = path.join(dir, 'missing');
  const resolve = (env) => loadResolver({ env: { PATH: '', ...env }, moduleDir, home }).resolveSearchCli();
  assert.match(resolve({ GITQ_BIN: missing }).error, /GITQ_BIN=/);
  assert.equal(resolve({ GTS_BIN: good, GITQ_BIN: missing }).bin, good);
  assert.match(resolve({ GTS_BIN: missing, GITQ_BIN: good }).error, /GTS_BIN=/, 'GTS_BIN 坏了不得转而使用 GITQ_BIN');
  assert.equal(resolve({ GTS_BIN: '' }).bin, projBin, '空串 = 未设置，走自动查找');
});

test('B7 GTS_BIN 可用时被采用；相对路径解析为绝对路径（探测与 spawn 同一文件）', { skip: SKIP_WIN }, () => {
  const { dir, home, moduleDir } = resolverFixture();
  const good = writeExe(path.join(dir, 'good', 'gts'));
  const direct = loadResolver({ env: { GTS_BIN: good, PATH: '' }, moduleDir, home }).resolveSearchCli();
  assert.deepEqual(direct, { bin: good, kind: 'direct' });
  const rel = path.relative(process.cwd(), good);
  const viaRel = loadResolver({ env: { GTS_BIN: rel, PATH: '' }, moduleDir, home }).resolveSearchCli();
  assert.equal(viaRel.bin, good);
  assert.ok(path.isAbsolute(viaRel.bin));
});

test('B7 GTS_BIN 未设置时才走自动查找（固定位）', { skip: SKIP_WIN }, () => {
  const { home, moduleDir, projBin } = resolverFixture();
  writeExe(projBin);
  const r = loadResolver({ env: { PATH: '' }, moduleDir, home }).resolveSearchCli();
  assert.equal(r.bin, projBin);
});

test('B6 safePathDirs 拒绝空项、"."、相对目录，只保留绝对目录', { skip: SKIP_WIN }, () => {
  const { safePathDirs } = loadResolver({ env: {}, moduleDir: '/x', home: '/h' });
  const { dirs, rejected } = safePathDirs(['/usr/bin', '', '.', 'rel/dir', '  ', './x', '/opt/bin'].join(path.delimiter));
  assert.deepEqual(dirs, ['/usr/bin', '/opt/bin']);
  assert.deepEqual(rejected, ['(空项)', '.', 'rel/dir', '(空项)', './x']);
  assert.deepEqual(safePathDirs(undefined), { dirs: [], rejected: [] }, 'PATH 未设置 = 没有任何项');
  assert.deepEqual(safePathDirs(''), { dirs: [], rejected: ['(空项)'] }, 'PATH 为空串 = 一个空项（POSIX 语义即当前目录）');
});

function plantedFixture() {
  const fx = resolverFixture();
  const marker = path.join(fx.dir, 'EXECUTED');
  // 植入的「gts」：打印足以蒙混启发式的 gitea 字样，同时留下被执行的证据。
  const planted = writeExe(path.join(fx.dir, 'evil-repo', 'gts'), `#!/bin/sh\ntouch ${JSON.stringify(marker)}\necho "gitea gts help"\n`);
  return { ...fx, marker, planted };
}

test('B6 PATH 中的相对目录/空项/"." 不会让仓库内植入的程序被执行', { skip: SKIP_WIN || (SYSTEM_GTS && '系统已安装 /usr/local/bin/gts，固定位会先命中') }, () => {
  const { dir, home, moduleDir, marker, planted } = plantedFixture();
  const rel = path.relative(process.cwd(), path.dirname(planted));
  const good = writeExe(path.join(dir, 'good-bin', 'gts'));
  const env = { PATH: [rel, '', '.', path.dirname(good)].join(path.delimiter) };
  const resolver = loadResolver({ env, moduleDir, home });
  const r = resolver.resolveSearchCli();
  assert.equal(fs.existsSync(marker), false, '植入的程序绝不能被执行（含启发式 --help 探测）');
  assert.equal(r.bin, fs.realpathSync(good), '只会选中绝对目录里的 gts');
  const warn = resolver.logs.find((l) => l.includes('非绝对路径'));
  assert.ok(warn, '必须有显式警告');
  assert.ok(warn.includes(JSON.stringify(rel)) && warn.includes('(空项)') && warn.includes('"."'), `警告应列出被忽略的项: ${warn}`);
});

test('B6 PATH 只含相对/空项且 cwd 里有植入程序 ⇒ 找不到 CLI（返回 null），植入程序未执行', { skip: SKIP_WIN || (SYSTEM_GTS && '系统已安装 /usr/local/bin/gts，固定位会先命中') }, () => {
  const { home, moduleDir, marker, planted } = plantedFixture();
  const cwd = process.cwd();
  process.chdir(path.dirname(planted));
  try {
    for (const raw of ['.', '', `${path.delimiter}`, `.${path.delimiter}`]) {
      const r = loadResolver({ env: { PATH: raw }, moduleDir, home }).resolveSearchCli();
      assert.equal(r, null, `PATH=${JSON.stringify(raw)}`);
    }
  } finally {
    process.chdir(cwd);
  }
  assert.equal(fs.existsSync(marker), false);
});

test('B6 PATH 命中的符号链接按 realpath 采用（真实文件），悬空链接被跳过', { skip: SKIP_WIN || (SYSTEM_GTS && '系统已安装 /usr/local/bin/gts，固定位会先命中') }, () => {
  const { dir, home, moduleDir } = resolverFixture();
  const real = writeExe(path.join(dir, 'real', 'gts-real'));
  const binDir = path.join(dir, 'bin-link');
  fs.mkdirSync(binDir);
  fs.symlinkSync(real, path.join(binDir, 'gts'));
  fs.symlinkSync(path.join(dir, 'gone'), path.join(binDir, 'gitq')); // 悬空
  const resolver = loadResolver({ env: { PATH: binDir }, moduleDir, home });
  const r = resolver.resolveSearchCli();
  assert.equal(r.bin, fs.realpathSync(real), 'spawn 的必须是 realpath 之后的真实文件，而不是链接路径');
  assert.notEqual(r.bin, path.join(binDir, 'gts'));
});

test('B6 启发式不匹配的 PATH 程序被跳过并提示「非安全判定」；verifyIsGtsCli 已降级改名', { skip: SKIP_WIN || (SYSTEM_GTS && '系统已安装 /usr/local/bin/gts，固定位会先命中') }, () => {
  const { dir, home, moduleDir } = resolverFixture();
  const other = writeExe(path.join(dir, 'other-bin', 'gts'), '#!/bin/sh\necho "GNU Triangulated Surface tool"\n');
  const resolver = loadResolver({ env: { PATH: path.dirname(other) }, moduleDir, home });
  assert.equal(resolver.resolveSearchCli(), null);
  assert.ok(resolver.logs.some((l) => l.includes('非安全判定')), resolver.logs.join('\n'));
  assert.doesNotMatch(SERVER_SRC, /verifyIsGtsCli/, '旧名字暗示它是校验，必须改名');
  assert.match(SERVER_SRC, /不是安全边界，不构成身份校验/);
});

async function callServerTool(env, name, args) {
  const { Client } = await import('@modelcontextprotocol/sdk/client/index.js');
  const { StdioClientTransport } = await import('@modelcontextprotocol/sdk/client/stdio.js');
  const transport = new StdioClientTransport({ command: process.execPath, args: [path.join(ROOT, 'lib', 'server.mjs')], env, stderr: 'ignore' });
  const client = new Client({ name: 'rc7-test', version: '0' }, { capabilities: {} });
  try {
    await client.connect(transport);
    return await client.callTool({ name, arguments: args });
  } finally {
    await client.close().catch(() => {});
  }
}

test('B7 端到端：MCP server 在 GTS_BIN 不可用时返回结构化错误，而不是静默改用 bin/gts', { skip: SKIP_WIN, timeout: 30000 }, async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gts-b7-e2e-'));
  const missing = path.join(dir, 'no-such-gts');
  const env = { ...process.env, GITEA_MIRROR_ROOT: dir, GITEA_OWNERS: 'test-owner', GTS_BIN: missing };
  for (const [tool, args] of [['search_gitea', { query: 'x' }], ['list_gitea_repos', {}], ['search_gitea_docs', { query: 'x' }]]) {
    const res = await callServerTool(env, tool, args);
    assert.equal(res.isError, true, tool);
    assert.match(res.content[0].text, /GTS_BIN=/, tool);
    assert.ok(res.content[0].text.includes(missing), tool);
    assert.match(res.content[0].text, /不会静默回落/, tool);
  }
});
