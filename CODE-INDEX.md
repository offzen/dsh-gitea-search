# CODE-INDEX —— dsh-gitea-search 代码正册

> 规则：本仓所有可执行脚本、源码模块、自动化测试与 CI gate 均登记于此；生成缓存与构建输出不属于源码资产。状态截至 2026-11-12。这里记录覆盖范围和最近已知证据，**不把历史 rc 的运行结果当作当前候选的 exact-P 收据**。

## 1. CLI 与 MCP 入口

| 路径 | 用途 | 负载/写操作 |
|---|---|---|
| `bin/gts` | 跨仓 Git/FTS CLI，兼容 `gitq` / `dsh-search` | 查询只读；reindex/sync 会写索引或镜像 |
| `bin/mcp-server` | MCP stdio server 包装入口；以 file URL 动态导入，兼容 Windows ESM | 启动临时 Node 子进程 |
| `gts_cli.py` | setuptools console-script 入口，加载已安装的 `bin/gts` | 单次启动与模块加载 |

## 2. 核心实现

| 路径 | 用途 | 负载/写操作 |
|---|---|---|
| `lib/config.py` / `lib/config.mjs` | Python/Node effective-config resolver；缺失或畸形显式配置 fail closed | 轻量文件读取 |
| `lib/indexer.py` | SQLite FTS5/jieba 索引与查询；R2 仅在完整可信构建后按仓原子清理已消失 mirror 的四表记录 | build/reindex 写 SQLite |
| `lib/mirror_state.py` / `lib/mirror-state.mjs` | Python/Node 镜像同步状态解释，区分成功与失败尝试 | 读取 state 元数据 |
| `lib/next-actions.mjs` | 将 MCP/CLI 失败归因并生成结构化下一步 | 只读解释 |
| `lib/server.mjs` | 四工具 MCP stdio server、CLI 进程管理与输出护栏 | 长驻时启动 CLI；`sync:true` 可写镜像 |

## 3. 安装、同步、诊断与发布脚本

| 路径 | 用途 | 负载/写操作 |
|---|---|---|
| `scripts/setup.sh` | Linux 依赖检查、兼容链接与显式 profile 安装 | 可安装依赖、写链接/profile |
| `scripts/setup-macos.sh` | macOS venv、链接、显式 profile 安装 | 可安装依赖、写 HOME/profile |
| `scripts/setup-windows.ps1` | Windows pip 安装、PE 入口发现、显式 profile 注册 | 可安装包、建索引、写 profile |
| `scripts/profile-patch.py` | 备份、原子、幂等修改单个 profile | 写目标与时间戳备份 |
| `scripts/launch-spec.mjs` | Node 脚本/可执行入口分类及 Windows 启动策略 | 无持久写入 |
| `scripts/probe-stdio.mjs` | MCP initialize、tools/list、tools/call 探针 | 临时子进程 |
| `scripts/doctor-mcp.sh` / `scripts/doctor-mcp.ps1` | 安装后独立 MCP 只读验收 | 临时子进程；不改 profile/宿主 |
| `scripts/onboarding.sh` / `scripts/onboarding.ps1` | 安装后配置、镜像、索引只读汇总；给出显式下一步 | 只读；不自动同步、索引或 reload |
| `scripts/test-python-package.py` | 从源码树外安装 wheel/sdist 并验证三个入口 | 临时 venv/文件 I/O |
| `scripts/generate-provenance.py` / `scripts/check-provenance.py` | 从 tracked tree 生成/核验非自引用 SHA-256 manifest | 写/读 `PROVENANCE.json` |
| `scripts/collect-test-counts.py` | 在 receipt job 内运行 Node、Python 与 E2E 并输出 schema-2 计数 | 运行测试、写树外 JSON |
| `scripts/fetch-remote-evidence.py` | 只读拉取 forge run/job/artifact 深证据并输出 schema-2 JSON | 网络读取、写树外 JSON |
| `scripts/ci/linux-gate.sh` | 清理 ambient 配置、绑定 exact SHA、运行 Linux 完整门禁并原子生成树外 schema-3 receipt | 临时 HOME/cache/mirror、构建目录与树外 receipt |
| `scripts/release-preflight.py` | 只读核验 D/P、四收据、深证据、artifact、external anchor 和 exact ref | 只读 Git/JSON；不 push/tag/dispatch |
| `scripts/run-e2e-test.sh` | 31 项隔离全链路验收 | 临时 bare repos/SQLite |
| `scripts/run-realdata-test.sh` | 真实镜像验收 | 可能增量写索引；高负载 |
| `scripts/sync.sh` | Gitea bare mirror 同步、经远端 symref+OID 验证的默认分支 `HEAD` 收敛，以及带完整/局部/降级 trust 信号的索引刷新 | 网络与磁盘写入 |
| `scripts/quickstart-local.sh` | 创建合成 Zero-Gitea demo 仓并建索引；不导入已有本地仓 | 写目标目录及项目根 `config.json` |
| `scripts/respawn-mcp.sh` | 重启单个 MCP 子进程 | 影响运行进程 |
| `scripts/dsh-session-health.mjs` | 会话健康检查/迁移 | `--fix` 可写会话/SQLite |
| `scripts/session-export-incremental.py` | 增量导出会话 Markdown | 写导出目录 |

