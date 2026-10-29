# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.2-RC] - 2026-10-28

> **Release Scope / 版本说明**：本次发布为**原生 Windows 跨平台支持与稳健性加固里程碑**。在保持 Linux / macOS 完整兼容的前提下，彻底打破以往 Windows 下 4 个 MCP 工具 3 个无法运行的限制，提供原生 PE launcher、PATH 自动探测与命令注入安全防护；全面建立覆盖路径归一化、参数防注入、并发锁对齐与环境探测的生产级防护体系，并在 GitHub Actions 真实 Windows Runner 上通过 7/7 jobs 及 21/21 tests（100% 通过、0 skipped、0 failed）。

### Added
- **Windows 原生平台架构支持**:
  - **原生 PE Launcher 与打包支持 (`pyproject.toml`, `gts_cli.py`)**：通过 setuptools 的 `[project.scripts]` 生成 Windows 原生 PE 启动器 `Scripts\gts.exe`，解决 Windows `CreateProcess` 无法直接运行 shebang 脚本的底层限制；数据文件采用 `share/dsh-gitea-search/{bin,lib}` 独立隔离路径，消除与 console_scripts 同名启动器的覆盖冲突。
  - **Windows 一键自动化配置脚本 (`scripts/setup-windows.ps1`)**：支持一键镜像仓库拉取、依赖安装、FTS5 索引构建与 DSH `cordis.patch.yml` 自动注册。
  - **Windows CI 全矩阵工作流 (`.github/workflows/windows-ci.yml`)**：新增覆盖 W5（PE 结构与执行）、W6（PowerShell AST 语法）、W7（文件区间排他锁）、W8（命令行防注入与边界转义）、W9（CRLF 检索与换行）的 7 个专项测试任务，覆盖 Python 3.11/3.12 与 Node 20/22 组合矩阵。
  - **跨平台测试套件 (`test/windows-adapt.test.mjs`, `windows-lock.test.mjs`, `windows-quote.test.mjs`)**：提供 21 项跨平台与 Windows 行为回归断言，`npm test` 自动采用原生 `node --test` 执行。

### Fixed (跨平台与运行环境稳健性加固)
- **Git 路径反斜杠归一化 (`lib/server.mjs`, `bin/gts`)**：`readGiteaFile`、`gitDir` 以及 CLI `gts --read` 自动将 Windows 反斜杠路径（如 `lib\server.mjs` 或 `owner\repo`）统一归一化为正斜杠 `/`，杜绝 Git 抛出 `fatal: path '...' does not exist in 'HEAD'`。
- **CLI 同步跨平台容错与前缀规范 (`bin/gts`)**：`gts --sync` 增加 `shutil.which('bash')` 动态探测与友好的跨平台回退提示；统一 `die()` 报错前缀为 `gts:`。
- **PATH 环境变量引号安全剥离 (`lib/server.mjs`)**：增强 `findSearchCli()` 对 Windows 系统 `PATH` 中包含双引号目录项的清洗处理，防止路径拼接残损。
- **Gitea Actions 队列卡滞防护 (`.github/workflows/windows-ci.yml`)**：为 Windows CI 工作流任务增设 `if: github.server_url == 'https://github.com'` 守卫，避免在缺少 Windows runner 的自建 Gitea/gitpub Actions 环境中触发永久 queued 队列堆积。
- **PowerShell YAML 路径转义安全 (`scripts/setup-windows.ps1`)**：改用 `.Replace('\', '/')` 正斜杠进行 DSH 配置插值，彻底杜绝 Windows 路径反斜杠在 YAML 解析器中触发 `\U` / `\b` 等非法转义错误。
- **CRLF 换行符尾部截断残留 (`bin/gts`, `lib/indexer.py`)**：文本流读取升级为 `.rstrip('\r\n')`，索引摘要生成器彻底规整孤立 `\r`，消除 Windows 文本在终端与 Web UI 渲染时的光标跳行与显示混乱。
- **Windows 文件排他锁区间一致性 (`lib/indexer.py`)**：`IndexLock` 在 Windows `msvcrt.locking` 加锁前显式执行 `seek(0)`，与 `_release()` 保持严格对称，确保多进程并发构建索引时的严格互斥。
- **系统 `PATH` 环境变量自动探测 (`lib/server.mjs`)**：自动遍历系统 `process.env.PATH` 并智能匹配可执行后缀，支持全局 PATH、虚拟环境 (venv) 与 Scoop 开箱免配置识别 `gts.exe`。
- **用户级安装路径兼容 (`gts_cli.py`)**：在 `_script_candidates()` 候选路径中加入 `site.USER_BASE` 与 `sys.prefix`，全面支持 `pip install --user .` 免管理员提权安装。
- **PyPI 分类器规范化 (`pyproject.toml`)**：修正 Trove Classifier 为标准规范的 `"Operating System :: MacOS :: MacOS X"`。
- **文档体系手册化重构与断言数字对齐 (`README.md`, `MANUAL.md`, `WINDOWS-PORT.md`)**：响应精简化规范，将全部环境安装部署细节从 `README.md` 迁移至 `MANUAL.md §3` 与 `WINDOWS-PORT.md`；将合成测试断言数精确校准为实测一致的 31 项，并补充 21 项跨平台行为测试说明。

