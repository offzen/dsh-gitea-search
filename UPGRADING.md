# 升级指南：从 v1.2.x（`dsh-gitea-search`）升级到 v2.0.0（`gitea-search-mcp`）

`v2.0.0` 将项目从 DeepSeek Harness (DSH) 专属插件演进为**面向任意 MCP 宿主的通用 Gitea / Forgejo / 本地 Git 裸镜像检索引擎**（支持 Claude Desktop、Cursor、Cline、Windsurf、Codex、DSH 等），同时完整继承 `v1.2.3` 的跨平台（Linux / macOS / 原生 Windows）可靠性修复。

本版本为老用户提供**平滑兼容过渡期**：旧工具名、旧环境变量、旧默认配置路径与 CLI 别名均保留至 `v3.0.0` 之前。

---

## 1. 一览：v2.0.0 变了什么、什么没变

| 项目 | v1.2.x | v2.0.0 | 对老用户的影响 |
|---|---|---|---|
| **仓库与包名** | `dsh-gitea-search` | `gitea-search-mcp` | GitHub 自动重定向旧仓库 URL；建议择机更新本地 `git remote` |
| **运行依赖** | Node.js ≥ 20.11 + Python ≥ 3.10 + `jieba` + Git ≥ 2.25 | **保持不变（Python 为必备依赖）** | 无新增运行时依赖；不提供无 Python 降级模式 |
| **MCP 裸工具名** | `search_gitea` / `search_gitea_docs` / `read_gitea_file` / `list_gitea_repos` | `search_code` / `search_docs` / `read_file` / `list_repositories` | `tools/list` 仅返回新名；`tools/call` **继续透明转发两族旧名**（`search_gitea*`、`search_gitorca*`）并在 stderr 输出废弃告警 |
| **环境变量** | `GITEA_*` 与部分 `GITQ_*` 混用 | 统一以 `GITEA_*` 为主 | 旧 `GITQ_*` / `GTS_BIN` / `GITQ_BIN` 继续兜底生效 |
| **默认配置目录** | `~/.config/dsh-gitea-search/config.json` | `~/.config/gitea-search-mcp/config.json` | 新路径不存在时自动回退读取旧路径并提示迁移 |
| **CLI 命令入口** | `gts` / `gitq` / `dsh-search` | **全部保留** | 现有脚本和 cron 任务无需修改 |
| **SQLite FTS5 索引** | `v1.2.2`: `user_version=0/1`（裸仓名）<br>`v1.2.3`: `user_version=2`（`owner/repo`） | `user_version=2`（`owner/repo`） | **`v1.2.3 → v2.0.0` 免重建**；**`≤ v1.2.2 → v2.0.0` 需重建一次索引** |

---

## 2. 按当前版本选择升级路径

### 路径 A：从 `v1.2.3` 升级到 `v2.0.0`（免重建索引）

1. **拉取新版本代码并更新依赖**：
   ```bash
   git fetch --tags
   git checkout v2.0.0
   npm ci --omit=dev
   python3 -m pip install --force-reinstall '.[fts]'
   ```
   *(Windows PowerShell 同样在项目根目录执行 `npm ci --omit=dev` 与 `python -m pip install --force-reinstall ".[fts]"`。)*

2. **执行只读自检**：
   ```bash
   # Linux / macOS
   bash scripts/doctor-mcp.sh

   # Windows (PowerShell)
   powershell -ExecutionPolicy Bypass -File scripts\doctor-mcp.ps1
   ```
   确认握手成功、4 个标准工具正常列出且索引检查无报错。

3. **重载 MCP 宿主**：重启或重载你的 DSH / Claude Desktop / Cursor / Cline。

---

### 路径 B：从 `v1.2.2`（或更早版本）升级到 `v2.0.0`（需重建索引）

`v1.2.3+` 引入了带 `owner/repo` 身份隔离的索引格式（`PRAGMA user_version = 2`）。如果直接打开旧版索引，`search_docs` 会安全拦截并报 `INDEX_REBUILD_REQUIRED`。

1. **先备份现有 SQLite 索引库（保障可无损回滚）**：
   ```bash
   # 将 /srv/gitea-mirror 替换为你的实际 GITEA_MIRROR_ROOT
   cp -a /srv/gitea-mirror/state/gitea-fts.sqlite /srv/gitea-mirror/state/gitea-fts.sqlite.v122.bak
   ```
   *(如存在 `gitea-fts.sqlite-wal` / `gitea-fts.sqlite-shm` 文件，请先确保无正在运行的同步/索引任务，或一并备份。)*

2. **拉取 `v2.0.0` 并重装依赖**：
   ```bash
   git fetch --tags
   git checkout v2.0.0
   npm ci --omit=dev
   python3 -m pip install --force-reinstall '.[fts]'
   ```

3. **执行一次全量索引重建**：
   ```bash
   gts --reindex --full
   ```
   *(或指定配置：`gts --config /path/to/config.json --reindex --full`。)*

4. **运行自检并验证搜索**：
   ```bash
   bash scripts/doctor-mcp.sh
   gts -D "测试关键词"
   ```

---

## 3. DeepSeek Harness (DSH) 生产环境两步切换指南