## 4. 自动化测试

### Node 测试（`node --test` 自动发现）

| 路径 | 覆盖目标 |
|---|---|
| `test/config-contract.test.mjs` | Node/Python 配置优先级、错误与 placeholder 拒绝 |
| `test/launch-spec.test.mjs` | Node-aware launcher |
| `test/linux-gate-receipt-path.test.mjs` | gate2 receipt 必须位于 tracked checkout 外 |
| `test/linux-gate-static.test.mjs` | Linux gate 静态合同与 Windows hosted 边界 |
| `test/macos-setup.test.mjs` | macOS installer profile 与 Bash 3 兼容 |
| `test/first-run.test.mjs` | effective sync 配置、失败退出、R1 索引失败 CLI 归因与 onboarding 回归 |
| `test/mirror-state.test.mjs` | 镜像/索引成功与失败状态、时间、stale 解释及 Python/Node parity |
| `test/next-actions.test.mjs` | MCP/CLI 失败分类、R1 `INDEX_UPDATE_FAILED` 与 C2 `GIT_SEARCH_FAILED` 归因，以及 v1.2.3 C1 查询/控制选项分隔与无副作用回归 |
| `test/git-error-not-zero-hit.test.mjs` | C2 真实 Git fixture：clean no-hit、invalid ERE、坏仓+部分命中与 MCP 错误传播 |
| `test/sync-index-failure.test.mjs` | R1 索引失败传播；R2 验证权威完整同步才允许 global prune，`-r`/降级/部分失败必须传 `--no-global-prune` |
| `test/sync-stage6-contract.test.mjs` | D1/D2/D3/D5 同步合同、macOS Bash 3.2 UTF-8 变量静态门，以及真实 fallback timeout 的直接/管道 stdin 与子进程退出码回归 |
| `test/sync-default-head.test.mjs` | C4 real-Git fixture：多分支 `main→trunk` 收敛、post-fetch 不可达、既有/新建空 remote fail closed、固定 marker 与凭据 canary 不泄漏 |
| `test/posix-path.test.mjs` | POSIX 反斜杠路径与 CR 语义 |
| `test/probe-stdio.test.mjs` | MCP stdio 探针成功与错误路径 |
| `test/profile-patch.test.mjs` | profile 幂等、备份、check mode |
| `test/release-scope.test.mjs` | 版本、scope、tool naming、optional skill 契约 |
| `test/windows-adapt.test.mjs` | Windows 静态适配、BOM、installer 契约 |
| `test/windows-lock.test.mjs` | Windows `msvcrt` 锁；非 Windows 跳过 |
| `test/windows-quote.test.mjs` | cmd/batch 参数拒绝与引号合同 |

### Python 测试（`npm run test:preflight` 与 receipt counter 显式执行）

