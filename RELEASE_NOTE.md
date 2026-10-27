# Release Notes - DeepSeek Harness (DSH) Gitea Search

## v1.2.1 (2026-10-27)

### 版本概述 / Overview

`dsh-gitea-search` v1.2.1 是一次以**用户体验、上下文保护与接入避坑**为核心的文档与规范重大升级版本。针对以往技术细节过重、缺少实战指导的痛点，本版本将主页重塑为“以节省 Agent 上下文为中心”的价值导向，新增独立的使用指南（`docs/USAGE.md`）与 MCP 工具命名防坑指南（`docs/TOOL-NAMING.md`），并将深度部署、运维与护栏机制沉淀入 `MANUAL.md`，构建了层次分明的全新文档体系。

---

### 中文更新说明

#### 1. README 核心重塑：聚焦节省上下文与注意力保护
- **量化实测与注意力保护**：增加实测数据对比——排查一个多仓配置或历史故障，传统对话交互需 5 至 15 轮工具交互、消耗高达 8 万 Tokens，且巨量噪音极易导致早期上下文被稀释甚至引发模型失忆；通过本插件两步法（搜索定位 + 切片精读）只需 1 至 2 步调用，Token 消耗大幅压缩，预估节省 85% 开销，从根源上杜绝上下文污染。
- **30 秒极速上手**：突出 Zero-Gitea 本地免服务快速启动模式，零外部依赖即开即用。
- **核心避坑指南**：将易导致静默失败的硬门槛提炼为专业贴士（明示 `transport: stdio` 必填性与宿主前缀映射规则）。

#### 2. 新增独立使用指南 (`docs/USAGE.md`)
- **核心实践三法则**：“先定位再精读”、“多词与中文优先走倒排索引”、“检索范围为已推送到镜像的内容”。
- **4 个核心工具速查**：详细解读 `search_gitea`、`search_gitea_docs`、`read_gitea_file`、`list_gitea_repos` 参数规范。
- **5 大实战场景示范**：涵盖配置项定位两步法、跨会话故障排查复盘、全局影响面探测（`files_only: true` 防爆窗）、搜索为空排查阶梯与 CLI 命令行同款用法。

#### 3. 新增 MCP 工具命名深度解析 (`docs/TOOL-NAMING.md`)
- **协议机制溯源**：深入 MCP 官方规范，解析“服务命名空间局部性”与“宿主前缀映射”原理。
- **4 种冲突失败形态**：剖析 Unknown tool、注册被静默覆盖、静默打到错误服务及入参错位等根因。
- **Agent 规则示范**：提供标准的 Agent System Prompt / Skill 指令声明模板。

#### 4. 生产运维与安全加固 (`MANUAL.md`)
- **最小特权凭据**：规范生产环境私有 Gitea 对接标准，强调使用只读权限的 Access Token / Deploy Key（仅需 `repo:read`）。
- **四重安全护栏与 FTS5 原理**：完整收拢条数硬夹、单行截断（2000B）、总体缓冲区限制（128KB）与窗口化读取（400行/60KB）。

---

### English Release Notes

#### Highlights
- **User-Centric Documentation**: Refactored `README.md` to emphasize token conservation and attention preservation (mitigating the critical pain point of 80k-token context bloat causing attention dilution and model amnesia, slashing usage by 85% via two-step targeted retrieval).
- **Dedicated Usage Guide (`docs/USAGE.md`)**: Core lookup principles, comprehensive tool parameter tables, and 5 practical workflows highlighting `files_only` context-saving capabilities.
- **MCP Tool Naming Guide (`docs/TOOL-NAMING.md`)**: Deep-dive into protocol namespaces, 4 multi-server collision failure modes, and standard System Prompt templates.
- **Least-Privilege Security**: Hardened production Gitea integration guidelines requiring read-only tokens (`repo:read`).

---

## v1.2.0 (2026-10-26)

### 版本概述 / Overview

