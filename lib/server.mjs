#!/usr/bin/env node
/**
 * gitea-search —— 把本地 Gitea 全量镜像（/srv/gitea-mirror）暴露成 MCP 工具。
 *
 * 为什么需要它：Gitea 服务端未开 repo indexer 时，网页/API 无法跨仓搜文件内容；
 * 这里由 sync.sh 把仓库 bare-mirror 到本地，本服务让任意 DSH 会话/子 agent
 * 都能直接搜到并读出可复用内容（脚本、compose、runbook、命令、排障记录、文档与导出会话）。
 *
 * 工具：
 *   search_gitea      内容 / 路径 / 提交信息搜索（跨全部镜像仓、全部本地分支）
 *   search_gitea_docs 文档 / 会话 / 配置 / 脚本 SQLite FTS5 + Jieba 全文检索
 *   read_gitea_file   按 仓+路径[+ref] 读文件全文（搜到之后直接取用）
 *   list_gitea_repos  列出镜像里的仓（可按名字过滤）
 *
 * 依赖：gts CLI（bin/gts 或 /usr/local/bin/gts，兼容别名 gitq / dsh-search）、
 *       /srv/gitea-mirror 里的 bare 镜像、@modelcontextprotocol/sdk（本目录 node_modules）。
 *
 * 注意：stdout 只能出现 JSON-RPC，日志一律走 stderr。
 */
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from '@modelcontextprotocol/sdk/types.js';

const MIRROR_ROOT = process.env.GITEA_MIRROR_ROOT || process.env.GITEA_MIRROR_PATH || process.env.GITQ_MIRROR_ROOT || '/srv/gitea-mirror';
const OWNERS_RAW = process.env.GITEA_OWNERS || process.env.GITEA_OWNER || process.env.GITQ_OWNER || 'myorg';
const OWNERS = OWNERS_RAW.split(',').map((s) => s.trim()).filter(Boolean);
const OWNER = OWNERS[0] || 'myorg';
const MIRROR = path.join(MIRROR_ROOT, OWNER);
const MAX_READ_BYTES_RAW = Number(process.env.GITEA_MAX_READ_BYTES || process.env.GITQ_MAX_READ_BYTES);
const MAX_READ_BYTES = MAX_READ_BYTES_RAW > 0 ? MAX_READ_BYTES_RAW : 60000;
const READ_DEFAULT_LINES_RAW = Number(process.env.GITEA_READ_DEFAULT_LINES || process.env.GITQ_READ_DEFAULT_LINES);
const READ_DEFAULT_LINES = READ_DEFAULT_LINES_RAW > 0 ? READ_DEFAULT_LINES_RAW : 400;
// 命中条数硬夹：max_results 是软约束（模型可传大数），这里硬夹到 GITQ_MAX_RESULTS（默认 200），
// 防止输出面无限放大；配合字节护栏形成双层有界。
const MAX_RESULTS_RAW = Number(process.env.GITEA_MAX_RESULTS || process.env.GITQ_MAX_RESULTS);
const MAX_RESULTS = MAX_RESULTS_RAW > 0 ? Math.min(MAX_RESULTS_RAW, 1000) : 200;
// 输出护栏：单行上限 + 总量上限，防止压缩/打包单行大文件撑爆上下文，均可用 env 覆盖。
const MAX_SEARCH_BYTES_RAW = Number(process.env.GITEA_MAX_SEARCH_BYTES || process.env.GITQ_MAX_SEARCH_BYTES);
const MAX_SEARCH_BYTES = MAX_SEARCH_BYTES_RAW > 0 ? MAX_SEARCH_BYTES_RAW : 131072;
const MAX_LINE_BYTES_RAW = Number(process.env.GITEA_MAX_LINE_BYTES || process.env.GITQ_MAX_LINE_BYTES);
const MAX_LINE_BYTES = MAX_LINE_BYTES_RAW > 0 ? MAX_LINE_BYTES_RAW : 2000;