| 路径 | 覆盖目标 |
|---|---|
| `test/release-preflight.test.py` | D/P、schema-3 receipts、evidence、artifact、anchor、ignored 面负向合同 |
| `test/hosted-receipt.test.py` | hosted receipt writer 的平台、身份与 input hash |
| `test/collect-test-counts.test.py` | 机器可读计数、算术和零套件拒绝 |
| `test/fetch-remote-evidence.test.py` | evidence 分页、深字段、run id 与 token scrub |
| `test/workflow-contract.test.py` | workflow needs/jobs/artifact/writer 静态对齐 |
| `test/linux-gate-context.test.py` | Gitea/GitHub workflow identity 正向与缺标记、冲突 ref、event payload 漂移负向合同 |
| `test/dependency-missing.test.py` | jieba 缺失时的 CJK fail-closed 与显式 override |
| `test/owner-qualified-repos.test.py` | owner-qualified 身份、legacy rebuild、C3 读取原子性，以及 R2 trusted/partial/degraded stale-repo prune 合同 |
| `test/fts-result-contract.test.py` | C6 跨仓 blob 可见性、C7 repo filter 字面匹配与 R3 分页真值合同 |
| `test/profile-yaml.test.py` | YAML round-trip、控制字符和缩进回归；依赖 PyYAML，缺失时门禁失败，gate2/hosted receipt jobs 显式安装该依赖 |
| `test/repo-zero-hit.test.py` | 显式未知 repo 的结构化零命中 |
| `test/documentation-contract.test.py` | release guide、formal state、用户 onboarding/quickstart、内容分区、贡献指南与 Windows 安全文档静态契约 |

## 5. CI 与 receipt writer

| 路径 | 权威范围 |
|---|---|
| `.gitea/workflows/ci.yml` | private forge/gate2 exact-SHA Linux gate；receipt 摘要留在 job log |
| `.github/workflows/ci.yml` | GitHub-hosted Ubuntu/macOS 单测、E2E、包门与 exact-P receipts |
| `.github/workflows/windows-ci.yml` | GitHub-hosted PE、PS5.1/PS7、runtime、CRLF、Git Bash 与 Windows receipt |
| `.github/scripts/write-gate-receipt.py` | 在 hosted job 内实测 runner/run/workflow、绑定 counts 与 writer hash，写 schema-3 receipt |

## 6. 发布元数据与文档资产

| 路径 | 用途 |
|---|---|
| `pyproject.toml` | Python metadata、3 个入口与数据文件清单 |
| `package.json` / `package-lock.json` | Node metadata、依赖锁与默认 test scripts |
| `PROVENANCE.json` | D 的 tracked 文件哈希；只允许在 P 中单独变更 |
| `extras/dsh-skill/gitea-search/SKILL.md` | 可选 Agent 检索指导；installer 不自动安装 |
| `docs/DEVELOPMENT-AND-RELEASE.md` | D/P、schema-3 receipt、schema-2 evidence 与发布状态机正本 |
| `docs/POST-INSTALL.md` | 安装后闭环验收：首次同步、索引、独立 MCP、宿主工具表、真实调用、可选 skill 与回滚 |
| `PROJECT-STATE.md` | 当前版本、ref、树清理和候选判定的简明快照 |
| `WIKI.md` | 有证据、带范围和失效条件的耐久陷阱 |

## 7. 当前证据边界

- rc7 原始 P（`e20b37c…`）曾在 2026-11-07 本地跑过 Node 125、Python 合约 35、E2E 31/31；该证据只绑定旧 exact P。本次 hygiene 修改在未冻结新 D/P 前测得 Node 125（121 pass/4 Windows-only skip）、Python 9 套件（49 tests）、E2E 31/31；冻结后还须在 exact P 重验。
- 先前私有候选的 gate2 因 runner 缺少 hosted-only workflow identity 变量而红；兼容修订必须产生新的 D/P 与新候选。新 P 的本地测试、私有 gate2 与 hosted evidence 必须分别记录，不得复用旧 P 的 receipt。
- 没有 fresh GitHub-hosted Linux/macOS/Windows schema-3 证据时，release preflight 只能是 `configured-unverified`；信任上限始终是 `same-party`。
