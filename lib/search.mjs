/**
 * The search engine.
 *
 * Every repository in the mirror is scanned in parallel; within one repository
 * every local branch is scanned separately, so a hit is always reported as
 * `repo@ref:path:line`. Nothing here contacts the network: the mirror is the
 * whole search surface, which is what makes a self-hosted Gitea/Forgejo without
 * a server-side code index searchable at all.
 */
import { GIT_BASE, runLines } from './git.mjs';
import { defaultBranch, listRefs, matches } from './mirror.mjs';

async function pool(items, limit, worker) {
  const results = new Array(items.length);
  let next = 0;
  const runners = Array.from({ length: Math.max(1, Math.min(limit, items.length)) }, async () => {
    for (;;) {
      const index = next;
      next += 1;
      if (index >= items.length) return;
      results[index] = await worker(items[index], index);
    }
  });
  await Promise.all(runners);
  return results;
}

function compile(pattern, caseSensitive) {
  try {
    return new RegExp(pattern, caseSensitive ? '' : 'i');
  } catch (error) {
    throw new Error(`invalid regular expression: ${error.message}`);
  }
}

function splitHit(line, ref) {
  const prefix = `${ref}:`;
  const body = line.startsWith(prefix) ? line.slice(prefix.length) : line;
  const first = body.indexOf(':');
  if (first === -1) return { path: body, line: '', text: '' };
  const rest = body.slice(first + 1);
  const second = rest.indexOf(':');
  if (second === -1) return { path: body.slice(0, first), line: '', text: rest };
  return { path: body.slice(0, first), line: rest.slice(0, second), text: rest.slice(second + 1) };
}

async function refsFor(repo, options) {
  let refs = await listRefs(repo.dir, { allRefs: options.allRefs, timeoutMs: options.timeoutMs });
  if (options.branch) {
    const wanted = String(options.branch).split(',').map((s) => s.trim()).filter(Boolean);
    if (wanted.some((w) => w.toUpperCase() === 'HEAD')) {
      const head = await defaultBranch(repo.dir, { timeoutMs: options.timeoutMs });
      for (let i = wanted.length - 1; i >= 0; i -= 1) if (wanted[i].toUpperCase() === 'HEAD') wanted.splice(i, 1);
      if (head) wanted.push(head);
    }
    refs = refs.filter((ref) => wanted.some((w) => matches(ref, w)));
  }
  return refs;
}

async function searchRepo(repo, options) {
  const records = [];
  let capped = false;
  const refs = await refsFor(repo, options);
  if (refs.length === 0) return { records, capped };

  const perCap = options.filesOnly ? 5000 : Math.max(500, options.max * 5);
  const { pattern, mode, timeoutMs } = options;

  for (const ref of refs) {
    if (mode === 'content') {
      const args = [...GIT_BASE, `--git-dir=${repo.dir}`, 'grep', '-n', '-I', '--no-color'];
      if (!options.caseSensitive) args.push('-i');
      args.push(options.regex ? '-E' : '-F');
      if (options.word) args.push('-w');
      if (options.count) {
        // Exact count: read everything, keep nothing, so the ranking stays true.
        const res = await runLines('git', [...args, '-e', pattern, ref], { cap: 500000, timeoutMs });
        capped = capped || res.capped;
        records.push({ repo: repo.id, ref, path: '', line: '', text: String(res.lines.length) });
        continue;
      }
      if (options.filesOnly) args.push('-l');
      const res = await runLines('git', [...args, '-e', pattern, ref], { cap: perCap, timeoutMs });
      capped = capped || res.capped;
      for (const line of res.lines) {
        const hit = splitHit(line, ref);
        records.push({ repo: repo.id, ref, path: hit.path, line: hit.line, text: hit.text });
      }
      continue;
    }

    if (mode === 'path') {
      const res = await runLines('git', [
        ...GIT_BASE, `--git-dir=${repo.dir}`, 'ls-tree', '-r', '--name-only', ref,
      ], { cap: 200000, timeoutMs });
      const re = options.regex ? compile(pattern, options.caseSensitive) : null;
      const needle = options.caseSensitive ? pattern : pattern.toLowerCase();
      const hits = res.lines.filter((l) => (re
        ? re.test(l)
        : (options.caseSensitive ? l.includes(needle) : l.toLowerCase().includes(needle))));
      for (const hit of hits.slice(0, perCap)) records.push({ repo: repo.id, ref, path: hit });
      if (hits.length > perCap) capped = true;
      continue;
    }

    const res = await runLines('git', [
      ...GIT_BASE, `--git-dir=${repo.dir}`, 'log', '--all', '--no-color', '--date=short',
      '--format=%h%x09%ad%x09%s',
    ], { cap: 200000, timeoutMs });
    const re = compile(pattern, options.caseSensitive);
    const needle = options.caseSensitive ? pattern : pattern.toLowerCase();
    for (const line of res.lines) {
      const parts = line.split('\t');
      if (parts.length < 3) continue;
      const [commit, date, subject] = [parts[0], parts[1], parts.slice(2).join('\t')];
      const target = options.regex ? subject : `${date} ${subject}`;
      const hit = options.regex
        ? re.test(subject)
        : (options.caseSensitive ? target.includes(needle) : target.toLowerCase().includes(needle));
      if (!hit) continue;
      records.push({ repo: repo.id, ref: '-', commit, date, subject });
      if (records.length >= perCap) {
        capped = true;
        break;
      }
    }
  }
  return { records, capped };
}

