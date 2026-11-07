# 安装后的 5 分钟验收

适用于 Linux、macOS 与 Windows 的 `dsh-gitea-search` 安装完成后核验。这里的检查不会自动同步 Gitea、修改 profile、重建索引或重启 DSH。

## 1. 确认配置与镜像

推荐先运行只读汇总助手；它不会同步、索引、编辑 profile 或 reload 宿主：

```sh
# Linux / macOS
bash scripts/onboarding.sh --verify
```

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File scripts\onboarding.ps1 -Verify
```

也可以按下面各步手工核查。先设置有效 owner 与镜像路径（或确认 DSH profile 已包含这两个环境变量）：

```sh
gts --show-effective-config
gts --repos --json
```

预期：`valid` 为 `true`，`owners` 是真实组织/用户名；`--repos` 返回至少一个镜像仓。若镜像为空，先确认配置与凭据，再由你显式执行 `bash scripts/sync.sh -q`。同步会访问 Gitea 并写入本地 bare 镜像，不由验收脚本自动触发。

## 2. 确认 FTS 索引与健康状态

```sh
gts --fts-stats
gts --doctor --json
```

若 FTS 索引不存在或不对齐，在确认镜像路径正确后显式执行 `gts --reindex`。`--doctor` 的 DSH 会话检查是附加健康信息；宿主未安装或无法访问时可显示未检测到。

同步状态说明：成功同步时间只来自 `state/last_ok`；`last_sync` 可能只代表一次失败尝试。无记录表示镜像可能手工建立，不等于损坏。工具返回 `MIRROR_STALE` 是提示而非拦截，不会自动同步。

## 3. 验证独立 MCP stdio server

POSIX:

```sh
bash scripts/doctor-mcp.sh --verify
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\doctor-mcp.ps1 -Verify
```

探针会对**独立启动的 server 进程**执行 MCP initialize、tools/list 与一次 `search_gitea` 调用。它不能证明 DSH 已加载 profile 或宿主侧注册名是什么。

MCP 裸工具名为 `search_gitea`、`search_gitea_docs`、`read_gitea_file`、`list_gitea_repos`。宿主可能添加前缀或改名；profile 重载后请在实际工具表核对。示例 `mcp__gitea-search__search_gitea` 仅为命名示意，不保证每个宿主都使用该格式。

## 4. 让配置生效并回滚

安装器不会重启 DSH。profile 变更只有在宿主重新加载该 profile / MCP server 后才生效。由管理员/用户按自己宿主的操作方式执行 reload，再检查工具表并调用 `list_gitea_repos`。

若接入造成问题，恢复 `profile-patch.py` 创建的带时间戳 `.bak-*` 备份，再重新加载 profile。不要为回滚而重启整个 DSH 服务，除非你确认了解会话中断影响。

## 平台说明

- Linux/macOS：`bash scripts/sync.sh -q` 是显式同步入口；macOS 默认数据目录包含 `Application Support` 空格，命令与 profile 路径须保留引号。
- Windows：`gts` 应使用 `pip install .` 生成的 `gts.exe`。`sync.sh` 是 Bash 脚本，不由 PowerShell 原生执行；需要同步时使用 Git Bash，或使用已配置的 Git 镜像流程。
- npm 安装不会生成 `gts` CLI；本项目的 npm 依赖只提供 MCP Node SDK。Python wheel/sdist 用 Python 安装器管理 CLI。
- 配置示例请用 YAML 单引号；Windows 路径优先写正斜杠，例如 `'C:/Users/name/.../gts.exe'`，并确认最终 `GTS_BIN` 字面量路径确实存在。

查看更多：[`MANUAL.md`](../MANUAL.md)、[`docs/USAGE.md`](USAGE.md)、[`docs/TOOL-NAMING.md`](TOOL-NAMING.md)。
