#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# gitea 全量镜像同步 (sync.sh)
#
# 目的：把私有 Gitea（git.example.com，全部仓在 myorg/ 名下）完整镜像到本机，
#       给 gitq / gitea-search MCP 一个「本地、可枚举、全分支」的检索面。
#       —— Gitea 服务端没开 repo indexer（app.ini 无 REPO_INDEXER_ENABLED），
#          所以网页/API 搜不到内容；镜像 + git grep 是唯一能搜全分支的路子。
#
# 用法：
#   sync.sh                 # 增量同步全部仓（新仓 clone --mirror，已有仓 remote update --prune）
#   sync.sh -q              # 安静模式（只报错和汇总），给 cron 用
#   sync.sh -j 12           # 并发度（默认 6）
#   sync.sh -r incidents    # 只同步指定仓（可重复给）
#   sync.sh --list          # 只列出源上的仓名，不下载
#
# 环境变量覆盖：GITEA_MIRROR_ROOT (或 GITQ_MIRROR_ROOT) / GITEA_OWNERS (或 GITQ_OWNER) /
#              GITQ_REMOTE / GITQ_JOBS / GITQ_ENUM(auto|api|ssh) /
#              GITQ_TRANSPORT(auto|ssh|https) / GITEA_TOKEN (或 GITQ_API_TOKEN) /
#              GITEA_URL (或 GITQ_API_BASE) / GITQ_SSH_HOST …
# 通用性：token 与 ssh 只需其一——枚举 auto 会 api→ssh 两路试；克隆 auto 先 ssh、
#         ssh 失败且有 token 时该仓改走 https（详见下方 GITQ_TRANSPORT 注释）。
#
# 状态文件：
#   $ROOT/state/last_status   每轮都写：time/host/ok/fail/total/dur/degraded/enum_source/cache_stale
#   $ROOT/state/last_ok       只在本轮全成功时刷新 ⇒ 巡检按它断言新鲜度（maxAgeHours）
#   $ROOT/last_sync           每轮刷新（保留兼容）
#   $ROOT/state/repos.cache   最近一次成功枚举的仓名（api/ssh 任一成功即刷新），
#                             供下次不可达时救急；超 $GITQ_CACHE_MAXAGE_H 标 cache_stale=1
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

