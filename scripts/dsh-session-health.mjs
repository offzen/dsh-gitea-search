#!/usr/bin/env node
/**
 * dsh-session-health.mjs — DSH 会话存储代数 (v1~v4) 与 session-query.sqlite 预检测/在线无损修复工具
 *
 * 背景与用途：
 *   DSH 0.2.x 当前使用 v4 会话格式 (session.v4.jsonl.zstd)。由于 DSH 采用惰性写时迁移
 *   (Lazy Write-on-Open Migration)，只读扫描 (如 session_search) 不会将旧版 v1/v2/v3 会话
 *   落盘为 v4，且每个旧版会话的 revision 末尾会拼接整个语料库的 64 位哈希 (:corpusRevision)。
 *   当宿主存在大量旧版 (<v4) 会话时：
 *     1. 每次调用 session_search 会触发 O(M * N) 次 zstd 解压与内存迁移 (historicalCorpusRevision 风暴)；
 *     2. 任意活跃会话写入都会使所有旧版会话的 :corpusRevision 集体失效，触发 _observeStable 重试死循环
 *        或对無索引 FTS5 表 persisted_docs 执行数百次全表扫描 DELETE，导致 600,000ms (10分钟) 假死超时。
 *
 * 功能：
 *   --check (默认) : 扫描 DSH_HOME/sessions 与 session-query.sqlite，输出耗时、磁盘 IO 及 OK/WARN/CRITICAL 判定
 *   --fix          : 1) 无损将所有 <v4 会话升级生成 session.v4.jsonl.zstd（保留原 v1/v2/v3 文件不删除）；
 *                    2) 同步对齐 session-query.sqlite 中 persisted_sessions.revision 为新文件 5 段式指纹，避免重索引锁表；
 *                    3) 若 WAL > 32MB，自动执行在线 PRAGMA wal_checkpoint(TRUNCATE) 回收膨胀；全程零修改任何 DSH 官方代码。
 *   --dsh-home <p> : 指定 .dsh 目录（默认自动探测 DSH_HOME / ~/.dsh / /var/lib/dsh/.dsh）
 *   --json         : 输出结构化 JSON
 */

import fs from 'node:fs';
import fsp from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { performance } from 'node:perf_hooks';
import { pathToFileURL } from 'node:url';
import { zstdCompressSync, zstdDecompressSync } from 'node:zlib';

function expandHome(p) {
  if (!p) return p;
  if (p === '~') return os.homedir();
  if (p.startsWith('~/')) return path.join(os.homedir(), p.slice(2));
  return p;
}

function readProcIo() {
  const out = { rchar: 0, wchar: 0, syscr: 0, syscw: 0, read_bytes: 0, write_bytes: 0 };
  try {
    const raw = fs.readFileSync('/proc/self/io', 'utf8');
    for (const line of raw.split('\n')) {
      const idx = line.indexOf(':');
      if (idx < 0) continue;
      const k = line.slice(0, idx).trim();
      if (k in out) out[k] = Number(line.slice(idx + 1).trim()) || 0;
    }
  } catch { /* non-Linux fallback */ }
  return out;
}

function diffIo(before, after, t0) {
  return {
    elapsed_ms: Number((performance.now() - t0).toFixed(2)),
    rchar: Math.max(0, after.rchar - before.rchar),
    wchar: Math.max(0, after.wchar - before.wchar),
    syscr: Math.max(0, after.syscr - before.syscr),
    syscw: Math.max(0, after.syscw - before.syscw),
    read_bytes: Math.max(0, after.read_bytes - before.read_bytes),
    write_bytes: Math.max(0, after.write_bytes - before.write_bytes),
  };
}

function resolveDshHome(explicitHome) {
  const defaultHome = path.join(os.homedir(), '.dsh');
  const cands = [
    expandHome(explicitHome),
    expandHome(process.env.DSH_HOME),
    defaultHome,
    '/var/lib/dsh/.dsh',
  ].filter(Boolean);
  for (const c of cands) {
    if (fs.existsSync(path.join(c, 'sessions'))) return c;
  }
  return expandHome(explicitHome) || expandHome(process.env.DSH_HOME) || defaultHome;
}

