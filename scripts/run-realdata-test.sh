#!/usr/bin/env bash
# ==============================================================================
# run-realdata-test.sh —— dsh-gitea-search 真实数据全链路端到端验收脚本
#
# 用途：
#   在具备真实 Gitea 裸镜像（/srv/gitea-mirror）与真实 DSH 会话目录的主机上，
#   执行非破坏性的真实数据验收：
#     - Phase 0: 环境、依赖、真实镜像仓数量预检
#     - Phase 1: 真实镜像 FTS5 索引、doc_rowid 1:1 映射不变量与 head_commit 增量门控
#     - Phase 2: CLI 真实语料检索（git grep / 路径 / 提交 / --read / FTS5 分词与软分区）
#     - Phase 3: 真实 DSH 会话增量导出（隔离到 /tmp）→ 独立镜像建库 → FTS5 session 检索
#     - Phase 4: MCP Server stdio 协议 4 工具真实数据联合调用与护栏探针
#     - Phase 5: 真实环境耗时/磁盘 I/O 监测 (--perf-stats) 与健康诊断 (--doctor)
#
# 用法：
#   bash scripts/run-realdata-test.sh [MIRROR_ROOT]
#   环境变量可选覆盖：
#     GITQ_MIRROR_ROOT   镜像根目录（默认 /srv/gitea-mirror）
#     GITEA_OWNERS       镜像 owner（默认 myorg）
#     DSH_HOME_DIR       真实会话所在的 .dsh 目录（自动探测 $HOME/.dsh 等）
# ==============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
if [ "$(basename "$SCRIPT_DIR")" = "scripts" ]; then
  ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
else
  ROOT="$SCRIPT_DIR"
fi
MIRROR="${1:-${GITEA_MIRROR_ROOT:-${GITQ_MIRROR_ROOT:-/srv/gitea-mirror}}}"
OWNER="${GITEA_OWNERS:-${GITQ_OWNER:-myorg}}"
PRIMARY_OWNER="${OWNER%%,*}"
OUT="/tmp/gts-realdata-test-$$"
trap 'rm -rf "$OUT"' EXIT

# 自动探测 DSH_HOME_DIR
if [ -z "${DSH_HOME_DIR:-}" ]; then
  for cand in "${DSH_HOME:-}" "$HOME/.dsh" "/var/lib/dsh/.dsh"; do
    if [ -n "$cand" ] && [ -d "$cand/sessions" ]; then
      DSH_HOME_DIR="$cand"
      break
    fi
  done
fi
DSH_HOME_DIR="${DSH_HOME_DIR:-$HOME/.dsh}"

GTS=""
for cand in "$ROOT/bin/gts" "$ROOT/gts" "$ROOT/../bin/gts" "/usr/local/bin/gts"; do
  if [ -x "$cand" ]; then GTS="$cand"; break; fi
done
[ -n "$GTS" ] || GTS="$ROOT/bin/gts"
# MCP 入口探测：独立仓布局为 bin/mcp-server；部署扁平目录（/srv/gitea-tool 等）只有
# bin/mcp-server-gitea-search。按顺序回退，避免「脚本只能在自己的目录布局里跑」的漂移。
MCP=""
for cand in "$ROOT/bin/mcp-server" "$ROOT/bin/mcp-server-gitea-search" "$ROOT/mcp-server" "$ROOT/mcp-server-gitea-search" "/usr/local/bin/mcp-server-gitea-search"; do
  if [ -x "$cand" ]; then MCP="$cand"; break; fi
done
[ -n "$MCP" ] || MCP="$ROOT/bin/mcp-server"
# 会话导出器与 probe-stdio 同理：独立仓在 scripts/，扁平部署可能只在根目录。
SESS_EXPORT=""
for cand in "$ROOT/scripts/session-export-incremental.py" "$ROOT/session-export-incremental.py"; do
  if [ -f "$cand" ]; then SESS_EXPORT="$cand"; break; fi
done
[ -n "$SESS_EXPORT" ] || SESS_EXPORT="$ROOT/scripts/session-export-incremental.py"
PROBE_STDIO=""
for cand in "$ROOT/scripts/probe-stdio.mjs" "$ROOT/probe-stdio.mjs"; do
  if [ -f "$cand" ]; then PROBE_STDIO="$cand"; break; fi
