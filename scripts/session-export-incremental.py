#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
session-export-incremental.py —— DSH 会话增量导出为 Markdown（供 Gitea 镜像 → FTS5 索引）

特性：
  1. **支持 v1~v4**：按会话目录里最高版本的 session.vN.jsonl.zstd 选取。
  2. **增量导出**：按「工作区指纹 = 排序后的 (会话目录名, zstd mtime)」比对状态文件，
     指纹未变的会话/工作区跳过，避免每轮全量重写。
  3. **活跃会话跳过**：检测 session.lock 是否被进程持有（flock 非阻塞探测），
     正在写入的活跃会话跳过且不写指纹，下一轮自动补导。
  4. **只导真用户消息**：type=="user/message" 且 data.source.kind=="user" 才导出正文；
     系统注入消息（runtime-context / agent-instructions / skill-catalog）不导正文。
  5. **自动敏感信息脱敏（REDACT）**。

用法：
  python3 session-export-incremental.py                     # 默认导出 ~/.dsh/sessions
  python3 session-export-incremental.py --instance default --src ~/.dsh/sessions --out <dir>
  python3 session-export-incremental.py --force             # 忽略指纹，全量重导（首次/修复用）
  python3 session-export-incremental.py --dry-run           # 只报告会做什么，不写文件
