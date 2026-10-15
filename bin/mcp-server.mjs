#!/usr/bin/env node
/**
 * dsh-gitea-search - MCP stdio server.
 *
 * Exposes three tools over the local mirror:
 *   search_gitea       contents / paths / commit messages, all repositories, all branches
 *   read_gitea_file    one file, by repo + path [+ ref]
 *   list_gitea_repos   enumerate mirrored repositories
 *
 * stdout carries JSON-RPC only; every log line goes to stderr.
 */
import fs from 'node:fs';
import path from 'node:path';
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { CallToolRequestSchema, ListToolsRequestSchema } from '@modelcontextprotocol/sdk/types.js';
import { loadConfig } from '../lib/config.mjs';
import { createLimiter } from '../lib/limits.mjs';
import { mirrorInfo, pickRepos, readBlob, scanRepos } from '../lib/mirror.mjs';
import { search } from '../lib/search.mjs';

const config = loadConfig();
const limiter = createLimiter({
  maxLineBytes: config.maxLineBytes,
  maxSearchBytes: config.maxSearchBytes,
});

function log(...parts) {
  process.stderr.write(`[gitea-search] ${parts.join(' ')}\n`);
}

const pkg = JSON.parse(fs.readFileSync(path.join(import.meta.dirname, '..', 'package.json'), 'utf8'));

const TOOLS = [
  {
    name: 'search_gitea',
    description:
      'Search a local mirror of a self-hosted Gitea/Forgejo instance across every repository and every local '
      + 'branch. Use it to find reusable material: scripts, docker-compose files, runbooks, commands, incident '
      + 'notes, configuration fragments. The server-side code index is usually disabled on self-hosted Gitea, so '
      + 'the web UI and API cannot search file contents; this searches the mirror instead. '
      + 'mode=content searches file contents (default, literal, case-insensitive); mode=path searches file paths; '
      + 'mode=message searches commit messages. Only content already pushed to the server is in the mirror.',
    inputSchema: {
      type: 'object',
      properties: {
        query: { type: 'string', description: 'Search term. Literal by default; an extended regular expression when regex=true.' },
        mode: { type: 'string', enum: ['content', 'path', 'message'], description: 'Default content (file contents).' },
        repo: { type: 'string', description: 'Limit to repositories: comma separated, * wildcards, e.g. "infra,docs-*".' },
        branch: { type: 'string', description: 'Limit to branches (default: every branch; HEAD means the default branch).' },
        regex: { type: 'boolean', description: 'Treat query as an extended regular expression.' },
        case_sensitive: { type: 'boolean', description: 'Case sensitive matching (default false).' },
        files_only: { type: 'boolean', description: 'List matching files only, without the matching lines.' },
        max_results: { type: 'integer', description: `Maximum records returned, default 40; hard capped at ${config.maxResults}.` },
      },
      required: ['query'],
      additionalProperties: false,
    },
  },
  {
    name: 'read_gitea_file',
    description:
      'Read one file from the mirror by repository and path (optionally at a ref), so a search hit can be used '
      + `without cloning. The default ref is the repository default branch. Files larger than ${config.maxReadBytes} `
      + 'bytes are truncated; raise GITEA_MAX_READ_BYTES to change that.',
    inputSchema: {
      type: 'object',
      properties: {
        repo: { type: 'string', description: 'Repository id as printed by search_gitea, e.g. "owner/name" or "name".' },
        path: { type: 'string', description: 'Path inside the repository, e.g. "deploy/compose.yml".' },
        ref: { type: 'string', description: 'Branch, tag or commit (default: the repository default branch).' },
      },
      required: ['repo', 'path'],
      additionalProperties: false,
    },
  },
  {
    name: 'list_gitea_repos',
    description: 'List the repositories present in the mirror, optionally filtered by name.',
    inputSchema: {
      type: 'object',
      properties: {
        filter: { type: 'string', description: 'Substring or wildcard, e.g. "infra" or "team/docs-*". Omit to list all.' },
      },
      additionalProperties: false,
    },
  },
];

function requireString(value, name) {
  if (typeof value !== 'string' || value.trim() === '') {
    throw new Error(`${name} must be a non-empty string`);
  }
  return value;
}

function mirrorMissing() {
  const repos = scanRepos(config);
  if (repos.size > 0) return { repos };
  return {
    repos,
    error:
      `no repositories under ${config.mirrorDir}. Build the mirror first: `
      + '`dsh-gitea-search sync` (or `npx -y dsh-gitea-search sync`) with GITEA_BASE_URL and GITEA_TOKEN set.',
  };
}

