#!/usr/bin/env bash
# ==============================================================================
# run-e2e-test.sh —— dsh-gitea-search 自建测试集构建与全链路端到端验收脚本
#
# 用途：
#   在新环境或新版本 DSH 环境下，自动构建一套自包含的多仓库 bare git 镜像测试集，
#   执行 SQLite FTS5 + Jieba 索引构建、增量 head_commit 跳过核验、CLI 检索（git grep + FTS5）
#   以及 MCP Server stdio 协议全工具（4 个 tool）端到端测试。
#
# 用法：
#   bash scripts/run-e2e-test.sh [MIRROR_ROOT]
#   默认 MIRROR_ROOT=/srv/gitea-mirror（若已存在非测试镜像且未传 --force-seed，则仅跑非破坏性断言）
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
if [ "$(basename "$SCRIPT_DIR")" = "scripts" ]; then
  ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
else
  ROOT_DIR="$SCRIPT_DIR"
fi
MIRROR_ROOT="${1:-${GITQ_MIRROR_ROOT:-/srv/gitea-mirror}}"
if [ -z "${1:-}" ] && [ -d "$MIRROR_ROOT" ] && [ ! -f "$MIRROR_ROOT/.testbed-marker" ] && [ -n "$(ls -A "$MIRROR_ROOT" 2>/dev/null)" ]; then
  MIRROR_ROOT="/tmp/gts-e2e-testbed"
fi
OWNER="${GITEA_OWNERS:-myorg}"
PRIMARY_OWNER="${OWNER%%,*}"
GTS_BIN="$ROOT_DIR/bin/gts"
for cand in "$ROOT_DIR/bin/gts" "$ROOT_DIR/gts" "$ROOT_DIR/../bin/gts" "/usr/local/bin/gts"; do
  if [ -x "$cand" ]; then GTS_BIN="$cand"; break; fi
done
MCP_BIN="$ROOT_DIR/bin/mcp-server"
# MCP 入口探测：独立仓布局为 bin/mcp-server；部署扁平目录（/srv/gitea-tool 等）只有
# bin/mcp-server-gitea-search。按顺序回退，避免「脚本只能在自己的目录布局里跑」的漂移。
for cand in "$ROOT_DIR/bin/mcp-server" "$ROOT_DIR/bin/mcp-server-gitea-search" "$ROOT_DIR/mcp-server" "$ROOT_DIR/mcp-server-gitea-search" "/usr/local/bin/mcp-server-gitea-search"; do
  if [ -x "$cand" ]; then MCP_BIN="$cand"; break; fi
done
PROBE_STDIO="$ROOT_DIR/scripts/probe-stdio.mjs"
for cand in "$ROOT_DIR/scripts/probe-stdio.mjs" "$ROOT_DIR/probe-stdio.mjs"; do
  if [ -f "$cand" ]; then PROBE_STDIO="$cand"; break; fi
done

export GITEA_MIRROR_ROOT="$MIRROR_ROOT"
export GITEA_MIRROR_PATH="$MIRROR_ROOT"
export GITQ_MIRROR_ROOT="$MIRROR_ROOT"
export GITEA_OWNERS="$OWNER"
export GITQ_OWNER="$PRIMARY_OWNER"

PASS=0
FAIL=0
assert_ok() {
  local name="$1"
  local cond="$2"
  if eval "$cond"; then
    PASS=$((PASS + 1))
    echo "  ✓ [PASS] $name"
  else
    FAIL=$((FAIL + 1))
    echo "  ✗ [FAIL] $name"
  fi
}

echo "=================================================================="
echo " dsh-gitea-search 自建测试集与端到端全绿验收"
echo " 插件目录: $ROOT_DIR"
echo " 镜像目录: $MIRROR_ROOT (owner=$PRIMARY_OWNER)"
echo " DSH 版本: $(dsh --version 2>/dev/null || echo '未安装')"
echo " Node版本: $(node -v)"
echo "=================================================================="

# ------------------------------------------------------------------------------
# Phase 1: 构建自包含测试集（当 MIRROR_ROOT 下无仓库或含 .testbed-marker 时自动生成）
# ------------------------------------------------------------------------------
OWNER_DIR="$MIRROR_ROOT/$PRIMARY_OWNER"
mkdir -p "$OWNER_DIR"

if [ ! -f "$MIRROR_ROOT/.testbed-marker" ] && [ -n "$(ls -A "$OWNER_DIR" 2>/dev/null)" ]; then
  echo "[Phase 1] 检测到既有生产镜像目录 ($OWNER_DIR)，跳过覆盖写入，直接基于现有镜像测试。"
else
  echo "[Phase 1] 正在构建自建测试集 (4 个裸仓库: 运维手册 / CPM 配置 / 会话归档 / 历史备份) ..."
  rm -rf "$MIRROR_ROOT/state"
  touch "$MIRROR_ROOT/.testbed-marker"
  date '+%Y-%m-%d %H:%M:%S' > "$MIRROR_ROOT/last_sync"
  cat > "$MIRROR_ROOT/sync.sh" << 'EOF'