/** 截断到字节上限，并退掉残缺的 UTF-8 序列（**含孤立首字节**，否则切出半个汉字会漏 U+FFFD）。 */
function truncateUtf8(s, maxBytes) {
  let b = Buffer.from(s).subarray(0, Math.max(0, maxBytes));
  while (b.length > 0 && (b[b.length - 1] & 0xc0) === 0x80) b = b.subarray(0, b.length - 1);
  while (b.length > 0 && (b[b.length - 1] & 0xc0) === 0xc0) b = b.subarray(0, b.length - 1);
  return b;
}

/**
 * search_gitea 的输出护栏：就地截断超长行、丢弃超出总量预算的行。
 * 2026-10-14 加固（与 dsh-search-delivery/gitea-search-guard/cap-search-lines.mjs 对齐）：
 *   · 截断标注**计入单行配额**（此前标注拼在配额外，「单行上限 2000B」实际渲染 2050B）
 *   · 护栏提示行**计入总量预算**并做预留+收尾（此前提示在预算外，上限设得比提示还小时产出照超）
 *   · 截断退**孤立 UTF-8 首字节**（此前只退续位字节，切在汉字中间会漏 U+FFFD）
 * @param {string[]} lines 已拼好的结果行（含首行统计与末尾提示）。
 * @returns {[string[], string[]]} [截断后的行, 护栏提示行（追加到结果尾部）]。
 */
function capSearchLines(lines) {
  const capped = [];
  const notes = [];
  let used = 0;
  let clippedLines = 0;
  let clippedBytes = 0;
  let dropped = 0;
  let budgetDropped = 0;
  for (const raw of lines) {
    let line = raw;
    const rawLen = Buffer.byteLength(line);
    if (rawLen > MAX_LINE_BYTES) {
      // 标注算进配额：先给标注留位置，放不下就换更短标注。
      let tag = `[…截断，原行 ${rawLen}B > 单行上限 ${MAX_LINE_BYTES}B]`;
      if (Buffer.byteLength(tag) >= MAX_LINE_BYTES) tag = `[…${rawLen}B]`;
      const budget = MAX_LINE_BYTES - Buffer.byteLength(tag);
      line = (budget > 0 ? truncateUtf8(line, budget).toString('utf8') : '') + tag;
      clippedLines += 1;
      clippedBytes += rawLen - Buffer.byteLength(line);
    }
    const lineLen = Buffer.byteLength(line);
    if (used + lineLen > MAX_SEARCH_BYTES) {
      dropped += 1;
      continue;
    }
    capped.push(line);
    used += lineLen;
  }
  if (clippedLines > 0) {
    notes.push(`⚠️ 输出护栏：${clippedLines} 行超单行上限 ${MAX_LINE_BYTES}B 已就地截断（省约 ${(clippedBytes / 1024).toFixed(0)}KB）。` +
      `这类行多为压缩/打包产物：改用 files_only=true 探路，再 read_gitea_file 按文件取（自带 60KB 截断）。`);
  }
  if (dropped > 0) {
    notes.push(`⚠️ 输出护栏：总量达 ${MAX_SEARCH_BYTES}B，末尾 ${dropped} 行被丢弃。` +
      `要全部命中请缩 max_results / 限定 repo / 用 files_only=true；护栏上限可经 env GITQ_MAX_SEARCH_BYTES 调大。`);
  }
  // 提示行也必须落在预算内：先按最宽计数预留，再收尾，保证「最终产出 ≤ 预算」是硬承诺。
  const bytes = (arr) => arr.reduce((n, l) => n + Buffer.byteLength(l) + 1, 0);
  const budgetNote = (n) => `⚠️ 输出护栏：为容纳本提示又丢弃 ${n} 行（预算 ${MAX_SEARCH_BYTES}B 过小，建议调大 GITQ_MAX_SEARCH_BYTES）。`;
  while (capped.length > 0 && bytes(capped) + bytes(notes) + Buffer.byteLength(budgetNote(999999)) > MAX_SEARCH_BYTES) {
    capped.pop();
    budgetDropped += 1;
  }
  if (budgetDropped > 0) notes.push(budgetNote(budgetDropped));
  while (capped.length > 0 && bytes([...capped, ...notes]) > MAX_SEARCH_BYTES) capped.pop();
  if (bytes(notes) > MAX_SEARCH_BYTES) {
    notes.length = 0;
    const minimal = `⚠️ 输出护栏：预算 ${MAX_SEARCH_BYTES}B 过小，已省略明细。请调大 GITQ_MAX_SEARCH_BYTES。`;
    notes.push(Buffer.byteLength(minimal) <= MAX_SEARCH_BYTES ? minimal : truncateUtf8(minimal, MAX_SEARCH_BYTES).toString('utf8'));
  }
  return [capped, notes];
}