function detectSessionVersion(files) {
  const set = new Set(files);
  if (set.has('session.v4.jsonl.zstd') || set.has('session.v4.jsonl')) {
    return { version: 4, file: set.has('session.v4.jsonl.zstd') ? 'session.v4.jsonl.zstd' : 'session.v4.jsonl' };
  }
  if (set.has('session.v3.jsonl.zstd') || set.has('session.v3.jsonl')) {
    return { version: 3, file: set.has('session.v3.jsonl.zstd') ? 'session.v3.jsonl.zstd' : 'session.v3.jsonl' };
  }
  if (set.has('session.v2.jsonl.zstd') || set.has('session.v2.jsonl')) {
    return { version: 2, file: set.has('session.v2.jsonl.zstd') ? 'session.v2.jsonl.zstd' : 'session.v2.jsonl' };
  }
  if (set.has('session.jsonl.zstd') || set.has('session.jsonl')) {
    return { version: 1, file: set.has('session.jsonl.zstd') ? 'session.jsonl.zstd' : 'session.jsonl' };
  }
  return { version: 0, file: null };
}

function scanSessionsDir(sessionsRoot) {
  const counts = { v4: 0, v3: 0, v2: 0, v1: 0 };
  const legacyItems = [];
  const allById = new Map();
  if (!fs.existsSync(sessionsRoot)) {
    return { counts, legacyItems, allById, total: 0 };
  }
  for (const proj of fs.readdirSync(sessionsRoot)) {
    const pp = path.join(sessionsRoot, proj);
    let st;
    try { st = fs.statSync(pp); } catch { continue; }
    if (!st.isDirectory()) continue;
    for (const sdir of fs.readdirSync(pp)) {
      const sp = path.join(pp, sdir);
      try { if (!fs.statSync(sp).isDirectory()) continue; } catch { continue; }
      const files = fs.readdirSync(sp);
      const det = detectSessionVersion(files);
      if (det.version >= 1 && det.version <= 4) {
        counts[`v${det.version}`] += 1;
        const sid = sdir.startsWith('session-') ? sdir.slice('session-'.length) : sdir;
        const item = { id: sdir, bareId: sid, dir: sp, version: det.version, file: det.file, fullPath: path.join(sp, det.file) };
        allById.set(sdir, item);
        allById.set(sid, item);
        if (det.version < 4) legacyItems.push(item);
      }
    }
  }
  const total = counts.v4 + counts.v3 + counts.v2 + counts.v1;
  return { counts, legacyItems, allById, total };
}

async function findPersistenceModule() {
  const home = os.homedir();
  const cands = [
    expandHome(process.env.DSH_PERSISTENCE_MODULE),
    path.join(home, 'node_modules/.pnpm/node_modules/@deepseek-ai/dsh-session-persistence-jsonl/lib/index.js'),
    path.join(home, '.dsh/profiles/node_modules/@deepseek-ai/dsh-session-persistence-jsonl/lib/index.js'),
    path.join(home, 'dsh/deepseek-harness/node_modules/@deepseek-ai/dsh-session-persistence-jsonl/lib/index.js'),
    '/srv/dsh/deepseek-harness/node_modules/@deepseek-ai/dsh-session-persistence-jsonl/lib/index.js',
  ].filter(Boolean);
  for (const c of cands) {
    if (fs.existsSync(c)) {
      try {
        return await import(pathToFileURL(c).href);
      } catch { /* try next */ }
    }
  }
  return null;
}

const RENAMED_PRODUCERS = {
  compact: 'compact-checkpoint',
  'tools-code-mode': 'ptc-mode',
  'tools-ptc': 'ptc-mode',
  'dsh-compaction-basic': 'compact-basic',
  '@deepseek-ai/dsh-system-prompt': 'runtime-context',
};

function migrateMessageSourceToV4(msg) {
  if (!msg || typeof msg !== 'object') return msg;
  const src = msg.source;
  if (!src || typeof src !== 'object') return msg;
  if (src.kind === 'plugin' && typeof src.plugin === 'string') {
    const mapped = RENAMED_PRODUCERS[src.plugin] || src.plugin;
    const nextSrc = { ...src, kind: mapped };
    delete nextSrc.plugin;
    return { ...msg, source: nextSrc };
  }
  return msg;
}

