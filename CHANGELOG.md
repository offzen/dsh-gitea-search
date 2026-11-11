# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.2] - Unreleased

> **Candidate scope note (not a release announcement).** This line contains the
> owner-qualified repository identity work and the Windows legacy-index SQLite handle
> release. It supersedes the earlier private-only freeze published as `v1.2.2` on the
> private forges (exact P `9db7010…`); that private tag is immutable and is **not**
> moved, deleted or recreated by this entry. No public stable `v1.2.2` tag or Release
> exists, and the date stays `Unreleased` until formal exact-P evidence is closed.

Closes the remaining rc6 audit findings. The default verifier is fail-closed and
can only report `verified` when all four schema-3 receipts, deep forge evidence,
GitHub artifact digests, an exact expected ref, and an externally held four-file
trust anchor agree. The result remains explicitly capped at `same-party`; it is
not an independent attestation.

### Added
- Deep schema-2 forge evidence: repository, workflow path, event, branch,
  run-attempt, complete paginated job lists, hosted-runner signals, artifacts,
  fetch time, and artifact digests.
- Negative tests for `collect-test-counts.py` and `fetch-remote-evidence.py`, plus
  a static no-PyYAML workflow/needs/artifact-name contract test.
- Schema-3 receipts bind the exact test-counts bytes, counter script, and actual
  receipt writer; GitHub artifact zips must carry byte-identical receipts and
  their sha256 must match the forge record.

### Changed
- Clean-hosted release evidence is collected from an explicitly approved private GitHub staging repository before publication. Receipt writers record the actual repository; `release-preflight.py --expected-hosted-repository` independently pins the accepted staging identity. Public refs remain unchanged until strict preflight is verified and publication is separately authorized.
- `release-preflight.py` now defaults to `configured-unverified` unless evidence,
  external anchor, expected ref, and GitHub artifacts are complete. Relaxation
  switches can never produce `verified`.
- The trust anchor is an out-of-tree directory containing pinned copies of the
  preflight, provenance checker, evidence fetcher, and count collector; symlink,
  hardlink/self-compare, in-tree, missing, and mismatched anchors are rejected.
- Workflow/ref/repository/run-attempt comparisons are exact; required `needs`
  and load-bearing jobs are fixed sets rather than optional checks.
- Test counts record pass/fail/cancelled/skipped/todo, per-suite Python counts,
  and e2e failures; bool, negative, inconsistent, zero, or below-floor counts fail.
- All git subprocesses strip ambient `GIT_*`. Ignored inputs are allowlisted, and
  the three legacy entrypoints are accepted only as exact compatibility symlinks.

### Fixed
- Pass effective CLI/JSON mirror and owner settings to `--sync`; reject multiple
  owners before starting the single-owner synchronizer instead of silently syncing
  only the first owner.
- Windows setup installs the FTS extra when indexing, installs and imports the MCP
  SDK before profile registration, and validates Python >= 3.10 / Node >= 20.11.
- Post-install verification keeps stderr diagnostics out of JSON and supports CLI
  paths containing spaces or non-ASCII characters. Added executable regressions
  and a clean Windows installer gate without preinstalled jieba or node_modules.
- Gitea gate2 no longer requires the GitHub-only `GITHUB_WORKFLOW_REF` variable. GitHub still
  requires the native value exactly; Gitea derives the same canonical value only when its
  Actions marker and event payload independently match repository, ref and exact checkout SHA,
  with negative tests for missing markers and identity mismatches.
- CLI configuration, MCP configuration, empty-owner semantics, malformed config
  handling, explicit `GTS_BIN`, safe PATH resolution, Windows cmd/batch argument
  rejection, timeout attribution, and the early `mirror_state` import.
- The default release `test:preflight`/receipt chain now executes all 12 Python contract/regression files,
  including gate-context, dependency-missing, profile-YAML, unknown-repository and
  documentation guards. All receipt jobs provision PyYAML; missing optional test
  dependencies fail the measured suite instead of silently skipping coverage. Release/manual/Windows/index documentation now matches 1.2.2 contracts,
  and the superseded dirty-tree `RC5-REVIEW` record moved to the private audit archive.

## [1.2.2-rc6] - Unreleased

Hardens the release evidence chain. Every item below closes a finding from the
2026-11-07 external audit of the rc5 candidate; none of them changes product
behaviour.