done
[ -n "$PROBE_STDIO" ] || PROBE_STDIO="$ROOT/scripts/probe-stdio.mjs"
export GITQ_MIRROR_ROOT="$MIRROR" GITEA_MIRROR_ROOT="$MIRROR" GITEA_OWNERS="$OWNER" GITQ_OWNER="$PRIMARY_OWNER"
export PATH="/usr/local/bin:$HOME/.local/share/pnpm:$HOME/.local/share/pnpm/node_modules/.bin:$PATH"

PASS=0; FAIL=0
assert_ok() {
  local name="$1" cond="$2"
  if eval "$cond" >/dev/null 2>&1; then
    PASS=$((PASS+1))
    echo "  ✓ [PASS] $name"
  else
    FAIL=$((FAIL+1))
    echo "  ✗ [FAIL] $name"
  fi
}
jcount() { python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("count",d.get("total",0)))' 2>/dev/null || echo 0; }
ftstotal() { "$GTS" -D --json "$@" 2>/dev/null | jcount; }

mkdir -p "$OUT"
echo "=================================================================="
echo " dsh-gitea-search 真实数据全链路验收"
echo " 主机: $(hostname)  插件目录: $ROOT"
echo " 镜像: $MIRROR (owner=$PRIMARY_OWNER)  会话源: $DSH_HOME_DIR"
echo " DSH 版本: $(dsh --version 2>/dev/null || echo '未安装/非全局')  Node: $(node -v 2>/dev/null)"
echo " 时间: $(date -Is)"
echo "=================================================================="