ROOT=${GITEA_MIRROR_ROOT:-${GITEA_MIRROR_PATH:-${GITQ_MIRROR_ROOT:-/srv/gitea-mirror}}}
RAW_OWNER=${GITEA_OWNERS:-${GITEA_OWNER:-${GITQ_OWNER:-myorg}}}
OWNER=${RAW_OWNER%%,*}
REMOTE=${GITEA_REMOTE:-${GITQ_REMOTE:-gitea}}          # ~/.ssh/config 里的 ssh 别名
JOBS=${GITQ_JOBS:-6}
# 枚举源：
#   GITQ_ENUM=auto    → 先 api（有 token 就试，兼容 User 与 Org 端点）再 ssh（若配置了 GITQ_SSH_HOST）；任一成功即用
#   GITQ_ENUM=api     → 只走 Gitea HTTP API（需 GITEA_TOKEN / GITQ_API_TOKEN）
#   GITQ_ENUM=ssh     → 只走 ssh 管理端目录枚举（需配置 GITQ_SSH_HOST）
#   两路都失败 → 用缓存（state/repos.cache，若在 GITQ_CACHE_MAXAGE_H 内）→ 再退本地镜像目录
ENUM=${GITQ_ENUM:-auto}
API_BASE=${GITEA_URL:-${GITEA_API_BASE:-${GITQ_API_BASE:-https://git.example.com}}}
API_BASE=${API_BASE%/}
API_TOKEN=${GITEA_TOKEN:-${GITEA_API_TOKEN:-${GITQ_API_TOKEN:-}}}
if [ -z "$API_TOKEN" ]; then
  for _tf in "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")/.sync-token" "/srv/gitea-tool/.sync-token" "$ROOT/.sync-token"; do
    if [ -s "$_tf" ]; then
      API_TOKEN=$(tr -d '\r\n ' < "$_tf" 2>/dev/null || true)
      break
    fi
  done
fi
SSH_ADMIN_HOST=${GITEA_SSH_HOST:-${GITQ_SSH_HOST:-}}
SSH_REPO_ROOT=${GITEA_SSH_REPO_ROOT:-${GITQ_SSH_REPO_ROOT:-/srv/git/repositories}}
SSH_GITEA_CONTAINER=${GITEA_SSH_CONTAINER:-${GITQ_SSH_CONTAINER:-}}
# 克隆/更新通道（token OR ssh 通用性）：
#   GITQ_TRANSPORT=auto  → 默认：有 ssh 别名用 ssh；仅当本轮 ssh 克隆失败且有 token 时，该仓改走 https
#   GITQ_TRANSPORT=ssh   → 强制 ssh（$REMOTE:$OWNER/x.git）
#   GITQ_TRANSPORT=https → 强制 https（用 $API_TOKEN 作密码：https://<user>:<token>@host/...）
#   —— token 与 ssh 只需其一即可完成镜像（https 只要 token，ssh 只要私钥）。
TRANSPORT=${GITQ_TRANSPORT:-auto}
GIT_USER=${GITQ_GIT_USER:-git}           # https 通道的 Gitea 用户名（一般就是 git）
CACHE_FILE=${GITQ_REPOS_CACHE:-$ROOT/state/repos.cache}
CACHE_MAXAGE_H=${GITQ_CACHE_MAXAGE_H:-24}   # 缓存仓名列表最长可用小时数（超期只用于救急）
LOG=$ROOT/sync.log
HOST=$(hostname -s)
export GIT_TERMINAL_PROMPT=0
# 固定 ssh 连接超时，避免远端半死不活时拖慢整轮同步。
export GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=8 -o ServerAliveInterval=15 -o ServerAliveCountMax=3"

QUIET=0; LIST_ONLY=0; ONLY=(); DEGRADED=0
repos=()
while [ $# -gt 0 ]; do
  case "$1" in
    -q|--quiet) QUIET=1; shift ;;
    -j|--jobs)  JOBS=${2:?-j 需要一个数字}; shift 2 ;;
    -r|--repo)  ONLY+=("${2:?-r 需要一个仓名}"); shift 2 ;;
    --list|--list-only) LIST_ONLY=1; shift ;;
    -h|--help)  sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "未知参数: $1（-h 看用法）" >&2; exit 2 ;;
  esac
done

# 兼容 bash 3.2（macOS 自带）：无 mapfile
_fill_repos() { repos=(); while IFS= read -r _lr; do [ -n "$_lr" ] && repos+=("$_lr"); done; }
# 兼容无 GNU timeout 的环境（如 macOS）：自带软超时封装
if ! command -v timeout >/dev/null 2>&1; then
  timeout() {
    local secs="$1"; shift
    "$@" &
    local pid=$!
    ( sleep "$secs"; kill -9 "$pid" 2>/dev/null ) &
    local watcher=$!
    local rc=0
    wait "$pid"; rc=$?
    kill "$watcher" 2>/dev/null
    return $rc
  }
fi
say() { [ "$QUIET" = 1 ] || echo "$@"; }
log() { echo "$(date '+%F %T') [$HOST] $*" >> "$LOG"; }

