#!/usr/bin/env bash
# ==============================================================================
# setup-macos.sh —— dsh-gitea-search 在 macOS 上的一键安装 + DSH 接入
#
# 与 Linux 版 setup.sh 的差异（macOS 特有）：
#   1. jieba 走独立 venv：系统 python3 是 Homebrew 装的，带 PEP-668 EXTERNALLY-MANAGED 标记，
#      `pip install --user` 会被拒。改用 venv + 项目自带的 GITQ_VENV_PATH 机制
#      （indexer.py 会把该 venv 的 site-packages 追加到 sys.path）。
#   2. 不装 /usr/local/bin 全局软链：macOS 的 /usr/local/bin 需要 sudo。
#      改为在 ~/bin 下建软链（若 PATH 里有），并提示用户。
#   3. 依赖检查增加 jq / zstd / node 绝对路径。
#
# 用法：
#   bash scripts/setup-macos.sh                # 安装 + 建索引
#   bash scripts/setup-macos.sh --install-dsh  # 额外把 MCP 写进 DSH profile
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

MIRROR_ROOT="${GITEA_MIRROR_ROOT:-$ROOT_DIR/mirror}"
OWNERS="${GITEA_OWNERS:-myorg}"
VENV_DIR="${GITQ_VENV_DIR:-$ROOT_DIR/.venv}"

echo "=== dsh-gitea-search macOS 安装 ($ROOT_DIR) ==="

# ── 1. 依赖检查 ─────────────────────────────────────────────────────────────
echo -n "[1/6] 检查运行时依赖 ... "
missing=""
for c in python3 node git jq zstd npm; do
  command -v "$c" >/dev/null 2>&1 || missing="$missing $c"
done
if [ -n "$missing" ]; then
  echo "❌ 缺少:$missing"
  echo "   安装: brew install python3 node jq zstd"
  exit 1
fi
echo "✅ $(python3 --version | cut -d' ' -f2) / node $(node -v | cut -d' ' -f1) / $(git --version | cut -d' ' -f3) / jq / zstd"

# ── 2. SQLite FTS5 支持 ────────────────────────────────────────────────────
echo -n "[2/6] 检查 SQLite FTS5 ... "
if python3 -c "import sqlite3;c=sqlite3.connect(':memory:');c.execute('CREATE VIRTUAL TABLE t USING fts5(x)')" 2>/dev/null; then
  echo "✅"
else
  echo "❌ 当前 Python 的 SQLite 缺少 FTS5"
  exit 1
fi

# ── 3. jieba 装进独立 venv（绕过 PEP 668）──────────────────────────────────
echo -n "[3/6] 准备 jieba 中文分词 ... "
if python3 -c "import jieba" 2>/dev/null; then
  echo "✅ 系统 python 已装 jieba"
elif [ -x "$VENV_DIR/bin/python" ] && "$VENV_DIR/bin/python" -c "import jieba" 2>/dev/null; then
  echo "✅ venv 已装 jieba ($VENV_DIR)"
else
  [ -d "$VENV_DIR" ] || python3 -m venv "$VENV_DIR"
  "$VENV_DIR/bin/pip" install --quiet --upgrade pip >/dev/null 2>&1 || true
  "$VENV_DIR/bin/pip" install --quiet jieba
  echo "✅ 已装入 venv ($VENV_DIR)"
fi

# ── 4. Node 依赖（@modelcontextprotocol/sdk）──────────────────────────────
echo -n "[4/6] 安装 MCP SDK ... "
if [ -d "$ROOT_DIR/node_modules/@modelcontextprotocol/sdk" ]; then
  echo "✅ 已就绪"
else
  # npm cache 可能因权限失败，落到临时目录重试一次
  (cd "$ROOT_DIR" && npm install --no-audit --no-fund --silent) \
    || (cd "$ROOT_DIR" && npm install --no-audit --no-fund --silent --cache "$(mktemp -d)")
  echo "✅ 安装完成"
fi

# ── 5. 可执行权限 + 本地软链 ──────────────────────────────────────────────
echo -n "[5/6] 配置可执行权限与本地软链 ... "
chmod +x "$ROOT_DIR/bin/gts" "$ROOT_DIR/bin/mcp-server" "$ROOT_DIR/lib/indexer.py" \
         "$ROOT_DIR/lib/server.mjs" "$ROOT_DIR/scripts/dsh-session-health.mjs" \
         "$ROOT_DIR/scripts/setup-macos.sh"