> **重要原理**：MCP 宿主通常会把配置里的 `serverName` 与裸工具名拼成宿主函数名（如 `mcp__<serverName>__<toolName>`）。服务端内部的 `ALIASES` 只能承接旧裸工具名，**不能**抵消你修改 `serverName` 带来的函数名变化。因此生产 DSH 升级必须拆成两步，**切勿合并操作**。

### Step A：零契约变更升级内核（推荐先完成并观察）

- **完全不动** `cordis.patch.yml` 中的 `serverName`、不改已有 Prompt/Skill 中的 `search_gitea*` 名称、不改旧 `GITQ_*` 环境变量；
- 仅更新代码目录到 `v2.0.0`（若从 `v1.2.2` 升级则按路径 B 重建索引）并重载 DSH；
- 验证现有任务中的 `search_gitea` / `search_gitea_docs` / `read_gitea_file` / `list_gitea_repos` 均可正常返回（stderr 会打印预期中的废弃提示，不影响工具返回值）。

### Step B：择机迁移至通用契约（Step A 稳定后再做）

1. 将启动入口从 `lib/server.mjs` 更新为 `bin/mcp-server`（两者皆可运行，推荐统一使用 `bin/mcp-server`）；
2. 将 `GITQ_*` 环境变量替换为对应 `GITEA_*` 名称（见第 4 节对照表）；
3. 将配置文件从 `~/.config/dsh-gitea-search/config.json` 复制迁移到 `~/.config/gitea-search-mcp/config.json`；
4. 在宿主工具表中确认新注册名后，再同步更新你的 DSH Skill 与系统提示词中的工具名：
   - `search_gitea` → `search_code`
   - `search_gitea_docs` → `search_docs`
   - `read_gitea_file` → `read_file`
   - `list_gitea_repos` → `list_repositories`

---

## 4. 环境变量与配置迁移对照表

| 旧变量（已废弃，v2.x 仍兜底） | v2.0.0 标准变量 | 说明 |
|---|---|---|
| `GITQ_MIRROR_ROOT` / `GITEA_MIRROR_PATH` | `GITEA_MIRROR_ROOT` | 本地 bare 镜像根目录 |
| `GITQ_OWNER` / `GITEA_OWNER` | `GITEA_OWNERS` | 仓库所属组织/用户，支持逗号分隔 |
| `GITQ_API_BASE` / `GITEA_API_BASE` | `GITEA_URL` | Gitea / Forgejo 实例根地址 |
| `GITQ_API_TOKEN` / `GITEA_API_TOKEN` | `GITEA_TOKEN` | 只读访问令牌（`repo:read`） |
| `GITQ_BIN` / `GTS_BIN` | `GITEA_SEARCH_CLI`（仍兼容 `GTS_BIN`） | 显式指定 `gts` / `gts.exe` 路径 |
| `GITQ_VENV_PATH` | `GITEA_VENV_PATH` | 指定 Python 虚拟环境路径 |
| `GITQ_ALLOW_NO_JIEBA` | `GITEA_ALLOW_NO_JIEBA` | 显式允许无 jieba 时仅建英文索引（设为 `1`） |
| `GITQ_MAX_RESULTS` | `GITEA_MAX_RESULTS` | 单次检索结果硬上限（默认 `200`） |
| `GITQ_MAX_SEARCH_BYTES` | `GITEA_MAX_SEARCH_BYTES` | 搜索总输出字节上限（默认 `131072`） |
| `GITQ_MAX_LINE_BYTES` | `GITEA_MAX_LINE_BYTES` | 单行最大输出字节（默认 `2000`） |
| `GITQ_MAX_READ_BYTES` | `GITEA_MAX_READ_BYTES` | `read_file` 单次最大读取字节（默认 `60000`） |
| `GITQ_READ_DEFAULT_LINES` | `GITEA_READ_DEFAULT_LINES` | `read_file` 默认返回行数（默认 `400`） |

---

## 5. 其他 MCP 宿主快速接入（Claude Desktop / Cursor / Cline）

在宿主的 MCP JSON 配置中加入：

```json
{
  "mcpServers": {
    "gitea-search": {
      "command": "node",
      "args": ["/path/to/gitea-search-mcp/bin/mcp-server"],
      "env": {
        "GITEA_MIRROR_ROOT": "/path/to/gitea-mirror",
        "GITEA_OWNERS": "your-org,your-user"
      }
    }
  }
}
```

如需离线导出 Function Calling Schema 供非 stdio 编排框架使用：

```bash
node /path/to/gitea-search-mcp/bin/mcp-server --dump-schema
```

---

## 6. 回滚手册

若升级后遇到任何异常，可按原起点快速回滚：

- **回滚至 `v1.2.3`**（索引格式一致，无需动库）：
  ```bash
  git checkout v1.2.3
  npm ci --omit=dev
  python3 -m pip install --force-reinstall '.[fts]'
  ```
- **回滚至 `v1.2.2`**（同时恢复旧版索引副本）：
  ```bash
  git checkout v1.2.2
  npm ci --omit=dev
  python3 -m pip install --force-reinstall '.[fts]'
  rm -f /srv/gitea-mirror/state/gitea-fts.sqlite-wal /srv/gitea-mirror/state/gitea-fts.sqlite-shm
  mv /srv/gitea-mirror/state/gitea-fts.sqlite.v122.bak /srv/gitea-mirror/state/gitea-fts.sqlite
  ```
- 最后重载 MCP 宿主即可恢复原状。
