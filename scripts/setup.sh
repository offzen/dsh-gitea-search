#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== dsh-gitea-search 环境预检与初始化 ($ROOT_DIR) ==="

# 1. 检查 Python 3 与 Jieba
echo -n "[1/7] 检查 Python 3 与 jieba ... "
if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ 缺少 python3，请先安装"
    exit 1
fi

if python3 -c "import jieba" >/dev/null 2>&1; then
    echo "✅ 已安装 (python3-jieba)"
else
    echo "⚠️ 未安装 jieba，正在尝试安装 python3-jieba ..."
    if command -v apt-get >/dev/null 2>&1 && [ "$(id -u)" -eq 0 ]; then
        DEBIAN_FRONTEND=noninteractive apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3-jieba
        echo "✅ python3-jieba 安装完成"
    else
        echo "❌ 请手动执行: pip install jieba 或 apt install python3-jieba"
        exit 1
    fi
fi

# 2. 检查 Node.js
echo -n "[2/7] 检查 Node.js ... "
if command -v node >/dev/null 2>&1; then
    NODE_VER=$(node -v)
    echo "✅ $NODE_VER"
else
    echo "❌ 缺少 Node.js (推荐 v18+)"
    exit 1
fi

# 3. 检查 Git
echo -n "[3/7] 检查 Git ... "
if command -v git >/dev/null 2>&1; then
    echo "✅ $(git --version)"
else
    echo "❌ 缺少 git，请先安装"
    exit 1
fi

# 4. 检查 SQLite3 FTS5 支持
echo -n "[4/7] 检查 SQLite3 FTS5 支持 ... "
if python3 -c "import sqlite3; conn = sqlite3.connect(':memory:'); conn.execute('CREATE VIRTUAL TABLE t USING fts5(x);');" >/dev/null 2>&1; then
    echo "✅ FTS5 引擎支持正常"
else
    echo "❌ 当前 Python/SQLite 缺少 FTS5 模块支持"
    exit 1
fi

# 5. 安装 MCP SDK 依赖 (@modelcontextprotocol/sdk)
echo -n "[5/7] 检查 MCP SDK 依赖 ... "
if [ -d "$ROOT_DIR/node_modules/@modelcontextprotocol/sdk" ]; then
    echo "✅ node_modules/@modelcontextprotocol/sdk 已就绪"
elif [ -d "$HOME/gitea-search/node_modules/@modelcontextprotocol/sdk" ]; then
    ln -sfn "$HOME/gitea-search/node_modules" "$ROOT_DIR/node_modules"
    echo "✅ 已复用 $HOME/gitea-search/node_modules"
elif [ -d "/srv/gitea-tool/node_modules/@modelcontextprotocol/sdk" ]; then
    ln -sfn /srv/gitea-tool/node_modules "$ROOT_DIR/node_modules"
    echo "✅ 已复用 /srv/gitea-tool/node_modules"
else
    echo "正在安装 npm 依赖 ..."
    (cd "$ROOT_DIR" && npm install --no-audit --no-fund --silent)
    echo "✅ @modelcontextprotocol/sdk 安装完成"
fi

# 6. 注册全局 CLI 软链 (gts / gitq / dsh-search) 及兼容软链
echo -n "[6/7] 配置可执行权限与 CLI 软链 ... "
chmod +x "$ROOT_DIR/bin/gts" "$ROOT_DIR/bin/mcp-server" "$ROOT_DIR/lib/indexer.py" "$ROOT_DIR/lib/server.mjs" "$ROOT_DIR/scripts/dsh-session-health.mjs"
ln -sf "$ROOT_DIR/bin/gts" "$ROOT_DIR/bin/gitq"
ln -sf "$ROOT_DIR/bin/gts" "$ROOT_DIR/bin/dsh-search"
ln -sf "$ROOT_DIR/bin/mcp-server" "$ROOT_DIR/bin/mcp-server-gitea-search"
ln -sf "$ROOT_DIR/lib/server.mjs" "$ROOT_DIR/server.mjs"
ln -sf "$ROOT_DIR/lib/indexer.py" "$ROOT_DIR/indexer.py"
if [ "${SKIP_GLOBAL_LINKS:-0}" != "1" ] && [ -w /usr/local/bin ]; then
    ln -sf "$ROOT_DIR/bin/gts" /usr/local/bin/gts
    ln -sf "$ROOT_DIR/bin/gts" /usr/local/bin/gitq
    ln -sf "$ROOT_DIR/bin/gts" /usr/local/bin/dsh-search
fi
echo "✅ CLI 软链 (gts / gitq / dsh-search) 已就绪"

# 7. 预检测宿主 DSH 会话存储格式 (v1~v4) 与索引健康度（防 <v4 会话冷迁移风暴）
echo "[7/7] 预检测宿主 DSH 会话格式与索引健康度 ..."
FIX_FLAG="--check"
if [ "${DSH_AUTO_FIX_SESSIONS:-0}" = "1" ] || [ "${1:-}" = "--fix-sessions" ]; then
    FIX_FLAG="--fix"
fi
node "$ROOT_DIR/scripts/dsh-session-health.mjs" "$FIX_FLAG" || true

echo "=== 全部依赖与安装预检通过！插件已就绪 ==="
