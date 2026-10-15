/**
 * Offline fixture builder: a temporary mirror with real bare repositories,
 * committed locally. No network, no real mirror, no global git config.
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { run } from '../../lib/git.mjs';

const quietEnv = () => ({
  HOME: os.tmpdir(),
  GIT_CONFIG_NOSYSTEM: '1',
  GIT_TERMINAL_PROMPT: '0',
});

async function gitIn(dir, args) {
  const res = await run('git', args, { env: quietEnv(), cwd: dir, timeoutMs: 30000 });
  if (res.code !== 0) throw new Error(`git ${args[0]} failed: ${res.err}`);
  return res.out;
}

/**
 * Build a mirror.
 * @param {Array<{owner:string, name:string, files?:Record<string,string>, branch?:string}>} spec
 * @returns {Promise<{root:string, mirror:string, cleanup:()=>void, config:object}>}
 */
export async function makeMirror(spec) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'gitea-search-test-'));
  const mirror = path.join(root, 'mirror');
  const work = path.join(root, 'work');
  fs.mkdirSync(work, { recursive: true });

  const defaultFiles = {
    'README.md': '# repo\n\nhello world\n',
    'src/app.js': 'export const greeting = "hello world";\n// a line to search for: needle marker\n',
    'docs/note.txt': 'plain text with needle marker too\n',
    'cjk.txt': '中文内容：需要护栏 needle 标记。\n'.repeat(40),
    'big.txt': 'x'.repeat(5000) + '\n',
  };

  let counter = 0;
  for (const item of spec) {
    const branch = item.branch || 'main';
    const dir = path.join(work, `${counter}-${item.name}`);
    fs.mkdirSync(dir, { recursive: true });
    await gitIn(dir, ['init', '-q', '-b', branch]);
    for (const [name, content] of Object.entries(item.files || defaultFiles)) {
      const file = path.join(dir, name);
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, content);
      await gitIn(dir, ['add', '--', name]);
    }
    await gitIn(dir, ['-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', `${item.name}: initial commit`]);
    // A second commit on a second branch proves multi-branch searching.
    const other = branch === 'main' ? 'dev' : 'main';
    await gitIn(dir, ['checkout', '-q', '-b', other]);
    const extra = path.join(dir, 'src', 'extra.js');
    fs.writeFileSync(extra, 'export const extra = "needle in branch";\n');
    await gitIn(dir, ['add', '--', 'src/extra.js']);
    await gitIn(dir, ['-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', `${item.name}: branch work`]);
    await gitIn(dir, ['checkout', '-q', branch]);

    const bare = item.owner
      ? path.join(mirror, item.owner, `${item.name}.git`)
      : path.join(mirror, `${item.name}.git`);
    fs.mkdirSync(path.dirname(bare), { recursive: true });
    await gitIn(root, ['clone', '--quiet', '--bare', dir, bare]);
    counter += 1;
  }
  fs.writeFileSync(path.join(mirror, 'last_sync'), '2026-01-01T00:00:00Z\n');

  const config = {
    mirrorDir: mirror,
    baseUrl: '',
    token: '',
    owners: [],
    maxReadBytes: 2048,   // small enough to exercise truncation
    maxResults: 5,
    maxSearchBytes: 4096,
    maxLineBytes: 256,
    jobs: 4,
    syncJobs: 2,
    timeoutMs: 30000,
  };
  return {
    root,
    mirror,
    config,
    cleanup: () => fs.rmSync(root, { recursive: true, force: true }),
  };
}