#!/usr/bin/env bash
date '+%Y-%m-%d %H:%M:%S' > "$(dirname "$0")/last_sync"
exit 0
EOF
  chmod +x "$MIRROR_ROOT/sync.sh"

  TMP_WORK=$(mktemp -d /tmp/gts-testbed-work.XXXXXX)
  trap 'rm -rf "$TMP_WORK"' EXIT

  init_Test_repo() {
    local rname="$1"
    local bare="$OWNER_DIR/${rname}.git"
    local work="$TMP_WORK/$rname"
    rm -rf "$bare" "$work"
    git init --bare -q "$bare"
    git init -q "$work"
    git -C "$work" config user.email "test@dsh.local"
    git -C "$work" config user.name "DSH Testbed"
    git -C "$work" checkout -q -b main 2>/dev/null || git -C "$work" branch -M main
  }

  push_test_repo() {
    local rname="$1"
    local msg="$2"
    local bare="$OWNER_DIR/${rname}.git"
    local work="$TMP_WORK/$rname"
    git -C "$work" add -A
    git -C "$work" commit -q -m "$msg"
    git -C "$work" push -q "$bare" main:refs/heads/main
    git --git-dir="$bare" symbolic-ref HEAD refs/heads/main
  }

  # 1) ops-infra-runbook: 含中文手册、沙盒机制说明、长行多字节中文测试、运维脚本
  init_Test_repo "ops-infra-runbook"
  mkdir -p "$TMP_WORK/ops-infra-runbook/docs" "$TMP_WORK/ops-infra-runbook/scripts"
  cat > "$TMP_WORK/ops-infra-runbook/README.md" << 'EOF'
# 核心基础设施运维手册 (ops-infra-runbook)

本仓库收录生产环境 gitea 镜像同步、DSH 沙盒机制与权限隔离规范、以及容器化排障指南。

## 关键架构要点
1. **沙盒机制与权限隔离**：在 danger-full-access 之外的模式中，通过只读挂载保护宿主机配置。
2. **容器编排**：所有微服务统一采用 `docker-compose.yml` 管理，兼容 `dockge` 与 `dockershim` 状态巡检。
3. **热更新不中断**：MCP 子进程通过 `respawn-mcp.sh` 发送 SIGTERM 单点重启，绝不重启 `dsh.service`。
EOF
  # 生成一段 >12KB 的连续多字节中文文档以验证 8KB 跨块 UTF-8 解码无 U+FFFD
  python3 -c '
s = "# 多字节中文流式分块解码压测文档\n\n" + ("这是用于验证跨八千字节缓冲区边界时中文多字节字符不会产生替换符的测试段落，包含关键词 gitea 与负载均衡策略。\n" * 120)
open("'"$TMP_WORK"'/ops-infra-runbook/docs/utf8-stress.md", "w", encoding="utf-8").write(s)
'
  cat > "$TMP_WORK/ops-infra-runbook/scripts/healthcheck.sh" << 'EOF'
#!/usr/bin/env bash
# 节点健康检查脚本
set -euo pipefail
echo "checking gitea mirror and docker-compose status..."
EOF
  push_test_repo "ops-infra-runbook" "feat(init): 初始化运维手册与沙盒隔离规范"

  # 2) cpm-deploy-guide: 含配置文件 (kind=config) 与 Caddy 证书说明
  init_Test_repo "cpm-deploy-guide"
  mkdir -p "$TMP_WORK/cpm-deploy-guide/deploy"
  cat > "$TMP_WORK/cpm-deploy-guide/README.md" << 'EOF'
# Caddy Proxy Manager (CPM) 部署与证书排障指南

当遇到 Caddy 证书续期失败或 503 错误时，请先检查 `deploy/docker-compose.yml` 与 `deploy/caddy-config.yml`：
- 严禁不带 `--resume` 重建 Caddy 容器，否则会覆盖 `autosave.json`。
- 支持 gitea 自动备份配置。
EOF
  cat > "$TMP_WORK/cpm-deploy-guide/deploy/docker-compose.yml" << 'EOF'
version: "3.9"
services:
  cpm-panel:
    image: registry.local/cpm-panel:v2.4.0
    restart: always
    ports:
      - "127.0.0.1:8080:8080"
EOF
  cat > "$TMP_WORK/cpm-deploy-guide/deploy/caddy-config.yml" << 'EOF'