`dsh-gitea-search` v1.2.0 是一个重要的生态扩展与健壮性升级版本。本版本合入了首个外部社区 PR，带来了原生的 macOS 与 BSD 跨平台兼容支持；引入了零服务依赖的 Zero-Gitea 本地极速启动模式；实现了 `config.json` 自动解析机制与 Node.js 跨版本优雅降级，并在自动化测试中扩充至 32 项端到端断言，覆盖多系统、多版本的 CI 测试矩阵。

---

### 中文更新说明

#### 1. 原生 macOS 与 BSD 平台支持 (合入社区 [PR #2](https://github.com/offzen/dsh-gitea-search/pull/2))
- **环境一键配置**：新增 `scripts/setup-macos.sh`，原生支持 macOS 默认的 Bash 3.2 环境，自动通过 Homebrew 补齐环境依赖。
- **管道防死锁与 BSD 工具链兼容**：针对缺乏 GNU `timeout` 命令的 macOS/BSD 系统，重构了非阻塞的子进程超时控制，彻底消除管道缓冲造成的死锁假死；同时兼容 BSD 版 `stat` 与 `awk` 命令行语法。

#### 2. Zero-Gitea 本地免服务快速启动 (`quickstart-local.sh`)
- **零服务端依赖**：无需安装或连接 Gitea 服务，只要本地存在任意普通 Git 仓库，运行 `bash scripts/quickstart-local.sh <path>` 即可一键生成 bare 镜像并完成 FTS5 索引构建。
- **极速体验**：为本地开发调试、非 Gitea 托管的单一代码库提供即开即用的 MCP 全文与代码检索能力。

#### 3. 配置文件自动寻径解析 (`config.json`)
- **免环境变量注入**：`lib/server.mjs` 支持自动向上查找并加载当前工作目录或父目录中的 `config.json` 配置文件。
- **多途径规范统一**：建立配置优先级（CLI 参数 > 环境变量 > `config.json`），完全打通 `GITEA_*` 标准规范与历史 `GITQ_*` 变量。

#### 4. Node.js 跨版本优雅降级与防御增强
- **CLI 自动 Fallback**：在缺少原生 `node:sqlite` 或 `zlib.zstdCompressSync` 的 Node.js `20.11 ~ 22.14` 环境下，自动平滑回退至系统 `sqlite3` CLI 与 `zstd` CLI，杜绝加载崩溃。
- **空配置预警**：MCP 启动时若检测到未配置有效镜像目录，输出友好的引导预警日志，取代无提示的异常退出。

#### 5. 质量保证与 CI 矩阵扩展
- **32 项全链路断言**：E2E 测试套件扩充至 32 项测试，新增 Phase 5 专项校验 `config.json` 与本地快速启动模式。
- **跨版本双矩阵验证**：CI 流水线全面覆盖 Node.js 20 LTS / 22 LTS 与 Python 3.11-3.13 组合。

---

## v1.1.0 (2026-10-23)

### 版本概述
`dsh-gitea-search` v1.1.0 是专为 DeepSeek Harness (DSH) 打造的高性能跨仓检索与会话检索插件。通过标准 Model Context Protocol (MCP) 协议，赋予 Agent 在大型代码与文档库中毫秒级检索、定位与阅读的能力。

### 核心特性
1. **双层极速检索引擎**：裸镜像 `git grep`（全分支 + 同行多词 AND 自动回退）与 SQLite FTS5 + Jieba 全文检索（BM25 加权与多类型分区）。
2. **Agent 上下文安全护栏**：条数硬夹（默认 5 / 最大 200）、单行截断（2000B）、总体缓冲区限制（128KB）与行窗口读取（默认 400 行）。
3. **自动化测试套件**：GitHub Actions CI 自动化 28 项端到端断言测试。
4. **性能遥测与无损维护**：`IoTracker` 读写统计遥测与 `dsh-session-health.mjs` 在线 WAL 截断维护。
