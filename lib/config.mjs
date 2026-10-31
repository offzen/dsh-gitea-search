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

function readConfig(explicitPath, projectDir) {
  const candidates = [];
  if (explicitPath) candidates.push([explicitPath, 'explicit-config']);
  else if (process.env.GITEA_SEARCH_CONFIG) candidates.push([process.env.GITEA_SEARCH_CONFIG, 'env-config']);
  candidates.push([path.join(os.homedir(), '.config', 'dsh-gitea-search', 'config.json'), 'user-config']);
  if (projectDir) candidates.push([path.join(projectDir, 'config.json'), 'project-config']);
  for (const [candidate, source] of candidates) {
    const full = path.resolve(candidate.replace(/^~(?=$|[\\/])/, os.homedir()));
    if (!fs.existsSync(full)) continue;
    try {
      return { data: JSON.parse(fs.readFileSync(full, 'utf8').replace(/^\uFEFF/, '')), path: full, source };
    } catch (error) {
      throw new Error(`invalid config file ${full}: ${error.message}`);
    }
  }
  return { data: {}, path: null, source: null };
}

function splitOwners(raw) {
  const values = Array.isArray(raw) ? raw : String(raw || '').split(',');
  return values.map((value) => String(value).trim()).filter(Boolean);
}

export function resolveConfig({ mirrorRoot, owners, configPath, projectDir } = {}) {
  const loaded = readConfig(configPath, projectDir);
  const fileRoot = loaded.data.storage?.mirror_root || loaded.data.mirror_root;
  const fileOwners = loaded.data.gitea?.owners || loaded.data.owners;
  const envRoot = process.env.GITEA_MIRROR_ROOT || process.env.GITEA_MIRROR_PATH || process.env.GITQ_MIRROR_ROOT;
  const envOwners = process.env.GITEA_OWNERS || process.env.GITEA_OWNER || process.env.GITQ_OWNER;

  let rootSource;
  let resolvedRoot;
  if (mirrorRoot) [resolvedRoot, rootSource] = [mirrorRoot, 'cli'];
  else if (envRoot) [resolvedRoot, rootSource] = [envRoot, 'env'];
  else if (fileRoot) [resolvedRoot, rootSource] = [fileRoot, loaded.source];
  else [resolvedRoot, rootSource] = [platformDefaultRoot(), 'platform-default'];

  let ownersSource;
  let resolvedOwners;
  if (owners) [resolvedOwners, ownersSource] = [splitOwners(owners), 'cli'];
  else if (envOwners) [resolvedOwners, ownersSource] = [splitOwners(envOwners), 'env'];
  else if (fileOwners) [resolvedOwners, ownersSource] = [splitOwners(fileOwners), loaded.source];
  else [resolvedOwners, ownersSource] = [[], 'missing'];

  const errors = [];
  if (!resolvedOwners.length) errors.push('owners are not configured');
  else if (resolvedOwners.some((owner) => PLACEHOLDER_OWNERS.has(owner.toLowerCase()))) errors.push('owners contain a placeholder value');

  return {
    mirror_root: path.resolve(String(resolvedRoot).replace(/^~(?=$|[\\/])/, os.homedir())),
    owners: resolvedOwners,
    sources: { mirror_root: rootSource, owners: ownersSource },
    config_path: loaded.path,
    valid: errors.length === 0,
    errors,
  };
}