function log(...a) {
  process.stderr.write('[gitea-search] ' + a.join(' ') + '\n');
}

function expandHome(p) {
  if (!p) return p;
  if (p === '~') return os.homedir();
  if (p.startsWith('~/')) return path.join(os.homedir(), p.slice(2));
  return p;
}

function findSearchCli() {
  const home = os.homedir();
  const cands = [
    expandHome(process.env.GTS_BIN),
    expandHome(process.env.GITQ_BIN),
    path.join(import.meta.dirname || '.', '..', 'bin', 'gts'),
    path.join(import.meta.dirname || '.', '..', 'bin', 'gitq'),
    path.join(import.meta.dirname || '.', 'bin', 'gts'),
    path.join(import.meta.dirname || '.', 'bin', 'gitq'),
    path.join(import.meta.dirname || '.', 'gts'),
    path.join(import.meta.dirname || '.', 'gitq'),
    '/usr/local/bin/gts',
    '/usr/local/bin/dsh-search',
    '/usr/local/bin/gitq',
    path.join(home, 'bin', 'gts'),
    path.join(home, 'bin', 'gitq'),
  ].filter(Boolean);
  for (const c of cands) {
    try {
      fs.accessSync(c, fs.constants.X_OK);
      return c;
    } catch { /* next */ }
  }
  return null;
}

const findGts = findSearchCli;

function run(cmd, args, timeoutMs = 120000) {
  return new Promise((resolve) => {
    const p = spawn(cmd, args, { env: { ...process.env, GITQ_MIRROR_ROOT: MIRROR_ROOT, GITQ_OWNER: OWNERS.join(','), GITEA_OWNERS: OWNERS.join(',') } });
    // 2026-10-15 修：**必须收 Buffer、最后一次性解码**。
    // 旧写法 `out += d`（d 是 Buffer）等于「按块 toString('utf8')」——Node 的 'data' 事件按
    // 写入块触发（git 常按 8KB 写），只要块边界落在一个多字节字符中间，那一块单独解码就产出
    // U+FFFD，且字符永久损坏（后面的块补不回来）。实测：90001B 中文 5/5 次出现 U+FFFD；
    // 同样内容 >8KB 的搜索命中/文件读取都可能中招。收块 + Buffer.concat 只解码一次 ⇒ 0/5。
    const outChunks = [];
    const errChunks = [];
    const decode = (chunks) => Buffer.concat(chunks).toString('utf8');
    let done = false;
    const t = setTimeout(() => { if (!done) { p.kill('SIGKILL'); } }, timeoutMs);
    p.stdout.on('data', (d) => { outChunks.push(d); });
    p.stderr.on('data', (d) => { errChunks.push(d); });
    p.on('error', (e) => {
      done = true; clearTimeout(t);
      resolve({ code: -1, out: decode(outChunks), err: String(e) + decode(errChunks) });
    });
    p.on('close', (code) => {
      done = true; clearTimeout(t);
      resolve({ code, out: decode(outChunks), err: decode(errChunks) });
    });
  });
}

// core.quotePath=false：否则非 ASCII（中文）路径会以 "\345\257\274…" 形式返回，
// 读文件时对不上号。core.pager/color.ui 是防非 tty 下的意外转义。
const GIT = ['git', '-c', 'core.pager=cat', '-c', 'color.ui=false', '-c', 'core.quotePath=false'];

