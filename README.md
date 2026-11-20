# gitea-search-mcp

[![License](https://img.shields.io/badge/License-Apache--2.0-C21325)](LICENSE)
[![CI](https://github.com/offzen/gitea-search-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/offzen/gitea-search-mcp/actions/workflows/ci.yml)
[![Windows CI](https://github.com/offzen/gitea-search-mcp/actions/workflows/windows-ci.yml/badge.svg)](https://github.com/offzen/gitea-search-mcp/actions)
[![Version](https://img.shields.io/badge/Version-v2.0.0-blue)](RELEASE-NOTES.md)
[![Archive](https://img.shields.io/badge/Archive-v0.1.1%20(Pure%20Node)-orange)](https://github.com/offzen/gitea-search-mcp/tree/legacy-0.1.x)
[![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20macOS%20%7C%20Windows%20(Native)-brightgreen)](MANUAL.md#3-多场景部署流程)
[![Node](https://img.shields.io/badge/Node-%3E%3D20.11-success)](package.json)

> 本仓库文档描述 `v2.0.0` 的版本内容；正式发布状态以对应 Release 记录为准，验收结论以同一 exact-SHA 的 provenance、收据与严格预检为准。版本号本身不证明工作树已通过发布门禁。历史稳定版 `v1.2.2` 已于 2026-11-11 正式发布，`v1.2.3` 已完成发布。

**面向任意 MCP 宿主的通用 Gitea / Forgejo / 本地 Git 裸镜像混合检索 MCP Server**（支持 **Claude Desktop**、**Cursor**、**Cline**、**Windsurf**、**Codex**、**DeepSeek Harness (DSH)** 等）：把 Gitea/Forgejo 上全部仓库（或任意本地 bare Git 镜像）维护在宿主机本地，让 AI Agent 一次工具调用即可精准定位全仓代码、文档、配置与历史排障记录——不用逐个 clone、不把大文件塞进上下文、防止海量噪音稀释模型注意力。

> SPDX-License-Identifier: Apache-2.0 · 原仓库 `dsh-gitea-search` 已演进为通用 MCP 服务 `gitea-search-mcp`；从 `v1.2.x` 升级的老用户请直接阅读 **[UPGRADING.md](UPGRADING.md)**（保留完整向后兼容别名与配置路径回退）。早期纯 Node.js 原型保留在 [`legacy-0.1.x`](https://github.com/offzen/gitea-search-mcp/tree/legacy-0.1.x) 分支。

---

## 架构定位与解决的核心痛点

本服务基于本地 bare 裸镜像目录（`<root>/<owner>/<repo>.git`）提供**两层极速检索引擎**，通过标准 Model Context Protocol (MCP) `stdio` 接口暴露给任意 MCP 客户端：

1. **代码检索引擎（`git grep`）**：跨全量仓库、全部本地分支、文件路径及提交记录的毫秒级检索；支持多词短语连写 0 命中时自动回退为「同行多词 AND（`--and`）」模式。
2. **文档与会话全文检索（SQLite FTS5 + `jieba` 中文分词）**：索引每仓默认分支，SQLite warm query 可毫秒级响应，支持 BM25 与内容类型分区；当前 MCP 端到端延迟还包含 Python/jieba 冷启动。非默认分支请用 `search_code` 定位后按 `ref` 精读。
3. **Zero-Gitea 本地演示模式**：无需远端 Gitea，由脚本在指定输出目录生成一个合成的 `demo-project.git` bare 仓并构建索引，用来验证 CLI/MCP 基本链路。它**不会扫描或导入已有本地 Git 仓库**。
4. **离线 Function Calling Schema 导出（`--dump-schema`）**：支持非 stdio 编排框架纯静态导出 OpenAI/Function Calling 兼容 JSON Schema，不读镜像、不 spawn 子进程。

## 不仅省上下文预算，更防模型注意力稀释与失忆

| 没有它（Agent 原生手段盲目翻找） | 有它（本地两层索引靶向直达） | 认知与注意力保护 |
|---|---|---|
| 逐个 clone 仓库或抓取网页寻找配置 | 一次工具调用覆盖**全量仓库 + 全部本地分支** | 消除多轮探测命令，杜绝过程噪音 |
| 网页搜索引入的 HTML 与无关长文全量灌入上下文 | 只返回匹配精准行，默认 Top 5（先定位、再精读） | **高信噪比注入，防止核心指令被冲淡稀释** |
| 遇到大文件不得不整篇塞入上下文 | 默认仅读取前 400 行，支持 `offset`/`limit` 按需切片续读 | 消除非相关代码对有限注意力资源的挤占 |
| 撞上编译打包产物或超长文本，数十万 Token 瞬间爆窗 | 单行 ≤ 2000B、单次 ≤ 128KiB **原地安全截断** | **物理护栏熔断，守住长程推理的记忆基线** |
| 中文/多词查询历史排障，内置分词极易 0 命中 | SQLite FTS5 + jieba 分词，中文短语、多词跨行精准召回 | 1 步直达事实，避免盲目试错引发上下文污染 |

> **为什么采用“两步检索法”**：
> 先查少量命中行定位，再按需切片精读，可以避免逐仓盲搜和整篇加载无关文件，减少工具往返与注入上下文的体积。实际节省幅度取决于仓库规模、查询复杂度、模型分词器与缓存策略；本项目不把缺少标准化基准方法学的单次观测当作通用性能承诺。

---

## 主流 MCP 客户端接入

### Claude Desktop / Cursor / Cline / Windsurf / Codex（标准 MCP JSON）

在宿主的 MCP 配置文件（如 Claude Desktop `claude_desktop_config.json`、Cursor `.cursor/mcp.json`、Cline MCP Settings）中加入：

```json
{
  "mcpServers": {
    "gitea-search": {
      "command": "node",
      "args": ["/path/to/gitea-search-mcp/bin/mcp-server"],
      "env": {
        "GITEA_MIRROR_ROOT": "/srv/gitea-mirror",
        "GITEA_OWNERS": "my-org,my-team"
      }
    }
  }
}
```

### DeepSeek Harness (DSH)

在 DSH 配置文件（如 `cordis.patch.yml`）中添加如下片段（顶层必须是 YAML 数组；老用户 `args: [".../lib/server.mjs"]` 亦继续兼容）：

```yaml
- insert:
    - id: mcp-gitea-search
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: gitea-search        # 宿主可能据此生成前缀；实际调用名以工具注册表为准
        transport: stdio                # 必填项：缺失会导致连接静默失败
        command: node
        args: ["/opt/gitea-search-mcp/bin/mcp-server"]
        cwd: /opt/gitea-search-mcp
        env:
          GITEA_MIRROR_ROOT: "/srv/gitea-mirror"
          GITEA_OWNERS: "my-org,my-team"
```

> **配置边界与平滑迁移**：运行时 CLI/MCP/indexer 只从 `config.json` 读取 `mirror_root` 与 `owners`；同步 URL、token、枚举/传输策略及输出护栏仍由环境变量控制。推荐把共享运行时配置放在 `~/.config/gitea-search-mcp/config.json`（若新路径不存在会自动回退读取旧 `~/.config/dsh-gitea-search/config.json` 并在 stderr 提示迁移），或用 `GITEA_SEARCH_CONFIG` 显式指定。详见 [MANUAL §5](MANUAL.md#5-配置参数全表参考) 与 [UPGRADING.md](UPGRADING.md)。

### 💡 核心接入避坑指南

1. **DSH 中必须显式声明 `transport: stdio`**：  
   若遗漏此配置项，配置文件 Schema 校验仍会通过，但客户端底层无法建立传输信道，表现为**静默不连接**。
2. **区分裸名、宿主注册名与 GUI 显示名**：
   MCP server 的裸名固定为 `search_code` / `search_docs` / `read_file` / `list_repositories`；宿主可能加前缀、清洗或重命名。实际调用名必须以当前宿主工具表为准。详见 **[docs/TOOL-NAMING.md](docs/TOOL-NAMING.md)**。

---

## 四个标准 MCP 工具（含向后兼容别名）

MCP server `tools/list` 仅声明以下 4 个通用中性裸工具名；同时 `tools/call` 透明兼容 `v1.x`（`search_gitea*`）与历史（`search_gitorca*`）两族旧名（保留至 `v3.0.0` 前，调用旧名时仅在 stderr 输出废弃告警）：

| MCP 裸名（v2.0.0） | 兼容旧名（`tools/call` 自动转发） | 核心能力 |
|---|---|---|
| `search_code` | `search_gitea` / `search_gitorca` | 跨所有镜像仓与全部分支检索代码内容 / 文件路径 / 提交信息 |
| `search_docs` | `search_gitea_docs` / `search_gitorca_docs` | 默认分支上的 SQLite FTS5 文档、会话、配置、脚本检索 |
| `read_file` | `read_gitea_file` / `read_gitorca_file` | 按仓库、相对路径和可选 ref 分片读取文件 |
| `list_repositories` | `list_gitea_repos` / `list_gitorca_repos` | 列出已镜像仓库名（`owner/repo` 规范身份） |

### 静态导出 Function Calling Schema（`--dump-schema`）

```bash
node bin/mcp-server --dump-schema           # 输出 { service, version, transport, functions }
node bin/mcp-server --dump-schema --compact # 仅输出 { functions }
```

纯静态导出，不读配置或镜像、不 spawn 子进程、无 stderr 副作用。

**核心实践法则：先定位，再精读。**  
通过 `search_code` / `search_docs` 锁定目标文件与行号后，再用 `read_file` 配合 `limit: 30~50` 切片精读。完整实战示范参见 **[docs/USAGE.md](docs/USAGE.md)**。

---

## 任意本地 Git 裸镜像接入（不限于 Gitea）

虽然 `scripts/sync.sh` 面向 Gitea / Forgejo API，但检索与索引核心（`bin/gts`、`lib/indexer.py`、`lib/server.mjs`）只依赖标准的 `<mirror_root>/<owner>/<repo>.git` 裸镜像目录结构。只需把任意 Git 源（自建 Git、GitHub、GitLab、本地仓库）用 `git clone --mirror` 放到该目录并执行一次建库即可被 MCP 全量检索：

```bash
mkdir -p /srv/local-mirrors/my-team
git clone --mirror <任意-git-url> /srv/local-mirrors/my-team/my-repo.git
GITEA_MIRROR_ROOT=/srv/local-mirrors GITEA_OWNERS=my-team gts --reindex --full
```

---

## Zero-Gitea 本地演示（不导入现有仓库）

无需搭建 Gitea、API Token 或 SSH 密钥。下面的命令会把参数当作**演示镜像输出目录**，在其中生成合成的 `local/demo-project.git` 并构建 FTS5 索引；它**不会扫描或导入**该目录中已有的 Git 仓库。

```bash
git clone https://github.com/offzen/gitea-search-mcp.git && cd gitea-search-mcp

# 参数是专用输出目录；不要传已有项目目录
bash scripts/quickstart-local.sh /tmp/gitea-search-mcp-demo
```

该脚本默认在当前源码 checkout 根目录**新建** `config.json`；也可用 `GITEA_SEARCH_CONFIG` 指定已有父目录下的新配置路径。已有不同配置、软链接或非普通文件会被拒绝且保持不动；同一镜像参数生成的完全相同普通配置可无写入重跑。脚本还会打印通用 MCP 客户端的 `mcpServers` JSON 示例。DSH 使用 `cordis.patch.yml`，请改用上方 DSH 配置模板。

命令行门面工具 `bin/gts`（兼容 `gitq` / `dsh-search`）即开即测：

```bash
bin/gts dockge                        # 跨仓代码内容检索（毫秒级）
bin/gts -D "反向代理配置"              # -D：基于 FTS5 + jieba 检索文档与历史会话
bin/gts -D --kind doc,script "caddy"  # 按分区过滤：只检索文档与脚本
```

---

## 运行环境要求（Python 为必备依赖）

- **Node.js ≥ 20.11**（推荐 22.15+ LTS，低版本自动降级调用系统 `sqlite3` 与 `zstd` CLI）；
- **Python 3 ≥ 3.10 + `jieba` + 带 FTS5 的 `sqlite3`**（**明确必备依赖**，不提供无 Python 降级模式）；
- **Git ≥ 2.25**；
- **平台支持**：原生跨平台兼容 Linux、macOS 与 Windows（10+ / Server 2019+）。

> 📖 **安装与闭环验收**：详细部署参见 **[MANUAL.md §3](MANUAL.md#3-多场景部署流程)**；安装后按 **[docs/POST-INSTALL.md](docs/POST-INSTALL.md)** 完成首次同步、索引检查、独立 MCP 探针、宿主重载和实际工具调用。

### 三平台上手命令

```bash
# Linux / macOS
git clone https://github.com/offzen/gitea-search-mcp.git
cd gitea-search-mcp
npm ci --no-audit --no-fund
python3 -m pip install '.[fts]'
# 可选使用一键脚本：bash scripts/setup.sh [--install-dsh --profile desktop]
# macOS 使用 scripts/setup-macos.sh 的同名参数

# Windows（PowerShell，管理员非必需）
git clone https://github.com/offzen/gitea-search-mcp.git
cd gitea-search-mcp
powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1 -Owners my-org -WhatIf
# 复核计划后去掉 -WhatIf；接入 DSH 时再显式加 -RegisterDsh -Profile desktop/web/headless
```

> 📦 **发行渠道说明**：npm 上的 `dsh-gitea-search@0.1.1`（MIT、纯 Node、另一维护者 `rayuai`）是早期遗留原型，与当前 Apache-2.0 代码库无关；PyPI 亦无官方包。请从本仓库源码或经哈希核验的 Release 制品安装 —— `pip install '.[fts]'` 走本地源码。

---

## Windows 原生跨平台支持

`v2.0.0` 完整继承自 `v1.2.2`/`v1.2.3` 的原生 Windows 适配：
- **原生 PE 执行入口**：通过标准 `pip install '.[fts]'` 生成原生 `Scripts\gts.exe` PE 启动器，免管理员权限直接运行；
- **防注入命令安全与环境自适配**：`.exe` / `.com` 直执行；批处理降级模式先拒绝 shell 元字符与换行，再执行参数定界；自动穿透系统 `PATH`、venv 与 Scoop；
- **跨平台证据边界**：历史提交的成功或未完结记录均只属于各自 exact SHA，不说明后续树的状态。任何代码、版本或文档改动都需重新冻结 D/P，在明确批准的私有 GitHub staging 获取新 exact P 的 Linux/macOS/Windows 收据并完成 strict joint preflight；正式发布仍需独立决定。

详见 **[WINDOWS-PORT.md](WINDOWS-PORT.md)** 与 **[MANUAL.md §3.3](MANUAL.md#33-场景-c原生-windows-环境部署)**。

---

## 文档导航

- **[UPGRADING.md](UPGRADING.md)** —— 从 `v1.2.x`（`dsh-gitea-search`）平滑升级到 `v2.0.0`（`gitea-search-mcp`）的完整指引与回滚手册
- **[PROJECT-STATE.md](PROJECT-STATE.md)** —— v2.0.0 版本内容与证据规则、历史快照与仓库角色
- **[docs/POST-INSTALL.md](docs/POST-INSTALL.md)** —— 安装后的完整闭环：只读预检、首次同步、索引、独立 MCP、宿主工具表、实际搜索与回滚
- **[docs/BOUNDARIES.md](docs/BOUNDARIES.md)** —— 能力覆盖范围与明确不支持事项
- **[docs/USAGE.md](docs/USAGE.md)** —— 四个工具参数速查、真实检索场景示范与防爆窗调用范例
- **[WINDOWS-PORT.md](WINDOWS-PORT.md)** —— Windows 原生跨平台技术架构、安全护栏设计与移植规范
- **[docs/TOOL-NAMING.md](docs/TOOL-NAMING.md)** —— MCP 裸名、宿主注册名与 GUI 显示名的三层模型及别名过渡说明
- **[docs/DEVELOPMENT-AND-RELEASE.md](docs/DEVELOPMENT-AND-RELEASE.md)** —— gate2/Linux 与 GitHub hosted clean-platform 门禁、D/P 候选及 exact-SHA receipt 契约
- **[MANUAL.md](MANUAL.md)** —— 生产级高可用部署、Cron 定时同步、四层安全护栏机制、参数全表与故障排查手册
- **[CHANGELOG.md](CHANGELOG.md)** / **[RELEASE-NOTES.md](RELEASE-NOTES.md)** —— 版本演进记录
- **[CONTRIBUTING.md](CONTRIBUTING.md)** —— 开发者贡献、测试与独立发布准则

---

## 许可证

Apache-2.0 License。欢迎提交 Issue 与 Pull Request。