退出码：0=成功；2=参数/环境错误。
"""
import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

_CUR_DIR = os.path.dirname(os.path.realpath(__file__))
for _p in (os.path.join(_CUR_DIR, "..", "lib"), os.path.join(_CUR_DIR, "..", "gitea-search"), _CUR_DIR):
    _np = os.path.normpath(_p)
    if _np not in sys.path and os.path.isdir(_np):
        sys.path.insert(0, _np)
try:
    import indexer as _indexer
except ImportError:
    _indexer = None

REDACT = [
    (r'(?i)(api[_-]?key|token|secret|password)\s*[=:]\s*\S+', r'\1=[REDACTED]'),
    (r'(?i)Authorization:\s*(Bearer|token)\s+\S+', r'Authorization: \1 [REDACTED]'),
    (r'(?i)([\w./-]*)\.admin-token', r'\1/[ADMIN_TOKEN]'),
    (r'token=[A-Za-z0-9_-]{20,}', 'token=[REDACTED]'),
    (r'-----BEGIN.*PRIVATE KEY-----.*?-----END.*PRIVATE KEY-----', '[SSH_KEY_REDACTED]'),
    (r'-----BEGIN [A-Z0-9 ]*KEY-----', '[KEY_HEADER_REDACTED]'),
    (r'-----END [A-Z0-9 ]*KEY-----', '[KEY_FOOTER_REDACTED]'),
    # 2026-10-22 修：原规则写成 `ssh-rsAAAA`（少空格）永远匹配不到。
    (r'ssh-rsa AAAA[A-Za-z0-9+/=]+', 'ssh-rsa [REDACTED]'),
    # 裸 Token 签名（涵盖 GitHub PAT、经典 Token、GitLab 等）
    (r'gh[pousr]_[A-Za-z0-9]{20,}', '[GITHUB_TOKEN_REDACTED]'),
    (r'github_pat_[A-Za-z0-9_]{20,}', '[GITHUB_PAT_REDACTED]'),
    (r'glpat-[A-Za-z0-9_-]{20,}', '[GITLAB_TOKEN_REDACTED]'),
]

# 系统注入消息的 source.kind（不导正文，只留一行占位）
INJECTION_KINDS = {
    'runtime-context',
    'agent-instructions',
    'skill-catalog',
    'system',
}

STATE_NAME = '.export-state.json'


def redact(text):
    for pat, repl in REDACT:
        text = re.sub(pat, repl, text, flags=re.DOTALL)
    return text


def decomp(path):
    """解压 zstd；路径可能以 -- 开头（工作区目录名），必须加 '--' 防被当选项。"""
    try:
        return subprocess.check_output(
            ['zstd', '-dc', '--', path], stderr=subprocess.DEVNULL
        ).decode('utf-8', 'replace')
    except Exception:
        return ''


def pick_session_file(sdir):
    """按最高版本选 session.vN.jsonl.zstd（v1=session.jsonl.zstd）。"""
    cands = []
    for f in os.listdir(sdir):
        m = re.match(r'^session(?:\.v(\d+))?\.jsonl\.zstd$', f)
        if not m:
            continue
        ver = int(m.group(1) or 1)
        p = os.path.join(sdir, f)
        try:
            if os.path.getsize(p) > 0:
                cands.append((ver, p))
        except OSError:
            continue
    if not cands:
        return None
    cands.sort(key=lambda x: x[0])
    return cands[-1][1]


def is_active(sdir):
    """会话是否正在写入。

    DSH 结束会话后可能保留 session.lock 文件，若仅判 `os.path.exists(session.lock)`
    会将已结束会话的陈旧锁文件误判为「活跃」导致跳过导出。
    正确判据 = 锁文件是否真被进程持有（flock 拿不到才算活跃）：用 LOCK_EX|LOCK_NB
    试获取，成功即陈旧（顺手 LOCK_UN 释放，不改变文件），EWOULDBLOCK 才是真活跃。
    """
    lock = os.path.join(sdir, 'session.lock')
    if not os.path.exists(lock):
        return False
    try:
        import fcntl
    except ImportError:            # 非 POSIX：退回旧行为（保守）
        return True
    try:
        fd = os.open(lock, os.O_RDONLY)
    except OSError:
        return False               # 打不开（权限/竞态）⇒ 不阻塞导出
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True            # 被持有 ⇒ 真活跃
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False           # 能拿到 ⇒ 陈旧锁
    finally:
        os.close(fd)


def msg_text(o):
    """从一条 event 提取 (role, text)；非用户/助手正文返回 None。"""
    t = o.get('type', '')
    data = o.get('data', {}) or {}
    # 真用户消息：type=user/message 且 source.kind=user
    if t == 'user/message':
        src = data.get('source', {}) or {}
        kind = src.get('kind', 'user')
        if kind != 'user':
            return None  # 系统注入，不导正文
        text = data.get('text')
        if text is None:
            c = data.get('content')
            if c is None:
                msg = data.get('message')
                if isinstance(msg, dict):
                    c = msg.get('content')
                    if c is None:
                        text = msg.get('text')
                elif isinstance(msg, str):
                    text = msg
            if isinstance(c, list):
                text = ' '.join(
                    x.get('text', '') for x in c if isinstance(x, dict)
                )
            elif isinstance(c, str):
                text = c
        return ('user', text) if text else None
    if t == 'assistant/message':
        text = data.get('text')
        if text is None:
            c = data.get('content')
            if c is None:
                msg = data.get('message')
                if isinstance(msg, dict):
                    c = msg.get('content')
                    if c is None:
                        text = msg.get('text')
            if isinstance(c, list):
                # 仅提取最终文本块（跳过 reasoning 思维链块，避免导出文件体积膨胀 3~5 倍）
                parts = [
                    x.get('text', '')
                    for x in c
                    if isinstance(x, dict) and x.get('type') in (None, 'text') and x.get('text')
                ]
                text = '\n\n'.join(parts)
            elif isinstance(c, str):
                text = c
        return ('assistant', text) if text else None
    return None


def normalize_sid(raw_id):
    """统一归一化会话 ID：剥除可选 session- 前缀后保留 13 字符（8+1+4=12 位 hex，48-bit 熵），消除 4-hex 碰撞与标题/文件名不一致。"""
    s = os.path.basename(str(raw_id or '')).strip()
    if s.startswith('session-'):
        s = s[len('session-'):]
    return f'session-{s[:13]}' if s else 'session-unknown'


def export_single_session(sd, z, mt, out_path, instance, workspace_name, dry_run):
    """把单个会话导出为一个独立的 .md 文件（细粒度索引模式）。"""
    content = decomp(z)
    if not content:
        return 0
    raw_lines = content.strip().split('\n')
    sid = normalize_sid(sd)
    created, cwd = '', ''
    try:
        meta = json.loads(raw_lines[0])
        sid = normalize_sid(meta.get('id') or sd)
        created = meta.get('createdAt', '')
        cwd = meta.get('cwd', '')
        if isinstance(created, (int, float)) and created > 1e12:
            created = datetime.fromtimestamp(created / 1000).isoformat()[:19]
    except Exception:
        pass

    lines = []
    lines.append(f'# DSH 会话: {sid}\n\n')
    lines.append(f'> 工作区: {workspace_name} | 实例: {instance} | 导出时间: {datetime.now().isoformat()[:19]}\n')
    lines.append(f'> 创建时间: {created} | 工作目录: {cwd}\n')
    lines.append('> ⚠️ 完整版：仅含真用户消息与助手正文（系统注入不导正文）\n\n---\n\n')

    mc = 0
    for line in raw_lines[1:]:
        try:
            o = json.loads(line)
        except Exception:
            continue
        got = msg_text(o)
        if not got:
            continue
        role, text = got
        if isinstance(text, list):
            text = ' '.join(str(x) for x in text)
        lines.append(f'**{role}**:\n{redact(str(text))}\n\n')
        mc += 1

    lines.append(f'*共 {mc} 条消息*\n')
    body = ''.join(lines)
    if dry_run:
        print(f'  [dry-run] 会写 {out_path}（{mc} 条消息, {len(body)} 字节）')
        return mc
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp = out_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(body)
    os.replace(tmp, out_path)
    return mc


def export_workspace(pd, out_path, instance, sessions, dry_run):
    """把某个工作区（--xxx--）的会话写成一个 .md。sessions=[(sid_dir, zpath, mtime)]"""
    lines = []
    lines.append(f'# DSH 会话完整导出 ({instance}): {os.path.basename(pd).strip("-")}\n')
    lines.append(
        f'> 导出时间: {datetime.now().isoformat()[:19]} | 会话数: {len(sessions)}\n'
    )
    lines.append('> ⚠️ 完整版：无截断；仅含真用户消息与助手正文（系统注入不导正文）\n\n---\n\n')
    total_msgs = 0
    for si, (sd, z, _mt) in enumerate(sessions, 1):
        content = decomp(z)
        if not content:
            continue
        raw_lines = content.strip().split('\n')
        sid, created, cwd = normalize_sid(sd), '', ''
        try:
            meta = json.loads(raw_lines[0])
            sid = normalize_sid(meta.get('id') or sd)
            created = meta.get('createdAt', '')
            cwd = meta.get('cwd', '')
            if isinstance(created, (int, float)) and created > 1e12:
                created = datetime.fromtimestamp(created / 1000).isoformat()[:19]
        except Exception:
            pass
        lines.append(f'## 会话 {si}: `{sid}`\n\n')
        lines.append(f'*创建: {created} | 工作目录: {cwd}*\n\n')
        mc = 0
        for line in raw_lines[1:]:
            try:
                o = json.loads(line)
            except Exception:
                continue
            got = msg_text(o)
            if not got:
                continue
            role, text = got
            if isinstance(text, list):
                text = ' '.join(str(x) for x in text)
            lines.append(f'**{role}**:\n{redact(str(text))}\n\n')
            mc += 1
        lines.append(f'*共 {mc} 条消息*\n\n---\n\n')
        total_msgs += mc

    body = ''.join(lines)
    if dry_run:
        print(f'  [dry-run] 会写 {out_path}（{len(sessions)} 会话, {total_msgs} 条消息, {len(body)} 字节）')
        return total_msgs
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp = out_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(body)
    os.replace(tmp, out_path)  # 原子替换，避免半截文件
    print(f'  {out_path}: {len(sessions)} 会话, {total_msgs} 条消息')
    return total_msgs


def main():
    ap = argparse.ArgumentParser(description="DSH 会话增量导出工具")
    ap.add_argument('--instance', default=os.environ.get('DSH_INSTANCE', 'default'))
    ap.add_argument('--src', default=os.environ.get('DSH_SESSIONS_SRC', '~/.dsh/sessions'))
    ap.add_argument('--out', default=os.environ.get('DSH_SESSIONS_OUT', '~/dsh-session-export/sessions'))
    ap.add_argument('--mode', choices=['per-session', 'per-workspace'], default='per-session',
                    help='导出模式：per-session(细粒度单会话独立文件，默认) 或 per-workspace(工作区聚合单文件)')
    ap.add_argument('--force', action='store_true', help='忽略指纹全量重导')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    args.src = os.path.expanduser(args.src)
    args.out = os.path.expanduser(args.out)

    if not os.path.isdir(args.src):
        print(f'源目录不存在: {args.src}', file=sys.stderr)
        return 2
    os.makedirs(args.out, exist_ok=True)
    state_file = f".export-state-{args.mode}.json"
    state_path = os.path.join(args.out, state_file)
    state = {}
    if not args.force and os.path.exists(state_path):
        try:
            with open(state_path, encoding='utf-8') as f:
                state = json.load(f)
        except Exception:
            state = {}

    grand = 0
    skipped_items = 0
    active_sessions = 0
    exported_count = 0
    legacy_sessions = 0
    t0 = time.perf_counter()
    tracker = _indexer.IoTracker("session_export") if _indexer and hasattr(_indexer, "IoTracker") else None
    if tracker:
        tracker.__enter__()
    # 使用 os.scandir 显式指定 follow_symlinks=False，杜绝软链环导致的递归死循环
    try:
        workspaces = sorted(
            e.path for e in os.scandir(args.src)
            if e.is_dir(follow_symlinks=False) and e.name.endswith('--')
        )
    except OSError:
        workspaces = []

    for pd in workspaces:
        proj = os.path.basename(pd).strip('-')
        try:
            sdirs = sorted(
                e.path for e in os.scandir(pd)
                if e.is_dir(follow_symlinks=False)
            )
        except OSError:
            continue
        if not sdirs:
            continue

        sessions = []
        for sd in sdirs:
            if is_active(sd):
                active_sessions += 1
                continue  # 活跃会话跳过、不进指纹 → 下轮补导
            z = pick_session_file(sd)
            if not z:
                continue
            zbase = os.path.basename(z)
            if not zbase.startswith('session.v4.'):
                legacy_sessions += 1
            try:
                mt = os.path.getmtime(z)
            except OSError:
                continue
            sessions.append((sd, z, mt))

        if not sessions:
            continue

        if args.mode == 'per-session':
            # 格式版本号 v2：纳入 assistant/message (data.message.content)，旧状态 int(mt) 自动重导
            fmt_ver = 2
            for sd, z, mt in sessions:
                sid = normalize_sid(sd)
                item_key = f"{proj}/{sid}"
                stamp = f"{int(mt)}:v{fmt_ver}"
                if not args.force and state.get(item_key) == stamp:
                    skipped_items += 1
                    continue
                out_path = os.path.join(args.out, proj, f"{sid}.md")
                n_msgs = export_single_session(sd, z, mt, out_path, args.instance, proj, args.dry_run)
                grand += n_msgs
                exported_count += 1
                state[item_key] = stamp
        else:
            # 工作区指纹：会话目录名 + zstd mtime
            fp_src = '|'.join(
                f'{os.path.basename(sd)}:{int(mt)}' for sd, _z, mt in sorted(sessions)
            )
            fingerprint = hashlib.sha1(fp_src.encode()).hexdigest()
            if not args.force and state.get(proj) == fingerprint:
                skipped_items += 1
                continue
            out_path = os.path.join(args.out, f'{proj}.md')
            grand += export_workspace(pd, out_path, args.instance, sessions, args.dry_run)
            exported_count += 1
            state[proj] = fingerprint

    if not args.dry_run:
        with open(state_path, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=1)

    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    perf_status = "OK"
    rchar = 0
    write_b = 0
    if tracker:
        tracker.__exit__(None, None, None)
        rchar = tracker.metrics.get("rchar", 0)
        write_b = tracker.metrics.get("write_bytes", 0)
        judgment = _indexer.evaluate_perf_budget(
            "session_export",
            tracker.metrics,
            {
                "instance": args.instance,
                "exported": exported_count,
                "skipped": skipped_items,
                "active_skipped": active_sessions,
                "legacy_sessions": legacy_sessions,
            },
        )
        perf_status = judgment["status"]
        mirror_root = os.environ.get("GITQ_MIRROR_ROOT", "/srv/gitea-mirror")
        if os.path.isdir(mirror_root):
            _indexer.record_perf_event(mirror_root, judgment)
        elif perf_status in ("WARN", "CRITICAL"):
            sys.stderr.write(
                f"[PERF-{perf_status}] op=session_export legacy_sessions={legacy_sessions} "
                f"elapsed={elapsed_ms}ms reasons={'; '.join(judgment.get('reasons', []))}\n"
            )

    print(
        f'\n=== {args.instance} [{args.mode}]: {len(workspaces)} 工作区, 导出 {exported_count} 篇, '
        f'跳过未变 {skipped_items}, 活跃跳过 {active_sessions} 会话, 旧版(<v4)会话 {legacy_sessions}, '
        f'累计消息 {grand} 条 | 耗时 {elapsed_ms:.1f}ms (rchar={rchar}B, write={write_b}B, status={perf_status}) ==='
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
