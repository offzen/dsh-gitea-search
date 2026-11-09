# Release Notes - DeepSeek Harness (DSH) Gitea Search

## v1.2.2 candidate (Unreleased) — cross-platform search and fail-closed release evidence

> **Documentation and tag-provenance clarification.** The turn-count, token-volume,
> and 85% saving figures retained in the v1.2.1 historical notes below were early,
> unbenchmarked observations without a standardized methodology. They are preserved
> only as historical record and are not a current performance promise. The private
> canonical and prerelease forges also retain an immutable legacy annotated
> `v1.2.2` tag at commit `9db7010…` (tree `e3964dd…`). A later public candidate with
> the same version name is different content. Cross-forge builds, SBOMs, mirrors and
> incident reports must therefore identify the full commit or tree, not assume that
> the tag name alone denotes identical bytes. This disclosure records the unresolved
> naming risk; it is not a release announcement or a risk-acceptance decision.

- `verified` now requires four schema-3 receipts, deep forge evidence, exact
  workflow/ref/run identity, fresh complete job listings, GitHub-hosted runner
  signals, matching artifact digests, and an out-of-tree four-script trust anchor.
- GitHub artifact zips bind the exact receipt and test-count bytes to the digest
  returned by the forge. Gate2 remains explicitly same-party and has no forged
  artifact-digest claim when Gitea cannot supply one.
- Test counts record skipped/cancelled/todo and reject bool, negative, inconsistent,
  zero, or weakened suites. Dedicated negative tests cover both evidence collection
  and count collection, and a static test pins workflow needs/jobs/artifact names.
- Product fixes make config decisions identical across CLI/MCP, reject missing
  explicit config and `GTS_BIN`, harden PATH and Windows cmd/batch argument handling,
  restore mirror-state diagnostics, and correctly classify process timeouts.
- Trust statement: this raises the cost of false-green evidence but remains
  `trust_ceiling: same-party`; it is not an independent or unforgeable attestation.
- The 1.2.2 chain connects all 11 Python contract/regression files from the default `test:preflight` chain, including the
  dual-forge gate-context negative suite, to the default test/receipt chain, provisions
  PyYAML in gate2 and hosted full-suite jobs (including Windows W10), rejects skipped suites in
  the receipt counter, refreshes release/Windows docs, and removes
  a superseded dirty-development review from the published tree (retained privately).

## v1.2.2-rc6 — release evidence you can check against the forge

- Receipts now carry real test counts and a real gate start time, and derive success
  from the upstream job results instead of asserting it.
- Each receipt can be cross-checked against the forge's own record of the run
  (`scripts/fetch-remote-evidence.py` + `release-preflight.py --remote-evidence`), so
  "verified" no longer means only "the JSON is internally consistent".
- Receipt provenance is bound to the released ref, and the verifier can be pinned to
  an externally held copy so the trust root is not the tree being verified.
- No product behaviour changes: this candidate hardens the release gate only.

## v1.2.2-rc5 — installation checks and operational clarity

- Adds explicit post-install verification scripts for POSIX and PowerShell; checks are read-only and never sync, index, edit profiles, or restart DSH.
- Adds user documentation for the five-minute install check, product boundaries, host reload and rollback.
- Fixes YAML profile scalar escaping/control rejection and makes Windows paths use forward slashes; retains sibling profile entries and supports existing indentation widths.
- Adds mirror sync-state interpretation based on successful-sync metadata, timestamp parity and a non-blocking stale warning; never infers freshness from a failed attempt.
- Improves MCP CLI failure attribution, sync failure reporting and packaging lookup for the sync script.
- Fixes missing-jieba reporting and filtered zero-hit behavior; updates the macOS installer hint and malformed JSON examples.
- Fixes a receipt-test assumption that the runner is Linux, which failed the macOS hosted gate; the test now selects the gate matching the current platform.
- Hosted GitHub receipt collection for Linux, macOS and Windows is part of this candidate's gate set; no immutable tag is created by this change.


## v1.2.2-rc4 (2026-11-05) — deterministic isolated release smoke

- Preserves the rc3 product tree while fixing its explicit-config parity test to remove ambient `GITEA_*` / `GITQ_*` variables symmetrically for the Node and Python resolvers.
- Prevents a valid isolated mirror/owner configuration supplied by release smoke from overriding the explicit fixture on only the Node side.
- Requires a new immutable rc4 candidate and tag; the existing rc3 tag remains unchanged.

Release status: the immutable rc4 candidate was byte-identical across its approved private stages and passed all 17 fresh-clone smoke checks. The tag cannot be moved; any fix requires a new candidate.