function builtinFallbackMigrateToV4(item) {
  const isZstd = item.file.endsWith('.zstd');
  const rawBuf = fs.readFileSync(item.fullPath);
  const text = (isZstd ? zstdDecompressSync(rawBuf) : rawBuf).toString('utf8');
  const lines = text.split('\n').filter((l) => l.trim().length > 0);
  if (lines.length === 0) return false;

  const header = JSON.parse(lines[0]);
  header.version = 4;
  if (header.isSeeded === undefined) header.isSeeded = false;
  if (header.delegationDepth === undefined) header.delegationDepth = 0;

  const outLines = [JSON.stringify(header)];
  for (let i = 1; i < lines.length; i++) {
    const ev = JSON.parse(lines[i]);
    if (ev && typeof ev === 'object' && ev.data && typeof ev.data === 'object') {
      if (ev.type === 'user/message') {
        ev.data = migrateMessageSourceToV4(ev.data);
      } else if (['developer/message', 'system/message', 'assistant/message', 'tool/result'].includes(ev.type) && ev.data.message) {
        ev.data = { ...ev.data, message: migrateMessageSourceToV4(ev.data.message) };
      }
    }
    outLines.push(JSON.stringify(ev));
  }
  const outPayload = Buffer.from(outLines.join('\n') + '\n', 'utf8');
  const targetName = isZstd ? 'session.v4.jsonl.zstd' : 'session.v4.jsonl';
  const targetPath = path.join(item.dir, targetName);
  const tmpPath = path.join(item.dir, `.${targetName}.tmp-${process.pid}`);
  fs.writeFileSync(tmpPath, isZstd ? zstdCompressSync(outPayload) : outPayload);
  fs.renameSync(tmpPath, targetPath);
  return true;
}

function fileRevisionString(filePath) {
  const st = fs.statSync(filePath, { bigint: true });
  return `${st.dev}:${st.ino}:${st.size}:${st.mtimeNs}:${st.ctimeNs}`;
}

