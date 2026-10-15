#!/usr/bin/env node
/**
 * dsh-gitea-search - command line interface.
 *
 *   search <pattern>   grep across every mirrored repository, every branch
 *   repos              list mirrored repositories
 *   read <repo> <path> print one file from a mirrored repository
 *   sync               refresh the mirror from the Gitea/Forgejo API
 *   mirror             show where the mirror is and when it last synced
 *   mcp                run the MCP stdio server (used by the DSH bundle)
 */
import path from 'node:path';
import { loadConfig } from '../lib/config.mjs';
import { createLimiter } from '../lib/limits.mjs';
import { mirrorInfo, pickRepos, readBlob, scanRepos } from '../lib/mirror.mjs';
import { formatJson, formatText, search } from '../lib/search.mjs';
import { syncMirror } from '../lib/sync.mjs';

const USAGE = `dsh-gitea-search - cross-repository search over a local Gitea/Forgejo mirror

Usage:
  dsh-gitea-search search <pattern> [options]
  dsh-gitea-search repos [options]
  dsh-gitea-search read <repo> <path> [--ref <ref>]
  dsh-gitea-search sync [--transport https|ssh] [--jobs N]
  dsh-gitea-search mirror [--json]
  dsh-gitea-search mcp

search options:
  -p, --path            match file paths instead of file contents
  -m, --msg             match commit messages
  -E, --regex           treat the pattern as an extended regular expression
  -s, --case-sensitive  case sensitive matching
  -w, --word            whole-word match (contents only)
  -l, --files-only      list matching files, without the matching lines
  -c, --count           per-repository hit counts, highest first
  -r, --repo <pattern>  limit to repositories (repeatable, comma separated, * wildcards)
  -b, --branch <name>   limit to branches (comma separated; HEAD means the default branch)
  -A, --all-refs        include tags
  -n, --max <n>         maximum records to print (default 200)
  -j, --jobs <n>        repositories searched in parallel
      --json            machine readable output

Environment:
  GITEA_MIRROR_DIR  where the bare mirrors live (default: ~/.local/share/gitea-search/mirror)
  GITEA_BASE_URL    Gitea/Forgejo base URL, required by sync
  GITEA_TOKEN       read-only API token, required by sync against a private instance
  GITEA_OWNER       comma separated owners to mirror (default: every repository the token reads)
  GITEA_MAX_RESULTS, GITEA_MAX_SEARCH_BYTES, GITEA_MAX_LINE_BYTES, GITEA_MAX_READ_BYTES, GITEA_JOBS
`;

function fail(message, code = 2) {
  process.stderr.write(`dsh-gitea-search: ${message}\n`);
  process.exit(code);
}

function takeValue(argv, index, name) {
  const value = argv[index + 1];
  if (value === undefined || value.startsWith('-')) fail(`${name} needs a value`);
  return value;
}

function parseArgs(argv) {
  const options = { repo: [] };
  const positional = [];
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    switch (arg) {
      case '-p': case '--path': options.mode = 'path'; break;
      case '-m': case '--msg': case '--messages': options.mode = 'message'; break;
      case '-E': case '--regex': options.regex = true; break;
      case '-s': case '--case-sensitive': options.caseSensitive = true; break;
      case '-w': case '--word': options.word = true; break;
      case '-l': case '--files-only': options.filesOnly = true; break;
      case '-c': case '--count': options.count = true; break;
      case '-A': case '--all-refs': options.allRefs = true; break;
      case '--json': options.json = true; break;
      case '--quiet': case '-q': options.quiet = true; break;
      case '-r': case '--repo': options.repo.push(takeValue(argv, i, arg)); i += 1; break;
      case '-b': case '--branch': options.branch = takeValue(argv, i, arg); i += 1; break;
      case '-n': case '--max': options.max = Number(takeValue(argv, i, arg)); i += 1; break;
      case '-j': case '--jobs': options.jobs = Number(takeValue(argv, i, arg)); i += 1; break;
      case '--ref': options.ref = takeValue(argv, i, arg); i += 1; break;
      case '--transport': options.transport = takeValue(argv, i, arg); i += 1; break;
      case '-h': case '--help': options.help = true; break;
      case '-v': case '--version': options.version = true; break;
      default:
        if (arg.startsWith('-')) fail(`unknown option: ${arg}`);
        positional.push(arg);
    }
  }
  return { options, positional };
}