function gitDir(repo) {
  const raw = String(repo || '').trim();
  const clean = raw.includes('/') ? raw.split('/').pop() : raw;
  if (!clean || clean === '.' || clean === '..' || !/^[A-Za-z0-9._-]+$/.test(clean)) return null; // 防路径穿越
  for (const o of OWNERS) {
    const d = path.join(MIRROR_ROOT, o, clean + '.git');
    if (fs.existsSync(d)) return d;
  }
  return null;
}

function mirrorInfo() {
  let last = '未知';
  try { last = fs.readFileSync(path.join(MIRROR_ROOT, 'last_sync'), 'utf8').trim(); } catch { /* ignore */ }
  let n = 0;
  for (const o of OWNERS) {
    try {
      n += fs.readdirSync(path.join(MIRROR_ROOT, o)).filter((e) => e.endsWith('.git')).length;
    } catch { /* ignore */ }
  }
  return { last, count: n };
}

// ── 工具实现 ────────────────────────────────────────────────────────────────

async function searchGitea(a) {
  const gts = findGts();
  if (!gts) return { text: '错误：找不到 gts CLI（设 GTS_BIN / GITQ_BIN 或安装到 /usr/local/bin/gts）。', isError: true };

  const mode = a.mode || 'content';
  // 条数硬夹（MAX_RESULTS）：模型可传大数，但护栏不让输出面无限放大。默认条数 5（先定位再精读）。
  const want = Number(a.max_results) > 0 ? Math.floor(Number(a.max_results)) : 5;
  const n = Math.min(want, MAX_RESULTS);
  const clampedNote = want > MAX_RESULTS ? `（请求 ${want} 条，已硬夹到 ${MAX_RESULTS}）` : '';
  const args = ['--json', '-n', String(n)];
  if (mode === 'path') args.push('-p');
  else if (mode === 'message') args.push('-m');
  if (a.regex) args.push('-E');
  if (a.case_sensitive) args.push('-s');
  if (a.files_only) args.push('-l');
  if (a.repo) args.push('-r', a.repo);
  if (a.branch) args.push('-b', a.branch);
  if (a.sync) args.push('--sync');
  args.push(a.query);

  const { code, out, err } = await run(gts, args);
  let j;
  try { j = JSON.parse(out); } catch {
    return { text: `gts 输出无法解析（rc=${code}）：\n${out.slice(0, 800)}\n${err.slice(0, 400)}`, isError: true };
  }
  const mi = mirrorInfo();
  const rows = j.results || [];
  const lines = [];
  const shown = rows.length;
  const total = Number(j.total) || shown;
  const moreNote = total > shown ? `，还有 ${total - shown} 条未显示（要更多请调大 max_results 或缩小 query）` : '';
  const fbNote = j.match_mode === 'line_and_fallback' ? '；原短语连写 0 命中，已自动回退为同行多词 AND 匹配' : '';
  lines.push(
    `返回 ${shown} 条 / 全库命中 ${total} 条 / ${j.repos_hit} 个仓（扫了 ${j.repos_scanned} 个镜像仓；` +
    `镜像 last_sync: ${j.mirror_last_sync || mi.last}${moreNote}${clampedNote}${fbNote}）`
  );
  if (!rows.length) {
    lines.push(
      '（无命中。可试：① 若查跨行多词、中文文档或历史会话，请改调 search_gitea_docs（FTS5 + jieba 分词，支持多词跨行与会话检索）；' +
      '② 换更短关键词或设 regex:true；③ 用 list_gitea_repos 确认仓名；④ 切勿手写脚本遍历解压 .dsh/sessions/*.zstd）'
    );
  }
  for (const r of rows) {
    if (mode === 'content') lines.push(`${r.repo}@${r.ref}:${r.path}:${r.line ?? ''}:${r.text ?? ''}`);
    else if (mode === 'path') lines.push(`${r.repo}@${r.ref}:${r.path}`);
    else lines.push(`${r.repo} ${r.commit} ${r.date} ${r.subject}`);
  }
  if (rows.length) {
    if (mode === 'content') lines.push('');
    lines.push('提示：用 read_gitea_file(repo, path, ref) 读上面命中的文件全文。');
  }
  const [cappedLines, guardNotes] = capSearchLines(lines);
  return { text: [...cappedLines, ...guardNotes].join('\n') };
}