async function inspectAndOptionallyFix(dshHome, doFix) {
  const t0 = performance.now();
  const io0 = readProcIo();
  const sessionsRoot = path.join(dshHome, 'sessions');
  const sqPath = path.join(dshHome, 'session-query.sqlite');

  const beforeScan = scanSessionsDir(sessionsRoot);
  let migratedCount = 0;
  let migrationFailed = 0;
  let migrationEngine = 'none';
  let revisionsAligned = 0;
  let walCheckpointed = false;

  if (doFix && beforeScan.legacyItems.length > 0) {
    const mod = await findPersistenceModule();
    if (mod && mod.JsonlSessionPersistence) {
      migrationEngine = 'dsh-native';
      const storage = new mod.JsonlSessionPersistence({ directory: sessionsRoot, compression: 'zstd' });
      for (const item of beforeScan.legacyItems) {
        try {
          const h = await storage.open(item.id, 'write');
          await h.close();
          migratedCount += 1;
        } catch {
          try {
            if (builtinFallbackMigrateToV4(item)) migratedCount += 1;
            else migrationFailed += 1;
          } catch {
            migrationFailed += 1;
          }
        }
      }
    } else {
      migrationEngine = 'builtin-fallback';
      for (const item of beforeScan.legacyItems) {
        try {
          if (builtinFallbackMigrateToV4(item)) migratedCount += 1;
          else migrationFailed += 1;
        } catch {
          migrationFailed += 1;
        }
      }
    }
  }

  const afterScan = doFix ? scanSessionsDir(sessionsRoot) : beforeScan;

  // 检查 & 对齐 session-query.sqlite
  let sqliteInfo = {
    exists: fs.existsSync(sqPath),
    persisted_sessions: 0,
    long_revisions: 0,
    mismatched_revisions: 0,
    wal_mb: 0,
  };

  if (sqliteInfo.exists) {
    if (fs.existsSync(sqPath + '-wal')) {
      sqliteInfo.wal_mb = Number((fs.statSync(sqPath + '-wal').size / (1024 * 1024)).toFixed(2));
    }
    try {
      const { DatabaseSync } = await import('node:sqlite');
      const db = new DatabaseSync(sqPath);
      db.exec('PRAGMA busy_timeout = 5000');
      const rows = db.prepare('SELECT id, revision FROM persisted_sessions').all();
      sqliteInfo.persisted_sessions = rows.length;

      const migratedSet = new Set(beforeScan.legacyItems.flatMap((it) => [it.id, it.bareId]));
      const toUpdate = [];
      for (const r of rows) {
        const rev = String(r.revision || '');
        const isLongRev = rev.split(':').length > 5 || rev.length > 100;
        if (isLongRev) sqliteInfo.long_revisions += 1;
        const diskItem = afterScan.allById.get(r.id);
        if (isLongRev || migratedSet.has(r.id)) {
          if (diskItem && diskItem.version === 4) {
            const expectedRev = fileRevisionString(diskItem.fullPath);
            if (rev !== expectedRev) {
              sqliteInfo.mismatched_revisions += 1;
              toUpdate.push([expectedRev, r.id]);
            }
          } else if (isLongRev) {
            const strippedRev = rev.split(':').slice(0, 5).join(':');
            sqliteInfo.mismatched_revisions += 1;
            toUpdate.push([strippedRev, r.id]);
          }
        }
      }

      if (doFix && toUpdate.length > 0) {
        db.exec('BEGIN IMMEDIATE');
        try {
          const stmt = db.prepare('UPDATE persisted_sessions SET revision = ? WHERE id = ?');
          for (const [newRev, id] of toUpdate) {
            stmt.run(newRev, id);
          }
          db.exec('COMMIT');
          revisionsAligned = toUpdate.length;
          sqliteInfo.long_revisions = db.prepare(
            "SELECT COUNT(*) as c FROM persisted_sessions WHERE (length(revision) - length(replace(revision, ':', ''))) >= 5 OR length(revision) > 100"
          ).get().c;
          sqliteInfo.mismatched_revisions = 0;
        } catch (e) {
          try { db.exec('ROLLBACK'); } catch { /* ignore */ }
          throw e;
        }
      }
      if (doFix && sqliteInfo.wal_mb > 32) {
        try {
          db.exec('PRAGMA wal_checkpoint(TRUNCATE)');
          walCheckpointed = true;
          if (fs.existsSync(sqPath + '-wal')) {
            sqliteInfo.wal_mb = Number((fs.statSync(sqPath + '-wal').size / (1024 * 1024)).toFixed(2));
          }
        } catch { /* ignore if active reader prevents full truncate */ }
      }
      db.close();
    } catch { /* sqlite unavailable or locked */ }
  }

  // 检查宿主 profiles 下是否存在 node_modules/.pnpm 软链拓扑（提醒勿用递归 glob/grep 扫该目录，严禁改动任何 DSH 官方文件）
  const pnpmDirs = [];
  const profilesRoot = path.join(dshHome, 'profiles');
  if (fs.existsSync(profilesRoot)) {
    try {
      for (const prof of fs.readdirSync(profilesRoot, { withFileTypes: true })) {
        if (!prof.isDirectory()) continue;
        const pdir = path.join(profilesRoot, prof.name, 'node_modules', '.pnpm');
        if (fs.existsSync(pdir)) pnpmDirs.push(pdir);
      }
    } catch { /* ignore */ }
  }

  const ioMetrics = diffIo(io0, readProcIo(), t0);
  const legacyRemaining = afterScan.legacyItems.length;
  const unindexedSessions = sqliteInfo.exists ? Math.max(0, afterScan.total - sqliteInfo.persisted_sessions) : 0;
  let status = 'OK';
  const reasons = [];
  const hints = [];

  if (legacyRemaining >= 50 || sqliteInfo.long_revisions >= 50 || sqliteInfo.wal_mb > 256) {
    status = 'CRITICAL';
    reasons.push(`存在 ${legacyRemaining} 个旧版 (<v4) 会话、${sqliteInfo.long_revisions} 条长 revision 记录，WAL=${sqliteInfo.wal_mb}MB`);
    hints.push(`运行: node scripts/dsh-session-health.mjs --fix --dsh-home ${dshHome}`);
  } else if (
    legacyRemaining > 0 ||
    sqliteInfo.long_revisions > 0 ||
    sqliteInfo.mismatched_revisions > 0 ||
    sqliteInfo.wal_mb > 64 ||
    unindexedSessions > 20
  ) {
    status = 'WARN';
    reasons.push(
      `存在 ${legacyRemaining} 个旧版 (<v4) 会话, ${sqliteInfo.long_revisions} 条长 revision, ` +
      `${sqliteInfo.mismatched_revisions} 条待对齐 revision, 未入库会话=${unindexedSessions}, WAL=${sqliteInfo.wal_mb}MB`
    );
    hints.push(`运行: node scripts/dsh-session-health.mjs --fix --dsh-home ${dshHome}`);
  }

  return {
    dsh_home: dshHome,
    mode: doFix ? 'fix' : 'check',
    status,
    sessions: {
      total: afterScan.total,
      before_counts: beforeScan.counts,
      after_counts: afterScan.counts,
      legacy_remaining: legacyRemaining,
      migrated: migratedCount,
      migration_failed: migrationFailed,
      migration_engine: migrationEngine,
    },
    sqlite: {
      ...sqliteInfo,
      unindexed_sessions: unindexedSessions,
      revisions_aligned: revisionsAligned,
      wal_checkpointed: walCheckpointed,
    },
    symlink_hazard_dirs: pnpmDirs,
    metrics: ioMetrics,
    reasons,
    hints,
  };
}