### Changed
- **Node.js 子进程平台分支 (`lib/server.mjs`)**：`.exe` / `.com` 直执行不经 shell；`.cmd` / `.bat` 经 `cmd.exe` 时显式构造 `cmd /d /s /c "<已引号化命令>"` 并逐参数加引号，封堵 `& | < > ^` 命令注入通道。
- **Python 跨平台底层兼容 (`lib/indexer.py`)**：`fcntl` 与 `resource` 改为条件导入，在 Windows 下优雅降级。
- **版本号升级**：升级至 `1.2.2`。

---

## [1.2.1] - 2026-10-27

> **Release Scope / 版本说明**：本次发布为纯文档与使用规范升级（对现有代码无破坏、无新代码特性，但包含了文档相关的必要修订）。重点强化 LLM 上下文预算保护、防注意力稀释与模型失忆、MCP 命名空间防冲突及生产权限安全加固。

### Added
- **Dedicated Usage Guide (`docs/USAGE.md`)**:
  - Detailed parameter cheat sheets for all 4 foundational MCP tools (`search_gitea`, `search_gitea_docs`, `read_gitea_file`, `list_gitea_repos`).
  - Added 5 real-world workflow scenarios demonstrating two-step lookup, session incident retrieval, and `files_only: true` blast-radius detection.
  - Added Agent Context Guard Checklist for humans and prompts.
- **MCP Tool Naming Guide (`docs/TOOL-NAMING.md`)**:
  - Deep-dive into MCP protocol namespace specification ("within the server's namespace").
  - Analyzed 4 typical multi-server collision failure modes: unknown tool, registration override, silent routing errors, and schema mismatches.
  - Added standard System Prompt / Skill declaration templates for Agent instructions.
- **Production Credential Hardening**:
  - Added read-only least-privilege token / deploy key guidelines (`repo:read`) for enterprise Gitea integration in `MANUAL.md`.

### Changed
- **User-Centric README Overhaul (`README.md`)**:
  - Refocused the project pitch on **Token & Context Conservation** and cognitive attention protection (mitigating the critical pain point where massive context bloat from 80k tokens causes attention dilution and model amnesia; slashing usage to 1.5k - 3k tokens per task via two-step targeted retrieval).
  - Promoted the "Zero-Gitea 30-Second Quickstart" to primary position.
  - Upgraded common integration gotchas into professional callouts ("💡 核心接入避坑指南") highlighting `transport: stdio` requirement and host namespace prefixes.
  - Cleaned and standardized public naming conventions (`gitea-search` / `mcp__gitea-search__*`).
