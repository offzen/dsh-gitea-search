import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const PLACEHOLDER_OWNERS = new Set(['myorg', 'example-org', 'your-org', 'your-owner']);
const SUPPORTED_TOP_LEVEL_KEYS = new Set(['gitea', 'storage', 'mirror_root', 'owners']);
const SUPPORTED_SECTION_KEYS = { gitea: new Set(['owners']), storage: new Set(['mirror_root']) };
const WARNED_LEGACY_ENV = new Set();
const WARNED_MIGRATIONS = new Set();

function warnOnce(store, key, message) {
  if (store.has(key)) return;
  store.add(key);
  process.stderr.write(`[gitea-search-mcp] ⚠️ ${message}\n`);
}

function safeConfigKey(keyPath) {
  return keyPath && /^[A-Za-z0-9_.-]+$/.test(keyPath) ? keyPath : '<invalid-key-name>';
}

function configWarnings(data) {
  if (data === null || typeof data !== 'object' || Array.isArray(data)) return [];
  const paths = [];
  for (const [key, value] of Object.entries(data)) {
    if (!SUPPORTED_TOP_LEVEL_KEYS.has(key)) {
      paths.push(key);
      continue;
    }
    if (SUPPORTED_SECTION_KEYS[key] && value !== null && typeof value === 'object' && !Array.isArray(value)) {
      for (const child of Object.keys(value)) {
        if (!SUPPORTED_SECTION_KEYS[key].has(child)) paths.push(`${key}.${child}`);
      }
    }
  }
  return paths.sort().map((keyPath) => `config: key "${safeConfigKey(keyPath)}" is not supported and is ignored`);
}

function platformDataBase() {
  if (process.platform === 'win32') {
    return process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local');
  }
  if (process.platform === 'darwin') {
    return path.join(os.homedir(), 'Library', 'Application Support');
  }
  return process.env.XDG_DATA_HOME || path.join(os.homedir(), '.local', 'share');
}

function platformDefaultRoot() {
  const base = platformDataBase();
  const preferred = path.join(base, 'gitea-search-mcp', 'mirror');
  const legacy = path.join(base, 'dsh-gitea-search', 'mirror');
  if (!fs.existsSync(preferred) && fs.existsSync(legacy)) {
    const notice = `config: using legacy default mirror directory ${legacy}; migrate to ${preferred}`;
    warnOnce(WARNED_MIGRATIONS, `mirror:${legacy}`, notice);
    return { root: legacy, warning: notice };
  }
  return { root: preferred, warning: null };
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
  const preferredUserConfig = path.join(os.homedir(), '.config', 'gitea-search-mcp', 'config.json');
  const legacyUserConfig = path.join(os.homedir(), '.config', 'dsh-gitea-search', 'config.json');
  const candidates = [];
  if (explicitPath) candidates.push([explicitPath, 'explicit-config', false]);
  else if (process.env.GITEA_SEARCH_CONFIG) candidates.push([process.env.GITEA_SEARCH_CONFIG, 'env-config', false]);
  candidates.push([preferredUserConfig, 'user-config', false]);
  candidates.push([legacyUserConfig, 'user-config', true]);
  if (projectDir) candidates.push([path.join(projectDir, 'config.json'), 'project-config', false]);
  for (const [candidate, source, isLegacyUser] of candidates) {
    const full = path.resolve(String(candidate).replace(/^~(?=$|[\\/])/, os.homedir()));
    const explicit = source === 'explicit-config' || source === 'env-config';
    let isFile = false;
    let exists = false;
    try { isFile = fs.statSync(full).isFile(); exists = true; } catch { /* missing or unreadable: treated as not existing */ }
    if (!isFile) {
      if (explicit) {
        const origin = source === 'explicit-config' ? '--config' : 'GITEA_SEARCH_CONFIG';
        const kind = exists ? 'is not a regular file' : 'does not exist';
        return { data: {}, path: null, source, errors: [`${origin} points to a config file that ${kind}: ${full} (an explicitly specified config never falls back to user/project config)`], migrationWarning: null };
      }
      continue;
    }
    let data;
    try {
      data = JSON.parse(fs.readFileSync(full, 'utf8').replace(/^\uFEFF/, ''));
    } catch (error) {
      return { data: {}, path: full, source, errors: [`invalid config file ${full}: ${error.message}`], migrationWarning: null };
    }
    const errors = validateConfig(data, full);
    if (errors.length) return { data: {}, path: full, source, errors, migrationWarning: null };
    let migrationWarning = null;
    if (isLegacyUser) {
      migrationWarning = `config: using legacy user config path ${full}; migrate to ${path.resolve(preferredUserConfig)}`;
      warnOnce(WARNED_MIGRATIONS, `user-config:${full}`, migrationWarning);
    }
    return { data, path: full, source, errors: [], migrationWarning };
  }
  return { data: {}, path: null, source: null, errors: [], migrationWarning: null };
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
  const primaryEnvRoot = process.env.GITEA_MIRROR_ROOT || process.env.GITEA_MIRROR_PATH;
  const legacyEnvRoot = !primaryEnvRoot ? process.env.GITQ_MIRROR_ROOT : undefined;
  if (legacyEnvRoot) {
    warnOnce(WARNED_LEGACY_ENV, 'GITQ_MIRROR_ROOT', '环境变量 GITQ_MIRROR_ROOT 已废弃，请改用 GITEA_MIRROR_ROOT。');
  }
  const envRoot = primaryEnvRoot || legacyEnvRoot;

  const primaryEnvOwners = process.env.GITEA_OWNERS || process.env.GITEA_OWNER;
  const legacyEnvOwners = !primaryEnvOwners ? process.env.GITQ_OWNER : undefined;
  if (legacyEnvOwners) {
    warnOnce(WARNED_LEGACY_ENV, 'GITQ_OWNER', '环境变量 GITQ_OWNER 已废弃，请改用 GITEA_OWNERS。');
  }
  const envOwners = primaryEnvOwners || legacyEnvOwners;

  const warnings = [...configWarnings(loaded.data)];
  if (loaded.migrationWarning) warnings.push(loaded.migrationWarning);

  let rootSource;
  let resolvedRoot;
  if (mirrorRoot) [resolvedRoot, rootSource] = [mirrorRoot, 'cli'];
  else if (envRoot) [resolvedRoot, rootSource] = [envRoot, 'env'];
  else if (fileRoot) [resolvedRoot, rootSource] = [fileRoot, loaded.source];
  else {
    const fallback = platformDefaultRoot();
    [resolvedRoot, rootSource] = [fallback.root, 'platform-default'];
    if (fallback.warning) warnings.push(fallback.warning);
  }

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
    warnings,
  };
}
