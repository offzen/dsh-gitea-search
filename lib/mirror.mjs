/**
 * The local mirror: repository discovery, ref listing and file reads.
 *
 * Layout, both accepted:
 *   <mirror>/<owner>/<repo>.git      (one directory per user or organisation)
 *   <mirror>/<repo>.git              (flat, single owner)
 * A repository is addressed as "owner/repo" in the first layout and "repo" in
 * the second; both the full id and the bare name are accepted as filters.
 *
 * Nothing here joins a caller-supplied string into a filesystem path: the set of
 * repositories is always the result of scanning the mirror directory, and a
 * request for an unknown id simply does not resolve.
 */
import fs from 'node:fs';
import path from 'node:path';
import { GIT_BASE, run } from './git.mjs';
import { LAST_SYNC_FILE, STATE_DIR } from './config.mjs';

export function scanRepos(config) {
  const repos = new Map();
  let entries = [];
  try {
    entries = fs.readdirSync(config.mirrorDir, { withFileTypes: true });
  } catch {
    return repos;
  }
  const add = (owner, name, dir) => {
    const id = owner ? `${owner}/${name}` : name;
    repos.set(id, { id, owner, name, dir });
  };
  for (const entry of entries) {
    if (entry.name === STATE_DIR || entry.name.startsWith('.')) continue;
    const full = path.join(config.mirrorDir, entry.name);
    if (entry.isDirectory() && entry.name.endsWith('.git')) {
      add('', entry.name.slice(0, -4), full);
      continue;
    }
    if (!entry.isDirectory()) continue;
    let children = [];
    try {
      children = fs.readdirSync(full, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const child of children) {
      if (child.isDirectory() && child.name.endsWith('.git')) {
        add(entry.name, child.name.slice(0, -4), path.join(full, child.name));
      }
    }
  }
  return repos;
}

/** Wildcard-aware matching: `*?[` switch to glob semantics, otherwise substring. */
export function matches(name, pattern) {
  if (!pattern) return true;
  if (/[*?[]/.test(pattern)) {
    let out = '^';
    for (let i = 0; i < pattern.length; i += 1) {
      const ch = pattern[i];
      if (ch === '*') out += '.*';
      else if (ch === '?') out += '.';
      else if (ch === '[') {
        const close = pattern.indexOf(']', i + 1);
        if (close === -1) out += '\\[';
        else {
          out += pattern.slice(i, close + 1).replace(/\\/g, '\\\\');
          i = close;
        }
      } else out += ch.replace(/[.+^${}()|\\]/g, '\\$&');
    }
    try {
      return new RegExp(`${out}$`).test(name);
    } catch {
      return false;
    }
  }
  return name.includes(pattern);
}

export function pickRepos(repos, patterns = []) {
  const wanted = [];
  for (const chunk of patterns) {
    for (const part of String(chunk).split(',')) {
      if (part.trim()) wanted.push(part.trim());
    }
  }
  const all = [...repos.values()].sort((a, b) => a.id.localeCompare(b.id));
  if (wanted.length === 0) return all;
  return all.filter((r) => wanted.some((p) => matches(r.id, p) || matches(r.name, p)));
}

const REF_RE = /^[A-Za-z0-9._][A-Za-z0-9._/@{}-]{0,299}$/;

/** Reject anything that could be read as a git option or escape the repository. */
export function assertRef(ref) {
  const value = String(ref || '');
  if (!value) throw new Error('ref must not be empty');
  if (value.startsWith('-')) throw new Error(`refusing a ref that starts with "-": ${value}`);
  if (!REF_RE.test(value)) throw new Error(`unexpected characters in ref: ${value}`);
  if (value.includes('..')) throw new Error(`refusing a ref containing "..": ${value}`);
  return value;
}

export function assertRelPath(p) {
  const value = String(p ?? '').replace(/^\/+/, '');
  if (!value) throw new Error('path must not be empty');
  if (value.includes('\0')) throw new Error('path must not contain NUL');
  const segments = value.split('/');
  if (segments.some((s) => s === '..' || s === '' || s === '.')) {
    throw new Error(`refusing a path with empty or ".." segments: ${value}`);
  }
  return value;
}

export async function listRefs(gitDir, { allRefs = false, timeoutMs } = {}) {
  const prefix = allRefs ? 'refs/' : 'refs/heads/';
  const res = await run('git', [
    ...GIT_BASE, `--git-dir=${gitDir}`, 'for-each-ref', '--format=%(refname:short)', prefix,
  ], { timeoutMs });
  return res.out.split('\n').map((l) => l.trim()).filter(Boolean);
}

export async function defaultBranch(gitDir, { timeoutMs } = {}) {
  const res = await run('git', [
    ...GIT_BASE, `--git-dir=${gitDir}`, 'symbolic-ref', '--short', 'HEAD',
  ], { timeoutMs });
  return res.out.trim() || 'HEAD';
}

/**
 * Read one blob out of one mirrored repository.
 * @returns {Promise<{ok:boolean, ref?:string, body?:string, truncated?:boolean, error?:string}>}
 */
export async function readBlob(config, repo, { filePath, ref, timeoutMs }) {
  const target = assertRef(ref || await defaultBranch(repo.dir, { timeoutMs }));
  const rel = assertRelPath(filePath);
  // --end-of-options: without it a ref such as --output=/tmp/x is parsed as a
  // git option, which turned this tool into an arbitrary-file-write primitive.
  const res = await run('git', [
    ...GIT_BASE, `--git-dir=${repo.dir}`, 'show', '--end-of-options', `${target}:${rel}`,
  ], { timeoutMs: timeoutMs ?? 60000 });
  if (res.code !== 0) {
    return { ok: false, ref: target, error: (res.err || '').trim() || `git show exited ${res.code}` };
  }
  let body = res.out;
  let truncated = false;
  if (Buffer.byteLength(body, 'utf8') > config.maxReadBytes) {
    const { truncateUtf8 } = await import('./limits.mjs');
    body = truncateUtf8(body, config.maxReadBytes).toString('utf8');
    truncated = true;
  }
  return { ok: true, ref: target, body, truncated };
}

export function readLastSync(config) {
  try {
    return fs.readFileSync(path.join(config.mirrorDir, LAST_SYNC_FILE), 'utf8').trim();
  } catch {
    return null;
  }
}

export function writeLastSync(config, value = new Date().toISOString()) {
  fs.mkdirSync(path.join(config.mirrorDir, STATE_DIR), { recursive: true });
  fs.writeFileSync(path.join(config.mirrorDir, LAST_SYNC_FILE), `${value}\n`);
  return value;
}

export function mirrorInfo(config, repos) {
  return {
    mirrorDir: config.mirrorDir,
    lastSync: readLastSync(config),
    repoCount: repos.size,
  };
}