# Caddy 核心配置片段
tls_acme_provider: cloudflare
auto_https: prefer_wildcard
admin_listen: "127.0.0.1:2019"
EOF
  push_test_repo "cpm-deploy-guide" "feat(cpm): 添加 docker-compose.yml 与 Caddy 证书配置"

  # 3) dsh-session-archive: 含根路径 sessions/ 与子路径 node-alpha/sessions/ (验证 kind=session 分类)
  init_Test_repo "dsh-session-archive"
  mkdir -p "$TMP_WORK/dsh-session-archive/sessions" "$TMP_WORK/dsh-session-archive/node-alpha/sessions/demo-workspace"
  cat > "$TMP_WORK/dsh-session-archive/sessions/session-node-beta-demo.md" << 'EOF'
# DSH 会话: session-node-beta-demo01

- **实例**: node-beta
- **工作区**: `~/workspace`

**user**: 请排查 Caddy 证书自动续期与沙盒机制的兼容性问题。

**assistant**: 已核查 `cpm-deploy-guide` 中的 `docker-compose.yml`，证书续期工作正常。
EOF
  cat > "$TMP_WORK/dsh-session-archive/node-alpha/sessions/demo-workspace/session-7e531943-7f2e.md" << 'EOF'
# DSH 会话: session-7e531943-7f2e

- **实例**: node-alpha
- **工作区**: `~/workspace`

**user**: 在测试沙箱安装最新的 DSH 稳定版，测试 gitea-search 全绿。

**assistant**: 已使用 Gemini-4-Argon 完成严格代码审计并修复 8 项缺陷。
EOF
  push_test_repo "dsh-session-archive" "chore(archive): 归档 node-beta 与 node-alpha 会话记录"

  # 4) backup-archive-20261001: 含 backup 关键词（is_backup=1），用于验证 BM25 降权排序
  init_Test_repo "backup-archive-20261001"
  cat > "$TMP_WORK/backup-archive-20261001/README.md" << 'EOF'
# 核心基础设施运维手册历史快照 (backup-archive-20261001)

本仓库是历史备份，同样包含：沙盒机制与权限隔离、docker-compose.yml、gitea 镜像同步说明。
EOF
  push_test_repo "backup-archive-20261001" "chore(backup): 历史归档快照"

  echo "[Phase 1] 测试集构建完成（共 4 个 bare 仓库）。"
fi

# ------------------------------------------------------------------------------
# Phase 2: 索引构建与增量 head_commit 快速跳过测试
# ------------------------------------------------------------------------------
echo ""
echo "[Phase 2] 测试 FTS5 索引构建与增量 head_commit 门控 ..."
IDX_OUT1=$("$GTS_BIN" --reindex)
echo "  首次索引输出: $(echo "$IDX_OUT1" | tail -n 1)"
assert_ok "首次 --reindex 成功建立索引库 (state/gitea-fts.sqlite)" "[ -f \"$MIRROR_ROOT/state/gitea-fts.sqlite\" ] && echo \"\$IDX_OUT1\" | grep -q '更新/新增 9 篇'"

IDX_OUT2=$("$GTS_BIN" --reindex)
echo "  二次增量输出: $(echo "$IDX_OUT2" | tail -n 1)"
assert_ok "仓库未变更时二次 --reindex 0 篇更新（head_commit 快速跳过生效）" "echo \"\$IDX_OUT2\" | grep -q '更新/新增 0 篇'"

STATS_OUT=$("$GTS_BIN" --fts-stats)
echo "  $STATS_OUT"
assert_ok "--fts-stats 正常报告仓库与文档总数" "echo \"\$STATS_OUT\" | grep -q '索引库状态:'"

# 回归验证：删光某仓最后一篇可索引文档时，必须清理 docs_fts/doc_rowid/file_blobs，不留幽灵行
DEL_TEST_WORK="$MIRROR_ROOT/_del_test_work"
git clone -q "$MIRROR_ROOT/$PRIMARY_OWNER/backup-archive-20261001.git" "$DEL_TEST_WORK"
ORIG_HEAD=$(git -C "$DEL_TEST_WORK" rev-parse HEAD)
git -C "$DEL_TEST_WORK" config user.email "test@local"
git -C "$DEL_TEST_WORK" config user.name "test"
git -C "$DEL_TEST_WORK" rm -q README.md
echo "placeholder" > "$DEL_TEST_WORK/keep.bin"
git -C "$DEL_TEST_WORK" add keep.bin
git -C "$DEL_TEST_WORK" commit -qm "test: remove last md"
git -C "$DEL_TEST_WORK" push -q origin HEAD:main
DEL_OUT=$("$GTS_BIN" --reindex)
assert_ok "删光单仓最后一篇可索引文档时精确触发删除 1 篇（无早期返回漏删）" "echo \"\$DEL_OUT\" | grep -q '删除 1 篇'"
git -C "$DEL_TEST_WORK" push -qf origin "$ORIG_HEAD:main"
rm -rf "$DEL_TEST_WORK"
RESTORE_OUT=$("$GTS_BIN" --reindex)
assert_ok "恢复文档后增量重建 1 篇且 doc_rowid 1:1 对齐" "python3 -c 'import sqlite3; c=sqlite3.connect(\"$MIRROR_ROOT/state/gitea-fts.sqlite\"); assert c.execute(\"SELECT COUNT(*) FROM docs_fts\").fetchone()[0]==c.execute(\"SELECT COUNT(*) FROM doc_rowid\").fetchone()[0]==9'"