/**
 * @param {object} config
 * @param {Array<{id:string,dir:string}>} repos
 * @param {object} options pattern, mode, regex, caseSensitive, word, filesOnly, count, max, jobs, branch, allRefs
 */
export async function search(config, repos, options) {
  const started = Date.now();
  const normalized = {
    pattern: String(options.pattern ?? ''),
    mode: options.mode || 'content',
    regex: Boolean(options.regex),
    caseSensitive: Boolean(options.caseSensitive),
    word: Boolean(options.word),
    filesOnly: Boolean(options.filesOnly),
    count: Boolean(options.count),
    max: Number(options.max) > 0 ? Math.floor(Number(options.max)) : 200,
    jobs: Number(options.jobs) > 0 ? Math.floor(Number(options.jobs)) : config.jobs,
    branch: options.branch || null,
    allRefs: Boolean(options.allRefs),
    timeoutMs: config.timeoutMs,
  };
  if (!normalized.pattern) throw new Error('a search pattern is required');
  if (normalized.regex && normalized.mode === 'content') {
    // Fail fast rather than once per repository.
    compile(normalized.pattern, normalized.caseSensitive);
  }

  const perRepo = await pool(repos, normalized.jobs, (repo) => searchRepo(repo, normalized));
  const records = [];
  let capped = false;
  for (const result of perRepo) {
    records.push(...result.records);
    capped = capped || result.capped;
  }

  const deduped = [];
  const seen = new Set();
  for (const record of records) {
    const key = JSON.stringify(record);
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(record);
  }
  deduped.sort((a, b) => (
    a.repo.localeCompare(b.repo)
    || String(a.path || '').localeCompare(String(b.path || ''))
    || (Number(a.line) || 0) - (Number(b.line) || 0)
  ));

  const mode = normalized.mode;
  // A count row is one row per repository, so the per-record cap does not apply.
  const limited = normalized.count ? deduped : deduped.slice(0, normalized.max);
  const hitRepos = new Set(deduped.map((r) => r.repo)).size;
  return {
    pattern: normalized.pattern,
    mode,
    options: normalized,
    results: limited,
    total: deduped.length,
    returned: Math.min(deduped.length, normalized.max),
    truncated: deduped.length > normalized.max || capped,
    capped,
    reposScanned: repos.length,
    reposHit: hitRepos,
    mirrorLastSync: null,
    elapsedMs: Date.now() - started,
  };
}

export function formatText(result) {
  const { options } = result;
  const lines = [];
  let total = result.total;
  if (options.count) {
    const per = new Map();
    for (const record of result.results) {
      const hits = Number(record.text) || 0;
      if (hits > 0) per.set(record.repo, (per.get(record.repo) || 0) + hits);
    }
    const ranked = [...per.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
    for (const [repo, hits] of ranked) lines.push(`${String(hits).padStart(6, ' ')}  ${repo}`);
    total = ranked.reduce((n, [, hits]) => n + hits, 0);
    if (ranked.length === 0) lines.push('(no hits)');
  } else if (options.filesOnly) {
    const seen = new Set();
    for (const record of result.results) {
      const key = `${record.repo}@${record.ref}:${record.path}`;
      if (seen.has(key)) continue;
      seen.add(key);
      lines.push(key);
    }
    total = seen.size;
  } else {
    for (const record of result.results) {
      if (result.mode === 'content') lines.push(`${record.repo}@${record.ref}:${record.path}:${record.line}:${record.text}`);
      else if (result.mode === 'path') lines.push(`${record.repo}@${record.ref}:${record.path}`);
      else lines.push(`${record.repo} ${record.commit} ${record.date} ${record.subject}`);
    }
  }

  const unit = options.count ? 'lines' : { content: 'lines', path: 'paths', message: 'commits' }[result.mode];
  const atLeast = result.capped ? 'at least ' : '';
  lines.push('');
  lines.push(
    `-- ${atLeast}${total} ${unit} in ${result.reposHit} repositor${result.reposHit === 1 ? 'y' : 'ies'}`
    + ` (${result.reposScanned} mirrored, ${(result.elapsedMs / 1000).toFixed(1)}s)`,
  );
  if (result.total > result.results.length) {
    lines.push(`-- showing the first ${result.results.length}; narrow with --repo or a tighter pattern`);
  }
  if (result.capped) lines.push('-- at least one repository hit its internal cap; the total is a lower bound');
  return lines.join('\n');
}

export function formatJson(result) {
  return JSON.stringify({
    pattern: result.pattern,
    mode: result.mode,
    total: result.total,
    truncated: result.truncated,
    repos_scanned: result.reposScanned,
    repos_hit: result.reposHit,
    mirror_last_sync: result.mirrorLastSync ?? null,
    elapsed_ms: result.elapsedMs,
    results: result.results,
  }, null, 1);
}
