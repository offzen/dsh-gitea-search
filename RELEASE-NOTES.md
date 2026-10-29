# Release Notes - DeepSeek Harness (DSH) Gitea Search

## v1.2.2 (2026-10-28)

### 版本概述 / Overview

`dsh-gitea-search` v1.2.2 是一个重大的跨平台生态扩展与生产级加固版本。在承接 v1.2.1 架构与规范的基础上，本版本正式推出了**原生 Windows 平台支持**，彻底打破以往 Windows 环境下因缺失 PE 入口、文件锁不兼容及 shebang 无法执行导致 3 个检索工具不可用的底层限制；全面建立了覆盖路径归一化、参数防注入、并发锁对齐与环境探测的生产级防护体系，并在 GitHub Actions 真实 Windows Runner 上通过了全套自动化工作流验证（7/7 Jobs，21/21 测试用例 100% 通过）。

---

### 中文更新说明

#### 1. 原生 Windows 平台支持 (dsh-gitea-search)
- **原生 PE Launcher**：新增 `pyproject.toml` 与 `gts_cli.py`，通过 setuptools `[project.scripts]` 在 Windows 上编译生成原生 PE 启动器 `Scripts\gts.exe`，避开 Windows `CreateProcess` 对无扩展名 shebang 脚本的执行限制。
- **独立数据隔离**：将 `bin/gts` 与 `lib/indexer.py` 安装至 `share/dsh-gitea-search/` 数据子目录，消除与同名启动器的文件覆盖与无限递归风险。
- **一键自动化部署 (`scripts/setup-windows.ps1`)**：提供专为 Windows 打造的 PowerShell 部署脚本，支持 Git 裸镜像拉取、依赖安装、FTS5 倒排索引构建及 DSH `cordis.patch.yml` 自动注册。
- **跨平台测试支持**：测试架构迁移至 Node 原生 `node --test`，新增 21 项跨平台行为与防注入断言。

#### 2. 核心跨平台稳健性设计与安全防护机制
- **Git 树对象路径统一归一化**：`readGiteaFile`、`gitDir` 以及 CLI `gts --read` 自动将 Windows 反斜杠 `\` 转换为正斜杠 `/`，杜绝 Git tree object 抛出 `fatal: path '...' does not exist in 'HEAD'`。
- **CLI 同步跨平台容错与前缀规范**：`gts --sync` 增加 `shutil.which('bash')` 动态探测与友好的跨平台回退提示；统一 `die()` 报错前缀为 `gts:`。
- **PATH 引号清洗与 Gitea Actions 卡滞防护**：增强 `findSearchCli()` 剥离 PATH 双引号能力；为 Windows CI 添加 `github.server_url == 'https://github.com'` 守卫，避免在缺少 Windows runner 的自建 Gitea 环境中永久排队。
- **PowerShell YAML 路径转义安全**：改用 `.Replace('\', '/')` 正斜杠进行 DSH 配置插值，彻底避免 YAML 反斜杠转义语法错误。
- **CRLF 尾部换行符彻底清洗**：`bin/gts` 的 `_stream()` 与 `lib/indexer.py` 的摘要生成器彻底规整孤立 `\r`，杜绝 Windows 检出代码的 `\r` 符号污染终端与 Web UI 渲染。
- **Windows 排他锁区间严格对齐**：`lib/indexer.py` 的 `IndexLock` 在 Windows 加锁前显式执行 `seek(0)`，与 `_release()` 保持严格对称，确保多进程并发建索引互斥有效。
- **系统 PATH 环境变量自动探测**：`findSearchCli()` 自动遍历 `process.env.PATH`，无需手动配置环境变量即可开箱识别全局/虚拟环境中的 `gts.exe`。
- **免提权用户级安装支持**：候选路径增加 `site.USER_BASE`，无管理员权限时可通过 `pip install --user .` 正常使用。
- **PyPI 元数据规范化**：修正 Trove Classifier 为 `"Operating System :: MacOS :: MacOS X"`。
- **文档体系手册化重构**：将所有安装部署细节从 `README.md` 完整收拢至 `MANUAL.md §3` 与 `WINDOWS-PORT.md`，校准合成测试断言为 31 项，并补充 Windows 专属行为测试说明。

#### 3. 验证与质量保证
- **GitHub Actions Windows Runner 全绿**：覆盖 Windows Server 2022 下 Python 3.11/3.12 与 Node 20/22 组合矩阵，7/7 jobs 全部 PASS，21/21 tests 真实执行无跳过、零失败。
- **全套回归保证**：Linux/macOS 31/31 e2e 测试与单测全量通过，保持 100% 向后兼容。

---

### English Release Notes

#### Highlights
- **Native Windows Platform Support**: Introduces native PE launcher generation (`Scripts\gts.exe`) via `pyproject.toml` and `gts_cli.py`, overcoming Windows `CreateProcess` limitations on shebang scripts.
- **Robustness Architecture & Security Guards**:
  - Path normalization (backslash to forward-slash) across `server.mjs` and `bin/gts` preventing Git fatal tree lookup errors.
  - Safe YAML path interpolation in `setup-windows.ps1` preventing profile syntax corruption.
  - Cross-platform synchronization fallback in `bin/gts --sync` and unified `gts:` diagnostic prefix.
  - Safe PATH quotes handling in `server.mjs` and GitHub server URL guard in Windows CI preventing queued runner stalls.
  - Clean CRLF `\r` stripping in `bin/gts` stream outputs and indexer snippet generation.
  - Consistent byte-range file locking (`IndexLock`) with `seek(0)` alignment on Windows.
  - Automatic `PATH` environment variable traversal for CLI executable discovery.
  - Full support for unprivileged `pip install --user .` installations via `site.USER_BASE`.
  - PyPI compliant Trove classifiers.
  - Documentation manual restructuring: migrated all installation flows from `README.md` to `MANUAL.md §3` and `WINDOWS-PORT.md`, reconciling 31-assertion testbed metrics.
- **Automated Windows CI**: 7/7 jobs and 21/21 test assertions verified green on GitHub Actions Windows runners with zero skips and zero failures.

---

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