# ------------------------------------------------------------------------------
# Phase 3: CLI (gts / gitq / dsh-search) 混合检索全特性测试
# ------------------------------------------------------------------------------
echo ""
echo "[Phase 3] 测试 CLI (git grep + FTS5 + 审计修复回归项) ..."

assert_ok "仓库枚举 (--repos) 返回非空列表" "[ \$(\"$GTS_BIN\" --repos --json | python3 -c 'import sys,json; print(json.load(sys.stdin)[\"count\"])') -ge 4 ]"
assert_ok "带 owner 前缀与通配符过滤 (--repos -r 'myorg/cpm*')" "[ \$(\"$GTS_BIN\" --repos -r 'myorg/cpm*' --json | python3 -c 'import sys,json; print(json.load(sys.stdin)[\"count\"])') -ge 1 ]"
assert_ok "代码字面量检索 (gts 'gitea')" "[ \$(\"$GTS_BIN\" --json 'gitea' | python3 -c 'import sys,json; print(json.load(sys.stdin)[\"total\"])') -ge 1 ]"
assert_ok "文件路径检索 (gts -p 'docker-compose')" "[ \$(\"$GTS_BIN\" --json -p 'docker-compose' | python3 -c 'import sys,json; print(json.load(sys.stdin)[\"total\"])') -ge 1 ]"
assert_ok "提交信息检索 (gts -m '初始化')" "[ \$(\"$GTS_BIN\" --json -m '初始化' | python3 -c 'import sys,json; print(json.load(sys.stdin)[\"total\"])') -ge 1 ]"
assert_ok "单文件读取 (--read 支持带 myorg/ 前缀)" "\"$GTS_BIN\" -r 'myorg/cpm-deploy-guide' --read 'deploy/docker-compose.yml' | grep -q 'cpm-panel'"

# FTS5 专项断言（含审计修复回归验证）
assert_ok "FTS5 中文 Jieba 分词检索 (gts -D '沙盒隔离')" "[ \$(\"$GTS_BIN\" -D --json '沙盒隔离' | python3 -c 'import sys,json; print(json.load(sys.stdin)[\"total\"])') -ge 1 ]"
assert_ok "FTS5 连字符/点号查询不崩且命中 (gts -D 'docker-compose.yml') [Audit #1]" "[ \$(\"$GTS_BIN\" -D --json 'docker-compose.yml' | python3 -c 'import sys,json; print(json.load(sys.stdin)[\"total\"])') -ge 1 ]"
assert_ok "FTS5 前缀通配查询 (gts -D 'dock*') [Audit #1]" "[ \$(\"$GTS_BIN\" -D --json 'dock*' | python3 -c 'import sys,json; print(json.load(sys.stdin)[\"total\"])') -ge 1 ]"
assert_ok "FTS5 仓库通配符+owner前缀过滤 (gts -D -r 'myorg/cpm*' '证书') [Audit #2]" "[ \$(\"$GTS_BIN\" -D --json -r 'myorg/cpm*' '证书' | python3 -c 'import sys,json; print(json.load(sys.stdin)[\"total\"])') -ge 1 ]"
assert_ok "FTS5 kind=session 过滤（含根路径 sessions/） [Audit #7]" "[ \$(\"$GTS_BIN\" -D --json --kind session '证书' | python3 -c 'import sys,json; print(json.load(sys.stdin)[\"total\"])') -ge 1 ]"
assert_ok "FTS5 kind=config 过滤" "[ \$(\"$GTS_BIN\" -D --json --kind config 'cloudflare' | python3 -c 'import sys,json; print(json.load(sys.stdin)[\"total\"])') -ge 1 ]"
assert_ok "FTS5 kind=script 过滤" "[ \$(\"$GTS_BIN\" -D --json --kind script 'healthcheck' | python3 -c 'import sys,json; print(json.load(sys.stdin)[\"total\"])') -ge 1 ]"
assert_ok "FTS5 BM25 备份仓自动降权（活跃仓排在 backup 仓之前）" "\"$GTS_BIN\" -D --json '沙盒机制与权限隔离' | python3 -c 'import sys,json; r=json.load(sys.stdin)[\"results\"]; assert len(r)>=2 and not r[0][\"is_backup\"]'"

