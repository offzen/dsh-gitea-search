import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const PLACEHOLDER_OWNERS = new Set(['myorg', 'example-org', 'your-org', 'your-owner']);

function platformDefaultRoot() {
  if (process.platform === 'win32') {
    return path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'), 'dsh-gitea-search', 'mirror');
  }
  if (process.platform === 'darwin') {
    return path.join(os.homedir(), 'Library', 'Application Support', 'dsh-gitea-search', 'mirror');
  }
  return path.join(process.env.XDG_DATA_HOME || path.join(os.homedir(), '.local', 'share'), 'dsh-gitea-search', 'mirror');
}

function jsonType(value) {
  if (value === null || value === undefined) return 'null';
  if (Array.isArray(value)) return 'array';
  return typeof value === 'object' ? 'object' : typeof value;
}

/**
 * Structural validation of a parsed config file; returns a list of error strings.
 * Messages are kept byte-identical with lib/config.py (_validate_config).
 */
function validateConfig(data, full) {
  const prefix = `invalid config file ${full}: `;
  if (data === null || typeof data !== 'object' || Array.isArray(data)) {
    return [`${prefix}top-level value must be a JSON object, got ${jsonType(data)}`];
  }
  const errors = [];
  const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
  for (const section of ['storage', 'gitea']) {
    const value = data[section];
    if (value !== undefined && value !== null && !isObj(value)) errors.push(`${prefix}"${section}" must be an object, got ${jsonType(value)}`);
  }
  const storage = isObj(data.storage) ? data.storage : {};
  const gitea = isObj(data.gitea) ? data.gitea : {};
  for (const [label, value] of [['storage.mirror_root', storage.mirror_root], ['mirror_root', data.mirror_root]]) {
    if (value !== undefined && value !== null && typeof value !== 'string') errors.push(`${prefix}"${label}" must be a string, got ${jsonType(value)}`);
  }
  for (const [label, value] of [['gitea.owners', gitea.owners], ['owners', data.owners]]) {
    if (value === undefined || value === null || typeof value === 'string') continue;
    if (!Array.isArray(value)) {
      errors.push(`${prefix}"${label}" must be a string or an array of strings, got ${jsonType(value)}`);
      continue;
    }
    const bad = value.findIndex((item) => typeof item !== 'string');
    if (bad !== -1) errors.push(`${prefix}"${label}" items must be strings, item ${bad} is ${jsonType(value[bad])}`);
  }
  return errors;
}

/**
 * Lazily read the effective config file. Never throws: failures come back in `errors`.
 *
 * Contract (single source of truth, mirrored in lib/config.py):
 *   - an explicitly requested file (explicitPath / GITEA_SEARCH_CONFIG) that is missing or not a
 *     regular file is an error and NEVER falls back to the user/project config;
 *   - fallback to user/project config only happens when nothing was requested explicitly;
 *   - unreadable / non-JSON / wrongly-typed content is reported in `errors`.
 */
function readConfig(explicitPath, projectDir) {
  const candidates = [];
  if (explicitPath) candidates.push([explicitPath, 'explicit-config']);
  else if (process.env.GITEA_SEARCH_CONFIG) candidates.push([process.env.GITEA_SEARCH_CONFIG, 'env-config']);
  candidates.push([path.join(os.homedir(), '.config', 'dsh-gitea-search', 'config.json'), 'user-config']);
  if (projectDir) candidates.push([path.join(projectDir, 'config.json'), 'project-config']);
  for (const [candidate, source] of candidates) {
    const full = path.resolve(String(candidate).replace(/^~(?=$|[\\/])/, os.homedir()));
    const explicit = source === 'explicit-config' || source === 'env-config';
    let isFile = false;
    let exists = false;
    try { isFile = fs.statSync(full).isFile(); exists = true; } catch { /* missing or unreadable: treated as not existing */ }
    if (!isFile) {
      if (explicit) {
        const origin = source === 'explicit-config' ? '--config' : 'GITEA_SEARCH_CONFIG';
        const kind = exists ? 'is not a regular file' : 'does not exist';
        return { data: {}, path: null, source, errors: [`${origin} points to a config file that ${kind}: ${full} (an explicitly specified config never falls back to user/project config)`] };
      }
      continue;
    }
    let data;
    try {
      data = JSON.parse(fs.readFileSync(full, 'utf8').replace(/^\uFEFF/, ''));
    } catch (error) {
      return { data: {}, path: full, source, errors: [`invalid config file ${full}: ${error.message}`] };
    }
    const errors = validateConfig(data, full);
    if (errors.length) return { data: {}, path: full, source, errors };
    return { data, path: full, source, errors: [] };
  }
  return { data: {}, path: null, source: null, errors: [] };
}

function splitOwners(raw) {
  const values = Array.isArray(raw) ? raw : String(raw || '').split(',');
  return values.map((value) => String(value).trim()).filter(Boolean);
}

/**
 * First layer whose *normalized* list is non-empty wins.
 * Single cross-language contract: an empty array / empty string / " , " is an empty set,
 * equivalent to "not set", so the next layer is consulted (lib/config.py does the same).
 */
function pickOwners(layers) {
  for (const [raw, source] of layers) {
    const names = splitOwners(raw);
    if (names.length) return [names, source];
  }
  return [[], 'missing'];
}

export function resolveConfig({ mirrorRoot, owners, configPath, projectDir } = {}) {
  const loaded = readConfig(configPath, projectDir);
  const fileRoot = loaded.data.storage?.mirror_root || loaded.data.mirror_root;
  const envRoot = process.env.GITEA_MIRROR_ROOT || process.env.GITEA_MIRROR_PATH || process.env.GITQ_MIRROR_ROOT;
  const envOwners = process.env.GITEA_OWNERS || process.env.GITEA_OWNER || process.env.GITQ_OWNER;

  let rootSource;
  let resolvedRoot;
  if (mirrorRoot) [resolvedRoot, rootSource] = [mirrorRoot, 'cli'];
  else if (envRoot) [resolvedRoot, rootSource] = [envRoot, 'env'];
  else if (fileRoot) [resolvedRoot, rootSource] = [fileRoot, loaded.source];
  else [resolvedRoot, rootSource] = [platformDefaultRoot(), 'platform-default'];

  const [resolvedOwners, ownersSource] = pickOwners([
    [owners, 'cli'],
    [envOwners, 'env'],
    [loaded.data.gitea?.owners, loaded.source],
    [loaded.data.owners, loaded.source],
  ]);

  const errors = [...loaded.errors];
  if (!resolvedOwners.length) {
    if (!errors.length) errors.push('owners are not configured');
  } else if (resolvedOwners.some((owner) => PLACEHOLDER_OWNERS.has(owner.toLowerCase()))) errors.push('owners contain a placeholder value');

  return {
    mirror_root: path.resolve(String(resolvedRoot).replace(/^~(?=$|[\\/])/, os.homedir())),
    owners: resolvedOwners,
    sources: { mirror_root: rootSource, owners: ownersSource },
    config_path: loaded.path,
    valid: errors.length === 0,
    errors,
  };
}
