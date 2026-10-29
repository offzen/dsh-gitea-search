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

  const owners = path.join(dir, 'myorg');
  fs.mkdirSync(owners, { recursive: true });
  git(['clone', '-q', '--bare', src, path.join(owners, 'repo.git')], dir);
  return dir;
}

test('CLI: POSIX 下 --read 必须区分 "a\\b.md" 与 "a/b.md"', { skip: SKIP_ON_WIN }, () => {
  const dir = makeFixture();
  try {
    const env = { ...process.env, GITEA_MIRROR_ROOT: dir, GITEA_OWNERS: 'myorg' };
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