async function main() {
  const args = process.argv.slice(2);
  const doFix = args.includes('--fix');
  const asJson = args.includes('--json');
  let explicitHome = null;
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--dsh-home' && args[i + 1]) {
      explicitHome = args[i + 1];
      i += 1;
    }
  }
  const dshHome = resolveDshHome(explicitHome);
  const report = await inspectAndOptionallyFix(dshHome, doFix);

  if (asJson) {
    console.log(JSON.stringify(report, null, 2));
  } else {
    console.log(`=== DSH 会话健康与 I/O 诊断 (${report.mode.toUpperCase()} -> ${report.status}) ===`);
    console.log(`DSH_HOME     : ${report.dsh_home}`);
    console.log(`会话版本分布 : ${JSON.stringify(report.sessions.after_counts)} (总计 ${report.sessions.total}, 残留 <v4: ${report.sessions.legacy_remaining})`);
    if (doFix) {
      console.log(`本次无损迁移 : 升级 ${report.sessions.migrated} 个会话 (engine=${report.sessions.migration_engine}, 失败=${report.sessions.migration_failed})`);
    }
    if (report.sqlite.exists) {
      console.log(`SQLite 索引  : persisted_sessions=${report.sqlite.persisted_sessions} (未入库=${report.sqlite.unindexed_sessions}), 长 revision=${report.sqlite.long_revisions}, 对齐=${report.sqlite.revisions_aligned}, WAL=${report.sqlite.wal_mb}MB`);
    }
    if (report.symlink_hazard_dirs && report.symlink_hazard_dirs.length > 0) {
      console.log(`软链环提示   : 检测到 ${report.symlink_hazard_dirs.length} 处 pnpm 软链目录 (${report.symlink_hazard_dirs.join(', ')})，切勿使用 glob(recursive=True) 扫描`);
    }
    console.log(`耗时与磁盘IO : elapsed=${report.metrics.elapsed_ms}ms, rchar=${report.metrics.rchar}B, wchar=${report.metrics.wchar}B, read_bytes=${report.metrics.read_bytes}B, write_bytes=${report.metrics.write_bytes}B`);
    for (const r of report.reasons) console.log(`  ! ${r}`);
    for (const h of report.hints) console.log(`  -> ${h}`);
  }
  process.exit(report.status === 'CRITICAL' && !doFix ? 2 : 0);
}

main().catch((err) => {
  console.error('[dsh-session-health] Fatal:', err);
  process.exit(1);
});
