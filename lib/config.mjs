/**
 * Configuration for dsh-gitea-search.
 *
 * Everything is environment driven on purpose: the plugin is distributed as a
 * configuration-only DSH bundle, whose patch row passes values through
 * `@deepseek-ai/dsh-mcp-client`. That client scrubs the ambient environment, so
 * the bundle patch re-exports the variables explicitly with Loader `!!js`.
 *
 * No value here has a site-specific default: the mirror lives under the user's
 * home directory and the Gitea base URL has no default at all.
 */
import os from 'node:os';
import path from 'node:path';

export const DEFAULTS = {
  mirrorDir: path.join(os.homedir(), '.local', 'share', 'gitea-search', 'mirror'),
  maxReadBytes: 60000,
  maxResults: 200,
  maxSearchBytes: 131072,
  maxLineBytes: 2000,
  jobs: 6,
  syncJobs: 6,
  timeoutMs: 120000,
};

function positiveInt(value, fallback, max = Number.MAX_SAFE_INTEGER) {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return fallback;
  return Math.min(Math.floor(n), max);
}

function list(value) {
  return String(value || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
}

export function loadConfig(env = process.env) {
  const mirrorDir = String(env.GITEA_MIRROR_DIR || '').trim() || DEFAULTS.mirrorDir;
  return {
    mirrorDir: path.resolve(mirrorDir),
    baseUrl: String(env.GITEA_BASE_URL || '').trim().replace(/\/+$/, ''),
    token: String(env.GITEA_TOKEN || '').trim(),
    owners: list(env.GITEA_OWNER),
    maxReadBytes: positiveInt(env.GITEA_MAX_READ_BYTES, DEFAULTS.maxReadBytes),
    maxResults: positiveInt(env.GITEA_MAX_RESULTS, DEFAULTS.maxResults, 1000),
    maxSearchBytes: positiveInt(env.GITEA_MAX_SEARCH_BYTES, DEFAULTS.maxSearchBytes),
    maxLineBytes: positiveInt(env.GITEA_MAX_LINE_BYTES, DEFAULTS.maxLineBytes),
    jobs: positiveInt(env.GITEA_JOBS, DEFAULTS.jobs, 32),
    syncJobs: positiveInt(env.GITEA_SYNC_JOBS, DEFAULTS.syncJobs, 32),
    timeoutMs: positiveInt(env.GITEA_TIMEOUT_MS, DEFAULTS.timeoutMs),
  };
}

/** Files the mirror keeps beside the repositories. */
export const STATE_DIR = 'state';
export const LAST_SYNC_FILE = 'last_sync';

export function statePath(config, ...rest) {
  return path.join(config.mirrorDir, STATE_DIR, ...rest);
}