async function readGiteaFile(a) {
  const d = gitDir(a.repo);
  if (!d) return { text: `错误：镜像里没有仓 "${a.repo}"（用 list_gitea_repos 看有哪些，或先运行 sync.sh 刷新镜像）。`, isError: true };
  const p = String(a.path || '').replace(/^\/+/, '');
  if (!p) return { text: '错误：需要 path。', isError: true };

  let ref = a.ref;
  if (!ref) {
    const r = await run('git', [...GIT.slice(1), `--git-dir=${d}`, 'symbolic-ref', '--short', 'HEAD']);
    ref = (r.out || '').trim() || 'HEAD';
  }
  const spec = `${ref}:${p}`;
  const res = await run('git', [...GIT.slice(1), `--git-dir=${d}`, 'show', spec], 60000);
  if (res.code !== 0) {
    return {
      text: `读取失败：${a.repo}:${spec}\n${(res.err || '').trim().slice(0, 400)}\n` +
            `（路径区分大小写；用 search_gitea(mode:"path") 确认确切路径与 ref。）`,
      isError: true,
    };
  }
  let body = res.out;
  const notes = [];
  const allLines = body.split('\n');
  const totalLines = allLines.length;
  const wantLimit = a.limit === undefined || a.limit === null
    ? READ_DEFAULT_LINES
    : Math.floor(Number(a.limit));
  const limit = Number.isFinite(wantLimit) && wantLimit > 0 ? wantLimit : (wantLimit === 0 ? 0 : READ_DEFAULT_LINES);
  const offset = Math.max(0, Math.floor(Number(a.offset) || 0));
  if (offset > 0 || (limit > 0 && totalLines > limit)) {
    const start = Math.min(offset, Math.max(0, totalLines - 1));
    const end = limit > 0 ? Math.min(start + limit, totalLines) : totalLines;
    body = allLines.slice(start, end).join('\n');
    const shown = end - start;
    if (shown < totalLines) {
      notes.push(`已显示第 ${start + 1}-${end} 行，共 ${totalLines} 行（续读：offset=${end}；取全文：limit=0）`);
    }
  }
  if (Buffer.byteLength(body) > MAX_READ_BYTES) {
    body = truncateUtf8(body, MAX_READ_BYTES).toString('utf8');
    notes.push(`文件超过 ${MAX_READ_BYTES} 字节，已截断`);
  }
  const note = notes.length ? `\n\n…（${notes.join('；')}）` : '';
  return { text: `# ${a.repo}@${ref}:${p}\n\n${body}${note}` };
}

async function listGiteaRepos(a) {
  const gts = findGts();
  if (!gts) return { text: '错误：找不到 gts CLI。', isError: true };
  const args = ['--repos', '--json'];
  if (a.filter) args.push('-r', a.filter);
  const { out, err } = await run(gts, args);
  let j;
  try { j = JSON.parse(out); } catch { return { text: `解析失败：${out.slice(0, 500)}${err.slice(0, 300)}`, isError: true }; }
  const mi = mirrorInfo();
  return {
    text: `${j.count} 个仓（镜像 last_sync: ${j.mirror_last_sync || mi.last}）\n` + (j.repos || []).join('\n'),
  };
}