Known gap: the `github-windows` release receipt is not produced. `release-preflight.py` allows only `github-actions-hosted` providers and structurally rejects `vtwsl` / `self-hosted` evidence, while the repository is not published to GitHub — so the hosted Windows jobs cannot run. This is a deliberate contract boundary, deferred to v1.3, not an rc4 defect.

## v1.2.2-rc3 (Unreleased) — installation and configuration release blockers

- Establishes the maintainable `release/1.2.x` line on the exact `cff4843` topology bridge; release promotion must reuse identical commit/tree/tag objects.
- Makes effective configuration observable across MCP/CLI/indexer and rejects missing or placeholder owners.
- Makes Windows setup PowerShell 5.1 BOM-safe, interpreter-aware, profile-selectable, idempotent, and dry-run capable without restarting DSH.
- Gives Linux and macOS the same explicit profile-selection contract through the shared idempotent patcher.
- Uses native temporary paths and Node-aware MCP launchers for Git Bash/Windows E2E.
- Packages the shared config resolver in wheel and sdist artifacts and verifies all three console aliases from outside the source checkout, including explicit UTF-8 decoding on Windows hosted runners.
- Documents the FTS default-branch scope and Python/jieba cold-start contribution to end-to-end latency.
- Corrects MCP tool naming into server/host/GUI layers and ships an optional, non-auto-installed DSH skill.

Structural all-ref FTS and a persistent jieba worker remain v1.3 work.

---

## v1.2.2-rc2 — POSIX regression fix

### 版本概述 / Overview

v1.2.2-rc2 修复 v1.2.2 早期候选在 Linux / macOS 上引入的回归。**Windows 侧能力全部保留**，
本版本只把"无条件 Windows 化"的共享代码改回按平台分流。

#### 修复清单

| 级别 | 问题 | 影响 |
|---|---|---|
| P0 | 路径归一化未做平台分流 | `read_gitea_file` / `gts --read` 读含反斜杠文件名的文件时，**静默返回另一个文件的内容且不报错** |
| P1 | PATH 全量扫描 | 命中同名无关命令（如 GNU Triangulated Surface 的 `gts`），退化成含糊的"输出无法解析" |
| P2 | `rstrip('\r\n')` | 吞掉行内容尾部的 CR，`'abc\r\r\n'` → `'abc'` |
| P2 | `classify_kind()` 扩展名 | `foo\.md` 判不出扩展名，`kind` 从 `doc` 落入 `code`，检索漏召回 |
| P3 | 候选探测无缓存 | 候选从 11 膨胀到 69，同步 `statSync` 阻塞事件循环，每次工具调用都触发 |

#### 顺带的改善（v1.2.2 中确实正确的部分）

`_force_utf8_stdio()` 修复了旧版在 `PYTHONIOENCODING=ascii` 下的 `UnicodeEncodeError`
崩溃；`probeExecutable` 的 `isFile()` 判定、`fcntl` 条件导入、`IndexLock` 重构
对 POSIX 均无行为变化。这些予以保留。

#### 测试与 CI

- 新增 `test/posix-path.test.mjs`（5 项）：实测在 v1.2.2 早期候选上 5/5 失败、v1.2.2-rc2 上 5/5 通过。
- `npm test` 改为 `node --test` 自动递归发现 `test/*.test.mjs`（不带 glob 参数，兼容 Node 20/22/24），消除覆盖割裂；完整组合用 `npm run test:all`。
- CI 新增 **macOS runner**（此前从未有过）。

---

## v1.2.2-rc (2026-10-28)

### 版本概述 / Overview

`dsh-gitea-search` v1.2.2-rc 是一个重大的跨平台生态扩展与生产级加固候选。在承接 v1.2.1 架构与规范的基础上，本版本正式推出了**原生 Windows 平台支持**，彻底打破以往 Windows 环境下因缺失 PE 入口、文件锁不兼容及 shebang 无法执行导致 3 个检索工具不可用的底层限制；全面建立了覆盖路径归一化、参数防注入、并发锁对齐与环境探测的生产级防护体系，并在 GitHub Actions 真实 Windows Runner 上通过了全套自动化工作流验证（7/7 Jobs，21/21 测试用例 100% 通过）。

---

### 中文更新说明

