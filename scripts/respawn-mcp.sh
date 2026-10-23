#!/bin/bash
# respawn-mcp.sh —— 让改过的 server.mjs 生效：**点名 kill** MCP 子进程，由 dsh-mcp-client 自动 respawn。
#
# 为什么要有这个脚本：改完 server.mjs 后，已在跑的会话仍用旧代码，「改了」是假的；
# 而停止进程这件事本机踩过两次自伤（红线 2：`pkill -f` 会命中 bash 工具自身 `bash -c "<整段脚本>"`）。
# 所以把「枚举 → 单点确认 → kill 单个 PID → 轮询新 PID」固化成脚本，而不是每次手敲。
#
# 用法：
#   bash respawn-mcp.sh                        # 默认 = 本脚本所在仓库目录
#   bash respawn-mcp.sh /srv/gitea-tool        # 指定副本目录
#   bash respawn-mcp.sh --dry-run /srv/gitea-tool   # 只看命中哪个 PID，不 kill
#   TARGET_DIR=/srv/gitea-tool ssh <remote-host> 'bash -s' < respawn-mcp.sh   # 远端执行（cmdline 只有 `bash -s`，不会自我命中）
# 退出码：0=已换新进程；1=kill 后 30s 内没有新进程（**不要**重启 dsh.service，先看 DSH 日志）；3=没找到进程；4=目录不存在。
set -uo pipefail

DRY=0; SELFTEST=0
[ "${1:-}" = "--dry-run" ] && { DRY=1; shift; }
[ "${1:-}" = "--selftest" ] && { SELFTEST=1; shift; }
# 默认目录 = 本脚本所在目录（若位于 scripts/ 子目录且上级含 lib/server.mjs，则自动取仓库根目录）
SELF_DIR=$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)
DEFAULT_DIR="$SELF_DIR"
if [ ! -f "$SELF_DIR/server.mjs" ] && [ -f "$SELF_DIR/../lib/server.mjs" ]; then
  DEFAULT_DIR=$(cd "$SELF_DIR/.." && pwd)
fi
TARGET_DIR="${1:-${TARGET_DIR:-$DEFAULT_DIR}}"

find_pid_in() {   # $1=目录（用于 selftest 复用同一套匹配逻辑）
  local dir=$1 d p c
  for d in /proc/[0-9]*; do
    p=${d#/proc/}
    [ "$p" = "$$" ] && continue
    [ "$p" = "$PPID" ] && continue
    # PID 会在 glob 与读取之间消失（真发生过：stderr 刷 "/proc/NNN/cmdline: No such file or directory"）
    [ -r "$d/cmdline" ] || continue
    c=$(tr '\0' ' ' < "$d/cmdline" 2>/dev/null | sed 's/ *$//')
    # 教训（2026-10-15 一天内踩两次）：`case` 的模式**从头锚定** —— 首个 `*` 不能省，
    # 否则 `node /路径/...` 永远不匹配，得到「未找到进程」的假阴性。
    # 同时**尾部不留 `*`**：模式以 server.mjs 结尾 ⇒ 扫描脚本自身的 cmdline 不会自我命中。
    case "$c" in
      *"$dir"/*server.mjs) echo "$p" ;;
    esac
  done
}

find_pid() { find_pid_in "$TARGET_DIR"; }

# ── 自证（2026-10-15 加）：匹配逻辑必须"在真数据上"被证明过 ──
# 起因：这类扫描两天内出了两次假阴性（模式串不匹配真实 cmdline、case 忘记锚定），
# 而假阴性表现为"未找到进程"= 看起来像"服务没起"，最难查。所以负向对照必须有：
#   ① 造一个持住 <tmp>/gitea-search/server.mjs 的真进程 ⇒ 必须命中它，且**只能命中它**（不自我命中）
#   ② 空目录 ⇒ 必须命中 0 个（而不是命中扫描脚本自己）
if [ "$SELFTEST" = 1 ]; then
  T=$(mktemp -d /tmp/respawn-selftest.XXXXXX); mkdir -p "$T/gitea-search" "$T/gitea-empty"
  printf '// selftest\n' > "$T/gitea-search/server.mjs"
  node -e 'setTimeout(()=>{},15000)' "$T/gitea-search/server.mjs" & HOLD=$!
  sleep 1
  hits=$(find_pid_in "$T/gitea-search")
  n=$(printf '%s\n' "$hits" | grep -c '[0-9]')
  pass=0; fail=0
  say() { if [ "$1" = ok ]; then pass=$((pass+1)); echo "  ✓ $2"; else fail=$((fail+1)); echo "  ✗ $2"; fi; }
  [ "$n" = 1 ] && say ok "唯一命中（n=$n）" || say no "应命中 1 个，实际 $n 个：$(echo $hits)"
  [ "$hits" = "$HOLD" ] && say ok "命中的正是 holder(pid=$HOLD)" || say no "命中 $hits ≠ holder $HOLD"
  printf '%s\n' "$hits" | grep -qx "$$" && say no "把自己当成了目标（自匹配）" || say ok "没有自我命中（自身 cmdline 不含匹配串）"
  empty=$(find_pid_in "$T/gitea-empty"); [ -z "$empty" ] && say ok "空目录 ⇒ 0 命中（不会乱报）" || say no "空目录却命中：$empty"
  kill "$HOLD" 2>/dev/null; rm -rf "$T"
  echo "自测结果：$pass 绿 / $fail 红"
  [ "$fail" = 0 ] && exit 0 || exit 1
fi

echo "目标目录：$TARGET_DIR"
[ -d "$TARGET_DIR" ] || { echo "✗ 目录不存在：$TARGET_DIR"; exit 4; }
[ -f "$TARGET_DIR/server.mjs" ] && stat -c '  mtime=%y  %n' "$TARGET_DIR/server.mjs"

OLD=$(find_pid)
if [ -z "${OLD:-}" ]; then
  echo "✗ 未找到在跑的 MCP 子进程（不猜 PID）——服务可能没起，或该实例的 MCP 未配置"
  exit 3
fi
echo "命中："
ps -o pid,ppid,lstart,cmd -p "$OLD" | tail -1 | sed 's/^/  /'

if [ "$DRY" = 1 ]; then
  echo "（--dry-run：不 kill）"
  exit 0
fi

echo "kill $OLD（SIGTERM，只这一个 PID）"
kill "$OLD"

NEW=""
for _ in $(seq 1 30); do
  sleep 1
  NEW=$(find_pid)
  [ -n "${NEW:-}" ] && [ "$NEW" != "$OLD" ] && break
  NEW=""
done
if [ -z "${NEW:-}" ]; then
  echo "✗ 30s 内没有新进程出现（dsh-mcp-client 未 respawn）"
  echo "  处置：不要重启 dsh.service；先看该实例 DSH 日志，再人工确认 MCP 配置（command 路径/可执行位）"
  exit 1
fi
echo "新进程："
ps -o pid,ppid,lstart,cmd -p "$NEW" | tail -1 | sed 's/^/  /'
echo "OLD=$OLD NEW=$NEW"
echo "复核：node \"$TARGET_DIR/verify-live.mjs\" \"<标签>=$TARGET_DIR/server.mjs\"   （应报 ok）"
