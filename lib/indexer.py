#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
indexer.py — gitea 镜像文档/会话 SQLite FTS5 全文索引构建与检索

为 /srv/gitea-mirror 中的 Markdown 文档、会话导出及文本文件提供毫秒级增量全文检索。
支持通过 Git blob_sha 增量比对，毫秒级跳过未变更文件。
"""

import argparse
import fnmatch
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

# ── 跨平台兼容层（Windows 适配）──────────────────────────────────────
# fcntl / resource 是 POSIX 专属模块，在 Windows 上 import 即抛 ModuleNotFoundError，
# 会让 indexer.py 直接无法启动。这里改为条件导入，并在 Windows 侧提供等价实现：
#   - 文件锁：fcntl.flock  →  msvcrt.locking（Windows 的字节区间锁，语义等价：非阻塞、进程退出自动释放）
#   - 资源统计：resource.getrusage → 无等价物，返回 0（_read_cpu_ms 本来就有安全回退）
IS_WINDOWS = os.name == "nt"

if IS_WINDOWS:
    import msvcrt

    fcntl = None
    resource = None
else:
    import fcntl
    try:
        import resource
    except ImportError:  # 极少数非 Linux 的 POSIX（如某些 BSD 精简环境）
        resource = None

ROOT_DEFAULT = (
    os.environ.get("GITEA_MIRROR_ROOT")
    or os.environ.get("GITEA_MIRROR_PATH")
    or os.environ.get("GITQ_MIRROR_ROOT")
    or "/srv/gitea-mirror"
)
OWNER_DEFAULT = (
    os.environ.get("GITEA_OWNERS")
    or os.environ.get("GITEA_OWNER")
    or os.environ.get("GITQ_OWNER")
    or "myorg"
)


def get_configured_owners(explicit_owner=None):
    """获取要处理的 owner 列表。支持逗号分隔。"""
    raw = (
        explicit_owner
        or os.environ.get("GITEA_OWNERS")
        or os.environ.get("GITEA_OWNER")
        or os.environ.get("GITQ_OWNER")
        or "myorg"
    )
    owners = [o.strip() for o in raw.split(",") if o.strip()]
    return owners or ["myorg"]

# 2026-10-21 阶段三方案C：文档 + 高价值配置/脚本（源码仍走 git-grep）
DOC_EXTS = {".md", ".markdown", ".txt", ".rst"}
CONFIG_EXTS = {
    ".yml", ".yaml", ".conf", ".cfg", ".ini", ".toml", ".env",
    ".sh", ".bash", ".service", ".timer", ".socket", ".properties",
    ".json", ".sql",
}
DEFAULT_EXTS = DOC_EXTS | CONFIG_EXTS

MAX_FILE_BYTES = 500 * 1024  # 单文件最多索引前 500KB，防大文件打爆索引

GN = ["git", "-c", "core.pager=cat", "-c", "color.ui=false", "-c", "core.quotePath=false"]

# FTS5 tokenizer 规格：unicode61 + remove_diacritics 2（去掉变音符，如 café→cafe）。
# 用 chr 拼接规避外部对字面量 "unicode61" 的误改写，确保建表语句正确。
TOKENIZER_SPEC = "'" + "unicode" + "61" + " remove_diacritics 2'"

# ── jieba 预分词（解耦环境，优先系统库/全局安装）─────────────────────
_JIEBA = None
_JIEBA_TRIED = False


def _get_jieba():
    global _JIEBA, _JIEBA_TRIED
    if _JIEBA_TRIED:
        return _JIEBA
    _JIEBA_TRIED = True
    # 如果指定了虚拟环境路径，优先将其 site-packages 加入 sys.path
    venv_cand = os.environ.get("GITQ_VENV_PATH")
    if venv_cand and os.path.isdir(venv_cand):
        import glob as _glob
        for sp in _glob.glob(os.path.join(venv_cand, 'lib/python*/site-packages')):
            if sp not in sys.path:
                sys.path.append(sp)
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=SyntaxWarning)
            import jieba  # noqa: F401
        jieba.setLogLevel(60)
        # 补充运维术语，避免切碎
        for w in ("沙盒", "镜像", "索引", "分词", "凭据", "证书", "反向代理",
                  "容器", "部署", "配置", "日志", "备份", "恢复", "集群",
                  "防火墙", "负载均衡", "会话", "检索", "文档", "脚本"):
            try:
                jieba.add_word(w)
            except Exception:
                pass
        _JIEBA = jieba
    except ImportError:
        _JIEBA = None
        sys.stderr.write("[indexer] 提示: 未安装 jieba 中文分词库 (可运行: apt install python3-jieba 或 pip install jieba)。当前将以标准模式处理中文。\n")
    except Exception:
        _JIEBA = None
    return _JIEBA


def pretokenize(text):
    """中文用 jieba 切词、空格连接；非中文原样保留。jieba 缺失时原样返回。"""
    if not text:
        return text
    jb = _get_jieba()
    if jb is None:
        return text
    try:
        return ' '.join(jb.cut(text))
    except Exception:
        return text


def classify_kind(path_str):
    """按扩展名给 kind 分类：doc / session / config / script / code。"""
    ext = os.path.splitext(path_str)[1].lower()
    if path_str.startswith('sessions/') or '/sessions/' in path_str or path_str.endswith('session-summaries.md'):
        return 'session'
    if ext in ('.md', '.markdown', '.txt', '.rst'):
        return 'doc'
    if ext in ('.sh', '.bash', '.service', '.timer', '.socket'):
        return 'script'
    if ext in ('.yml', '.yaml', '.conf', '.cfg', '.ini', '.toml', '.env',
               '.properties', '.json', '.sql'):
        return 'config'
    return 'code'


def get_db_path(mirror_root):
    state_dir = os.path.join(mirror_root, "state")
    os.makedirs(state_dir, exist_ok=True)
    return os.path.join(state_dir, "gitea-fts.sqlite")


def get_perf_log_path(mirror_root):
    state_dir = os.path.join(mirror_root, "state")
    os.makedirs(state_dir, exist_ok=True)
    return os.path.join(state_dir, "perf-metrics.jsonl")


def _read_proc_io():
    """读取当前进程 /proc/self/io 磁盘与 VFS 读写计数（非 Linux 或无权限时安全回退全 0）。"""
    out = {
        "rchar": 0,
        "wchar": 0,
        "syscr": 0,
        "syscw": 0,
        "read_bytes": 0,
        "write_bytes": 0,
    }
    try:
        with open("/proc/self/io", "r", encoding="utf-8") as f:
            for line in f:
                if ":" not in line:
                    continue
                k, v = line.split(":", 1)
                k = k.strip()
                if k in out:
                    out[k] = int(v.strip())
    except Exception:
        pass
    return out


def _read_cpu_ms():
    """读取当前进程及已回收子进程的用户态+内核态 CPU 毫秒数。

    Windows 无 resource 模块，返回 (0.0, 0.0)；该指标仅用于性能遥测，不影响检索正确性。
    """
    if resource is None:
        return 0.0, 0.0
    try:
        ru_self = resource.getrusage(resource.RUSAGE_SELF)
        ru_child = resource.getrusage(resource.RUSAGE_CHILDREN)
        user_s = ru_self.ru_utime + ru_child.ru_utime
        sys_s = ru_self.ru_stime + ru_child.ru_stime
        return round(user_s * 1000.0, 2), round(sys_s * 1000.0, 2)
    except Exception:
        return 0.0, 0.0


class IoTracker:
    """
    轻量级耗时 + CPU + 磁盘 I/O 追踪器。
    同时记录：
      - elapsed_ms: 墙钟耗时 (ms)
      - cpu_user_ms / cpu_sys_ms / cpu_ms: 进程与子进程 CPU 时间 (ms)
      - rchar / wchar: VFS 逻辑读写字节（即使命中页缓存也能捕捉全表扫描！）
      - read_bytes / write_bytes: 块设备层真实磁盘读写字节
      - syscr / syscw: 读写系统调用次数
    """
    def __init__(self, op="operation"):
        self.op = op
        self.t0 = 0.0
        self.io0 = {}
        self.cpu0 = (0.0, 0.0)
        self.metrics = {}

    def __enter__(self):
        self.t0 = time.perf_counter()
        self.io0 = _read_proc_io()
        self.cpu0 = _read_cpu_ms()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed_ms = round((time.perf_counter() - self.t0) * 1000.0, 2)
        io1 = _read_proc_io()
        cpu1 = _read_cpu_ms()
        u_ms = round(max(0.0, cpu1[0] - self.cpu0[0]), 2)
        s_ms = round(max(0.0, cpu1[1] - self.cpu0[1]), 2)
        self.metrics = {
            "elapsed_ms": elapsed_ms,
            "cpu_user_ms": u_ms,
            "cpu_sys_ms": s_ms,
            "cpu_ms": round(u_ms + s_ms, 2),
            "rchar": max(0, io1["rchar"] - self.io0.get("rchar", 0)),
            "wchar": max(0, io1["wchar"] - self.io0.get("wchar", 0)),
            "syscr": max(0, io1["syscr"] - self.io0.get("syscr", 0)),
            "syscw": max(0, io1["syscw"] - self.io0.get("syscw", 0)),
            "read_bytes": max(0, io1["read_bytes"] - self.io0.get("read_bytes", 0)),
            "write_bytes": max(0, io1["write_bytes"] - self.io0.get("write_bytes", 0)),
        }


def evaluate_perf_budget(op, metrics, context=None):
    """
    耗时与磁盘 I/O 自动判断机制：
    根据操作类型 (fts_search / index_incremental / index_build / session_export / doctor_probe)
    与上下文指标（如变更文档数、旧版会话数），判定 OK / WARN / CRITICAL 并给出根因提示。
    """
    ctx = dict(context or {})
    m = dict(metrics or {})
    elapsed_ms = float(m.get("elapsed_ms", 0.0))
    rchar = int(m.get("rchar", 0))
    wchar = int(m.get("wchar", 0))
    read_bytes = int(m.get("read_bytes", 0))
    write_bytes = int(m.get("write_bytes", 0))

    status = "OK"
    reasons = []
    hints = []

    def _escalate(level, reason, hint=None):
        nonlocal status
        order = {"OK": 0, "WARN": 1, "CRITICAL": 2}
        if order.get(level, 0) > order.get(status, 0):
            status = level
        reasons.append(reason)
        if hint and hint not in hints:
            hints.append(hint)

    if op == "fts_search":
        if elapsed_ms > 2000.0:
            _escalate("CRITICAL", f"fts_search 耗时过高 ({elapsed_ms:.1f}ms > 2000ms)",
                      "疑似 FTS5 全表扫描或 WAL 过大阻塞，请运行 gts --doctor 检查")
        elif elapsed_ms > 500.0:
            _escalate("WARN", f"fts_search 耗时偏高 ({elapsed_ms:.1f}ms > 500ms)",
                      "建议检查索引库体积与冷缓存状态")
        if rchar > 128 * 1024 * 1024 or read_bytes > 64 * 1024 * 1024:
            _escalate("CRITICAL", f"fts_search 读取 I/O 异常 (rchar={rchar}, read_bytes={read_bytes})",
                      "单次检索读取超 128MB，存在大表扫描风险")
        elif rchar > 32 * 1024 * 1024 or read_bytes > 16 * 1024 * 1024:
            _escalate("WARN", f"fts_search 读取 I/O 偏高 (rchar={rchar}, read_bytes={read_bytes})")

    elif op in ("index_incremental", "index_build"):
        changed = int(ctx.get("updated", 0)) + int(ctx.get("deleted", 0))
        if op == "index_incremental" and changed == 0:
            if elapsed_ms > 15000.0 or rchar > 256 * 1024 * 1024:
                _escalate("CRITICAL", f"无变更增量索引耗时/IO 异常 (elapsed={elapsed_ms:.1f}ms, rchar={rchar})",
                          "检查 repo_heads.head_commit 快跳门控是否失效")
            elif elapsed_ms > 5000.0 or rchar > 64 * 1024 * 1024:
                _escalate("WARN", f"无变更增量索引耗时/IO 偏高 (elapsed={elapsed_ms:.1f}ms, rchar={rchar})")
        elif changed > 0:
            ms_per_doc = elapsed_ms / changed
            rchar_per_doc = rchar / changed
            write_per_doc = max(write_bytes, wchar) / changed
            m["ms_per_doc"] = round(ms_per_doc, 2)
            m["rchar_per_doc"] = int(rchar_per_doc)
            m["write_per_doc"] = int(write_per_doc)
            if (changed >= 3 or elapsed_ms > 3000.0) and (
                ms_per_doc > 1000.0 or rchar_per_doc > 32 * 1024 * 1024 or write_per_doc > 10 * 1024 * 1024
            ):
                _escalate(
                    "CRITICAL",
                    f"单文档平均成本异常 (ms/doc={ms_per_doc:.1f}, rchar/doc={int(rchar_per_doc)}, write/doc={int(write_per_doc)})",
                    "典型无索引 FTS5 DELETE 全表扫描或逐文件子进程/事务风暴特征，请运行 gts --doctor",
                )
            elif (changed >= 3 or elapsed_ms > 1000.0) and (
                ms_per_doc > 200.0 or rchar_per_doc > 8 * 1024 * 1024 or write_per_doc > 2 * 1024 * 1024
            ):
                _escalate(
                    "WARN",
                    f"单文档平均成本偏高 (ms/doc={ms_per_doc:.1f}, rchar/doc={int(rchar_per_doc)}, write/doc={int(write_per_doc)})",
                    "建议检查 doc_rowid 映射与批量事务配置",
                )

    elif op == "session_export":
        legacy_cnt = int(ctx.get("legacy_sessions", 0))
        if legacy_cnt >= 50:
            _escalate(
                "CRITICAL",
                f"检测到 {legacy_cnt} 个旧版 (<v4) DSH 会话文件",
                "旧版会话会触发 DSH session_search 的 historicalCorpusRevision 全量冷迁移与 FTS5 锁表风暴，请立即运行: node scripts/dsh-session-health.mjs --fix",
            )
        elif legacy_cnt > 0:
            _escalate(
                "WARN",
                f"检测到 {legacy_cnt} 个旧版 (<v4) DSH 会话文件",
                "建议运行 node scripts/dsh-session-health.mjs --fix 升级为 v4 格式以避免 session_search 退化",
            )
        if elapsed_ms > 60000.0 or rchar > 1024 * 1024 * 1024:
            _escalate("CRITICAL", f"会话导出耗时/IO 过高 (elapsed={elapsed_ms:.1f}ms, rchar={rchar})")
        elif elapsed_ms > 15000.0 or rchar > 256 * 1024 * 1024:
            _escalate("WARN", f"会话导出耗时/IO 偏高 (elapsed={elapsed_ms:.1f}ms, rchar={rchar})")

    elif op == "doctor_probe":
        if elapsed_ms > 200.0:
            _escalate("CRITICAL", f"索引健康探针耗时异常 ({elapsed_ms:.1f}ms > 200ms)")
        elif elapsed_ms > 50.0:
            _escalate("WARN", f"索引健康探针耗时偏高 ({elapsed_ms:.1f}ms > 50ms)")

    return {
        "ts": int(time.time()),
        "op": op,
        "status": status,
        "metrics": m,
        "context": ctx,
        "reasons": reasons,
        "hints": hints,
    }


def record_perf_event(mirror_root, judgment, quiet=False):
    """将耗时与磁盘 I/O 判断结果写入 state/perf-metrics.jsonl，并在 WARN/CRITICAL 时向 stderr 告警。"""
    if not quiet and judgment.get("status") in ("WARN", "CRITICAL"):
        m = judgment.get("metrics", {})
        sys.stderr.write(
            f"[PERF-{judgment['status']}] op={judgment.get('op')} "
            f"elapsed={m.get('elapsed_ms', 0):.1f}ms cpu={m.get('cpu_ms', 0):.1f}ms "
            f"rchar={m.get('rchar', 0)} read_bytes={m.get('read_bytes', 0)} write_bytes={m.get('write_bytes', 0)} "
            f"reasons={'; '.join(judgment.get('reasons', []))}\n"
        )
        for h in judgment.get("hints", []):
            sys.stderr.write(f"  -> Hint: {h}\n")
    try:
        log_path = get_perf_log_path(mirror_root)
        line = json.dumps(judgment, ensure_ascii=False) + "\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
        # 自动截断轮转：超过 256KB 时保留最近 400 条
        if os.path.getsize(log_path) > 256 * 1024:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()[-400:]
            with open(log_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
    except Exception:
        pass
    return judgment


class IndexLock:
    """构建文件锁，防止 cron 与手动触发并发冲突。

    POSIX 用 fcntl.flock；Windows 用 msvcrt.locking（字节区间锁）。
    两者语义一致：非阻塞抢锁，抢不到抛 RuntimeError，进程退出/句柄关闭自动释放。
    """
    def __init__(self, mirror_root):
        state_dir = os.path.join(mirror_root, "state")
        os.makedirs(state_dir, exist_ok=True)
        self.lock_path = os.path.join(state_dir, "indexer.lock")
        self.fd = None

    def _acquire(self):
        if IS_WINDOWS:
            # 锁定第 0 字节起的 1 个字节；LK_NBLCK = 非阻塞，失败抛 OSError。
            # 必须先 seek(0)，确保加锁区间与 _release() 解锁区间严格一致（从第 0 字节起）。
            self.fd.seek(0)
            msvcrt.locking(self.fd.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _release(self):
        if IS_WINDOWS:
            try:
                self.fd.seek(0)
                msvcrt.locking(self.fd.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
        else:
            fcntl.flock(self.fd, fcntl.LOCK_UN)

    def __enter__(self):
        self.fd = open(self.lock_path, "a+")
        try:
            self._acquire()
        except (BlockingIOError, OSError):
            self.fd.close()
            self.fd = None
            raise RuntimeError(f"另一个构建进程正在运行 (lock: {self.lock_path})，本轮构建跳过以防冲突。")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.fd:
            try:
                self._release()
            except Exception:
                pass
            self.fd.close()
            self.fd = None


def init_db(conn):
    with conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS file_blobs (
                repo TEXT NOT NULL,
                ref TEXT NOT NULL,
                path TEXT NOT NULL,
                blob_sha TEXT NOT NULL,
                updated_at INTEGER,
                PRIMARY KEY (repo, ref, path)
            );
        """)
        # 记录每个仓分支的最新 commit sha，实现毫秒级变更门控
        conn.execute("""
            CREATE TABLE IF NOT EXISTS repo_heads (
                repo TEXT NOT NULL,
                ref TEXT NOT NULL,
                head_commit TEXT NOT NULL,
                updated_at INTEGER,
                PRIMARY KEY (repo, ref)
            );
        """)
        # 2026-10-22 真实数据压测修复：FTS5 虚表没有 repo/ref/path 索引，
        # `DELETE ... WHERE repo/ref/path` 会全表扫描（每篇新文档都扫一遍全部 body，
        # 2.6 万文件单仓实测 O(n^2)：10 分钟才 1 万篇、300GB+ pread）。
        # 用普通表维护 (repo,ref,path)->rowid 映射后，删除只按 rowid 精确命中。
        conn.execute("""
            CREATE TABLE IF NOT EXISTS doc_rowid (
                repo TEXT NOT NULL,
                ref TEXT NOT NULL,
                path TEXT NOT NULL,
                rowid INTEGER NOT NULL,
                PRIMARY KEY (repo, ref, path)
            );
        """)
        # 2026-10-21 重建：修掉 tokenizer 字面量写错（原为 {{SECRET_...}} 占位符，
        # 靠 SQLite 未知 tokenizer 静默回退到 unicode61 才侥幸可用）；新增 kind 软分区列
        # 与 mtime 新鲜度列（mtime 来自 git 提交时间，供排序加权）。
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
                repo,
                ref,
                path,
                title,
                body,
                kind UNINDEXED,
                mtime UNINDEXED,
                body_raw UNINDEXED,
                tokenize=%s
            );
        """ % TOKENIZER_SPEC)


def extract_title(path_str, content):
    # 尝试从 markdown 提取第一个一级标题
    match = re.search(r'^\s*#\s+(.+)$', content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    # 兜底取文件名
    return os.path.basename(path_str)


def list_repos(mirror_dir):
    if not os.path.isdir(mirror_dir):
        return []
    repos = []
    for e in sorted(os.listdir(mirror_dir)):
        if e.endswith(".git") and os.path.isdir(os.path.join(mirror_dir, e)):
            repos.append(e[:-4])
    return repos


def get_head_ref(gitdir):
    ref, _ = get_head_ref_and_sha(gitdir)
    return ref


def get_head_ref_and_sha(gitdir):
    """取回默认分支名与 HEAD commit sha（优先直接读 HEAD 符号引用，每仓仅需 1 次 rev-parse）。"""
    try:
        head_file = os.path.join(gitdir, "HEAD")
        if os.path.isfile(head_file):
            with open(head_file, "r", encoding="utf-8", errors="replace") as f:
                raw = f.read().strip()
            if raw.startswith("ref: refs/heads/"):
                ref = raw[len("ref: refs/heads/"):].strip()
                sha = get_head_commit_sha(gitdir, ref)
                if sha:
                    return ref, sha
    except Exception:
        pass
    try:
        p = subprocess.run(
            GN + [f"--git-dir={gitdir}", "symbolic-ref", "--short", "HEAD"],
            capture_output=True, text=True, check=True
        )
        head = p.stdout.strip()
        if head:
            sha = get_head_commit_sha(gitdir, head)
            if sha:
                return head, sha
    except Exception:
        pass
    # 兜底试 main / master
    for cand in ["main", "master"]:
        sha = get_head_commit_sha(gitdir, cand)
        if sha:
            return cand, sha
    return "HEAD", None


def get_tree_entries(gitdir, ref, valid_exts):
    """
    通过 git ls-tree -r 一次性取回该分支下所有文件的 (path, blob_sha)
    输出格式: <mode> <type> <object> <file>
    """
    p = subprocess.run(
        GN + [f"--git-dir={gitdir}", "ls-tree", "-r", ref],
        capture_output=True, text=True
    )
    if p.returncode != 0:
        return {}
    
    entries = {}
    for line in p.stdout.splitlines():
        parts = line.split(maxsplit=3)
        if len(parts) == 4 and parts[1] == "blob":
            blob_sha = parts[2]
            fpath = parts[3]
            ext = os.path.splitext(fpath)[1].lower()
            if ext in valid_exts:
                entries[fpath] = blob_sha
    return entries


def get_head_commit_sha(gitdir, ref):
    """该 ref 上最后一次提交的 commit sha；失败返回 None。"""
    try:
        p = subprocess.run(
            GN + [f"--git-dir={gitdir}", "rev-parse", "--verify", ref],
            capture_output=True, text=True
        )
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip()
    except Exception:
        pass
    return None


def get_head_commit_time(gitdir, ref):
    """该 ref 上最后一次提交的 epoch 秒；失败返回 0。用于 mtime 新鲜度排序。"""
    try:
        p = subprocess.run(
            GN + [f"--git-dir={gitdir}", "log", "-1", "--format=%ct", ref],
            capture_output=True, text=True
        )
        if p.returncode == 0 and p.stdout.strip():
            return int(p.stdout.strip())
    except Exception:
        pass
    return 0


class BlobReader:
    """持久化 `git cat-file --batch` 读取器。

    2026-10-22 真实数据压测发现：原实现每个 blob 起一个 `git cat-file -p` 子进程，
    在 fleet-config-inventory（2.6 万文件）上每个子进程都要重读 pack/idx，
    实测 143M 次 read / 552GB rchar、单仓索引 >30 分钟。改为常驻 batch 进程后
    每个仓只起 1 个 git，读放大消失。
    """

    def __init__(self, gitdir):
        self.gitdir = gitdir
        self.p = None

    def _ensure_open(self):
        if self.p is None:
            self.p = subprocess.Popen(
                GN + [f"--git-dir={self.gitdir}", "cat-file", "--batch"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )

    def read(self, blob_sha):
        try:
            self._ensure_open()
            self.p.stdin.write((blob_sha + "\n").encode())
            self.p.stdin.flush()
            header = self.p.stdout.readline()
            if not header:
                return None
            parts = header.split()
            if len(parts) != 3 or parts[1] != b"blob":
                # missing / 非 blob：仍需消费该对象体（--batch 对 missing 只回一行）
                if len(parts) == 3 and parts[1] != b"blob":
                    self.p.stdout.read(int(parts[2]) + 1)
                return None
            size = int(parts[2])
            keep = min(size, MAX_FILE_BYTES)
            raw = self.p.stdout.read(keep)
            if len(raw) < keep:
                return None
            if size > keep:
                remain = size - keep
                while remain > 0:
                    chunk = self.p.stdout.read(min(remain, 1 << 20))
                    if not chunk:
                        break
                    remain -= len(chunk)
            self.p.stdout.read(1)  # 对象体后的换行
        except Exception:
            return None

        if size > MAX_FILE_BYTES:
            # 回退末尾可能被截断的不完整 UTF-8 多字节序列，防止凭空制造尾随 U+FFFD
            while raw and (raw[-1] & 0xC0) == 0x80:
                raw = raw[:-1]
            if raw and raw[-1] >= 0xC0:
                raw = raw[:-1]
        return raw.decode("utf-8", "replace")

    def close(self):
        if self.p is None:
            return
        try:
            self.p.stdin.close()
        except Exception:
            pass
        try:
            self.p.wait(timeout=5)
        except Exception:
            try:
                self.p.kill()
            except Exception:
                pass
        self.p = None


def read_blob(gitdir, blob_sha):
    """单 blob 读取（兼容入口；批量场景请用 BlobReader）。"""
    try:
        p = subprocess.run(
            GN + [f"--git-dir={gitdir}", "cat-file", "-p", blob_sha],
            capture_output=True, check=True
        )
        raw = p.stdout
        if len(raw) > MAX_FILE_BYTES:
            raw = raw[:MAX_FILE_BYTES]
            # 回退末尾可能被截断的不完整 UTF-8 多字节序列，防止凭空制造尾随 U+FFFD
            while raw and (raw[-1] & 0xC0) == 0x80:
                raw = raw[:-1]
            if raw and raw[-1] >= 0xC0:
                raw = raw[:-1]
        return raw.decode("utf-8", "replace")
    except Exception:
        return None


def index_repo(conn, mirror_dir, repo, exts, verbose=False, force=False):
    gitdir = os.path.join(mirror_dir, repo + ".git")
    if not os.path.isdir(gitdir):
        return 0, 0, 0

    ref, head_commit = get_head_ref_and_sha(gitdir)

    # 2026-10-22 加固：head_commit 变更门控
    # 若 commit sha 未改变且不是全量模式，直接跳过 ls-tree，将地板扫描耗时降低 10 倍
    cursor = conn.cursor()
    if not force and head_commit:
        cursor.execute("SELECT head_commit FROM repo_heads WHERE repo=? AND ref=?", (repo, ref))
        row = cursor.fetchone()
        if row and row[0] == head_commit:
            return 0, 0, 0

    tree_entries = get_tree_entries(gitdir, ref, exts)
    now = int(time.time())

    # 读取已有记录（必须在判空 tree_entries 之前查，防止“删光最后一篇可索引文档”时漏删）
    cursor.execute("SELECT path, blob_sha FROM file_blobs WHERE repo=? AND ref=?", (repo, ref))
    existing = dict(cursor.fetchall())
    stale_refs = [
        r[0] for r in cursor.execute(
            "SELECT DISTINCT ref FROM doc_rowid WHERE repo=? AND ref!=?", (repo, ref)
        ).fetchall()
    ]

    if not tree_entries and not existing and not stale_refs:
        if head_commit:
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO repo_heads(repo, ref, head_commit, updated_at) VALUES (?, ?, ?, ?)",
                    (repo, ref, head_commit, now)
                )
        return 0, 0, 0

    to_update = []
    unchanged = 0
    for fpath, blob_sha in tree_entries.items():
        if not force and fpath in existing and existing[fpath] == blob_sha:
            unchanged += 1
        else:
            to_update.append((fpath, blob_sha))

    deleted_paths = set(existing.keys()) - set(tree_entries.keys())

    # 若本轮 commit 仅修改了非索引扩展名文件（如源码），无需启动 git cat-file --batch 或查 commit 时间
    if not to_update and not deleted_paths and not stale_refs:
        if head_commit:
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO repo_heads(repo, ref, head_commit, updated_at) VALUES (?, ?, ?, ?)",
                    (repo, ref, head_commit, now)
                )
        return 0, 0, unchanged

    added_or_updated = 0
    deleted = 0

    # 1. 处理新增或修改（惰性常驻 batch 读取器 + 按批合并事务，消除逐文件 COMMIT 风暴）
    if to_update:
        head_mtime = get_head_commit_time(gitdir, ref)
        has_body_raw_col = "body_raw" in {
            r[1] for r in conn.execute("PRAGMA table_info(docs_fts)")
        }
        reader = BlobReader(gitdir)
        batch = []
        BATCH_SIZE = 200

        def _flush_batch(items):
            if not items:
                return 0
            with conn:
                for fpath, blob_sha, title_idx, body_idx, kind, content in items:
                    old_row = conn.execute(
                        "SELECT rowid FROM doc_rowid WHERE repo=? AND ref=? AND path=?",
                        (repo, ref, fpath),
                    ).fetchone()
                    if old_row:
                        conn.execute("DELETE FROM docs_fts WHERE rowid=?", (old_row[0],))
                    if has_body_raw_col:
                        cur = conn.execute("""
                            INSERT INTO docs_fts(repo, ref, path, title, body, kind, mtime, body_raw)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (repo, ref, fpath, title_idx, body_idx, kind, head_mtime, content))
                    else:
                        cur = conn.execute("""
                            INSERT INTO docs_fts(repo, ref, path, title, body, kind, mtime)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (repo, ref, fpath, title_idx, body_idx, kind, head_mtime))
                    new_rowid = cur.lastrowid
                    conn.execute("""
                        INSERT OR REPLACE INTO doc_rowid(repo, ref, path, rowid)
                        VALUES (?, ?, ?, ?)
                    """, (repo, ref, fpath, new_rowid))
                    conn.execute("""
                        INSERT OR REPLACE INTO file_blobs(repo, ref, path, blob_sha, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                    """, (repo, ref, fpath, blob_sha, now))
            return len(items)

        try:
            for fpath, blob_sha in to_update:
                content = reader.read(blob_sha)
                if content is None:
                    continue
                title = extract_title(fpath, content)
                kind = classify_kind(fpath)
                title_idx = pretokenize(title)
                body_idx = pretokenize(content)
                batch.append((fpath, blob_sha, title_idx, body_idx, kind, content))
                if len(batch) >= BATCH_SIZE:
                    added_or_updated += _flush_batch(batch)
                    batch.clear()
            if batch:
                added_or_updated += _flush_batch(batch)
                batch.clear()
        finally:
            reader.close()

    # 2. 处理已删除文件、默认分支改名残留及记录该仓最新 head_commit（单事务原子提交）
    with conn:
        for dpath in deleted_paths:
            old_row = conn.execute(
                "SELECT rowid FROM doc_rowid WHERE repo=? AND ref=? AND path=?",
                (repo, ref, dpath),
            ).fetchone()
            if old_row:
                conn.execute("DELETE FROM docs_fts WHERE rowid=?", (old_row[0],))
            conn.execute("DELETE FROM doc_rowid WHERE repo=? AND ref=? AND path=?", (repo, ref, dpath))
            conn.execute("DELETE FROM file_blobs WHERE repo=? AND ref=? AND path=?", (repo, ref, dpath))
            deleted += 1

        for sref in stale_refs:
            stale_rows = conn.execute(
                "SELECT rowid FROM doc_rowid WHERE repo=? AND ref=?", (repo, sref)
            ).fetchall()
            for (srowid,) in stale_rows:
                conn.execute("DELETE FROM docs_fts WHERE rowid=?", (srowid,))
            conn.execute("DELETE FROM doc_rowid WHERE repo=? AND ref=?", (repo, sref))
            conn.execute("DELETE FROM file_blobs WHERE repo=? AND ref=?", (repo, sref))
            conn.execute("DELETE FROM repo_heads WHERE repo=? AND ref=?", (repo, sref))

        if head_commit:
            conn.execute(
                "INSERT OR REPLACE INTO repo_heads(repo, ref, head_commit, updated_at) VALUES (?, ?, ?, ?)",
                (repo, ref, head_commit, now)
            )

    if verbose and (added_or_updated > 0 or deleted > 0):
        print(f"[{repo}] 变更: +{added_or_updated}, -{deleted}, 未变: {unchanged}")

    return added_or_updated, deleted, unchanged


def _normalize_repo_pattern(pat):
    p = pat.strip()
    if "/" in p:
        p = p.split("/", 1)[1].strip()
    return p


def _match_repo_any(repo_name, pats):
    rl = repo_name.lower()
    for raw_p in pats:
        p = _normalize_repo_pattern(raw_p).lower()
        if not p:
            continue
        if any(ch in p for ch in "*?["):
            if fnmatch.fnmatchcase(rl, p):
                return True
        elif p in rl:
            return True
    return False


def build_fts_query(raw_query):
    """
    将用户输入转化为安全的 FTS5 MATCH 表达式与用于摘要高亮的词项列表：
    1. 过滤掉分词后纯标点/连字符 token（避免 `docker-compose` 变成 `docker AND - AND compose` 抛语法错）；
    2. 保留末尾带 `*` 的前缀通配语义（如 `dock*` -> `"dock"*`）；
    3. 对每个词项使用双引号包裹（避免 `AND/OR/NOT` 保留字或特殊字符导致语法异常）。
    """
    q = (raw_query or "").strip()
    if not q:
        return '""', []

    # 识别形如 word* 的前缀查询词
    prefix_words = set(re.findall(r'([A-Za-z0-9_\u4e00-\u9fff]+)\*', q))
    raw_tokens = pretokenize(q).split()
    clean_tokens = []
    fts_parts = []
    for tok in raw_tokens:
        # 仅保留含字母/数字/中日韩文字的词项，剔除孤立的 `_` `-` `.` `/` `:` `*` 等纯符号（FTS5 unicode61 将 `_` 视为分隔符）
        if not re.search(r'[A-Za-z0-9\u4e00-\u9fff]', tok):
            continue
        clean_tokens.append(tok)
        esc = tok.replace('"', '""')
        if tok in prefix_words and q.endswith(tok + "*") or (tok + "*") in q:
            fts_parts.append(f'"{esc}"*')
        else:
            fts_parts.append(f'"{esc}"')

    if not fts_parts:
        esc = q.replace('"', '""')
        return f'"{esc}"', [q]

    return ' AND '.join(fts_parts), clean_tokens


def build_index(mirror_root, owners=None, repo_filter=None, exts=DEFAULT_EXTS, full=False, verbose=True):
    db_path = get_db_path(mirror_root)
    
    with IndexLock(mirror_root):
        if full and not repo_filter:
            for suffix in ("", "-wal", "-shm"):
                p = db_path + suffix
                if os.path.exists(p):
                    os.remove(p)

        conn = sqlite3.connect(db_path)
        init_db(conn)

        # 旧库升级与一致性自愈：一次性为 docs_fts 补建 rowid 映射，并清理可能存在的历史重复行/孤儿行
        n_map = conn.execute("SELECT COUNT(*) FROM doc_rowid").fetchone()[0]
        n_docs = conn.execute("SELECT COUNT(*) FROM docs_fts").fetchone()[0]
        if (n_map == 0 and n_docs > 0) or (n_map != n_docs):
            rows = conn.execute("SELECT rowid, repo, ref, path FROM docs_fts ORDER BY rowid ASC").fetchall()
            latest = {}
            dup_rowids = []
            for r_id, r_repo, r_ref, r_path in rows:
                key = (r_repo, r_ref, r_path)
                if key in latest:
                    dup_rowids.append(latest[key])
                latest[key] = r_id
            with conn:
                for d_id in dup_rowids:
                    conn.execute("DELETE FROM docs_fts WHERE rowid=?", (d_id,))
                conn.execute("DELETE FROM doc_rowid")
                conn.executemany(
                    "INSERT INTO doc_rowid(repo, ref, path, rowid) VALUES (?, ?, ?, ?)",
                    [(k[0], k[1], k[2], v) for k, v in latest.items()],
                )
            if verbose:
                print(f"[migrate] 已同步 {len(latest)} 篇文档的 rowid 映射（清理重复行 {len(dup_rowids)} 条）")

        if not owners:
            owners = get_configured_owners()
        elif isinstance(owners, str):
            owners = [o.strip() for o in owners.split(",") if o.strip()]

        t0 = time.time()
        op_name = "index_build" if full else "index_incremental"
        total_mod = 0
        total_del = 0
        total_same = 0
        total_repos_scanned = 0

        with IoTracker(op_name) as tracker:
            for owner in owners:
                mirror_dir = os.path.join(mirror_root, owner)
                if not os.path.isdir(mirror_dir):
                    continue
                all_r = list_repos(mirror_dir)
                if repo_filter:
                    pats = [p for p in repo_filter.split(",") if p.strip()]
                    repos = [r for r in all_r if _match_repo_any(r, pats)]
                else:
                    repos = all_r

                total_repos_scanned += len(repos)
                for r in repos:
                    mod, dele, same = index_repo(conn, mirror_dir, r, exts, verbose=verbose, force=full)
                    total_mod += mod
                    total_del += dele
                    total_same += same

        dur = time.time() - t0
        judgment = evaluate_perf_budget(
            op_name,
            tracker.metrics,
            {
                "repos": total_repos_scanned,
                "updated": total_mod,
                "deleted": total_del,
                "unchanged": total_same,
                "full": bool(full),
            },
        )
        record_perf_event(mirror_root, judgment, quiet=not verbose)
        if verbose:
            m = judgment["metrics"]
            print(
                f"索引完成: 扫描 {total_repos_scanned} 仓 | 更新/新增 {total_mod} 篇 | 删除 {total_del} 篇 | "
                f"未变 {total_same} 篇 | 耗时 {dur:.2f}s (cpu={m.get('cpu_ms', 0):.1f}ms, "
                f"rchar={m.get('rchar', 0)}, write={m.get('write_bytes', 0)}, status={judgment['status']})"
            )

        conn.close()
        return total_mod, total_del, total_same


def make_snippet(raw, tokens, width=120):
    """从原文截取命中词附近的片段并加【】高亮（替代 FTS snippet，避免分词空格影响可读性）。"""
    if not raw:
        return ""
    text = raw.replace("\n", " ")
    low = text.lower()
    pos = -1
    hit_tok = ""
    for t in tokens:
        if not t:
            continue
        i = low.find(t.lower())
        if i >= 0 and (pos < 0 or i < pos):
            pos, hit_tok = i, t
    if pos < 0:
        return text[:width].strip()
    start = max(0, pos - width // 3)
    end = min(len(text), pos + len(hit_tok) + width)
    seg = text[start:end]
    # 高亮所有命中 token
    for t in tokens:
        if not t:
            continue
        seg = re.sub(re.escape(t), lambda m: "【" + m.group(0) + "】", seg, count=1, flags=re.IGNORECASE)
    return ("..." if start > 0 else "") + seg.strip() + ("..." if end < len(text) else "")


def search_fts(mirror_root, query, repo=None, limit=20, kind=None):
    db_path = get_db_path(mirror_root)
    if not os.path.exists(db_path):
        return {"total": 0, "results": [], "error": "索引尚未建立，请先执行 indexer.py 进行索引。"}

    conn = sqlite3.connect(db_path)
    # 2026-10-21 加固：默认 busy_timeout=0 会让并发写入（每小时 indexer 重建窗口）时
    # 立即抛 "database is locked"。这里给一个有界等待（2000ms），避免瞬时 BUSY 误判，
    # 也绝不无限期卡住搜索（有界上限=2s，超时按未命中返回，不会拖出 600s 级超时）。
    conn.execute("PRAGMA busy_timeout=2000;")
    cursor = conn.cursor()

    # 清洗/规范化 query 为 FTS5 合法表达式
    raw_query = (query or "").strip()
    clean_query, qtok = build_fts_query(raw_query)
    #
    # 2026-10-21 批次1（索引质量）：
    #  a) bm25 列权重改为 (repo,ref,path,title,body)=(0,0,2,5,1)：
    #     FTS5 默认 [5,1,1,1,1] 的 5 落在 repo 列 → 仓名主导排序，
    #     导致「搜中文词却顶出一堆 fleet-config-inventory 归档」。现压低 repo/ref、
    #     抬高 title(5)/path(2)，让正文与标题命中优先。
    #  b) backup/ 归档副本不硬删（那会丢掉各主机配置档案，正是常要查的内容），
    #     改为排序降权：非 backup 优先，backup 排后但保留召回。
    #  c) 按 blob_sha 内容去重：同一 blob 在 backup 与非 backup 路径重复时，
    #     只保留排序更靠前的那条（通常是非 backup 正册）。
    #  d) 批次2 追加 mtime 新鲜度：同相关度下更新的文档略优先（-mtime 归一化很小量级，
    #     不压过 bm25 主体排序）。
    # 2026-10-21 修复（schema 漂移导致搜索整体报错 no such column: docs_fts.kind）：
    # 旧库只有 5 列（repo/ref/path/title/body），新代码按 7 列（+kind/mtime）查询。
    # `CREATE VIRTUAL TABLE IF NOT EXISTS` 对已存在的表是**空操作**，加不上新列，
    # 于是磁盘库没重建前搜索直接抛错。这里探测实际列并按可用列拼 SQL，
    # 使旧库（5 列）与新库（7 列）都能搜——重建/切换期间搜索不中断。
    cols = {r[1] for r in cursor.execute("PRAGMA table_info(docs_fts)")}
    has_kind = "kind" in cols
    has_mtime = "mtime" in cols
    has_body_raw = "body_raw" in cols

    fetch_n = max(limit * 4, limit + 20)
    if fetch_n > 500:
        fetch_n = 500
    sel_snip = ("docs_fts.body_raw" if has_body_raw
                else "snippet(docs_fts, 4, '【', '】', '...', 32)")
    sel_kind = "docs_fts.kind AS kind" if has_kind else "NULL AS kind"
    sel_mtime = "docs_fts.mtime AS mtime" if has_mtime else "NULL AS mtime"
    sql = """
        SELECT
            docs_fts.repo, docs_fts.ref, docs_fts.path, docs_fts.title,
            %s AS snip,
            bm25(docs_fts, 0, 0, 2, 5, 1) AS rank,
            fb.blob_sha AS blob_sha,
            %s,
            %s,
            CASE WHEN docs_fts.path LIKE 'backup/%%' THEN 1 ELSE 0 END AS is_backup
        FROM docs_fts
        LEFT JOIN file_blobs fb
          ON fb.repo = docs_fts.repo
         AND fb.ref = docs_fts.ref
         AND fb.path = docs_fts.path
        WHERE docs_fts MATCH ?
    """ % (sel_snip, sel_kind, sel_mtime)
    params = [clean_query]
    if repo:
        repo_pats = [_normalize_repo_pattern(p) for p in str(repo).split(",") if _normalize_repo_pattern(p)]
        if repo_pats:
            like_clauses = []
            for rp in repo_pats:
                # 将 glob 通配符 * ? 转换为 SQL LIKE 的 % _，并支持子串匹配
                sql_pat = rp.replace("*", "%").replace("?", "_")
                if not sql_pat.startswith("%"):
                    sql_pat = "%" + sql_pat
                if not sql_pat.endswith("%"):
                    sql_pat = sql_pat + "%"
                while "%%" in sql_pat:
                    sql_pat = sql_pat.replace("%%", "%")
                like_clauses.append("docs_fts.repo LIKE ?")
                params.append(sql_pat)
            sql += " AND (" + " OR ".join(like_clauses) + ")"
    if kind:
        # kind 支持逗号分隔多值，如 kind='doc,config'；旧库无该列时忽略过滤（不报错）
        kinds = [k.strip() for k in str(kind).split(',') if k.strip()]
        if kinds and has_kind:
            sql += " AND docs_fts.kind IN (%s)" % ','.join('?' * len(kinds))
            params.extend(kinds)

    # 非 backup 优先（is_backup=0 在前），其次按加权 bm25 升序（越小越相关），
    # 最后 mtime 降序（新的优先，作为同分时的次序信号）；旧库无 mtime 列时省略该键。
    if has_mtime:
        sql += " ORDER BY is_backup ASC, rank ASC, mtime DESC LIMIT ?"
    else:
        sql += " ORDER BY is_backup ASC, rank ASC LIMIT ?"
    params.append(fetch_n)

    with IoTracker("fts_search") as tracker:
        try:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        except sqlite3.OperationalError:
            # 如果 MATCH 语法由于特殊符号解析失败，降级为对原始查询词加引号字面匹配
            escaped = '"' + raw_query.replace('"', '""') + '"'
            params[0] = escaped
            try:
                cursor.execute(sql, params)
                rows = cursor.fetchall()
            except Exception as e:
                conn.close()
                return {"total": 0, "results": [], "error": f"FTS 查询错误: {e}"}

    results = []
    seen_blobs = set()
    q_tokens = [t for t in qtok if t]
    for r, ref, p, title, snip, rank, blob_sha, kind_v, mtime_v, is_backup in rows:
        # 内容级去重：同一 blob_sha 只保留首次（即排序最靠前、通常非 backup）的一条
        if blob_sha:
            if blob_sha in seen_blobs:
                continue
            seen_blobs.add(blob_sha)
        results.append({
            "repo": r,
            "ref": ref,
            "path": p,
            "title": title,
            "snippet": (make_snippet(snip, q_tokens) if has_body_raw
                        else snip.replace("\n", " ").strip()),
            "score": round(rank, 4),
            "kind": kind_v,
            "is_backup": bool(is_backup),
        })
        if len(results) >= limit:
            break

    conn.close()
    judgment = evaluate_perf_budget(
        "fts_search",
        tracker.metrics,
        {"query": raw_query[:64], "hits": len(results), "repo": repo or "", "kind": kind or ""},
    )
    record_perf_event(mirror_root, judgment)
    return {
        "total": len(results),
        "results": results,
        "perf": {
            "status": judgment["status"],
            "elapsed_ms": tracker.metrics.get("elapsed_ms", 0.0),
            "rchar": tracker.metrics.get("rchar", 0),
            "read_bytes": tracker.metrics.get("read_bytes", 0),
        },
    }


def get_stats(mirror_root):
    db_path = get_db_path(mirror_root)
    if not os.path.exists(db_path):
        return "索引库不存在。"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM file_blobs;")
    cnt_files = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT repo) FROM file_blobs;")
    cnt_repos = cur.fetchone()[0]
    db_size = os.path.getsize(db_path) / (1024 * 1024)
    conn.close()
    return f"索引库状态: 共索引 {cnt_repos} 个仓库, {cnt_files} 篇文档 | 库文件大小: {db_size:.2f} MB"


def _percentile(sorted_vals, pct):
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, int(round((pct / 100.0) * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


def get_perf_stats(mirror_root, limit=100, as_json=False):
    log_path = get_perf_log_path(mirror_root)
    events = []
    if os.path.exists(log_path):
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f.readlines()[-limit:]:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except Exception:
                        pass
        except Exception:
            pass
    counts = {"OK": 0, "WARN": 0, "CRITICAL": 0}
    by_op = {}
    anomalies = []
    for ev in events:
        st = ev.get("status", "OK")
        counts[st] = counts.get(st, 0) + 1
        op = ev.get("op", "unknown")
        m = ev.get("metrics", {})
        slot = by_op.setdefault(op, {"elapsed_ms": [], "rchar": [], "read_bytes": [], "write_bytes": []})
        slot["elapsed_ms"].append(float(m.get("elapsed_ms", 0.0)))
        slot["rchar"].append(int(m.get("rchar", 0)))
        slot["read_bytes"].append(int(m.get("read_bytes", 0)))
        slot["write_bytes"].append(int(m.get("write_bytes", 0)))
        if st in ("WARN", "CRITICAL"):
            anomalies.append(ev)

    summary_ops = {}
    for op, vals in by_op.items():
        el = sorted(vals["elapsed_ms"])
        rc = sorted(vals["rchar"])
        rb = sorted(vals["read_bytes"])
        wb = sorted(vals["write_bytes"])
        summary_ops[op] = {
            "count": len(el),
            "p50_ms": round(_percentile(el, 50), 2),
            "p95_ms": round(_percentile(el, 95), 2),
            "max_ms": round(max(el) if el else 0.0, 2),
            "max_rchar": max(rc) if rc else 0,
            "max_read_bytes": max(rb) if rb else 0,
            "max_write_bytes": max(wb) if wb else 0,
        }

    payload = {
        "log_path": log_path,
        "total_events": len(events),
        "status_counts": counts,
        "operations": summary_ops,
        "recent_anomalies": anomalies[-10:],
    }
    if as_json:
        return json.dumps(payload, ensure_ascii=False, indent=2)
    lines = [
        f"=== 性能与磁盘 I/O 监测统计 (最近 {len(events)} 次操作) ===",
        f"状态分布: OK={counts.get('OK', 0)} | WARN={counts.get('WARN', 0)} | CRITICAL={counts.get('CRITICAL', 0)}",
    ]
    for op, s in sorted(summary_ops.items()):
        lines.append(
            f"  - {op}: {s['count']} 次 | P50={s['p50_ms']}ms P95={s['p95_ms']}ms Max={s['max_ms']}ms | "
            f"Max rchar={s['max_rchar']}B read={s['max_read_bytes']}B write={s['max_write_bytes']}B"
        )
    if anomalies:
        lines.append("最近告警记录:")
        for a in anomalies[-5:]:
            m = a.get("metrics", {})
            lines.append(
                f"  [{a.get('status')}] op={a.get('op')} elapsed={m.get('elapsed_ms', 0)}ms "
                f"rchar={m.get('rchar', 0)} reasons={'; '.join(a.get('reasons', []))}"
            )
    return "\n".join(lines)


def inspect_dsh_sessions_health(dsh_home=None):
    """
    预检测与运行期检测宿主 DSH 会话目录与 session-query.sqlite 状态：
      1. 统计 v1 / v2 / v3 / v4 会话目录数（非 v4 会导致 DSH session_search 触发全量 historicalCorpusRevision 冷迁移风暴）；
      2. 检查 session-query.sqlite 的 persisted_sessions 中是否存在带 64 位 corpusRevision 后缀的记录（length(revision) > 100）及 WAL 膨胀。
    """
    raw_candidates = [dsh_home] if dsh_home else [
        os.environ.get("DSH_HOME"),
        os.path.expanduser("~/.dsh"),
        "/var/lib/dsh/.dsh",
    ]
    resolved_home = None
    for c in raw_candidates:
        if not c:
            continue
        expanded = os.path.expanduser(c)
        if os.path.isdir(os.path.join(expanded, "sessions")):
            resolved_home = expanded
            break
    if not resolved_home:
        return {
            "found": False,
            "dsh_home": dsh_home or "",
            "status": "OK",
            "version_counts": {},
            "legacy_sessions": 0,
            "long_revisions": 0,
            "reasons": [],
            "hints": [],
        }

    sessions_dir = os.path.join(resolved_home, "sessions")
    v_counts = {"v4": 0, "v3": 0, "v2": 0, "v1": 0}
    for p in os.listdir(sessions_dir):
        pp = os.path.join(sessions_dir, p)
        if not os.path.isdir(pp):
            continue
        for s in os.listdir(pp):
            sp = os.path.join(pp, s)
            if not os.path.isdir(sp):
                continue
            files = set(os.listdir(sp))
            if "session.v4.jsonl.zstd" in files or "session.v4.jsonl" in files:
                v_counts["v4"] += 1
            elif "session.v3.jsonl.zstd" in files or "session.v3.jsonl" in files:
                v_counts["v3"] += 1
            elif "session.v2.jsonl.zstd" in files or "session.v2.jsonl" in files:
                v_counts["v2"] += 1
            elif "session.jsonl.zstd" in files or "session.jsonl" in files:
                v_counts["v1"] += 1

    legacy_cnt = v_counts["v1"] + v_counts["v2"] + v_counts["v3"]
    sq_db = os.path.join(resolved_home, "session-query.sqlite")
    long_revs = 0
    persisted_cnt = 0
    sq_wal_mb = 0.0
    if os.path.exists(sq_db):
        if os.path.exists(sq_db + "-wal"):
            sq_wal_mb = round(os.path.getsize(sq_db + "-wal") / (1024 * 1024), 2)
        try:
            con = sqlite3.connect(f"file:{sq_db}?mode=ro", uri=True, timeout=2.0)
            persisted_cnt = con.execute("SELECT COUNT(*) FROM persisted_sessions").fetchone()[0]
            long_revs = con.execute(
                "SELECT COUNT(*) FROM persisted_sessions WHERE (length(revision) - length(replace(revision, ':', ''))) >= 5 OR length(revision) > 100"
            ).fetchone()[0]
            con.close()
        except Exception:
            pass

    status = "OK"
    reasons = []
    hints = []
    if legacy_cnt >= 50 or long_revs >= 50:
        status = "CRITICAL"
        reasons.append(f"DSH 会话目录含 {legacy_cnt} 个旧版 (<v4) 会话, session-query.sqlite 含 {long_revs} 条长 revision")
        hints.append(f"请运行: node scripts/dsh-session-health.mjs --fix --dsh-home {resolved_home}")
    elif legacy_cnt > 0 or long_revs > 0:
        status = "WARN"
        reasons.append(f"DSH 会话目录含 {legacy_cnt} 个旧版 (<v4) 会话, session-query.sqlite 含 {long_revs} 条长 revision")
        hints.append(f"建议运行: node scripts/dsh-session-health.mjs --fix --dsh-home {resolved_home}")

    return {
        "found": True,
        "dsh_home": resolved_home,
        "status": status,
        "version_counts": v_counts,
        "legacy_sessions": legacy_cnt,
        "persisted_sessions": persisted_cnt,
        "long_revisions": long_revs,
        "session_query_wal_mb": sq_wal_mb,
        "reasons": reasons,
        "hints": hints,
    }


def run_doctor(mirror_root, dsh_home=None, as_json=False):
    """
    综合健康与 I/O 诊断 (--doctor)：
      1. 检查 gitea-fts.sqlite 四表 1:1 对齐 (file_blobs / docs_fts / docs_fts_data_docsize / doc_rowid) 与 WAL 大小；
      2. 执行带 IoTracker 的 doc_rowid 索引删除定位探针与 FTS5 检索探针；
      3. 检查宿主 DSH 会话格式版本分布 (<v4 预警) 与 session-query.sqlite 状态；
      4. 汇总近期 perf-metrics.jsonl 异常。
    """
    db_path = get_db_path(mirror_root)
    fts_health = {
        "exists": os.path.exists(db_path),
        "db_path": db_path,
        "status": "OK",
        "counts": {},
        "aligned_1to1": False,
        "wal_mb": 0.0,
        "probe_metrics": {},
        "reasons": [],
        "hints": [],
    }
    if os.path.exists(db_path):
        if os.path.exists(db_path + "-wal"):
            fts_health["wal_mb"] = round(os.path.getsize(db_path + "-wal") / (1024 * 1024), 2)
        try:
            con = sqlite3.connect(db_path, timeout=2.0)
            c_blobs = con.execute("SELECT COUNT(*) FROM file_blobs").fetchone()[0]
            c_fts = con.execute("SELECT COUNT(*) FROM docs_fts").fetchone()[0]
            c_docsize = con.execute("SELECT COUNT(*) FROM docs_fts_data WHERE id > 10").fetchone()[0]
            # 精确统计 docs_fts_docsize 行数
            c_docsize = con.execute("SELECT COUNT(*) FROM docs_fts_docsize").fetchone()[0]
            c_rowid = con.execute("SELECT COUNT(*) FROM doc_rowid").fetchone()[0]
            fts_health["counts"] = {
                "file_blobs": c_blobs,
                "docs_fts": c_fts,
                "docs_fts_docsize": c_docsize,
                "doc_rowid": c_rowid,
            }
            fts_health["aligned_1to1"] = (c_blobs == c_fts == c_docsize == c_rowid)
            if not fts_health["aligned_1to1"]:
                fts_health["status"] = "CRITICAL"
                fts_health["reasons"].append(f"FTS5 四表行数不对齐: {fts_health['counts']}")
                fts_health["hints"].append("请运行 gts --reindex 触发自动对齐或 --full 全量重建")

            with IoTracker("doctor_probe") as probe_tracker:
                con.execute("SELECT rowid FROM doc_rowid WHERE repo='__doctor_probe__' AND ref='main' AND path='README.md'").fetchone()
                if c_fts > 0:
                    con.execute("SELECT rowid FROM docs_fts WHERE docs_fts MATCH '\"dsh\"' LIMIT 1").fetchall()
            con.close()
            probe_j = evaluate_perf_budget("doctor_probe", probe_tracker.metrics)
            fts_health["probe_metrics"] = probe_tracker.metrics
            if probe_j["status"] != "OK" and fts_health["status"] == "OK":
                fts_health["status"] = probe_j["status"]
                fts_health["reasons"].extend(probe_j["reasons"])
        except Exception as e:
            fts_health["status"] = "CRITICAL"
            fts_health["reasons"].append(f"索引库检查异常: {e}")

    dsh_health = inspect_dsh_sessions_health(dsh_home)
    order = {"OK": 0, "WARN": 1, "CRITICAL": 2}
    overall = "OK"
    for st in (fts_health["status"], dsh_health["status"]):
        if order.get(st, 0) > order.get(overall, 0):
            overall = st

    report = {
        "overall_status": overall,
        "fts_index": fts_health,
        "dsh_sessions": dsh_health,
    }
    if as_json:
        return json.dumps(report, ensure_ascii=False, indent=2)

    lines = [
        f"=== dsh-gitea-search 健康与 I/O 诊断报告 (Overall: {overall}) ===",
        f"[1] FTS5 索引库 ({fts_health['status']}): {db_path}",
    ]
    if fts_health["exists"]:
        pm = fts_health.get("probe_metrics", {})
        lines.append(
            f"    四表计数: {fts_health['counts']} | 1:1 对齐: {fts_health['aligned_1to1']} | WAL: {fts_health['wal_mb']} MB"
        )
        lines.append(
            f"    探针耗时: {pm.get('elapsed_ms', 0)}ms | CPU: {pm.get('cpu_ms', 0)}ms | "
            f"rchar: {pm.get('rchar', 0)}B | read_bytes: {pm.get('read_bytes', 0)}B"
        )
    else:
        lines.append("    (索引库尚未创建)")
    for r in fts_health["reasons"]:
        lines.append(f"    ! {r}")
    for h in fts_health["hints"]:
        lines.append(f"    -> {h}")

    lines.append(f"[2] 宿主 DSH 会话环境 ({dsh_health['status']}): {dsh_health.get('dsh_home') or '未检测到'}")
    if dsh_health["found"]:
        lines.append(
            f"    会话版本分布: {dsh_health['version_counts']} | 旧版 (<v4): {dsh_health['legacy_sessions']} | "
            f"persisted_sessions: {dsh_health['persisted_sessions']} (长 revision: {dsh_health['long_revisions']})"
        )
    for r in dsh_health["reasons"]:
        lines.append(f"    ! {r}")
    for h in dsh_health["hints"]:
        lines.append(f"    -> {h}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="gitea 全文文档/会话 SQLite FTS5 索引工具")
    parser.add_argument("--build", action="store_true", help="构建或增量更新索引")
    parser.add_argument("--full", action="store_true", help="强制全量重新构建")
    parser.add_argument("--stats", action="store_true", help="查看索引库状态统计")
    parser.add_argument("--perf-stats", action="store_true", help="查看性能与磁盘 I/O 监测统计")
    parser.add_argument("--doctor", action="store_true", help="运行索引与宿主 DSH 会话健康及 I/O 诊断")
    parser.add_argument("--dsh-home", help="指定宿主 .dsh 目录路径（供 --doctor 检查）")
    parser.add_argument("-r", "--repo", help="限定仓库名/通配符")
    parser.add_argument("-q", "--query", help="执行全文检索测试")
    parser.add_argument("-n", "--limit", type=int, default=15, help="检索返回上限")
    parser.add_argument("--kind", help="限定内容类型（doc,session,config,script）")
    parser.add_argument("-o", "--owner", help="指定 owner（支持逗号分隔多个，默认取环境配置）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    args = parser.parse_args()

    mirror_root = ROOT_DEFAULT
    owners = get_configured_owners(args.owner)

    if args.doctor:
        print(run_doctor(mirror_root, dsh_home=args.dsh_home, as_json=args.json))
        return

    if args.perf_stats:
        print(get_perf_stats(mirror_root, limit=args.limit or 100, as_json=args.json))
        return

    if args.stats:
        print(get_stats(mirror_root))
        return

    if args.query:
        res = search_fts(mirror_root, args.query, repo=args.repo, limit=args.limit, kind=args.kind)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            if "error" in res:
                print(f"错误: {res['error']}")
                return
            print(f"命中 {res['total']} 条文档:")
            for item in res["results"]:
                print(f"[{item['repo']}@{item['ref']}] {item['path']} ({item['title']})")
                print(f"  摘要: {item['snippet']}")
                print(f"  相关度得分: {item['score']}\n")
        return

    if args.build or len(sys.argv) == 1:
        build_index(mirror_root, owners=owners, repo_filter=args.repo, full=args.full, verbose=True)


if __name__ == "__main__":
    main()