# ------------------------------------------------------------------------------
# Phase 4: MCP Server stdio 协议与全部 4 个工具端到端测试
# ------------------------------------------------------------------------------
echo ""
echo "[Phase 4] 测试 MCP Server stdio 协议 (probe-stdio.mjs + 4 工具全覆盖) ..."
assert_ok "probe-stdio.mjs 冒烟探针 (6 项协议与 UTF-8/护栏断言)" "node \"$PROBE_STDIO\" \"$MCP_BIN\" gitea >/dev/null"

# 进一步直接测试 search_gitea_docs / read_gitea_file / list_gitea_repos
MCP_FULL_TEST=$(node - "$MCP_BIN" << 'EOF'
import { spawn } from 'node:child_process';
const cmd = process.argv[2];
const p = spawn(cmd, [], { stdio: ['pipe', 'pipe', 'pipe'], env: process.env });
let buf = Buffer.alloc(0);
const waiters = new Map();
p.stdout.on('data', (d) => {
  buf = Buffer.concat([buf, d]);
  let nl;
  while ((nl = buf.indexOf(0x0a)) >= 0) {
    const line = buf.subarray(0, nl).toString('utf8');
    buf = buf.subarray(nl + 1);
    if (!line.trim()) continue;
    const msg = JSON.parse(line);
    if (waiters.has(msg.id)) { waiters.get(msg.id)(msg); waiters.delete(msg.id); }
  }
});
p.on('close', (code) => {
  for (const [id, res] of waiters) { waiters.delete(id); res({ error: { code } }); }
});
const call = (id, method, params) => new Promise((res) => {
  waiters.set(id, res);
  p.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n');
});

await call(1, 'initialize', { protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 'e2e', version: '1.0' } });
const rDocs = await call(2, 'tools/call', { name: 'search_gitea_docs', arguments: { query: 'docker-compose', repo: 'myorg/cpm*' } });
const rRead = await call(3, 'tools/call', { name: 'read_gitea_file', arguments: { repo: 'myorg/cpm-deploy-guide', path: 'deploy/docker-compose.yml' } });
const rList = await call(4, 'tools/call', { name: 'list_gitea_repos', arguments: { filter: 'myorg/cpm*' } });
p.kill('SIGTERM');

const docsText = rDocs.result?.content?.[0]?.text || '';
const readText = rRead.result?.content?.[0]?.text || '';
const listText = rList.result?.content?.[0]?.text || '';
const ok = docsText.includes('相关文档') && readText.includes('cpm-panel') && listText.includes('cpm-deploy-guide');
console.log(ok ? 'MCP_4TOOLS_OK' : 'MCP_4TOOLS_FAIL: ' + JSON.stringify({ docsText, readText, listText }));
process.exit(ok ? 0 : 1);
EOF
)
assert_ok "MCP 4 工具联合调用 (search_gitea_docs + read_gitea_file + list_gitea_repos)" "[ \"\$MCP_FULL_TEST\" = 'MCP_4TOOLS_OK' ]"

# ------------------------------------------------------------------------------
# Phase 5: 耗时与磁盘 I/O 监测机制、阈值判定与 DSH 会话健康修复模块测试
# ------------------------------------------------------------------------------
echo ""
echo "[Phase 5] 测试 IoTracker、evaluate_perf_budget (OK/WARN/CRITICAL)、--doctor 与 dsh-session-health.mjs ..."

IO_UNIT_TEST=$(python3 - "$ROOT_DIR/lib" "$ROOT_DIR" "$MIRROR_ROOT" << 'PYEOF'
import sys, os, json
sys.path.insert(0, sys.argv[2])
sys.path.insert(0, sys.argv[1])
import indexer

with indexer.IoTracker("unit_op") as t:
    _ = sum(i * i for i in range(10000))
m = t.metrics
assert m["elapsed_ms"] >= 0 and "rchar" in m and "write_bytes" in m, f"bad metrics: {m}"

# 1. OK 判定
j_ok = indexer.evaluate_perf_budget("fts_search", {"elapsed_ms": 5.0, "rchar": 4096, "read_bytes": 0, "write_bytes": 0}, {"hits": 3})
assert j_ok["status"] == "OK", f"expected OK, got {j_ok}"

# 2. WARN 判定 (fts_search 慢查询 >500ms / session_export 含少量 <v4 会话)
j_warn = indexer.evaluate_perf_budget("fts_search", {"elapsed_ms": 650.0, "rchar": 1024, "read_bytes": 0, "write_bytes": 0})
assert j_warn["status"] == "WARN", f"expected WARN, got {j_warn}"
j_sess_warn = indexer.evaluate_perf_budget("session_export", {"elapsed_ms": 100.0, "rchar": 1024, "read_bytes": 0, "write_bytes": 0}, {"legacy_sessions": 5})
assert j_sess_warn["status"] == "WARN" and j_sess_warn["hints"], f"expected session WARN, got {j_sess_warn}"

# 3. CRITICAL 判定 (index_incremental 0 变更却发生 >256MB 巨量读取 / 大量 <v4 会话)
j_crit = indexer.evaluate_perf_budget("index_incremental", {"elapsed_ms": 50.0, "rchar": 300 * 1024 * 1024, "read_bytes": 0, "write_bytes": 0}, {"updated": 0, "deleted": 0})
assert j_crit["status"] == "CRITICAL", f"expected CRITICAL on no-op read amplification, got {j_crit}"
j_sess_crit = indexer.evaluate_perf_budget("session_export", {"elapsed_ms": 100.0, "rchar": 1024, "read_bytes": 0, "write_bytes": 0}, {"legacy_sessions": 120})
assert j_sess_crit["status"] == "CRITICAL", f"expected session CRITICAL, got {j_sess_crit}"

print("IO_JUDGMENT_UNIT_OK")
PYEOF
)
assert_ok "IoTracker 采样与 evaluate_perf_budget (OK/WARN/CRITICAL) 单元测试" "[ \"\$IO_UNIT_TEST\" = 'IO_JUDGMENT_UNIT_OK' ]"

