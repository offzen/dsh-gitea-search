# dsh-gitea-search

[![License](https://img.shields.io/badge/License-Apache--2.0-C21325)](LICENSE)
[![CI](https://github.com/offzen/dsh-gitea-search/actions/workflows/ci.yml/badge.svg)](https://github.com/offzen/dsh-gitea-search/actions/workflows/ci.yml)
[![Windows CI](https://github.com/offzen/dsh-gitea-search/actions/workflows/windows-ci.yml/badge.svg)](https://github.com/offzen/dsh-gitea-search/actions)
[![Version](https://img.shields.io/badge/Version-v1.2.2%20candidate-yellow)](RELEASE-NOTES.md)
[![Archive](https://img.shields.io/badge/Archive-v0.1.1%20(Pure%20Node)-orange)](https://github.com/offzen/dsh-gitea-search/tree/legacy-0.1.x)
[![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20macOS%20%7C%20Windows%20(Native)-brightgreen)](MANUAL.md#3-多场景部署流程)
[![Node](https://img.shields.io/badge/Node-%3E%3D20.11-success)](package.json)

给 DeepSeek Harness (DSH) 及任意支持 Model Context Protocol (MCP) 的 Agent 装上**跨仓极速检索能力**：一条命令将 Gitea 上全部仓库镜像到本地，Agent 用一次工具调用即可毫秒级搜遍所有代码、文档、配置文件与历史排障记录——不用逐个 clone、不把大文件塞进上下文、彻底消除大模型在海量信息翻找中的高额开销。

> SPDX-License-Identifier: Apache-2.0 · 早期纯 Node.js 版本完整保留在 [`legacy-0.1.x`](https://github.com/offzen/dsh-gitea-search/tree/legacy-0.1.x) 分支。

---

## 架构定位与解决的核心痛点
可以将 Gitea 远端托管的**所有仓库以 bare 裸镜像**拉取并维护在宿主机本地，提供**两层极速检索引擎**，并通过标准 Model Context Protocol (MCP) 服务暴露给 DeepSeek Harness (DSH) Agent：

1. **代码检索引擎（git grep）**：跨全量仓库、全部分支、文件路径及提交记录的毫秒级检索；支持多词短语连写 0 命中时自动回退为「同行多词 AND（`--and`）」模式。
2. **文档与会话全文检索（SQLite FTS5 + jieba 中文分词）**：索引每仓默认分支，SQLite warm query 可毫秒级响应，支持 BM25 与内容类型分区；当前 MCP 端到端延迟还包含 Python/jieba 冷启动。非默认分支请用代码搜索后按 `ref` 精读。
3. **零服务本地演示模式 (Zero-Gitea Demo)**：无需远端 Gitea，由脚本在指定输出目录生成一个合成的 `demo-project.git` bare 仓并构建索引，用来验证 CLI/MCP 基本链路。它**不会扫描或导入已有本地 Git 仓库**。

## 不仅省上下文预算，更防模型注意力稀释与失忆

Agent 查一次资料，真正的代价从来不是“查没查到”，而是**消耗了多少对话轮数、塞入了多少冗余 Token**。没有本地索引时，每次盲目翻找都在严重透支模型宝贵的上下文窗口预算；而更为致命的是：**上下文急剧膨胀会导致严重的“注意力稀释”与模型失忆**。

| 没有它（Agent 原生手段盲目翻找） | 有它（本地两层索引靶向直达） | 认知与注意力保护 |
|---|---|---|
| 逐个 clone 仓库或抓取网页寻找配置 | 一次工具调用覆盖**全量仓库 + 全部本地分支** | 消除多轮探测命令，杜绝过程噪音 |
| 网页搜索引入的 HTML 与无关长文全量灌入上下文 | 只返回匹配精准行，默认 Top 5（先定位、再精读） | **高信噪比注入，防止核心指令被冲淡稀释** |
| 遇到大文件不得不整篇塞入上下文 | 默认仅读取前 400 行，支持 `offset`/`limit` 按需切片续读 | 消除非相关代码对有限注意力资源的挤占 |
| 撞上编译打包产物或超长文本，数十万 Token 瞬间爆窗 | 单行 ≤ 2000B、单次 ≤ 128KiB **原地安全截断** | **物理护栏熔断，守住长程推理的记忆基线** |
| 中文/多词查询历史排障，内置分词极易 0 命中 | SQLite FTS5 + jieba 分词，中文短语、多词跨行精准召回 | 1 步直达事实，避免盲目试错引发上下文污染 |
| 反复摸索排查留下的巨量冗余过程记录 | 过程干净零噪声，直接收敛至目标事实 | **彻底消除多轮冗余翻找导致的模型认知漂移与失忆** |

> **为什么采用“两步检索法”**：
> 先查少量命中行定位，再按需切片精读，可以避免逐仓盲搜和整篇加载无关文件，减少工具往返与注入上下文的体积。实际节省幅度取决于仓库规模、查询复杂度、模型分词器与缓存策略；本项目不把缺少标准化基准方法学的单次观测当作通用性能承诺。
>
> 本项目把 Agent 的“地毯式翻找”变成“精准点查”，以可控的返回窗口保护上下文预算并降低无关内容造成的注意力稀释。

---

## Zero-Gitea 本地演示（不导入现有仓库）

无需搭建 Gitea、API Token 或 SSH 密钥。下面的命令会把参数当作**演示镜像输出目录**，在其中生成合成的 `local/demo-project.git` 并构建 FTS5 索引；它不会扫描或导入该目录中已有的 Git 仓库。

```bash
git clone https://github.com/offzen/dsh-gitea-search.git && cd dsh-gitea-search

# 参数是专用输出目录；不要传已有项目目录
bash scripts/quickstart-local.sh /tmp/dsh-gitea-search-demo
```

该脚本还会在当前源码 checkout 根目录写入 `config.json`，并打印通用 MCP 客户端的 `mcpServers` JSON 示例。DSH 使用 `cordis.patch.yml`，请改用下方 DSH 配置模板；演示结束后若不再使用该配置，请删除生成的 `config.json`。

首次接入真实 Gitea 请使用下方平台安装流程；安装后按 [`docs/POST-INSTALL.md`](docs/POST-INSTALL.md) 完成首次同步、索引检查、独立 MCP 探针、宿主重载和实际工具调用。

想直接在终端测试检索效果？命令行门面工具 `bin/gts` 即开即测：

```bash
bin/gts dockge                        # 跨仓代码内容检索（毫秒级）
bin/gts -D "反向代理配置"              # -D：基于 FTS5 + jieba 检索文档与历史会话
bin/gts -D --kind doc,script "caddy"  # 按分区过滤：只检索文档与脚本
```

> 完整命令参数参见 `bin/gts --help` 或运维手册 [MANUAL.md](MANUAL.md)。

---

## 接入 DeepSeek Harness (DSH)

在 DSH 配置文件（如 `cordis.patch.yml`）中添加如下片段即可挂载 MCP 服务（顶层必须是 YAML 数组）：

```yaml
- insert:
    - id: mcp-gitea-search
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: gitea-search        # 宿主可能据此生成前缀；实际调用名以工具注册表为准
        transport: stdio                # 必填项：缺失会导致连接静默失败
        command: node
        args: ["/opt/dsh-gitea-search/lib/server.mjs"]
        cwd: /opt/dsh-gitea-search
        env:
          GITEA_MIRROR_ROOT: "/srv/gitea-mirror"
          GITEA_OWNERS: "my-org,my-team"
```

> **配置边界**：运行时 CLI/MCP/indexer 只从 `config.json` 读取 `mirror_root` 与 `owners`；同步 URL、token、枚举/传输策略及输出护栏仍由环境变量控制，因此它与 profile `env` **不完全等价**。推荐把共享运行时配置放在 `~/.config/dsh-gitea-search/config.json`，或用 `GITEA_SEARCH_CONFIG` 显式指定；仅在确认 checkout 专用于该部署时才使用项目根 `config.json`。详见 [MANUAL §5](MANUAL.md#5-配置参数全表参考)。

### 💡 核心接入避坑指南

1. **必须显式声明 `transport: stdio`**：  
   若遗漏此配置项，配置文件 Schema 校验仍会通过，但客户端底层无法建立传输信道，表现为**静默不连接**——没有明确报错，但所有检索工具都不会出现在 Agent 列表中。
2. **区分裸名、宿主注册名与 GUI 显示名**：
   MCP server 的裸名固定为 `search_gitea` 等；宿主可能加前缀、清洗或重命名，GUI 又可能显示友好中文名。某些 DSH 版本采用 `mcp__<serverName>__<toolName>`，但实际调用名必须以当前宿主工具表为准。详见 **[docs/TOOL-NAMING.md](docs/TOOL-NAMING.md)**。

---

## 四个核心工具

MCP server 声明的 4 个裸工具名如下；宿主侧实际注册名需在安装后查看工具表。

| MCP 裸名 | 核心能力 | 典型应用场景 |
|---|---|---|
| `search_gitea` | 跨所有镜像仓检索代码内容 / 文件路径 / 提交信息 | 查找“某个函数、配置项、Dockerfile 在哪定义” |
| `search_gitea_docs` | 默认分支上的 SQLite FTS5 文档、会话、配置、脚本检索 | 查找“之前的故障排查记录、决策背景与架构设计” |
| `read_gitea_file` | 按仓库、相对路径和可选 ref 读取文件 | 搜索定位后精确读取上下文 |
| `list_gitea_repos` | 列出已镜像仓库名 | 盘点仓库或确认过滤条件 |

**核心实践法则：先定位，再精读。**  
通过 `search_*` 工具锁定目标文件与关键行号后，再使用 `read_gitea_file` 配合 `limit: 30~50` 进行小窗口切片精读，切忌盲目读取大文件全文。

完整实战场景示范与参数配置参见 **[docs/USAGE.md](docs/USAGE.md)**。

---

## 运行环境要求

- **Node.js ≥ 20.11**（推荐 22.15+ LTS，低版本自动降级调用系统 `sqlite3` 与 `zstd` CLI）；
- **Python 3 ≥ 3.10**（需安装 `jieba` 分词库与支持 FTS5 的系统 `sqlite3`）；
- **Git ≥ 2.25**；
- **平台支持**：原生跨平台兼容 Linux、macOS 与 Windows（10+ / Server 2019+）。

> 📖 **安装与部署指引**：各平台（Linux / macOS / Windows）的一键初始化脚本、生产环境配置与详细依赖安装流程，请参见部署与运维手册 **[MANUAL.md §3（多场景部署流程）](MANUAL.md#3-多场景部署流程)**。
>
> **安装完成不等于已经能搜到内容。** 连接真实 Gitea 后还要完成首次同步、索引检查、独立 MCP 探针、宿主 profile 重载，并在实际会话中调用一次工具。统一清单见 **[docs/POST-INSTALL.md](docs/POST-INSTALL.md)**。

### 三平台上手命令

```bash
# Linux / macOS
git clone https://github.com/offzen/dsh-gitea-search.git
cd dsh-gitea-search
bash scripts/setup.sh --install-dsh --profile desktop  # 也可选 web/headless；Auto 仅接受唯一候选
# macOS 使用 scripts/setup-macos.sh 的同名 --install-dsh/--profile 参数
# npm install 只安装 MCP Node SDK，不生成 gts 命令；FTS 中文检索需要 jieba
npm install --no-audit --no-fund
python3 -m pip install '.[fts]'

# Windows（PowerShell，管理员非必需）
git clone https://github.com/offzen/dsh-gitea-search.git
cd dsh-gitea-search
powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1 -Owners my-org -WhatIf
# 复核计划后去掉 -WhatIf；接入 DSH 时再显式加 -RegisterDsh -Profile desktop/web/headless
```

> 📦 **发行渠道说明**：npm 上的 `dsh-gitea-search@0.1.1`（MIT、纯 Node、另一维护者 `rayuai`）是本项目**早期遗留原型**，与当前 Apache-2.0 代码库无关；PyPI 亦无官方包。请只从本仓库源码或经哈希核验的制品安装 —— `pip install '.[fts]'` 走本地源码，不会从注册表拉取本项目。

> ⚠️ **v1.2.2 早期候选用户请注意**：该版本在 Linux / macOS 上存在路径归一化回归
> （读取文件名含反斜杠的文件时会静默返回另一个文件的内容）。请直接使用 **v1.2.2-rc2+**，
> 详见 [CHANGELOG](CHANGELOG.md#122-rc2)。

---

## Windows 原生跨平台支持

`dsh-gitea-search` 的 `v1.2.2` 稳定版候选提供原生 Windows 适配；它仍是目标版本而非已发布稳定版，正式发布依赖同一 exact P 的 canonical hosted evidence 与无放宽 strict joint preflight：
- **原生 PE 执行入口**：通过标准 `pip install .` 生成原生 `Scripts\gts.exe` PE 二进制启动器，免管理员权限直接运行；
- **防注入命令安全与环境自适配**：`.exe` / `.com` 直执行；批处理降级模式先拒绝 shell 元字符与换行，再执行参数定界；自动穿透系统 `PATH`、venv 与 Scoop；
- **跨平台证据边界**：早期 Windows hosted 矩阵已取得历史绿灯；`v1.2.2` 候选的准确发布判据仍须先在显式批准的私有 GitHub staging 上，为最终 exact P 重新获取 fresh GitHub-hosted Linux/macOS/Windows 收据与 forge evidence，并通过 strict joint preflight；只有随后独立批准，才可将逐字节相同的候选晋级到公开仓，历史结果不可移用。

> 📖 **深入了解 Windows 平台支持**：
> - 完整技术架构、跨平台安全护栏与技术细节详见 **[WINDOWS-PORT.md](WINDOWS-PORT.md)**；
> - 一键 PowerShell 安装与生产环境接入指引详见 **[MANUAL.md §3.3（场景 C：原生 Windows 环境部署）](MANUAL.md#33-场景-c原生-windows-环境部署)**。

---

## 文档导航

- **[PROJECT-STATE.md](PROJECT-STATE.md)** —— 当前候选、仓库角色、tracked-tree 处置与 tag 判定
- **[docs/POST-INSTALL.md](docs/POST-INSTALL.md)** —— 安装后的完整闭环：只读预检、首次同步、索引、独立 MCP、宿主工具表、实际搜索、回滚与可选 Agent skill
- **[docs/BOUNDARIES.md](docs/BOUNDARIES.md)** —— 能力覆盖范围与明确不支持事项
- **[docs/USAGE.md](docs/USAGE.md)** —— 四个工具参数速查、真实检索场景示范与防爆窗调用范例（**推荐首读**）
- **[WINDOWS-PORT.md](WINDOWS-PORT.md)** —— Windows 原生跨平台技术架构、安全护栏设计与移植规范
- **[docs/TOOL-NAMING.md](docs/TOOL-NAMING.md)** —— MCP 裸名、宿主注册名与 GUI 显示名的三层模型及排查顺序
- **[docs/DEVELOPMENT-AND-RELEASE.md](docs/DEVELOPMENT-AND-RELEASE.md)** —— gate2/Linux 与 GitHub hosted clean-platform 门禁、D/P 候选及 exact-SHA receipt 契约
- **[MANUAL.md](MANUAL.md)** —— 生产级高可用部署、Cron 定时同步、四层安全护栏机制、参数全表与故障排查手册
- **[CHANGELOG.md](CHANGELOG.md)** / **[RELEASE-NOTES.md](RELEASE-NOTES.md)** —— 版本演进记录
- **[CONTRIBUTING.md](CONTRIBUTING.md)** —— 开发者贡献与双向同步准则

---

## 许可证

Apache-2.0 License。欢迎提交 Issue 与 Pull Request。