async function searchGitea(args) {
  const query = requireString(args.query, 'query');
  const { repos, error } = mirrorMissing();
  if (error) return { text: `Error: ${error}`, isError: true };

  const selected = pickRepos(repos, args.repo ? [String(args.repo)] : []);
  if (selected.length === 0) {
    return {
      text: `Error: no repository matches "${args.repo}" (${repos.size} mirrored; use list_gitea_repos).`,
      isError: true,
    };
  }

  const requested = Number(args.max_results) > 0 ? Math.floor(Number(args.max_results)) : 40;
  const max = Math.min(requested, config.maxResults);
  const clamped = requested > config.maxResults ? ` (requested ${requested}, hard capped at ${config.maxResults})` : '';

  const result = await search(config, selected, {
    pattern: query,
    mode: args.mode || 'content',
    regex: Boolean(args.regex),
    caseSensitive: Boolean(args.case_sensitive),
    filesOnly: Boolean(args.files_only),
    branch: args.branch,
    max,
  });
  const info = mirrorInfo(config, repos);

  const lines = [];
  lines.push(
    `${result.total} hit(s) in ${result.reposHit} repositor${result.reposHit === 1 ? 'y' : 'ies'}`
    + ` (scanned ${result.reposScanned} mirrored repositories; mirror last sync: ${info.lastSync || 'never'}`
    + `${result.truncated ? '; result truncated' : ''}${clamped})`,
  );
  if (result.results.length === 0) {
    lines.push(
      'No hits. Try a different term, regex=true for a regular expression, list_gitea_repos to confirm names, '
      + 'or `dsh-gitea-search sync` to refresh the mirror.',
    );
  }
  for (const record of result.results) {
    if (result.mode === 'content') lines.push(`${record.repo}@${record.ref}:${record.path}:${record.line}:${record.text}`);
    else if (result.mode === 'path') lines.push(`${record.repo}@${record.ref}:${record.path}`);
    else lines.push(`${record.repo} ${record.commit} ${record.date} ${record.subject}`);
  }
  if (result.results.length > 0 && result.mode === 'content') {
    lines.push('');
    lines.push('Next: read_gitea_file(repo, path, ref) to read a matching file.');
  }
  const capped = limiter.capSearchLines(lines);
  return { text: [...capped.lines, ...capped.notes].join('\n') };
}

async function readGiteaFile(args) {
  const repoId = requireString(args.repo, 'repo');
  const filePath = requireString(args.path, 'path');
  const repos = scanRepos(config);
  const repo = repos.get(repoId) || pickRepos(repos, [repoId])[0];
  if (!repo) {
    return {
      text: `Error: "${repoId}" is not in the mirror (${repos.size} repositories; use list_gitea_repos).`,
      isError: true,
    };
  }
  let result;
  try {
    result = await readBlob(config, repo, { filePath, ref: args.ref });
  } catch (error) {
    return { text: `Error: ${String(error.message || error)}`, isError: true };
  }
  if (!result.ok) {
    return {
      text: `Cannot read ${repo.id}:${filePath}\n${result.error.slice(0, 400)}\n`
        + '(Paths are case sensitive; use search_gitea with mode:"path" to confirm the exact path and ref.)',
      isError: true,
    };
  }
  return {
    text: `# ${repo.id}@${result.ref}:${filePath}\n\n${result.body}`
      + (result.truncated ? `\n\n... (file larger than ${config.maxReadBytes} bytes, truncated)` : ''),
  };
}

async function listGiteaRepos(args) {
  const { repos, error } = mirrorMissing();
  if (error) return { text: `Error: ${error}`, isError: true };
  const selected = args.filter ? pickRepos(repos, [String(args.filter)]) : pickRepos(repos, []);
  const info = mirrorInfo(config, repos);
  const lines = [
    `${selected.length} of ${repos.size} repositories (mirror: ${config.mirrorDir}; last sync: ${info.lastSync || 'never'})`,
    ...selected.map((r) => r.id),
  ];
  const capped = limiter.capSearchLines(lines);
  return { text: [...capped.lines, ...capped.notes].join('\n') };
}

const server = new Server(
  { name: pkg.name, version: pkg.version },
  { capabilities: { tools: {} } },
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: TOOLS }));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args = {} } = request.params || {};
  const wrap = (result) => ({
    content: [{ type: 'text', text: result.text }],
    ...(result.isError ? { isError: true } : {}),
  });
  try {
    if (name === 'search_gitea') return wrap(await searchGitea(args));
    if (name === 'read_gitea_file') return wrap(await readGiteaFile(args));
    if (name === 'list_gitea_repos') return wrap(await listGiteaRepos(args));
    return wrap({ text: `Unknown tool: ${name}`, isError: true });
  } catch (error) {
    log('tool error', name, String((error && error.stack) || error));
    return wrap({ text: `Error: ${String((error && error.message) || error)}`, isError: true });
  }
});

const transport = new StdioServerTransport();
await server.connect(transport);
log(`started; mirror=${config.mirrorDir}; repositories=${scanRepos(config).size}`);
