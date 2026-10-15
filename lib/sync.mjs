/**
 * Mirror synchronisation.
 *
 * Enumerates the repositories a token can read through the Gitea/Forgejo HTTP
 * API and keeps a bare mirror of each under the mirror directory, so that
 * searching never touches the network.
 *
 * Credentials never reach a command line: an `GIT_ASKPASS` helper receives the
 * token through the environment of the git process only, which keeps it out of
 * `ps`, out of shell history and out of any log line (git output is redacted
 * before it is printed).
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { run, redact } from './git.mjs';
import { statePath } from './config.mjs';
import { readLastSync, writeLastSync } from './mirror.mjs';

export { readLastSync };

function apiUrl(config, suffix) {
  return `${config.baseUrl}${suffix}`;
}

async function apiGet(config, suffix) {
  const headers = { accept: 'application/json' };
  if (config.token) headers.authorization = `token ${config.token}`;
  const res = await fetch(apiUrl(config, suffix), { headers, signal: AbortSignal.timeout(30000) });
  if (!res.ok) {
    const body = (await res.text()).slice(0, 200);
    throw new Error(`GET ${suffix} -> HTTP ${res.status}${body ? `: ${redact(body)}` : ''}`);
  }
  return res.json();
}

async function paginate(config, build) {
  const out = [];
  for (let page = 1; page <= 200; page += 1) {
    const data = await apiGet(config, build(page));
    const batch = Array.isArray(data) ? data : data.data;
    if (!Array.isArray(batch) || batch.length === 0) break;
    out.push(...batch);
    if (batch.length < 50) break;
  }
  return out;
}

/** List every repository the credentials can read (or the configured owners only). */
export async function listRemoteRepos(config) {
  if (!config.baseUrl) {
    throw new Error('GITEA_BASE_URL is required to sync (for example https://gitea.example.com)');
  }
  if (config.owners.length > 0) {
    const collected = new Map();
    for (const owner of config.owners) {
      let repos;
      try {
        repos = await paginate(config, (page) => `/api/v1/users/${encodeURIComponent(owner)}/repos?limit=50&page=${page}`);
      } catch {
        repos = await paginate(config, (page) => `/api/v1/orgs/${encodeURIComponent(owner)}/repos?limit=50&page=${page}`);
      }
      for (const repo of repos) collected.set(repo.full_name, repo);
    }
    return [...collected.values()];
  }
  const repos = await paginate(config, (page) => `/api/v1/repos/search?limit=50&page=${page}`);
  return repos;
}

function askpassScript() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gitea-search-askpass-'));
  const file = path.join(dir, 'askpass.sh');
  fs.writeFileSync(file, [
    '#!/bin/sh',
    'case "$1" in',
    '  *sername*) printf "%s\\n" "${GITEA_ASKPASS_USER:-oauth2}" ;;',
    '  *) printf "%s\\n" "$GITEA_ASKPASS_TOKEN" ;;',
    'esac',
    '',
  ].join('\n'), { mode: 0o700 });
  return { file, dir };
}

function remoteUrl(config, repo, transport) {
  if (transport === 'ssh') return repo.ssh_url;
  return `${config.baseUrl}/${repo.full_name}.git`;
}

async function syncOne(config, repo, { transport, askpass }) {
  const owner = repo.owner?.login || repo.full_name.split('/')[0];
  const name = repo.name || repo.full_name.split('/').pop();
  const dir = path.join(config.mirrorDir, owner, `${name}.git`);
  const env = {
    GIT_TERMINAL_PROMPT: '0',
    GIT_SSH_COMMAND: 'ssh -o BatchMode=yes -o ConnectTimeout=10',
  };
  if (askpass && config.token) {
    env.GIT_ASKPASS = askpass.file;
    env.GITEA_ASKPASS_TOKEN = config.token;
  }
  const exists = fs.existsSync(path.join(dir, 'HEAD'));
  if (!exists) {
    fs.mkdirSync(path.dirname(dir), { recursive: true });
    const res = await run('git', ['clone', '--mirror', '--quiet', remoteUrl(config, repo, transport), dir], {
      env,
      timeoutMs: Math.max(config.timeoutMs, 600000),
    });
    if (res.code !== 0) throw new Error(res.err.trim() || `git clone exited ${res.code}`);
    return { repo: repo.full_name, dir, created: true };
  }
  const res = await run('git', [
    `--git-dir=${dir}`, 'remote', 'update', '--prune',
  ], { env, timeoutMs: Math.max(config.timeoutMs, 600000) });
  if (res.code !== 0) throw new Error(res.err.trim() || `git remote update exited ${res.code}`);
  return { repo: repo.full_name, dir, created: false };
}

/**
 * @param {object} config
 * @param {{transport?:'https'|'ssh', jobs?:number, log?:(line:string)=>void}} options
 */
export async function syncMirror(config, options = {}) {
  const log = options.log || (() => {});
  const transport = options.transport || 'https';
  const jobs = Math.max(1, Math.min(options.jobs || config.syncJobs, 32));
  const repos = await listRemoteRepos(config);
  if (repos.length === 0) throw new Error('the API returned no repositories (check GITEA_BASE_URL, GITEA_TOKEN and its scopes)');
  log(`enumerated ${repos.length} repositories`);

  fs.mkdirSync(config.mirrorDir, { recursive: true });
  fs.writeFileSync(
    statePath(config, 'repos.json'),
    `${JSON.stringify(repos.map((r) => r.full_name).sort(), null, 1)}\n`,
  );

  const askpass = config.token ? askpassScript() : null;
  const ok = [];
  const failed = [];
  let next = 0;
  const workers = Array.from({ length: Math.min(jobs, repos.length) }, async () => {
    for (;;) {
      const index = next;
      next += 1;
      if (index >= repos.length) return;
      const repo = repos[index];
      try {
        const result = await syncOne(config, repo, { transport, askpass });
        ok.push(result);
        log(`${result.created ? 'clone' : 'update'} ok ${result.repo}`);
      } catch (error) {
        failed.push({ repo: repo.full_name, error: redact(String(error.message || error)) });
        log(`FAIL ${repo.full_name}: ${redact(String(error.message || error))}`);
      }
    }
  });
  await Promise.all(workers);
  if (askpass) {
    try { fs.rmSync(askpass.dir, { recursive: true, force: true }); } catch { /* best effort */ }
  }

  if (failed.length === 0) writeLastSync(config);
  return { total: repos.length, ok: ok.length, failed, lastSync: readLastSync(config) };
}