async function searchGiteaDocs(a) {
  const gts = findGts();
  if (!gts) return { text: '错误：找不到 gts CLI。', isError: true };

  const want = Number(a.max_results) > 0 ? Math.floor(Number(a.max_results)) : 5;
  const n = Math.min(want, MAX_RESULTS);
  const args = ['-D', '--json', '-n', String(n)];
  if (a.repo) args.push('-r', a.repo);
  if (a.kind) args.push('--kind', a.kind);
  args.push(a.query);

  const { code, out, err } = await run(gts, args);
  let j;
  try { j = JSON.parse(out); } catch {
    return { text: `gts 文档搜索输出无法解析（rc=${code}）：\n${out.slice(0, 800)}\n${err.slice(0, 400)}`, isError: true };
  }
  if (j.error) {
    return { text: `搜索错误：${j.error}`, isError: true };
  }

  const rows = j.results || [];
  const lines = [];
  const perfNote = j.perf && typeof j.perf.elapsed_ms === 'number'
    ? `；耗时 ${j.perf.elapsed_ms.toFixed(1)}ms [${j.perf.status || 'OK'}]`
    : '';
  lines.push(`【文档/会话全文检索】命中 ${rows.length} 篇相关文档（FTS5 相关度排序${perfNote}）：`);
  if (!rows.length) {
    lines.push('（无命中。可试：换关键词 / 缩短词组 / 去掉限定 repo）');
  }
  for (const r of rows) {
    lines.push(`• [${r.repo}@${r.ref}] ${r.path} (${r.title})`);
    lines.push(`  摘要: ${r.snippet}`);
  }
  if (rows.length) {
    lines.push('');
    lines.push('提示：用 read_gitea_file(repo, path, ref) 读上面命中文档的全文。');
  }
  const [cappedLines, guardNotes] = capSearchLines(lines);
  return { text: [...cappedLines, ...guardNotes].join('\n') };
}

const TOOLS = [
  {
    name: 'search_gitea',
    description:
      '在本地 Gitea 全量镜像里搜索，覆盖所有镜像仓的全部本地分支。' +
      '用来找回可复用的内容：脚本、docker-compose、runbook/手册、命令、排障记录、配置片段。' +
      'Gitea 服务端未开代码索引时，网页/API 无法跨仓搜内容，需用本工具。' +
      'mode=content 搜文件内容（默认，按字面量、不分大小写，多词短语 0 命中时自动回退同行多词 AND）；mode=path 搜文件路径；mode=message 搜提交信息。' +
      '只覆盖已推送到 Gitea 镜像的内容，本机未推送的工作副本不在内。' +
      '⚠️ 若查跨行多词组合、中文问题、历史故障或历史会话（内置 session_search 对连续中文与多词短语易 0 命中），请优先用 search_gitea_docs，严禁手写脚本遍历解压 .dsh/sessions/*.zstd。',
    inputSchema: {
      type: 'object',
      properties: {
        query: { type: 'string', description: '搜索词。默认按字面量匹配；regex:true 时按扩展正则（egrep 语法）。' },
        mode: { type: 'string', enum: ['content', 'path', 'message'], description: '默认 content（搜文件内容）。' },
        repo: { type: 'string', description: '限定仓名，支持逗号分隔与 * 通配，如 "example-repo,myorg/app-*"。' },
        branch: { type: 'string', description: '限定分支（默认搜全部分支；HEAD=默认分支）。' },
        regex: { type: 'boolean', description: '按扩展正则解释 query。' },
        case_sensitive: { type: 'boolean', description: '区分大小写（默认不区分）。' },
        files_only: { type: 'boolean', description: '只列命中文件，不显示行内容。' },
        max_results: { type: 'integer', description: '最多返回条数，默认 5（省上下文：先定位再精读）；超 GITQ_MAX_RESULTS(默认200) 会被硬夹并在首行标注。' },
        sync: { type: 'boolean', description: '搜索前先增量同步镜像（多花约 10-30s，默认 false；镜像另有定时自动同步）。' },
      },
      required: ['query'],
      additionalProperties: false,
    },
  },
  {
    name: 'read_gitea_file',
    description:
      '从 Gitea 镜像里读出一个文件（搜到之后直接取用内容，不必 clone）。' +
      'ref 省略时用该仓默认分支。**默认只返回前 400 行**（省上下文），' +
      '需要别的段落用 offset/limit 续读，取全文用 limit=0。' +
      '另有字节上限（默认 60KB，可经 GITQ_MAX_READ_BYTES 调整）。',
    inputSchema: {
      type: 'object',
      properties: {
        repo: { type: 'string', description: '仓名，如 "example-repo"。' },
        path: { type: 'string', description: '仓内相对路径，如 "bin/gts"。' },
        ref: { type: 'string', description: '分支/tag/commit，默认该仓默认分支。' },
        offset: { type: 'integer', description: '从第几行开始（0 基，默认 0）。配合 limit 做分段读取。' },
        limit: { type: 'integer', description: '返回多少行，默认 400（GITQ_READ_DEFAULT_LINES 可调）；传 0 表示不限行数（仍受字节上限约束）。' },
      },
      required: ['repo', 'path'],
      additionalProperties: false,
    },
  },
  {
    name: 'list_gitea_repos',
    description: '列出 Gitea 镜像里的仓名（可按名字过滤），用来确认仓名或盘点。',
    inputSchema: {
      type: 'object',
      properties: {
        filter: { type: 'string', description: '仓名子串或通配，如 "service"、"myorg/dsh-*"。省略则全列。' },
      },
      additionalProperties: false,
    },
  },
  {
    name: 'search_gitea_docs',
    description:
      '在本地 Gitea 镜像所有仓库的 Markdown 文档、会话导出（如 dsh-session-archive）与手册中执行 SQLite FTS5 高速全文检索。' +
      '毫秒级返回匹配文件、文档标题、高亮上下文摘要与相关度排序。' +
      '比普通代码搜索更适合查找历史排查记录、配置说明、决策原因与既往方案。' +
      '内置 jieba 中文分词与多词跨行检索（覆盖用户消息与助手回复）。当内置 session_search 查中文或多词返回 0 命中时，应首选本工具（可设 kind="session"），严禁手写脚本全量解压 .zstd 会话文件。',
    inputSchema: {
      type: 'object',
      properties: {
        query: { type: 'string', description: '搜索关键词或短语（如 "Caddy 证书"、"反向代理配置"）' },
        repo: { type: 'string', description: '可选：限定仓库名（支持通配符或部分匹配，如 "session*"）' },
        kind: { type: 'string', description: '可选：按内容类型过滤，逗号分隔。取值 doc（文档）/session（会话导出）/config（yml/conf/env 等配置）/script（sh/service 等脚本）。不填=全部。' },
        max_results: { type: 'integer', description: '最多返回条数，默认 5（省上下文：先定位再精读）' },
      },
      required: ['query'],
      additionalProperties: false,
    },
  },
];