### Added
- `scripts/fetch-remote-evidence.py` captures the forge's own record of a gate run
  (head_sha, run conclusion, every job's conclusion) so a receipt can be checked
  against something other than itself.
- `scripts/collect-test-counts.py` runs the suites and records real counts, so a
  receipt states how many tests ran instead of asserting success.
- `release-preflight.py --remote-evidence` / `--require-remote-evidence` cross-check
  receipts against that forge record; `--trust-anchor` requires the in-tree verifier
  to be byte-identical to an externally pinned copy.

### Changed
- Receipts are schema 2. `status` is derived from the upstream `needs` results rather
  than hardcoded, `started_at`/`gate_started_at` come from the real gate start (no
  `now()` fallback) with a computed `duration_seconds`, and `test_counts` is required.
- `workflow_ref` must name the gate's own workflow *and* the same ref the receipt
  claims, so a run on a side branch cannot mint a receipt for a released ref.
- Runner label and `toolchain.os` platform family are validated for Linux and macOS
  too, not only Windows.
- Run identity is deduplicated on `workflow_run_id`; gates that legitimately share a
  run (`github-linux` + `github-macos`) are allowed, different workflows sharing one
  run are rejected.
- The gate2 receipt declares `trust: same-party` and carries repository/workflow/
  event/ref, so its scope (regression coverage, not independent trust) is explicit.
- Untracked files in the checkout are rejected; `git diff --quiet` cannot see them.

### Fixed
- Release preflight no longer treats a receipt's internal consistency as proof that a
  run happened. Floors on test counts stop a silently weakened suite from verifying.

## [1.2.2-rc5] - Unreleased

### Added
- Read-only post-install and MCP stdio diagnostic entry points, plus a five-minute verification guide and user-facing capability boundaries.
- A non-blocking mirror freshness notice based on recorded successful syncs and clearer CLI failure attribution.

### Fixed
- Missing-jieba errors no longer masquerade as empty FTS results; repository-filtered zero hits stay a successful empty result.
- Profile YAML values reject control characters, escape apostrophes, normalize Windows paths and preserve unrelated profile entries.
- Corrected sync script discovery/failure reporting, wheel packaging of the sync script dependency and Python/Node timestamp parsing parity.
- Fixed malformed JSON examples and removed the unsupported macOS `--make-demo` hint.
- Fixed a hosted-receipt test that assumed a Linux runner, which made the macOS gate fail before reaching its assertion; the test now derives the gate from the running platform.

### Documentation
- Added explicit first-sync, index verification, host reload, rollback, npm/Python installation and support-boundary guidance.
- Documented the four-receipt gate set (gate2 Linux, hosted GitHub Linux/macOS/Windows) for this candidate.

## [1.2.2-rc4] - 2026-11-05

Tagged as `v1.2.2-rc4` (annotated tag object `6c4c516`, peeled commit `a78e456`). The tag is immutable; any subsequent correction requires a new `rc5` candidate.

### Fixed
- Isolated the Node/Python explicit-config parity test from ambient `GITEA_*` and legacy `GITQ_*` variables, so release smoke remains deterministic when it supplies a valid isolated runtime configuration.

### Release evidence
- Fresh-clone full smoke on the tag passed all 17 required checks: identity, provenance (`PROVENANCE_OK files=60`), `npm ci`, 50 node tests (46 pass / 0 fail / 4 platform skip), python release-preflight and hosted-receipt suites, wheel + sdist build, `[fts]` jieba, three console scripts, effective-config, reindex against a real bare mirror, FTS stats, Chinese FTS search, doctor, MCP `initialize`/`tools/list` (4 tools), and the rc2→rc4 upgrade path.
- Windows receipt under the hosted-only contract remains deferred to v1.3; the `github-windows` gate is intentionally unsatisfied for rc4 and the release is promoted with `--allow-partial`.

## [1.2.2-rc3] - Unreleased

### Fixed
- Unified effective configuration across MCP server, CLI, indexer, and doctor; missing/placeholder owners now fail closed.
- Made Windows setup BOM-safe for PowerShell 5.1, interpreter-aware for `gts.exe`, profile-selectable, idempotent, and dry-run capable.
- Unified Linux/macOS profile selection on the shared idempotent patcher; `Auto` now requires exactly one discovered profile.
- Made Git Bash E2E use native temporary paths and Node script launchers instead of direct `.mjs` spawn.
- Included the shared Python config resolver in both wheel and sdist artifacts, with isolated install gates for all three console aliases.

### Changed
- Document search reports `scope: default-branches`; all-ref FTS remains a v1.3 feature.
- Documentation distinguishes warm SQLite query time from end-to-end Python/jieba cold start.
- MCP naming documentation separates server bare names, host registration names, and GUI display labels.
- Added an optional DSH skill under `extras/`; installers do not install it automatically.

## [1.2.2-rc2]

> **Release Scope**：修复 1.2.2-rc 早期候选引入的 POSIX（Linux / macOS）回归。Windows 侧能力全部保留，
> 本版本只把"无条件 Windows 化"的共享代码改回**按平台分流**。

### Fixed
- **P0 路径归一化按平台分流（`lib/server.mjs`, `bin/gts`）**：新增 `normalizeGitPath()`，
  Windows 侧继续把 `\` 转成 `/`（NTFS 上反斜杠是保留分隔符，安全）；
  **POSIX 侧保持路径原样**（ext4 / APFS 上反斜杠是合法文件名字符）。
  修复前 `read_gitea_file(path:"a\\b.md")` 会静默返回 `a/b.md` 的内容且不报错。
- **P1 PATH 探测不再误命中同名无关命令（`lib/server.mjs`）**：PATH 是内容不可控的搜索面，
  实测 Linux 上 22 个 PATH 目录 × 3 个基名会生成 63 个候选，其中任何一个恰好叫 `gts`
  （如 `libgts-bin` 的 GNU Triangulated Surface 工具）都会被"可执行"这一条判据选中，
  随后报出含糊的"输出无法解析"。现在固定安装位优先命中，PATH 命中须通过一次性
  身份校验（`--help` 输出含 Gitea / gitq / dsh-search 特征）才被采用。
- **P2 行尾处理不再吞掉行内容尾部的 CR（`bin/gts`）**：`rstrip('\r\n')` 会把
  `'abc\r\r\n'` 削成 `'abc'`。改为只剥除行终止符本身。
- **P2 `classify_kind()` 扩展名判定（`lib/indexer.py`）**：归一化按 `IS_WINDOWS` 分流后，
  `foo\.md` 在 POSIX 上正确识别为 `.md` → `kind=doc`，不再漏召回。

### Changed
- **探测结果与开销（`lib/server.mjs`）**：`findSearchCli()` 结果缓存（显式 `GTS_BIN` /
  `GITQ_BIN` 时不缓存，保证改了立即生效）。候选数已从 11 膨胀到 69 且全为同步 `statSync`，
  每次工具调用都触发一次，缓存后启动期外不再重复探测。
- **`npm test` 语义变更**：由"仅 3 个 Windows 用例文件"改为 `node --test` 自动递归发现
  `test/*.test.mjs`（不带 glob 参数，兼容 Node 20/22/24），消除此前 Linux 只跑 e2e、Windows 只跑单测的覆盖割裂。
  完整组合仍用 `npm run test:all`（单测 + e2e）。

### Added
- **`test/posix-path.test.mjs`**：5 项 POSIX 路径语义回归用例。
  实测在 1.2.2-rc上 5/5 失败、在 1.2.2-rc2 上 5/5 通过，可锁死此类回归。
- **macOS CI runner（`.github/workflows/ci.yml`）**：新增 `test-macos` job。
  本项目 v1.2.0 起就带 BSD 兼容代码，但一直没有 macOS runner，
  Apple Silicon / APFS 侧的路径语义从未被验证过。

### Documentation
- 更正 `WINDOWS-PORT.md` 中"过滤目录穿越符号 `..`"的**虚假安全声明**（代码里并无此逻辑）。
- 更正 `PROVENANCE.json` 的 `source_commit`：原先写的是 `6712a7b`（v1.2.1），
  但文件哈希取自 1.2.2-rc，溯源链断裂。
- 更正 CHANGELOG / RELEASE-NOTES 中"保持 Linux / macOS 完整兼容""100% 向后兼容"的表述。

### Acknowledgements
- 特别感谢外部开发者 **@uaisu** 敏锐发现并详尽复现 v1.2.2-rc 在 POSIX 环境下的路径反斜杠静默串读缺陷（PR #3），并提供了关键的测试用例思路。

## [1.2.2-RC] - 2026-10-28

> **Release Scope / 版本说明**：本次发布为**原生 Windows 跨平台支持与稳健性加固里程碑**。彻底打破以往 Windows 下 4 个 MCP 工具 3 个无法运行的限制，提供原生 PE launcher、PATH 自动探测与命令注入安全防护；全面建立覆盖路径归一化、参数防注入、并发锁对齐与环境探测的生产级防护体系，并在 GitHub Actions 真实 Windows Runner 上通过 7/7 jobs 及 21/21 tests（100% 通过、0 skipped、0 failed）。
>
> ⚠️ **升级警示（1.2.2-rc 存在 POSIX 侧回归，已在 1.2.2-rc2 修复）**
>
> 本版本**并非**"保持 Linux / macOS 完整兼容"，也**不是** 100% 向后兼容。
> 由于共享代码里的路径归一化未做平台分流，Linux / macOS 上出现以下行为变更：
>
> - `read_gitea_file`（MCP）与 `gts --read`（CLI）在读取**文件名含反斜杠**的文件时，
>   会静默返回另一个文件的内容（如 `a\b.md` 读到 `a/b.md`），**且不报错**；
> - `classify_kind()` 对这类文件判不出扩展名，`kind` 从 `doc` 落入 `code`，检索漏召回。
>
> 若你的镜像仓中存在含反斜杠的文件名，请勿停留在 1.2.2-rc，直接升级到 **1.2.2-rc2**。

### Added
- **Windows 原生平台架构支持**:
  - **原生 PE Launcher 与打包支持 (`pyproject.toml`, `gts_cli.py`)**：通过 setuptools 的 `[project.scripts]` 生成 Windows 原生 PE 启动器 `Scripts\gts.exe`，解决 Windows `CreateProcess` 无法直接运行 shebang 脚本的底层限制；数据文件采用 `share/dsh-gitea-search/{bin,lib}` 独立隔离路径，消除与 console_scripts 同名启动器的覆盖冲突。
  - **Windows 一键自动化配置脚本 (`scripts/setup-windows.ps1`)**：支持依赖安装、镜像目录准备、对已有镜像构建 FTS5 索引，以及 DSH `cordis.patch.yml` 自动注册；仓库获取仍需 Git Bash 或既有镜像流程。
  - **Windows CI 全矩阵工作流 (`.github/workflows/windows-ci.yml`)**：新增覆盖 W5（PE 结构与执行）、W6（PowerShell AST 语法）、W7（文件区间排他锁）、W8（命令行防注入与边界转义）、W9（CRLF 检索与换行）的 7 个专项测试任务，覆盖 Python 3.11/3.12 与 Node 20/22 组合矩阵。
  - **跨平台测试套件 (`test/windows-adapt.test.mjs`, `windows-lock.test.mjs`, `windows-quote.test.mjs`)**：提供 21 项跨平台与 Windows 行为回归断言，`npm test` 自动采用原生 `node --test` 执行。

### Changed (行为变更 —— 影响 POSIX 侧，请务必评估)
- **Git 路径反斜杠归一化 (`lib/server.mjs`, `bin/gts`)**：`readGiteaFile`、`gitDir` 以及 CLI `gts --read` 将路径中的反斜杠统一归一化为正斜杠 `/`（**不再区分平台**）。
  - Windows：消除 `fatal: path '...' does not exist in 'HEAD'`，属预期修复；
  - Linux / macOS：**行为回归**。反斜杠在 ext4 / APFS 上是合法文件名字符，
    `a\b.md` 是一个真实文件名，归一化后会被改写为 `a/b.md`，导致读到另一个文件的内容。
  - 详见 1.2.2-rc2 的修复条目。

### Fixed (跨平台与运行环境稳健性加固)
- **CLI 同步跨平台容错与前缀规范 (`bin/gts`)**：`gts --sync` 增加 `shutil.which('bash')` 动态探测与友好的跨平台回退提示；统一 `die()` 报错前缀为 `gts:`。
- **PATH 环境变量引号安全剥离 (`lib/server.mjs`)**：增强 `findSearchCli()` 对 Windows 系统 `PATH` 中包含双引号目录项的清洗处理，防止路径拼接残损。
- **Gitea Actions 队列卡滞防护 (`.github/workflows/windows-ci.yml`)**：为 Windows CI 工作流任务增设 `if: github.server_url == 'https://github.com'` 守卫，避免在缺少 Windows runner 的自建 Gitea Actions 环境中触发永久 queued 队列堆积。
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
- **版本号升级**：升级至 `1.2.2-rc`。

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
- **Zero-Gitea Local Demo**: Added `scripts/quickstart-local.sh`, which creates a synthetic `demo-project.git` in a dedicated output directory and builds a local index without an external Gitea service. It does not import existing local repositories.
- **macOS & BSD Native Compatibility ([PR #2](https://github.com/offzen/dsh-gitea-search/pull/2)):**
  - Added `scripts/setup-macos.sh` supporting macOS default Bash 3.2 and Homebrew dependency installation.
  - Implemented non-blocking subshell timeout mechanism for platforms without GNU `timeout`.
  - Added compatibility wrappers for BSD `stat` and `awk`.
- **Automatic `config.json` Resolution**: `lib/server.mjs` now automatically resolves and loads `config.json` hierarchically from current or parent directories, removing the need for verbose environment variable exports.
- **Node.js `< 22.15` CLI Fallback**: Added seamless fallback to system `sqlite3` and `zstd` CLI when native `node:sqlite` or `zlib.zstdCompressSync` are unavailable in Node.js `20.11 ~ 22.14`.
- **Extended E2E Test Suite**: Expanded `scripts/run-e2e-test.sh` to 31 assertion calls across 5 phases, including assertions for `config.json` resolution and the Zero-Gitea demo.
- **Multi-Environment CI Matrix**: Expanded the historical workflows to validate Node 20 / 22 and Python 3.11/3.12 combinations.
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