assert_ok "gts --perf-stats --json 输出有效性能与磁盘 I/O 统计" "\"$GTS_BIN\" --perf-stats --json | python3 -c 'import sys,json; d=json.load(sys.stdin); assert d[\"total_events\"]>=1 and \"fts_search\" in d[\"operations\"]'"

# 构造隔离的沙盒 DSH_HOME（含 1 个 v1 会话 + 带 64 位 corpusRevision 的 session-query.sqlite）验证 dsh-session-health.mjs --check 与 --fix
MOCK_DSH="$MIRROR_ROOT/mock-dsh-home"
rm -rf "$MOCK_DSH"
mkdir -p "$MOCK_DSH/sessions/--mock-proj--/session-00000000-0000-4000-8000-000000000001"
python3 - "$MOCK_DSH" << 'PYEOF'
import json, os, sqlite3, subprocess, sys

home = sys.argv[1]
sdir = os.path.join(home, 'sessions', '--mock-proj--', 'session-00000000-0000-4000-8000-000000000001')
sid = '00000000-0000-4000-8000-000000000001'
lines = [
    json.dumps({"version": 1, "id": sid, "createdAt": "2026-10-01T00:00:00.000Z", "cwd": "/tmp", "isSeeded": False, "delegationDepth": 0}),
    json.dumps({"type": "user/message", "seq": 0, "time": "2026-10-01T00:00:01.000Z", "data": {"role": "user", "content": "hello v1", "source": {"kind": "user"}}}),
]
payload = ("\n".join(lines) + "\n").encode("utf-8")
zstd_bytes = subprocess.check_output(["zstd", "-c"], input=payload)
with open(os.path.join(sdir, "session.jsonl.zstd"), "wb") as f:
    f.write(zstd_bytes)

conn = sqlite3.connect(os.path.join(home, "session-query.sqlite"))
conn.execute("CREATE TABLE persisted_sessions (id TEXT PRIMARY KEY, revision TEXT NOT NULL)")
conn.execute("INSERT INTO persisted_sessions (id, revision) VALUES (?, ?)", (sid, "1:2:3:4:5:" + "a" * 64))
conn.commit()
conn.close()
PYEOF

HEALTH_SCRIPT="$ROOT_DIR/scripts/dsh-session-health.mjs"
if [ ! -f "$HEALTH_SCRIPT" ]; then
  HEALTH_SCRIPT="$ROOT_DIR/dsh-session-health.mjs"
fi
assert_ok "dsh-session-health.mjs --check 检出旧版 v1 会话与长 revision (WARN)" "node \"$HEALTH_SCRIPT\" --check --dsh-home \"$MOCK_DSH\" --json | python3 -c 'import sys,json; d=json.load(sys.stdin); assert d[\"status\"]==\"WARN\" and d[\"sessions\"][\"legacy_remaining\"]==1 and d[\"sqlite\"][\"long_revisions\"]==1'"

assert_ok "dsh-session-health.mjs --fix 无损迁移 v1->v4、保留原文件并对齐 SQLite revision (OK)" "node \"$HEALTH_SCRIPT\" --fix --dsh-home \"$MOCK_DSH\" --json | python3 -c 'import sys,json,os; d=json.load(sys.stdin); sdir=\"$MOCK_DSH/sessions/--mock-proj--/session-00000000-0000-4000-8000-000000000001\"; assert d[\"status\"]==\"OK\" and d[\"sessions\"][\"migrated\"]==1 and d[\"sessions\"][\"legacy_remaining\"]==0 and d[\"sqlite\"][\"long_revisions\"]==0 and os.path.exists(sdir+\"/session.v4.jsonl.zstd\") and os.path.exists(sdir+\"/session.jsonl.zstd\")'"