const server = new Server(
  { name: 'gitea-search', version: '1.0.0' },
  { capabilities: { tools: {} } },
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: TOOLS }));

server.setRequestHandler(CallToolRequestSchema, async (req) => {
  const { name, arguments: a = {} } = req.params || {};
  const t0 = Date.now();
  // 注意：MCP 的 CallToolResult 必须是 { content: [{type:'text',text}] }；
  // 写成 { text } 会被原样序列化，content 变成 []，客户端拿不到正文。
  const wrap = (r) => {
    const elapsedMs = Date.now() - t0;
    if (elapsedMs > 5000) {
      log(`[PERF-CRITICAL] tool=${name} elapsed=${elapsedMs}ms (>5000ms)`);
    } else if (elapsedMs > 2000) {
      log(`[PERF-WARN] tool=${name} elapsed=${elapsedMs}ms (>2000ms)`);
    }
    return {
      content: [{ type: 'text', text: r.text }],
      ...(r.isError ? { isError: true } : {}),
    };
  };
  try {
    if (name === 'search_gitea') return wrap(await searchGitea(a));
    if (name === 'read_gitea_file') return wrap(await readGiteaFile(a));
    if (name === 'list_gitea_repos') return wrap(await listGiteaRepos(a));
    if (name === 'search_gitea_docs') return wrap(await searchGiteaDocs(a));
    return wrap({ text: `未知工具：${name}`, isError: true });
  } catch (e) {
    log('tool error', name, String((e && e.stack) || e));
    return wrap({ text: `内部错误：${String((e && e.message) || e)}`, isError: true });
  }
});

const transport = new StdioServerTransport();
await server.connect(transport);
log(`started; mirror=${MIRROR}; gts=${findGts() || '(未找到)'}`);