# ── Phase 0: 前置条件 ─────────────────────────────────────────────────────────
echo ""
echo "[Phase 0] 前置条件"
assert_ok "python3-jieba 可用且无 SyntaxWarning 泄漏" "[ -z \"\$(python3 -c 'import sys; sys.path[:0] = [\"$ROOT/lib\", \"$ROOT\"]; import indexer; indexer._get_jieba()' 2>&1)\" ]"
assert_ok "SQLite FTS5 可用" "python3 -c 'import sqlite3;c=sqlite3.connect(\":memory:\");c.execute(\"create virtual table t using fts5(x)\")'"
REPO_N=$(ls -d "$MIRROR/$PRIMARY_OWNER"/*.git 2>/dev/null | wc -l)
assert_ok "真实镜像仓数量 = $REPO_N（真实镜像，>=120）" "[ $REPO_N -ge 120 ]"
assert_ok "交付仓 $PRIMARY_OWNER/dsh-gitea-search 已在真实镜像中" "[ -d \"$MIRROR/$PRIMARY_OWNER/dsh-gitea-search.git\" ]"

# ── Phase 1: 真实镜像索引构建 + doc_rowid 1:1 不变量 + 增量门控 ───────────────
echo ""
echo "[Phase 1] 真实镜像索引构建、doc_rowid 1:1 对齐与增量门控"
FIRST_INC=$("$GTS" --reindex 2>&1)
echo "  首轮 --reindex: $(echo "$FIRST_INC" | tail -1)"
STATS=$("$GTS" --fts-stats 2>&1)
echo "  $STATS"
assert_ok "索引库存在且体积 > 1MB" "[ -s \"$MIRROR/state/gitea-fts.sqlite\" ] && [ \$(stat -c%s \"$MIRROR/state/gitea-fts.sqlite\") -gt 1048576 ]"
IDX_REPOS=$(echo "$STATS" | grep -oE '共索引 [0-9]+ 个仓库' | grep -oE '[0-9]+' | head -1)
DOC_N=$(echo "$STATS" | grep -oE '[0-9]+ 篇文档' | grep -oE '^[0-9]+' | head -1)
assert_ok "索引覆盖 $IDX_REPOS 个有可索引内容的仓（>=115）" "[ ${IDX_REPOS:-0} -ge 115 ] && [ ${IDX_REPOS:-0} -le $REPO_N ]"
assert_ok "索引文档数 = $DOC_N（真实语料 > 1000）" "[ ${DOC_N:-0} -gt 1000 ]"

DB_ALIGN=$(python3 - "$MIRROR/state/gitea-fts.sqlite" << 'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
fb = c.execute("SELECT COUNT(*) FROM file_blobs").fetchone()[0]
fts = c.execute("SELECT COUNT(*) FROM docs_fts").fetchone()[0]
dr = c.execute("SELECT COUNT(*) FROM doc_rowid").fetchone()[0]
jn = c.execute("""
    SELECT COUNT(*) FROM doc_rowid r
    JOIN docs_fts f ON f.rowid = r.rowid
     AND f.repo = r.repo AND f.ref = r.ref AND f.path = r.path
""").fetchone()[0]
print(f"fb={fb} fts={fts} dr={dr} join={jn}")
sys.exit(0 if (fb == fts == dr == jn and fb > 1000) else 1)
PY
)
ALIGN_RC=$?
echo "  SQLite 四表一致性: $DB_ALIGN"
assert_ok "file_blobs == docs_fts == doc_rowid == JOIN 100% 1:1 对齐" "[ $ALIGN_RC -eq 0 ]"

T0=$(date +%s%3N); INC=$("$GTS" --reindex 2>&1); T1=$(date +%s%3N)
INC_MS=$((T1-T0))
echo "  二次 --reindex: $(echo "$INC" | tail -1)  (${INC_MS}ms)"
assert_ok "增量门控：二次 --reindex 更新/新增 0 篇" "echo \"\$INC\" | grep -q '更新/新增 0 篇'"
assert_ok "增量门控耗时 < 5000ms（head_commit 单进程快速跳过生效：${INC_MS}ms）" "[ $INC_MS -lt 5000 ]"

# ── Phase 2: CLI 真实数据检索（git grep + FTS5）──────────────────────────────
echo ""
echo "[Phase 2] CLI 真实数据检索"
assert_ok "仓枚举 --repos 返回 $REPO_N 个仓" "[ \$(\"$GTS\" --repos --json 2>/dev/null | jcount) -eq $REPO_N ]"
if [ -x /usr/local/bin/gts ]; then
  assert_ok "/usr/local/bin/gts 软链可正常加载 indexer 模块 (--fts-stats)" "/usr/local/bin/gts --fts-stats >/dev/null 2>&1"
fi
assert_ok "仓过滤 '$PRIMARY_OWNER/dsh-*' >= 5 个（owner 前缀 + 通配符）" "[ \$(\"$GTS\" --repos -r \"$PRIMARY_OWNER/dsh-*\" --json 2>/dev/null | jcount) -ge 5 ]"
assert_ok "代码字面量检索 gts 'gitea' 命中" "[ \$(\"$GTS\" --json 'gitea' 2>/dev/null | jcount) -ge 1 ]"
assert_ok "文件路径检索 -p 'docker-compose' 命中" "[ \$(\"$GTS\" --json -p 'docker-compose' 2>/dev/null | jcount) -ge 1 ]"
assert_ok "提交信息检索 -m 'release' 命中" "[ \$(\"$GTS\" --json -m 'release' 2>/dev/null | jcount) -ge 1 ]"
assert_ok "单文件读取 --read（带 $PRIMARY_OWNER/ 前缀）返回 README 正文" "\"$GTS\" -r \"$PRIMARY_OWNER/dsh-gitea-search\" --read 'README.md' 2>/dev/null | grep -q 'dsh-gitea-search'"

assert_ok "FTS5 中文 Jieba 检索 '镜像' 命中" "[ \$(ftstotal '镜像') -ge 1 ]"
assert_ok "FTS5 连字符查询 'docker-compose' 命中 [Audit #1]" "[ \$(ftstotal 'docker-compose') -ge 1 ]"
assert_ok "FTS5 前缀通配 'dock*' 命中 [Audit #1]" "[ \$(ftstotal 'dock*') -ge 1 ]"
assert_ok "FTS5 仓库通配+owner 前缀 -r '$PRIMARY_OWNER/dsh-*' 'gitea' [Audit #2]" "[ \$(ftstotal -r \"$PRIMARY_OWNER/dsh-*\" 'gitea') -ge 1 ]"
assert_ok "FTS5 kind=doc 过滤命中" "[ \$(\"$GTS\" -D --json --kind doc '镜像' 2>/dev/null | jcount) -ge 1 ]"
assert_ok "FTS5 kind=config 过滤命中" "[ \$(\"$GTS\" -D --json --kind config 'docker' 2>/dev/null | jcount) -ge 1 ]"
assert_ok "FTS5 kind=script 过滤命中" "[ \$(\"$GTS\" -D --json --kind script 'bash' 2>/dev/null | jcount) -ge 1 ]"
assert_ok "FTS5 kind=session 过滤命中（真实会话归档仓）" "[ \$(\"$GTS\" -D --json --kind session 'DSH' 2>/dev/null | jcount) -ge 1 ]"

# ── Phase 3: 真实 DSH 会话导出 → 建仓 → 索引 → 检索 ───────────────────────────
echo ""
echo "[Phase 3] 真实 DSH 会话内容导出与索引"
SESS_SRC="$DSH_HOME_DIR/sessions"
if [ -d "$SESS_SRC" ]; then
  SESS_OUT="$OUT/exported-sessions"
  rm -rf "$SESS_OUT"
  python3 "$SESS_EXPORT" --instance "$(hostname)" --src "$SESS_SRC" --out "$SESS_OUT" --force > "$OUT/export.log" 2>&1
  EXP_RC=$?
  assert_ok "真实会话导出成功（per-session）" "[ $EXP_RC -eq 0 ]"
  MD_N=$(find "$SESS_OUT" -name '*.md' -type f | wc -l)
  echo "  导出 Markdown 会话文件: $MD_N"
  assert_ok "导出会话文件数 >= 10（$MD_N 个真实会话）" "[ $MD_N -ge 10 ]"
  assert_ok "导出内容含真实 user 消息" "grep -rql '\*\*user\*\*' \"$SESS_OUT\""

  SMIRROR="$OUT/session-mirror"
  rm -rf "$SMIRROR"; mkdir -p "$SMIRROR/$PRIMARY_OWNER"
  BARE="$SMIRROR/$PRIMARY_OWNER/real-sessions.git"
  git init --bare -q "$BARE"
  WORK="$OUT/session-work"; rm -rf "$WORK"; mkdir -p "$WORK/sessions"
  cp -a "$SESS_OUT"/. "$WORK/sessions"/
  find "$WORK/sessions" -name '.export-state*.json' -delete
  git -C "$WORK" init -q; git -C "$WORK" config user.email t@local; git -C "$WORK" config user.name t
  git -C "$WORK" add -A >/dev/null 2>&1; git -C "$WORK" commit -qm "chore: real sessions" >/dev/null 2>&1
  git -C "$WORK" push -q "$BARE" HEAD:refs/heads/main >/dev/null 2>&1
  git --git-dir="$BARE" symbolic-ref HEAD refs/heads/main
  SB=$(GITEA_MIRROR_ROOT="$SMIRROR" GITQ_MIRROR_ROOT="$SMIRROR" GITEA_OWNERS="$PRIMARY_OWNER" GITQ_OWNER="$PRIMARY_OWNER" "$GTS" --reindex 2>&1)
  echo "  session 镜像索引: $(echo "$SB" | tail -1)"
  SSTATS=$(GITEA_MIRROR_ROOT="$SMIRROR" GITQ_MIRROR_ROOT="$SMIRROR" "$GTS" --fts-stats 2>&1)
  assert_ok "真实会话仓索引文档数 = $MD_N" "echo \"\$SSTATS\" | grep -qE \"共索引 1 个仓库, $MD_N 篇文档\""
  assert_ok "真实会话 FTS 检索命中（session 分类）" "[ \$(GITEA_MIRROR_ROOT=\"$SMIRROR\" GITQ_MIRROR_ROOT=\"$SMIRROR\" \"$GTS\" -D --json --kind session 'DSH' 2>/dev/null | jcount) -ge 1 ]"
else
  echo "  [SKIP] 未找到会话目录 $SESS_SRC"
fi

# ── Phase 4: MCP Server 4 工具真实数据端到端 ────────────────────────────────
echo ""
echo "[Phase 4] MCP Server 4 工具真实数据端到端"
assert_ok "probe-stdio.mjs 冒烟探针（6 项协议/UTF-8/护栏）" "node \"$PROBE_STDIO\" \"$MCP\" gitea >/dev/null"
MCP_OUT=$(node - "$MCP" << 'EOF'
import { spawn } from 'node:child_process';
const cmd = process.argv[2];
const p = spawn(cmd, [], { stdio: ['pipe','pipe','pipe'], env: process.env });
let buf = Buffer.alloc(0); const waiters = new Map();
p.stdout.on('data', (d) => {
  buf = Buffer.concat([buf, d]); let nl;
  while ((nl = buf.indexOf(0x0a)) >= 0) {
    const line = buf.subarray(0, nl).toString('utf8'); buf = buf.subarray(nl + 1);
    if (!line.trim()) continue; const msg = JSON.parse(line);
    if (waiters.has(msg.id)) { waiters.get(msg.id)(msg); waiters.delete(msg.id); }
  }
});
p.on('close', (code) => {
  for (const [id, res] of waiters) { waiters.delete(id); res({ error: { code } }); }
});
const call = (id, method, params) => new Promise((res) => { waiters.set(id, res);
  p.stdin.write(JSON.stringify({ jsonrpc:'2.0', id, method, params }) + '\n'); });
await call(1,'initialize',{protocolVersion:'2024-11-05',capabilities:{},clientInfo:{name:'realdata',version:'1.0'}});
const tools = await call(2,'tools/list',{});
const owner = process.env.GITQ_OWNER || 'myorg';
const rDocs = await call(3,'tools/call',{name:'search_gitea_docs',arguments:{query:'docker-compose',repo:`${owner}/dsh-*`}});
const rRead = await call(4,'tools/call',{name:'read_gitea_file',arguments:{repo:`${owner}/dsh-gitea-search`,path:'README.md'}});
const rList = await call(5,'tools/call',{name:'list_gitea_repos',arguments:{filter:`${owner}/dsh-*`}});
const rCn = await call(6,'tools/call',{name:'search_gitea_docs',arguments:{query:'镜像'}});
p.kill('SIGTERM');
const names = (tools.result?.tools||[]).map(t=>t.name).sort().join(',');
const docsText = rDocs.result?.content?.[0]?.text || '';
const readText = rRead.result?.content?.[0]?.text || '';
const listText = rList.result?.content?.[0]?.text || '';
const cnText = rCn.result?.content?.[0]?.text || '';
const ok = names.includes('search_gitea_docs') && names.includes('read_gitea_file') && names.includes('list_gitea_repos')
  && docsText.includes('相关文档') && readText.includes('dsh-gitea-search') && listText.includes('dsh-gitea-search') && cnText.includes('相关文档');
console.log(ok ? 'MCP_REAL_OK' : 'MCP_REAL_FAIL:' + JSON.stringify({names, docs:docsText.slice(0,200), read:readText.slice(0,120), list:listText.slice(0,120), cn:cnText.slice(0,120)}));
process.exit(ok?0:1);
EOF
)
echo "  $MCP_OUT"
assert_ok "MCP 工具清单含 search_gitea_docs/read_gitea_file/list_gitea_repos" "echo \"\$MCP_OUT\" | grep -q 'MCP_REAL_OK'"

# ── Phase 5: 真实环境耗时/磁盘 I/O 监测与宿主 DSH 会话健康诊断 ───────────────
echo ""
echo "[Phase 5] 真实环境耗时/磁盘 I/O 监测 (--perf-stats) 与健康诊断 (--doctor)"
assert_ok "真实镜像 --perf-stats --json 含 fts_search 耗时与 I/O 统计" "\"$GTS\" --perf-stats --json | python3 -c 'import sys,json; d=json.load(sys.stdin); assert d[\"total_events\"]>=1 and \"fts_search\" in d[\"operations\"]'"
assert_ok "真实镜像 --doctor --json FTS5 四表 1:1 对齐且 Overall=OK" "\"$GTS\" --doctor --json | python3 -c 'import sys,json; d=json.load(sys.stdin); assert d[\"fts_index\"][\"aligned_1to1\"] is True and d[\"overall_status\"]==\"OK\"'"

echo ""
echo "=================================================================="
echo " 真实数据验收汇总: ${PASS} 绿 (PASS) / ${FAIL} 红 (FAIL)"
echo "=================================================================="
[ "$FAIL" -eq 0 ] || exit 1