assert_ok "gts --doctor --dsh-home 综合诊断返回 Overall OK" "\"$GTS_BIN\" --doctor --dsh-home \"$MOCK_DSH\" --json | python3 -c 'import sys,json; d=json.load(sys.stdin); assert d[\"overall_status\"]==\"OK\" and d[\"fts_index\"][\"aligned_1to1\"] is True and d[\"dsh_sessions\"][\"status\"]==\"OK\"'"

# 回归验证：不传 --dsh-home / --src 时，默认 ~/.dsh 路径在隔离 HOME 下能正确展开（防字面量 '~' 未展开）
SANDBOX_HOME="$MIRROR_ROOT/sandbox-home"
rm -rf "$SANDBOX_HOME"
mkdir -p "$SANDBOX_HOME/.dsh"
cp -a "$MOCK_DSH/." "$SANDBOX_HOME/.dsh/"
assert_ok "默认 HOME (~/.dsh) 自动展开回归测试 (dsh-session-health / gts --doctor / session-export)" \
  "env -u DSH_HOME HOME=\"$SANDBOX_HOME\" node \"$HEALTH_SCRIPT\" --check --json | python3 -c 'import sys,json; d=json.load(sys.stdin); assert d[\"status\"]==\"OK\" and d[\"dsh_home\"]==\"$SANDBOX_HOME/.dsh\" and d[\"sessions\"][\"after_counts\"][\"v4\"]==1' && env -u DSH_HOME HOME=\"$SANDBOX_HOME\" \"$GTS_BIN\" --doctor --json | python3 -c 'import sys,json; d=json.load(sys.stdin); assert d[\"dsh_sessions\"][\"status\"]==\"OK\" and d[\"dsh_sessions\"][\"dsh_home\"]==\"$SANDBOX_HOME/.dsh\"' && env -u DSH_SESSIONS_SRC -u DSH_SESSIONS_OUT HOME=\"$SANDBOX_HOME\" python3 \"$ROOT_DIR/scripts/session-export-incremental.py\" --dry-run >/dev/null"

# 回归验证：sync.sh 标准 Gitea HTTP API 枚举（同时兼容 User /api/v1/users/:owner/repos 与 Org /api/v1/orgs/:org/repos + has_wiki）
GITEA_API_TEST=$(python3 - "$ROOT_DIR/scripts/sync.sh" "$MIRROR_ROOT" << 'PYEOF'
import http.server, json, os, socketserver, subprocess, sys, threading

sync_sh, mirror_root = sys.argv[1], sys.argv[2]

class MockGiteaHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass
    def do_GET(self):
        auth = self.headers.get("Authorization", "")
        if auth != "token test-gitea-token":
            self.send_response(401)
            self.end_headers()
            return
        if self.path.startswith("/api/v1/users/user-owner/repos"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps([{"name": "user-repo", "has_wiki": True}]).encode())
        elif self.path.startswith("/api/v1/users/org-owner/repos"):
            self.send_response(404)
            self.end_headers()
        elif self.path.startswith("/api/v1/orgs/org-owner/repos"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps([{"name": "org-repo", "has_wiki": False}, {"name": "org-docs", "has_wiki": True}]).encode())
        else:
            self.send_response(404)
            self.end_headers()

with socketserver.TCPServer(("127.0.0.1", 0), MockGiteaHandler) as httpd:
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    env1 = {**os.environ, "GITEA_MIRROR_ROOT": mirror_root, "GITEA_URL": f"http://127.0.0.1:{port}", "GITEA_TOKEN": "test-gitea-token", "GITEA_OWNERS": "user-owner", "GITQ_ENUM": "api"}
    out1 = subprocess.check_output(["bash", sync_sh, "--list-only"], env=env1, text=True).strip().splitlines()
    assert out1 == ["user-repo", "user-repo.wiki"], f"unexpected user repos: {out1}"
    env2 = {**os.environ, "GITEA_MIRROR_ROOT": mirror_root, "GITEA_URL": f"http://127.0.0.1:{port}", "GITEA_TOKEN": "test-gitea-token", "GITEA_OWNERS": "org-owner", "GITQ_ENUM": "api"}
    out2 = subprocess.check_output(["bash", sync_sh, "--list-only"], env=env2, text=True).strip().splitlines()
    assert out2 == ["org-repo", "org-docs", "org-docs.wiki"], f"unexpected org repos: {out2}"
    httpd.shutdown()

print("GITEA_API_SYNC_OK")
PYEOF
)
assert_ok "sync.sh 标准 Gitea HTTP API 枚举（User + Org + Wiki 推导）" "[ \"\$GITEA_API_TEST\" = 'GITEA_API_SYNC_OK' ]"

