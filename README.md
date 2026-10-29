# dsh-gitea-search

[![License](https://img.shields.io/badge/License-Apache--2.0-C21325)](LICENSE)
[![CI](https://github.com/offzen/dsh-gitea-search/actions/workflows/ci.yml/badge.svg)](https://github.com/offzen/dsh-gitea-search/actions/workflows/ci.yml)
[![Windows CI](https://github.com/offzen/dsh-gitea-search/actions/workflows/windows-ci.yml/badge.svg)](https://github.com/offzen/dsh-gitea-search/actions/workflows/windows-ci.yml)
[![Version](https://img.shields.io/badge/Version-v1.2.2-blue)](RELEASE-NOTES.md)
[![npm](https://img.shields.io/npm/v/dsh-gitea-search?color=red)](https://www.npmjs.com/package/dsh-gitea-search)
[![Archive](https://img.shields.io/badge/Archive-v0.1.1%20(Pure%20Node)-orange)](https://github.com/offzen/dsh-gitea-search/tree/legacy-0.1.x)
[![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20macOS%20%7C%20Windows%20(Native)-brightgreen)](scripts/setup-windows.ps1)
[![Node](https://img.shields.io/badge/Node-%3E%3D20.11-success)](package.json)

给 DeepSeek Harness (DSH) 及任意支持 Model Context Protocol (MCP) 的 Agent 装上**跨仓极速检索能力**：一条命令将 Gitea 上全部仓库镜像到本地，Agent 用一次工具调用即可毫秒级搜遍所有代码、文档、配置文件与历史排障记录——不用逐个 clone、不把大文件塞进上下文、彻底消除大模型在海量信息翻找中的高额开销。

> SPDX-License-Identifier: Apache-2.0 · 早期纯 Node.js 版本完整保留在 [`legacy-0.1.x`](https://github.com/offzen/dsh-gitea-search/tree/legacy-0.1.x) 分支。

---

## 架构定位与解决的核心痛点
可以将 Gitea 远端托管的**所有仓库以 bare 裸镜像**拉取并维护在宿主机本地，提供**两层极速检索引擎**，并通过标准 Model Context Protocol (MCP) 服务暴露给 DeepSeek Harness (DSH) Agent：

1. **代码检索引擎（git grep）**：跨全量仓库、全部分支、文件路径及提交记录的毫秒级检索；支持多词短语连写 0 命中时自动回退为「同行多词 AND（`--and`）」模式。
2. **文档与会话全文检索（SQLite FTS5 + jieba 中文分词）**：毫秒级响应，支持 BM25 智能加权与内容类型分区（`doc`、`session`、`config`、`script`），解决 Agent 历史长上下文与排障经验的召回问题。
3. **零服务本地快速体验模式 (Zero-Gitea Mode)**：支持将本地任意普通 Git 仓库目录一键裸镜像化并构建倒排索引，无需远端 Gitea 服务即可作为 MCP 检索端点。

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

> 📊 **量化对比实测**：
> 让 Agent 在多仓项目中排查一个配置或历史故障，我的一个实测数据是：
> - **传统对话交互方式**：需 5 至 15 轮工具交互、消耗高达 8 万 Tokens，且极易导致早期上下文被稀释甚至模型失忆；
> - **本插件「两步检索法」**：采用「先查命中行定位 + 按需切片精读」，相同任务可能只需 1 至 2 步调用，Token 消耗大幅压缩，预估节省 85% 开销。
> 
> 本项目**把 Agent 的“地毯式翻找”变成了“精准点查”**——不仅保护上下文预算，显著降低推理成本与等待延迟，更是从根源上杜绝了上下文污染，守护模型的清晰记忆与专注力。

---

## 30 秒上手（Zero-Gitea 本地免服务模式）

无需搭建 Gitea 远端服务，无需 API Token 或 SSH 密钥，只要本地有 Git 仓库即可一键体验：

```bash
git clone https://github.com/offzen/dsh-gitea-search.git && cd dsh-gitea-search

# 扫描本地任意 Git 项目目录，自动创建镜像 + 构建 FTS5 索引 + 打印 DSH 配置片段
bash scripts/quickstart-local.sh /path/to/your/git-projects
```

脚本运行完毕后会直接输出开箱即用的 DSH 配置片段。

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
        serverName: gitea-search        # 决定暴露给模型的工具名前缀（见下文说明）
        transport: stdio                # 必填项：缺失会导致连接静默失败
        command: node
        args: ["/opt/dsh-gitea-search/lib/server.mjs"]
        cwd: /opt/dsh-gitea-search
        env:
          GITEA_MIRROR_ROOT: "/srv/gitea-mirror"
          GITEA_OWNERS: "my-org,my-team"
```

> **提示**：如果不想在配置中书写 `env`，可以在插件根目录下放置 `config.json`（参考 `config.example.json`），`server.mjs` 会自动向上寻找并解析配置，两种方式完全等价。

### 💡 核心接入避坑指南

1. **必须显式声明 `transport: stdio`**：  
   若遗漏此配置项，配置文件 Schema 校验仍会通过，但客户端底层无法建立传输信道，表现为**静默不连接**——没有明确报错，但所有检索工具都不会出现在 Agent 列表中。
2. **实际调用名带有宿主服务前缀**：  
   MCP 协议中工具名在服务器内部是局部的，DSH 等客户端为了避免多服务命名冲突，会按 `mcp__<serverName>__<toolName>` 规则映射。  
   当配置 `serverName: gitea-search` 时，文档检索工具在 Agent 视角的完整调用名为 `mcp__gitea-search__search_gitea_docs`。若在指令中仅写裸名，会被宿主拒绝。原理解析与自检方法详见 **[docs/TOOL-NAMING.md](docs/TOOL-NAMING.md)**。

---

## 四个核心工具

当 `serverName` 采用推荐的 `gitea-search` 时，Agent 可直接调用的 4 个标准工具如下：

| 完整调用名 | 核心能力 | 典型应用场景 |
|---|---|---|
| `mcp__gitea-search__search_gitea` | 跨所有镜像仓检索代码内容 / 文件路径 / 提交信息 | 查找“某个函数、配置项、Dockerfile 在哪定义” |
| `mcp__gitea-search__search_gitea_docs` | SQLite FTS5 全文检索文档、历史会话、配置、脚本 | 查找“之前的故障排查记录、决策背景与架构设计” |
| `mcp__gitea-search__read_gitea_file` | 按仓库与相对路径直接读取文件（无需 clone） | 搜索定位到目标后，精确读取上下文内容 |
| `mcp__gitea-search__list_gitea_repos` | 列出已镜像的全部仓库名（支持通配符过滤） | 盘点仓库资产，或不确定具体仓名时快速探路 |

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

---

## Windows 支持说明

### 为什么原生 Windows 上原本用不了

`read_gitea_file` 直接调用 `git`，所以可用；**但另外 3 个检索工具全部经 `spawn(gts, …)` 转发**，
这里卡了三个硬障碍：

| # | 障碍 | 后果 |
|---|---|---|
| 1 | `bin/gts` 是 Python 脚本、无扩展名，靠 `#!/usr/bin/env python3` shebang 执行 | Windows 的 `CreateProcess` **只认 PE 文件**，没有 shebang 机制 ⇒ 无法执行 |
| 2 | `lib/server.mjs` 的 `spawn()` 未开 `shell` | 即使补 `gts.cmd` / `gts.bat` 包装也执行不了（批处理不在直执行范围） |
| 3 | `fs.accessSync(c, X_OK)` 在 Windows 上**退化为存在性检查** | 会把 `bin/gts` 误判成"找到了"，随后 spawn 抛 `EFTYPE`/`ENOEXEC`，用户看到莫名其妙的报错而非友好的"找不到 gts CLI" |

### Windows 原生支持特性

`dsh-gitea-search` v1.2.2 正式引入了生产级原生 Windows 平台支持，彻底打破以往 Windows 环境下因缺失 PE 入口、文件锁不兼容及 shebang 无法解析导致 3 个检索工具不可用的底层限制：

1. **原生 PE 二进制入口**：通过 `pyproject.toml` 标准构建生成原生 PE 二进制 `Scripts\gts.exe`，支持 `--user` 免提权安装，解决 Windows `CreateProcess` 无法执行无扩展名脚本的硬障碍；
2. **防注入命令安全与 PATH 自动探测**：`.exe` 直执行（免 shell），批处理模式采用严格引号化定界彻底防御 `& | < > ^` 恶意命令注入；`server.mjs` 自动遍历系统 `PATH` 环境变量识别启动器；
3. **跨平台机制对齐与鲁棒性加固**：引入 Windows `msvcrt.locking` 字节区间排他锁、Git 路径反斜杠自动归一化，以及严格的 CRLF 尾部换行符剥离。

> 完整技术实现细节、缺陷加固清单与移植历史请参阅 **[CHANGELOG.md](CHANGELOG.md)** 与 **[WINDOWS-PORT.md](WINDOWS-PORT.md)**。

### Windows 原生部署指引

Windows 平台的完整部署流程（包含 PowerShell 一键安装脚本 `setup-windows.ps1`、标准 `pip install .` 生成 PE launcher、PyInstaller 打包独立 exe、环境变量设置与避坑说明）已完整收录于运维手册 **[MANUAL.md §3.3（场景 C：原生 Windows 环境部署）](MANUAL.md#33-场景-c原生-windows-环境部署)**。

### 已知限制与 CI 验证状态

- **真实 Windows Runner CI 全绿通过**：全套 Windows 适配代码与 `setup-windows.ps1` 安装脚本已在 GitHub Actions Windows Runner（Windows Server 2022，覆盖 Python 3.11/3.12 + Node 20/22 组合矩阵）完成真实环境验证，**21/21 项测试用例 100% 通过（0 skipped, 0 failed）**，PowerShell AST 语法 0 错误。
- `scripts/sync.sh`、`quickstart-local.sh`、`respawn-mcp.sh` 为 Linux/macOS bash 脚本，Windows 上请使用 `git clone --bare` + `python lib\indexer.py --build`，或通过 `setup-windows.ps1` 进行自动初始化与同步。
- `resource.getrusage` 在 Windows 下返回全 0，CPU 遥测指标为空（不影响检索与读取的核心功能与正确性）。
- 经 `cmd.exe` 时，含 `%NAME%` 的参数会被 cmd 在解析阶段替换成环境变量值（已知 cmd 既定语义，已通过 `W8-8` 测试用例固化）；建议优先使用 `.exe` 直执行方式。

---

## 文档导航

- **[docs/USAGE.md](docs/USAGE.md)** —— 四个工具参数速查、真实检索场景示范与防爆窗调用范例（**推荐首读**）
- **[docs/TOOL-NAMING.md](docs/TOOL-NAMING.md)** —— MCP 工具名前缀机制深度解析、四种典型冲突失败与命名规范
- **[MANUAL.md](MANUAL.md)** —— 生产级高可用部署、Cron 定时同步、四层安全护栏机制、参数全表与故障排查手册
- **[CHANGELOG.md](CHANGELOG.md)** / **[RELEASE-NOTES.md](RELEASE-NOTES.md)** —— 版本演进记录
- **[CONTRIBUTING.md](CONTRIBUTING.md)** —— 开发者贡献与双向同步准则

---

## 许可证

Apache-2.0 License。欢迎提交 Issue 与 Pull Request。