ln -sf "$ROOT_DIR/bin/gts" "$ROOT_DIR/bin/gitq"
ln -sf "$ROOT_DIR/bin/gts" "$ROOT_DIR/bin/dsh-search"
ln -sf "$ROOT_DIR/bin/mcp-server" "$ROOT_DIR/bin/mcp-server-gitea-search"
# macOS: 往 ~/bin 放软链（用户自建目录，无需 sudo）
mkdir -p "$HOME/bin"
ln -sf "$ROOT_DIR/bin/gts" "$HOME/bin/gts"
ln -sf "$ROOT_DIR/bin/gts" "$HOME/bin/gitq"
ln -sf "$ROOT_DIR/bin/gts" "$HOME/bin/dsh-search"
ln -sf "$ROOT_DIR/bin/mcp-server" "$HOME/bin/mcp-server-gitea-search"
case ":$PATH:" in
  *":$HOME/bin:"*) echo "✅ ~/bin 已在 PATH" ;;
  *) echo "✅ 已建 ~/bin 软链（建议把 ~/bin 加入 PATH）" ;;
esac

# ── 6. 建索引 ──────────────────────────────────────────────────────────────
echo -n "[6/6] 构建 FTS5 索引 ... "
export GITEA_MIRROR_ROOT="$MIRROR_ROOT"
export GITEA_OWNERS="$OWNERS"
export GITQ_VENV_PATH="$VENV_DIR"
if [ -d "$MIRROR_ROOT/$OWNERS" ] && [ -n "$(ls -A "$MIRROR_ROOT/$OWNERS" 2>/dev/null)" ]; then
  python3 "$ROOT_DIR/lib/indexer.py" --build 2>&1 | tail -2
  echo "✅ 索引完成"
else
  echo "⚠️ 镜像目录为空，跳过（先用 scripts/sync.sh 同步 Gitea，或用 --make-demo 建演示库）"
fi

cat <<EOF

=== 安装完成 ===

环境变量（写进 ~/.zshrc）：
  export GITEA_MIRROR_ROOT="$MIRROR_ROOT"
  export GITEA_OWNERS="$OWNERS"
  export GITQ_VENV_PATH="$VENV_DIR"

试一下：
  $ROOT_DIR/bin/gts --repos
  $ROOT_DIR/bin/gts -D "关键词"
  $ROOT_DIR/bin/gts "代码符号"

EOF

# ── 可选：写入 DSH profile ────────────────────────────────────────────────
if [ "${1:-}" = "--install-dsh" ]; then
  echo "=== 接入 DSH profile ==="
  PROFILE_DIR="${DSH_PROFILE_DIR:-$HOME/.dsh/profiles/desktop}"
  PATCH_FILE="$PROFILE_DIR/cordis.patch.yml"
  [ -f "$PATCH_FILE" ] || { echo "❌ 找不到 $PATCH_FILE"; exit 1; }

  NODE_BIN="$(command -v node)"
  # 追加（幂等：先删掉同名旧行）。注意 cordis.patch.yml 顶层必须是**一个** YAML 数组，
  # 模板里的 [] 是占位符，直接在其后追加会形成两个数组 → 解析报错。
  python3 - "$PATCH_FILE" "$ROOT_DIR" "$MIRROR_ROOT" "$OWNERS" "$VENV_DIR" "$NODE_BIN" <<'PYEOF'
import sys, re
path, root, mirror, owners, venv, node_bin = sys.argv[1:7]
entry = f"""
- insert:
    - id: mcp-gitea-search
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: gitea_search
        transport: stdio
        command: {node_bin}
        args: ['{root}/lib/server.mjs']
        cwd: {root}
        env:
          GITEA_MIRROR_ROOT: {mirror}
          GITEA_OWNERS: {owners}
          GITQ_VENV_PATH: {venv}
"""
s = open(path, encoding="utf-8").read()
# 去掉旧的 mcp-gitea-search 块（含可能存在的裸 - id: 形式）
s = re.sub(r"\n- insert:\n(?:[ \t]+.*\n)*?[ \t]+- id: mcp-gitea-search\n(?:[ \t]+.*\n)*", "\n", s)
s = re.sub(r"\n- id: mcp-gitea-search\n(?:[ \t]+.*\n)*", "\n", s)
if s.strip() == "[]":
    s = s.replace("[]", entry.strip() + "\n")
else:
    s = s.rstrip("\n") + "\n" + entry
open(path, "w", encoding="utf-8").write(s)
print(f"已写入 {path}")
PYEOF
  echo "✅ DSH profile 已更新（改 cordis.patch.yml 后 DSH 会 HMR 热加载；桌面端若未生效，重启 DSH 应用）"
fi