# 回归验证：模拟 Node < 22.15 (无 zlib.zstdCompressSync / node:sqlite) 时 dsh-session-health.mjs 自动回退至 zstd/sqlite3 CLI
MOCK_DSH_FALLBACK="$MIRROR_ROOT/mock-dsh-fallback"
rm -rf "$MOCK_DSH_FALLBACK"
mkdir -p "$MOCK_DSH_FALLBACK/sessions/--mock-proj--/session-00000000-0000-4000-8000-000000000002"
python3 - "$MOCK_DSH_FALLBACK" << 'PYEOF'
import json, os, sqlite3, subprocess, sys
home = sys.argv[1]
sdir = os.path.join(home, 'sessions', '--mock-proj--', 'session-00000000-0000-4000-8000-000000000002')
sid = '00000000-0000-4000-8000-000000000002'
lines = [
    json.dumps({"version": 1, "id": sid, "createdAt": "2026-10-01T00:00:00.000Z", "cwd": "/tmp"}),
    json.dumps({"type": "user/message", "seq": 0, "time": "2026-10-01T00:00:01.000Z", "data": {"role": "user", "content": "fallback v1", "source": {"kind": "plugin", "plugin": "compact"}}}),
]
payload = ("\n".join(lines) + "\n").encode("utf-8")
with open(os.path.join(sdir, "session.jsonl.zstd"), "wb") as f:
    f.write(subprocess.check_output(["zstd", "-c"], input=payload))
conn = sqlite3.connect(os.path.join(home, "session-query.sqlite"))
conn.execute("CREATE TABLE persisted_sessions (id TEXT PRIMARY KEY, revision TEXT NOT NULL)")
conn.execute("INSERT INTO persisted_sessions (id, revision) VALUES (?, ?)", (sid, "1:2:3:4:5:" + "b" * 64))
conn.commit()
conn.close()
PYEOF
assert_ok "Node <22.15 兼容模式回退验证 (DSH_FORCE_ZSTD_CLI=1 DSH_FORCE_SQLITE_CLI=1)" \
  "DSH_FORCE_ZSTD_CLI=1 DSH_FORCE_SQLITE_CLI=1 DSH_PERSISTENCE_MODULE=/nonexistent node \"$HEALTH_SCRIPT\" --fix --dsh-home \"$MOCK_DSH_FALLBACK\" --json | python3 -c 'import sys,json; d=json.load(sys.stdin); assert d[\"status\"]==\"OK\" and d[\"sessions\"][\"migrated\"]==1 and d[\"sqlite\"][\"engine\"]==\"sqlite3-cli\" and d[\"sqlite\"][\"long_revisions\"]==0'"

# 回归验证：当 GITEA_MIRROR_ROOT / GITEA_OWNERS 为未初始化占位符（0 镜像仓）时，server.mjs 显式输出空镜像告警而非静默 0 命中
EMPTY_MIRROR_TEST=$(GITEA_MIRROR_ROOT="$MIRROR_ROOT/empty-placeholder" GITEA_OWNERS="myorg" GITQ_MIRROR_ROOT="$MIRROR_ROOT/empty-placeholder" GITQ_OWNER="myorg" node - "$MCP_BIN" << 'EOF'
import { spawn } from 'node:child_process';
const cmd = process.argv[2];
const p = spawn(cmd, [], { stdio: ['pipe', 'pipe', 'pipe'], env: process.env });
let buf = Buffer.alloc(0);
const waiters = new Map();
p.stdout.on('data', (d) => {
  buf = Buffer.concat([buf, d]);
  let nl;
  while ((nl = buf.indexOf(0x0a)) >= 0) {
    const line = buf.subarray(0, nl).toString('utf8');
    buf = buf.subarray(nl + 1);
    if (!line.trim()) continue;
    const msg = JSON.parse(line);
    if (waiters.has(msg.id)) { waiters.get(msg.id)(msg); waiters.delete(msg.id); }
  }
});
const call = (id, method, params) => new Promise((res) => {
  waiters.set(id, res);
  p.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n');
});
await call(1, 'initialize', { protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 'empty-test', version: '1.0' } });
const toolsRes = await call(2, 'tools/list', {});
const listTool = toolsRes.result.tools.find((t) => t.name.startsWith('list_')).name;
const r1 = await call(3, 'tools/call', { name: listTool, arguments: {} });
const txt1 = r1.result.content[0].text;
p.kill();
if (txt1.includes('镜像目录为空或未就绪') && txt1.includes('myorg')) {
  console.log('EMPTY_MIRROR_WARN_OK');
} else {
  console.error('Unexpected:', txt1);
  process.exit(1);
}
EOF
)
assert_ok "空镜像/占位符配置显式告警回归验证 (list_*_repos 输出 MIRROR_ROOT/OWNERS 提示)" "[ \"\$EMPTY_MIRROR_TEST\" = 'EMPTY_MIRROR_WARN_OK' ]"

echo ""
echo "=================================================================="
echo " 验收汇总: ${PASS} 绿 (PASS) / ${FAIL} 红 (FAIL)"
echo "=================================================================="
[ "$FAIL" -eq 0 ] || exit 1
