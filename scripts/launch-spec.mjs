import path from 'node:path';

export function launchSpec(command, args = []) {
  const ext = path.extname(command).toLowerCase();
  const base = path.basename(command).toLowerCase();
  if (ext === '.js' || ext === '.mjs' || ext === '.cjs') {
    return { command: process.execPath, args: [command, ...args], kind: 'node-script' };
  }
  if (process.platform === 'win32' && !ext && (base === 'mcp-server' || base === 'mcp-server-gitea-search')) {
    return { command: process.execPath, args: [command, ...args], kind: 'node-script' };
  }
  return { command, args, kind: 'executable' };
}
