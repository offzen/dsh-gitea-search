# CODE-INDEX —— dsh-gitea-search 代码正册

> 规则：本仓所有可执行脚本、源码模块、自动化测试与 CI gate 均登记于此；生成缓存与构建输出不属于源码资产。运行证据截至 2026-11-01，原生 Windows/macOS 未运行的项目明确标记为 unverified。

## 1. CLI 与 MCP 入口

| 路径 | 用途 | 负载/写操作 | rc3 运行证据 |
|---|---|---|---|
| `bin/gts` | 跨仓 Git/FTS CLI，兼容 `gitq` / `dsh-search` | 查询只读；reindex/sync 会写索引或镜像 | Linux unit/E2E verified |
| `bin/mcp-server` | MCP stdio server 包装入口；以 file URL 动态导入，兼容 Windows ESM | 启动临时 Node 子进程 | Linux E2E + native Windows 6/6 MCP probe verified |
| `gts_cli.py` | setuptools console-script 入口，加载已安装的 `bin/gts` | 单次启动与模块加载 | 隔离 wheel/sdist gate 待本机工具链运行；CI configured |

## 2. 核心实现

| 路径 | 用途 | 负载/写操作 | rc3 运行证据 |
|---|---|---|---|
| `lib/config.py` | Python effective-config resolver；owner fail closed | 轻量文件读取 | Linux unit/E2E verified |
| `lib/config.mjs` | Node effective-config resolver | 轻量文件读取 | Linux unit/MCP smoke verified |
| `lib/indexer.py` | SQLite FTS5/jieba 索引与查询 | build/reindex 写 SQLite | 隔离 Linux E2E verified |
| `lib/server.mjs` | 四工具 MCP stdio server 与输出护栏 | 长驻时启动 CLI 子进程；`sync:true` 可写镜像 | 临时 Linux MCP smoke verified |

## 3. 安装、同步与运维脚本

| 路径 | 用途 | 负载/写操作 | rc3 运行证据 |
|---|---|---|---|
| `scripts/setup.sh` | Linux 依赖检查、软链与显式 profile 安装 | 可安装依赖、写软链/profile | syntax/static/profile contract verified；未真装生产 |
| `scripts/setup-macos.sh` | macOS venv、软链、显式 profile 安装 | 可安装依赖、写 HOME/profile | Linux syntax/helper tests；native macOS unverified |
| `scripts/setup-windows.ps1` | Windows pip 安装、与 active interpreter 绑定的 PE 发现、显式 profile 注册 | 可安装包、建索引、写 profile | PS5.1 真机隔离安装/profile smoke verified；PS5.1/PS7 WhatIf hosted verified，runtime interpreter-binding 修复待新候选 hosted 重验 |
| `scripts/profile-patch.py` | 备份、原子、幂等修改单个 profile | 写目标与时间戳备份 | 临时 profile unit verified |
| `scripts/launch-spec.mjs` | Node script / executable launcher 分类；Windows 无扩展名 MCP wrapper 走 Node | 无持久写入 | Linux unit + native Windows MCP smoke verified |
| `scripts/probe-stdio.mjs` | MCP initialize/tools/list/tools/call 探针 | 临时进程 | Linux E2E verified |
| `scripts/test-python-package.py` | 从源码树外安装 wheel/sdist 并验证三个入口；以 UTF-8 解码子进程输出，避免 Windows locale reader 崩溃 | 临时 venv/文件 I/O | Ubuntu py3.12 hosted verified；Windows py3.12 首轮暴露 locale 缺陷后已最小修复，待新候选 hosted 重验 |
| `scripts/generate-provenance.py` / `scripts/check-provenance.py` | 从 `git ls-files` 生成/核验非自引用 SHA-256 manifest | 写/读 `PROVENANCE.json` | Linux local gate verified after candidate freeze |
| `.gitattributes` | 强制 shell/无扩展名入口以 LF 检出 | 无运行负载 | static verified |
| `scripts/run-e2e-test.sh` | 31 项隔离全链路验收；Windows 路径通过 argv、MCP wrapper 由 Node 启动 | 临时 bare repos/SQLite | Linux 31/31；native Windows 28/28（3 个 POSIX-only 显式 skip）verified |
| `scripts/run-realdata-test.sh` | 真实镜像验收 | 可能增量写索引；高负载 | 本轮未运行 |
| `scripts/sync.sh` | Gitea bare mirror 同步与索引刷新 | 网络与磁盘写入 | mock API E2E only |
| `scripts/quickstart-local.sh` | 本地仓快速镜像/建索引 | 写目标目录 | 隔离 E2E verified |
| `scripts/respawn-mcp.sh` | 重启单个 MCP 子进程 | 影响运行进程 | 本轮未运行，未动生产 |
| `scripts/dsh-session-health.mjs` | 隔离会话健康检查/迁移；SQLite fallback 失败时在 JSON 中公开非秘密错误而非静默吞掉 | `--fix` 可写会话/SQLite | Linux mock HOME E2E verified；Windows sqlite3-cli hosted gate 待新候选重验 |
| `scripts/session-export-incremental.py` | 增量导出会话 Markdown | 写导出目录 | mock HOME dry-run E2E verified |

## 4. 自动化测试与 CI

| 路径 | 覆盖目标 | rc3 运行证据 |
|---|---|---|
| `test/config-contract.test.mjs` | Node/Python 配置优先级与 placeholder 拒绝 | Linux + native Windows verified |
| `test/profile-patch.test.mjs` | profile 幂等、备份、check mode | Linux + native Windows verified |
| `test/launch-spec.test.mjs` | Node-aware launcher | Linux verified |
| `test/macos-setup.test.mjs` | macOS installer profile selection and Bash-3-safe discovery | Linux stub smoke verified; native macOS pending |
| `test/release-scope.test.mjs` | 版本、scope、tool naming、optional skill 契约 | Linux verified |
| `test/posix-path.test.mjs` | POSIX 路径与 CR 语义 | Linux verified |
| `test/windows-adapt.test.mjs` | Windows 静态适配、BOM、installer 契约 | Linux static verified |
| `test/windows-lock.test.mjs` | Windows `msvcrt` 锁；非 Windows 跳过 | native Windows verified |
| `test/windows-quote.test.mjs` | cmd quoting/injection；部分非 Windows 跳过 | native Windows verified |
| `.github/workflows/ci.yml` | Ubuntu/macOS unit+E2E；独立 wheel/sdist gate | Ubuntu local equivalent verified; macOS remote pending |
| `.github/workflows/windows-ci.yml` | PE、PS5.1/PS7、Windows runtime/CRLF/Git Bash/profile gates；显式安装 sqlite CLI 并向 Git Bash 传入已安装 gts | 首轮 hosted：4 个 runtime/CRLF jobs success，WhatIf 双壳 success；W5/W6-runtime/W10 暴露三个 gate 缺陷，修复后待新候选重验 |

## 5. 发布元数据与可选资产

| 路径 | 用途 | 说明 |
|---|---|---|
| `pyproject.toml` | Python metadata、3 个入口与数据文件清单 | rc3 必须包含 `lib/config.py` |
| `package.json` / `package-lock.json` | Node metadata、依赖锁与 test scripts | lockfile 随 rc3 入仓 |
| `PROVENANCE.json` | 候选树 SHA-256 清单 | 文件哈希已冻结验证；`source_commit` 在候选提交存在后补为 exact SHA |
| `extras/dsh-skill/gitea-search/SKILL.md` | 可选 Agent 检索指导 | 不由 installer 自动安装 |
| `WIKI.md` | 本仓耐久踩坑与判别方法 | 只保留有证据且有适用范围的事实 |