#### 1. 原生 Windows 平台支持 (dsh-gitea-search)
- **原生 PE Launcher**：新增 `pyproject.toml` 与 `gts_cli.py`，通过 setuptools `[project.scripts]` 在 Windows 上编译生成原生 PE 启动器 `Scripts\gts.exe`，避开 Windows `CreateProcess` 对无扩展名 shebang 脚本的执行限制。
- **独立数据隔离**：将 `bin/gts` 与 `lib/indexer.py` 安装至 `share/dsh-gitea-search/` 数据子目录，消除与同名启动器的文件覆盖与无限递归风险。
- **一键自动化部署 (`scripts/setup-windows.ps1`)**：提供 Windows PowerShell 部署脚本，支持依赖安装、镜像目录准备、对已有镜像构建 FTS5 索引及 DSH `cordis.patch.yml` 自动注册；仓库获取仍需 Git Bash 或既有镜像流程。
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
- **回归覆盖存在缺口（更正说明）**：Windows CI 7/7 jobs、21/21 tests 属实；
  但 Linux 侧当时**只跑 e2e、不跑单测**，Windows 侧**只跑单测、不跑 e2e**，
  且全矩阵**没有 macOS runner**。共享代码的 POSIX 回归正是从这道缝里漏过去的。
  **本版本并非 100% 向后兼容**，详见下方 v1.2.2-rc2 的修复说明。

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

`dsh-gitea-search` v1.2.0 是一个重要的生态扩展与健壮性升级版本。本版本合入了首个外部社区 PR，带来了原生的 macOS 与 BSD 跨平台兼容支持；引入了零服务依赖的 Zero-Gitea 合成 demo；实现了 `config.json` 自动解析机制与 Node.js 跨版本优雅降级，并将自动化 E2E 扩充至 31 个断言调用。

---

### 中文更新说明

#### 1. 原生 macOS 与 BSD 平台支持 (合入社区 [PR #2](https://github.com/offzen/dsh-gitea-search/pull/2))
- **环境一键配置**：新增 `scripts/setup-macos.sh`，原生支持 macOS 默认的 Bash 3.2 环境，自动通过 Homebrew 补齐环境依赖。
- **管道防死锁与 BSD 工具链兼容**：针对缺乏 GNU `timeout` 命令的 macOS/BSD 系统，重构了非阻塞的子进程超时控制，彻底消除管道缓冲造成的死锁假死；同时兼容 BSD 版 `stat` 与 `awk` 命令行语法。

#### 2. Zero-Gitea 本地免服务演示 (`quickstart-local.sh`)
- **零服务端依赖**：无需安装或连接 Gitea 服务。运行 `bash scripts/quickstart-local.sh <output-dir>` 会在专用输出目录生成合成的 `demo-project.git` bare 仓并完成 FTS5 索引构建；它不会扫描或导入已有本地 Git 仓库。
- **极速体验**：为本地开发调试、非 Gitea 托管的单一代码库提供即开即用的 MCP 全文与代码检索能力。

#### 3. 配置文件自动寻径解析 (`config.json`)
- **免环境变量注入**：`lib/server.mjs` 支持自动向上查找并加载当前工作目录或父目录中的 `config.json` 配置文件。
- **多途径规范统一**：建立配置优先级（CLI 参数 > 环境变量 > `config.json`），完全打通 `GITEA_*` 标准规范与历史 `GITQ_*` 变量。

#### 4. Node.js 跨版本优雅降级与防御增强
- **CLI 自动 Fallback**：在缺少原生 `node:sqlite` 或 `zlib.zstdCompressSync` 的 Node.js `20.11 ~ 22.14` 环境下，自动平滑回退至系统 `sqlite3` CLI 与 `zstd` CLI，杜绝加载崩溃。
- **空配置预警**：MCP 启动时若检测到未配置有效镜像目录，输出友好的引导预警日志，取代无提示的异常退出。

#### 5. 质量保证与 CI 矩阵扩展
- **31 项全链路断言**：E2E 测试套件包含 31 个断言调用，Phase 5 校验 `config.json` 与 Zero-Gitea demo。
- **跨版本双矩阵验证**：历史 CI 矩阵覆盖 Node.js 20 LTS / 22 LTS 与 Python 3.11/3.12 组合。

---

## v1.1.0 (2026-10-23)

### 版本概述
`dsh-gitea-search` v1.1.0 是专为 DeepSeek Harness (DSH) 打造的高性能跨仓检索与会话检索插件。通过标准 Model Context Protocol (MCP) 协议，赋予 Agent 在大型代码与文档库中毫秒级检索、定位与阅读的能力。

### 核心特性
1. **双层极速检索引擎**：裸镜像 `git grep`（全分支 + 同行多词 AND 自动回退）与 SQLite FTS5 + Jieba 全文检索（BM25 加权与多类型分区）。
2. **Agent 上下文安全护栏**：条数硬夹（默认 5 / 最大 200）、单行截断（2000B）、总体缓冲区限制（128KB）与行窗口读取（默认 400 行）。
3. **自动化测试套件**：GitHub Actions CI 自动化 28 项端到端断言测试。
4. **性能遥测与无损维护**：`IoTracker` 读写统计遥测与 `dsh-session-health.mjs` 在线 WAL 截断维护。