- **Comprehensive Operations Manual (`MANUAL.md`)**:
  - Standardized structure covering dual deployment modes (Zero-Gitea vs. Enterprise), Cron synchronization, 4-layer context guards, and FTS5 engine internals.
- **Version Bump**: Bumped package version to `1.2.1`.

---

## [1.2.0] - 2026-10-26

### Added
- **Zero-Gitea Local Quickstart**: Added `scripts/quickstart-local.sh` enabling developers to spin up a local MCP search engine directly from local Git repositories with zero external Gitea/network dependencies.
- **macOS & BSD Native Compatibility ([PR #2](https://github.com/offzen/dsh-gitea-search/pull/2)):**
  - Added `scripts/setup-macos.sh` supporting macOS default Bash 3.2 and Homebrew dependency installation.
  - Implemented non-blocking subshell timeout mechanism for platforms without GNU `timeout`.
  - Added compatibility wrappers for BSD `stat` and `awk`.
- **Automatic `config.json` Resolution**: `lib/server.mjs` now automatically resolves and loads `config.json` hierarchically from current or parent directories, removing the need for verbose environment variable exports.
- **Node.js `< 22.15` CLI Fallback**: Added seamless fallback to system `sqlite3` and `zstd` CLI when native `node:sqlite` or `zlib.zstdCompressSync` are unavailable in Node.js `20.11 ~ 22.14`.
- **Extended E2E Test Suite**: Expanded `scripts/run-e2e-test.sh` to 32 assertions across 5 phases, including new assertions for `config.json` resolution and Zero-Gitea local mode.
- **Multi-Environment CI Matrix**: Expanded GitHub Actions and Gitea Actions workflows to validate Node 20 / 22 and Python 3.11-3.13 combinations.
- **Open Source Governance**: Added `CONTRIBUTING.md` documenting contribution workflow, testing requirements, and upstream synchronization disciplines.

### Fixed
- Fixed process hang / deadlock caused by pipe buffering in fallback `timeout()` on macOS and BSD systems.
- Fixed unhandled exception when launching MCP server with empty or unconfigured mirror directories; now issues actionable warnings.
- Unified environment variable precedence: `GITEA_*` standard variables take precedence with full backward compatibility for legacy `GITQ_*` variables.
- Fixed path resolution in `sync.sh` where indexer script path check failed under certain directory layouts.

### Changed
- Refactored `README.md` and `MANUAL.md` to highlight local quickstart, macOS guides, `config.json` schema, and standardized `gitea_search` naming conventions.
- Enforced `engines.node >= 20.11.0` in `package.json`.

---

## [1.1.0] - 2026-10-23

### Added
- **SQLite FTS5 + Jieba Full-Text Search Engine**: Millisecond document and session full-text search with BM25 ranking and content kind segmentation (`doc`, `session`, `config`, `script`).
- **Smart `--and` Intersection Fallback**: Bare mirror `git grep` automatically falls back to same-line token intersection matching when multi-word literal queries yield zero hits.
- **4-Layer Context Guards**:
  - Max results clamp (default 5, hard limit 200).
  - Single-line clamp (2,000 bytes) with UTF-8 byte boundary preservation.
  - Search buffer budget clamp (128 KB total output).
  - Paged file reading window (`read_gitea_file` default 400 lines / 60 KB).
- **BlobReader Optimization**: Batch reading via persistent `git cat-file --batch` processes, eliminating `git show` subshell overhead.
- **DSH Session Health Diagnostic**: Added `scripts/dsh-session-health.mjs` supporting v1~v4 session archive inspection and online WAL truncation (`PRAGMA wal_checkpoint(TRUNCATE)`).
- **GitHub Actions CI Automation**: Initial 28-assertion testbed workflow on Ubuntu runners.

---

## [0.1.1] - 2026-10-15

### Added
- Initial standalone Node.js MCP server prototype with basic `git grep` bridging.
- Basic stdio MCP communication.