# ── 1) 枚举仓名 ──────────────────────────────────────────────────────────────
# 权威来源两个，任一可用即可（token 与 ssh 通用性）：
#   api → Gitea HTTP API（支持 /api/v1/users/$OWNER/repos 与 /api/v1/orgs/$OWNER/repos）
#   ssh → 可选 ssh 管理端枚举仓库目录（需显式配置 GITQ_SSH_HOST）
# 缓存 = 最近一次成功枚举（任一来源），供本次不可达时救急（可能过期，见 cache_stale）。
ENUM_SOURCE=""        # 本轮实际用的来源：api / ssh / cache / disk / (empty=未枚举)
CACHE_STALE=0         # 1 = 用的是过期缓存救急
_fetch_gitea_endpoint_repos() {
  local ep="$1" page=1 n=0 out name has_wiki emitted=0
  while :; do
    if [ -n "$API_TOKEN" ]; then
      out=$(timeout 30 curl -fsS --connect-timeout 8 \
          -H "Authorization: token $API_TOKEN" \
          "$API_BASE/api/v1/$ep?limit=50&page=$page" 2>/dev/null) || return 1
    else
      out=$(timeout 30 curl -fsS --connect-timeout 8 \
          "$API_BASE/api/v1/$ep?limit=50&page=$page" 2>/dev/null) || return 1
    fi
    n=$(printf '%s' "$out" | jq 'if type=="array" then length else 0 end' 2>/dev/null || echo 0)
    [ "${n:-0}" -eq 0 ] && break
    while IFS= read -r name; do
      [ -z "$name" ] && continue
      printf '%s\n' "$name"
      emitted=$((emitted + 1))
      has_wiki=$(printf '%s' "$out" | jq -r --arg n "$name" '.[] | select(.name==$n) | .has_wiki // false' | head -1)
      [ "$has_wiki" = "true" ] && printf '%s.wiki\n' "$name"
    done < <(printf '%s' "$out" | jq -r '.[].name')
    [ "${n:-0}" -lt 50 ] && break
    page=$((page + 1))
  done
  [ "$emitted" -gt 0 ]
}
list_from_gitea_api() {
  # 标准 Gitea 兼容：OWNER 既可能是 User（/users/$OWNER/repos），也可能是 Organization（/orgs/$OWNER/repos）。
  # .wiki 仓 API 不单独列出，按 repo 的 has_wiki 字段自动推导补齐。
  [ -n "$API_TOKEN" ] || [ "$ENUM" = "api" ] || return 1
  local got=""
  got=$(_fetch_gitea_endpoint_repos "users/$OWNER/repos" 2>/dev/null) || true
  if [ -z "$got" ]; then
    got=$(_fetch_gitea_endpoint_repos "orgs/$OWNER/repos" 2>/dev/null) || true
  fi
  [ -n "$got" ] || return 1
  printf '%s\n' "$got" | awk '!seen[$0]++'
}
list_from_gitea_ssh() {
  [ -n "$SSH_ADMIN_HOST" ] || return 1
  local remote_cmd
  if [ -n "$SSH_GITEA_CONTAINER" ]; then
    remote_cmd="docker exec $SSH_GITEA_CONTAINER sh -c 'ls -d $SSH_REPO_ROOT/$OWNER/*.git'"
  else
    remote_cmd="ls -d $SSH_REPO_ROOT/$OWNER/*.git"
  fi
  timeout 30 ssh -o ConnectTimeout=8 -o BatchMode=yes "$SSH_ADMIN_HOST" "$remote_cmd" 2>/dev/null \
    | sed -e 's#.*/##' -e 's#\.git$##' | grep -v '^[[:space:]]*$'
}
list_from_cache() {
  [ -f "$CACHE_FILE" ] || return 1
  grep -v '^#' "$CACHE_FILE" 2>/dev/null | grep -v '^[[:space:]]*$'
}
list_from_disk() {
  ls -d "$ROOT/$OWNER"/*.git 2>/dev/null | sed -e 's#.*/##' -e 's#\.git$##'
}
# 尝试一个枚举来源；成功（≥1 行）则把仓名填进全局数组 repos、记 ENUM_SOURCE、刷新缓存并返回 0。
try_enum() {
  local fn=$1 label=$2 got
  got=$("$fn" 2>/dev/null)
  [ -n "$got" ] || return 1
  _fill_repos <<< "$got"
  [ ${#repos[@]} -gt 0 ] || return 1
  ENUM_SOURCE=$label
  if [ "$label" = api ] || [ "$label" = ssh ]; then
    mkdir -p "$(dirname "$CACHE_FILE")"
    { echo "# gitea repos cache; source=$label; at=$(date '+%F %T')"; printf '%s\n' "$got"; } \
      > "$CACHE_FILE" 2>/dev/null || true
    CACHE_STALE=0
  fi
  return 0
}
# 按 GITQ_ENUM 决定尝试顺序，成功则把仓名写入全局数组 repos。
resolve_repos() {
  local order=()
  case "$ENUM" in
    api) order=(api) ;;
    ssh) order=(ssh) ;;
    *)   order=(api ssh) ;;   # auto：先 api（有 token 才试）再 ssh
  esac
  for src in "${order[@]}"; do
    case "$src" in
      api) try_enum list_from_gitea_api api && return 0 ;;
      ssh) try_enum list_from_gitea_ssh ssh && return 0 ;;
    esac
  done
  # 两路都不可达 → 先试缓存（救急），再退本地磁盘
  if try_enum list_from_cache cache; then
    local age_h
    age_h=$(awk -v m="$(stat -c %Y "$CACHE_FILE" 2>/dev/null || echo 0)" 'BEGIN{printf "%d",(systime()-m)/3600}' 2>/dev/null)
    age_h=${age_h:-0}
    if [ "$age_h" -gt "$CACHE_MAXAGE_H" ]; then
      CACHE_STALE=1
      say "!! 枚举源不可达，用的是 ${age_h}h 前的缓存（超 ${CACHE_MAXAGE_H}h，可能漏新仓）"
    else
      say "!! 枚举源不可达，用的是 ${age_h}h 前的缓存（可能漏新仓）"
    fi
    log "WARN 枚举降级：用缓存(age=${age_h}h stale=$CACHE_STALE)"
    DEGRADED=1
    return 0
  fi
  if [ "$ENUM" = auto ]; then
    say "!! 无法枚举 Gitea 仓库（api/ssh 均不可达），退回本地镜像目录（新仓不会被发现）"
  else
    say "!! 无法枚举 Gitea 仓库（GITQ_ENUM=$ENUM 不可达），退回本地镜像目录（新仓不会被发现）"
  fi
  log "WARN 枚举失败，退回本地镜像目录"
  DEGRADED=1
  ENUM_SOURCE=disk
  _fill_repos <<< "$(list_from_disk)"
  [ ${#repos[@]} -gt 0 ]
}

if [ ${#ONLY[@]} -gt 0 ]; then
  repos=("${ONLY[@]}")
  ENUM_SOURCE=only
elif [ "$LIST_ONLY" = 1 ]; then
  resolve_repos || _fill_repos <<< "$(list_from_disk)"
  printf '%s\n' "${repos[@]+"${repos[@]}"}"
  exit 0
else
  if ! resolve_repos; then
    _fill_repos <<< "$(list_from_disk)"
  fi
fi
[ ${#repos[@]} -eq 0 ] && { echo "没有可同步的仓" >&2; exit 1; }

mkdir -p "$ROOT/$OWNER"

# ── 2) 单仓同步 ──────────────────────────────────────────────────────────────
sync_one() {
  local r=$1
  # 注意：不要写 `local r=$1 d=$ROOT/$OWNER/$r.git` —— local/declare 会先把整行
  # 参数展开再赋值，$r 取到的是外层（未定义）的值，目录名会变成裸 ".git"（踩过）。
  local d="$ROOT/$OWNER/$r.git"
  local out rc=0 low lc cand
  [ -z "$r" ] && { echo "FAIL 空仓名"; return 1; }
  if [ ! -d "$d" ]; then
    # 2026-10-14：历史镜像目录可能是大小写变体（API 给规范名，磁盘是早期按别的方式建的，
    # 如 ha-s3-env vs HA-s3-ENV）。大小写不敏感找现有目录并**原地更新**，避免每轮克隆出重复目录。
    lc=$(printf '%s' "$r" | tr 'A-Z' 'a-z')
    for cand in "$ROOT/$OWNER"/*.git; do
      [ -e "$cand" ] || continue
      low=$(basename "$cand" .git | tr 'A-Z' 'a-z')
      if [ "$low" = "$lc" ]; then d="$cand"; break; fi
    done
  fi
  if [ -d "$d" ]; then
    out=$(timeout 300 git --git-dir="$d" remote update --prune 2>&1) || rc=1
    [ $rc = 1 ] && echo "FAIL fetch $r :: $(echo "$out" | tail -2 | tr '\n' ' ')" || echo "ok   fetch $r"
  else
    # 通道（token OR ssh 通用性）：ssh 优先；ssh 失败且有 token 时，该仓改走 http(s)。
    local scheme="${API_BASE%%://*}"
    [ "$scheme" = "$API_BASE" ] && scheme="https"
    local hostpart="${API_BASE#*://}"
    src_ssh="$REMOTE:$OWNER/$r.git"
    if [ -n "$API_TOKEN" ]; then
      src_https="$scheme://$GIT_USER:$API_TOKEN@$hostpart/$OWNER/$r.git"
    else
      src_https="$scheme://$hostpart/$OWNER/$r.git"
    fi
    if [ "$TRANSPORT" = https ]; then
      out=$(timeout 300 git clone --mirror -q "$src_https" "$d" 2>&1) || rc=1
    elif [ "$TRANSPORT" = auto ] && [ -n "$API_TOKEN" ]; then
      # 先试 ssh（网内快）；失败再试 https（token 兜底）
      out=$(timeout 300 git clone --mirror -q "$src_ssh" "$d" 2>&1) || rc=1
      if [ $rc = 1 ] && [ ! -d "$d" ]; then
        log "INFO clone $r 经 ssh 失败，改走 https(token)"
        out=$(timeout 300 git clone --mirror -q "$src_https" "$d" 2>&1) || rc=1
      fi
    else
      out=$(timeout 300 git clone --mirror -q "$src_ssh" "$d" 2>&1) || rc=1
    fi
    [ $rc = 1 ] && echo "FAIL clone $r :: $(echo "$out" | tail -2 | tr '\n' ' ')" || echo "ok   clone $r"
  fi
  return $rc
}
export -f sync_one
export ROOT OWNER REMOTE TRANSPORT API_TOKEN GIT_USER API_BASE
# 传给子 shell 的 log（sync_one 里 https 回退会用）—— 它自己会 append 到 $LOG
log() { echo "$(date '+%F %T') [$HOST] $*" >> "${GITQ_LOG:-$ROOT/sync.log}"; }
export -f log
export HOST GITQ_LOG

say "== 同步 $REMOTE:$OWNER —— ${#repos[@]} 个仓，并发 $JOBS，目标 $ROOT/$OWNER =="
start=$(date +%s)
fail=0
# 2026-10-08：临时文件改 mktemp + trap —— 原先固定写 $ROOT/.sync.out，
# cron 一旦重叠（无锁时真发生过）两轮会互相覆盖这个文件，ok/fail 计数与日志都会串。
OUT=$(mktemp "$ROOT/.sync.out.XXXXXX")
trap 'rm -f "$OUT"' EXIT
printf '%s\n' "${repos[@]+"${repos[@]}"}" | xargs -P "$JOBS" -I{} bash -c 'sync_one "$@"' _ {} > "$OUT" 2>&1
fail=$(grep -c '^FAIL' "$OUT" 2>/dev/null || true)
ok=$(grep -c '^ok' "$OUT" 2>/dev/null || true)

# 防御：正常绝不会出现 $ROOT/$OWNER/.git（那是仓名解析成空的症状），出现就报警
if [ -d "$ROOT/$OWNER/.git" ]; then
  echo "!! 异常：$ROOT/$OWNER/.git 存在（仓名为空的痕迹），请检查后再重跑" >&2
  log "ERROR 出现 $ROOT/$OWNER/.git，仓名解析异常"
fi

if [ "$QUIET" = 1 ]; then
  [ "$fail" -gt 0 ] && grep '^FAIL' "$OUT" >&2
else
  cat "$OUT"
fi

dur=$(( $(date +%s) - start ))
date '+%F %T' > "$ROOT/last_sync"
# 机器可读状态（供巡检/排障）：last_status 每轮都写；state/last_ok 只在本轮全成功时刷新，
# 巡检按它断言新鲜度 ⇒「同步静默停摆/一直失败」能被及时发现。
mkdir -p "$ROOT/state"
mtime=$(stat -c %Y "$ROOT/state/last_ok" 2>/dev/null || echo 0)
if [ "$mtime" -gt 0 ]; then
  last_ok_age_h=$(awk -v m="$mtime" 'BEGIN{printf "%.1f",(systime()-m)/3600}' 2>/dev/null)
else
  last_ok_age_h="none"   # 本机还没有过成功的 last_ok（首轮），别把 systime() 当年龄
fi
[ -z "$last_ok_age_h" ] && last_ok_age_h="unknown"
printf 'time=%s host=%s ok=%d fail=%d total=%d dur=%ds degraded=%d enum_source=%s cache_stale=%d transport=%s last_ok_age_h=%s\n' \
    "$(date '+%F %T')" "$HOST" "$ok" "$fail" "${#repos[@]}" "$dur" "$DEGRADED" \
    "${ENUM_SOURCE:-none}" "$CACHE_STALE" "$TRANSPORT" "$last_ok_age_h" > "$ROOT/state/last_status"
[ "$fail" -eq 0 ] && date '+%F %T' > "$ROOT/state/last_ok"
summary="完成：成功 $ok，失败 $fail，用时 ${dur}s（${#repos[@]} 个仓，枚举源=${ENUM_SOURCE:-none}）"
if [ "$DEGRADED" = 1 ]; then
  case "${ENUM_SOURCE:-}" in
    cache) [ "$CACHE_STALE" = 1 ] && summary="$summary【枚举降级：用过期缓存，新仓可能未被发现】" \
                                || summary="$summary【枚举降级：用缓存，新仓可能未被发现】" ;;
    disk)  summary="$summary【枚举降级：按本地目录同步，新仓不会被发现】" ;;
    *)     summary="$summary【枚举降级】" ;;
  esac
fi
say "$summary"
log "$summary"
# 只在「整轮全失败」或「枚举降级」时进 journal —— 避免任何一个仓抖动都刷警（告警疲劳）
if [ "$fail" -gt 0 ] && [ "$fail" -eq "${#repos[@]}" ]; then
  logger -t gitea-mirror "镜像同步整轮失败（$fail/${#repos[@]}）—— 远端可能不可达，详见 /var/log/gitea-mirror.log" 2>/dev/null || true
fi
if [ "$DEGRADED" = 1 ]; then
  logger -t gitea-mirror "枚举降级：Gitea 仓枚举失败，本轮按本地目录同步，新仓可能未被发现" 2>/dev/null || true
fi

# 增量刷新 SQLite FTS5 索引（文档与导出会话全文检索）
INDEXER="$(dirname "$(readlink -f "$0")")/indexer.py"
if [ -x "$INDEXER" ]; then
  python3 "$INDEXER" --build >/dev/null 2>&1 || true
fi

[ "$fail" -gt 0 ] && exit 1
exit 0