async function commandSearch(config, options, positional) {
  const pattern = positional.join(' ');
  if (!pattern) fail('search needs a pattern');
  const repos = pickRepos(scanRepos(config), options.repo);
  if (repos.length === 0) {
    fail('no repositories selected: check GITEA_MIRROR_DIR and the --repo filter', 1);
  }
  const max = Number(options.max) > 0 ? Math.floor(Number(options.max)) : 200;
  const result = await search(config, repos, {
    pattern,
    mode: options.mode || 'content',
    regex: options.regex,
    caseSensitive: options.caseSensitive,
    word: options.word,
    filesOnly: options.filesOnly,
    count: options.count,
    branch: options.branch,
    allRefs: options.allRefs,
    max,
    jobs: options.jobs,
  });
  result.mirrorLastSync = mirrorInfo(config, new Map()).lastSync;

  if (options.json) {
    process.stdout.write(`${formatJson(result)}\n`);
  } else {
    const limiter = createLimiter({
      maxLineBytes: config.maxLineBytes,
      maxSearchBytes: config.maxSearchBytes,
    });
    const text = formatText(result);
    const { lines, notes } = limiter.capSearchLines(text.split('\n'));
    process.stdout.write(`${[...lines, ...notes].join('\n')}\n`);
  }
  process.exit(result.results.length > 0 ? 0 : 1);
}

async function commandRepos(config, options) {
  const repos = pickRepos(scanRepos(config), options.repo);
  const info = mirrorInfo(config, new Map(repos.map((r) => [r.id, r])));
  if (options.json) {
    process.stdout.write(`${JSON.stringify({
      count: repos.length,
      mirror_dir: info.mirrorDir,
      mirror_last_sync: info.lastSync,
      repos: repos.map((r) => r.id),
    }, null, 1)}\n`);
    return;
  }
  for (const repo of repos) process.stdout.write(`${repo.id}\n`);
  process.stdout.write(`\n${repos.length} repositories; last sync: ${info.lastSync || 'never'}\n`);
}

async function commandRead(config, options, positional) {
  const [repoId, filePath] = positional;
  if (!repoId || !filePath) fail('read needs <repo> <path>');
  const repos = scanRepos(config);
  const repo = repos.get(repoId)
    || pickRepos(repos, [repoId])[0];
  if (!repo) fail(`no such repository in the mirror: ${repoId}`, 1);
  const result = await readBlob(config, repo, { filePath, ref: options.ref });
  if (!result.ok) fail(`cannot read ${repo.id}:${filePath}\n${result.error}`, 1);
  process.stdout.write(`# ${repo.id}@${result.ref}:${filePath}\n\n${result.body}`);
  if (result.truncated) process.stdout.write(`\n\n... (file larger than ${config.maxReadBytes} bytes, truncated)\n`);
}

async function commandSync(config, options) {
  const log = options.quiet ? () => {} : (line) => process.stderr.write(`${line}\n`);
  const result = await syncMirror(config, {
    transport: options.transport,
    jobs: options.jobs,
    log,
  });
  const summary = `synced ${result.ok}/${result.total} repositories`
    + `${result.failed.length ? `, ${result.failed.length} failed` : ''}`
    + `; last sync: ${result.lastSync || 'unchanged'}`;
  process.stdout.write(`${summary}\n`);
  for (const item of result.failed) process.stderr.write(`FAILED ${item.repo}: ${item.error}\n`);
  if (result.failed.length > 0) process.exit(1);
}

async function main() {
  const [command, ...rest] = process.argv.slice(2);
  if (!command || command === 'help' || command === '--help' || command === '-h') {
    process.stdout.write(USAGE);
    return;
  }
  if (command === '--version' || command === '-v') {
    const pkg = JSON.parse(
      (await import('node:fs')).readFileSync(path.join(import.meta.dirname, '..', 'package.json'), 'utf8'),
    );
    process.stdout.write(`${pkg.name} ${pkg.version}\n`);
    return;
  }
  if (command === 'mcp') {
    await import(new URL('./mcp-server.mjs', import.meta.url));
    return;
  }

  const { options, positional } = parseArgs(rest);
  if (options.help) {
    process.stdout.write(USAGE);
    return;
  }
  const config = loadConfig();

  switch (command) {
    case 'search': return commandSearch(config, options, positional);
    case 'repos': return commandRepos(config, options);
    case 'read': return commandRead(config, options, positional);
    case 'sync': return commandSync(config, options);
    case 'mirror': {
      const repos = scanRepos(config);
      const info = mirrorInfo(config, repos);
      if (options.json) {
        process.stdout.write(`${JSON.stringify({
          mirror_dir: info.mirrorDir,
          repo_count: info.repoCount,
          last_sync: info.lastSync,
          base_url: config.baseUrl || null,
        }, null, 1)}\n`);
      } else {
        process.stdout.write(`mirror: ${info.mirrorDir}\nrepositories: ${info.repoCount}\nlast sync: ${info.lastSync || 'never'}\n`);
      }
      return undefined;
    }
    default:
      return fail(`unknown command: ${command}\n\n${USAGE}`);
  }
}

main().catch((error) => {
  fail(String((error && error.message) || error), 2);
});
