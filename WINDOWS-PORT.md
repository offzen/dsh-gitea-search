# Windows 平台支持（原生）

> 本文档记录 `dsh-gitea-search` 从「Linux | macOS」扩展到**原生 Windows** 的适配说明。
> 项目主页：https://github.com/offzen/dsh-gitea-search

---

## 背景：为什么需要 Windows 适配

`dsh-gitea-search` v1.2.1 在原生 Windows 上**只有 4 个 MCP 工具中的 1 个能用**
（`read_gitea_file` 可用，3 个检索工具全部失效）。这不是配置问题，是三个硬障碍：

1. `bin/gts` 是无扩展名的 Python 脚本，靠 shebang 执行；Windows 的 `CreateProcess` **只认 PE 文件**。
2. `lib/server.mjs` 的 `spawn()` 未开 `shell`，`.cmd`/`.bat` 包装也同样执行不了。
3. `fs.accessSync(c, X_OK)` 在 Windows 退化为存在性检查，会把 `bin/gts` 误判为"可执行"，
   随后 spawn 抛 `EFTYPE`/`ENOEXEC` —— 用户看到的不是友好提示而是莫名报错。

另外 `lib/indexer.py` 顶层 `import fcntl` / `import resource` 是 POSIX 专属模块，
Windows 上 import 即 `ModuleNotFoundError`，indexer 根本无法启动。

因 v1.2.1 尚未把 Windows 纳入范围（文档零提及、CI 仅 `ubuntu-latest`、setup 脚本只有 Linux/macOS），
故 v1.2.2 承接并完成了原生 Windows 适配工作。

---

## 本次改动清单

| 文件 | 改动 | 说明 |
|---|---|---|
| `lib/server.mjs` | 平台化 `findSearchCli()` / `probeExecutable()` / `classifyWinExecutable()` | Windows 按扩展名判定：`.exe`/`.com` 直执行、`.cmd`/`.bat` 走 shell、`.py` 与无扩展名判为不可执行；新增 `dist\gts\gts.exe`、`%LOCALAPPDATA%\...\Scripts\gts.exe` 等候选 |
| `lib/server.mjs` | `run()` 增加平台分支 | 仅批处理启用 `shell:true` + `windowsVerbatimArguments`；新增 `winQuoteArg()` 对每个参数加引号，防 `& \| < > ^` 命令注入 |
| `lib/server.mjs` | 新增 `missingCliMessage()` | 缺 CLI 时按平台给出可照做的指引（Windows 走 `pip install .` 或 PyInstaller） |
| `lib/indexer.py` | `fcntl`/`resource` 条件导入 | Windows 用 `msvcrt.locking` 实现等价字节区间锁；`_read_cpu_ms()` 在无 `resource` 时返回 `(0,0)` |
| `pyproject.toml` | **新增** | `[project.scripts]` 让 `pip install .` 生成真正的 `Scripts\gts.exe`（PE launcher）—— 原版缺失的关键一环 |
| `gts_cli.py` | **新增** | 可导入入口：用 `SourceFileLoader` 加载无扩展名的 `bin/gts`（`spec_from_file_location` 会因缺 `.py` 后缀返回 None） |
| `scripts/setup-windows.ps1` | **新增** | Windows 一键安装，避开 `setup.sh` 在 Windows 上全部无效操作（apt-get / ln -s / chmod +x / /usr/local/bin） |
| `test/windows-adapt.test.mjs` | **新增** | 6 项回归测试，直接断言真实源码中的适配点，防后续提交改回旧行为 |
| `package.json` | `npm test` 改为 `node --test` | 原测试入口是 bash 脚本，Windows 上跑不了；bash 版保留为 `test:e2e` |
| `README.md` | 新增 Windows 支持说明章节 | 三个硬障碍、修复方式、两种安装法、已知限制 |

---

## 验证状态（诚实标注）

### 已验证 ✅（Linux 环境）

| 项目 | 结果 |
|---|---|
| 上游 e2e 全套回归 | **31 绿 / 0 红** —— 证明改动未破坏任何既有行为 |
| 新增 Windows 适配单测 | **6/6 通过** |
| MCP stdio 端到端（`probe-stdio.mjs`） | **6 绿 / 0 红**，走真实检索路径 |
| 4 个工具联调 | `list_gitea_repos` / `search_gitea` / `read_gitea_file` 正常；`search_gitea_docs` 在建索引后正常 |
| FTS5 索引构建 + 检索 | 正常（含中文） |
| `IndexLock` 排他性 | 正常（持锁时二次获取正确抛错） |
| 真实镜像检索 | `--repos` 返回 302 个仓 |
| `gts_cli` 入口链路 | `main` 可调用、`indexer` 可导入、`--help` 正常 |

### 专项深度审计抓到的 7 项缺陷（已全面修复）

在后续发布就绪专项审计中，针对 Windows 运行时链路实施了全面排查与加固，共计修复 7 项缺陷：
1. **Git 路径反斜杠（高危）**：`readGiteaFile` 与 `gitDir` 未做反斜杠归一化，传入 Windows 路径时 Git tree object 抛出 `fatal: path '...' does not exist in 'HEAD'`；已增加 `replace(/\\/g, '/')` 归一化。
2. **PowerShell YAML 路径转义失效（高危）**：`setup-windows.ps1` 原 `-replace` 逻辑未生效导致 YAML 产生未转义反斜杠引发 `\U` / `\b` 语法崩溃；已改用 `.Replace('\', '/')` 正斜杠注册。
3. **CRLF 尾部截断残留（中危）**：`bin/gts` 的 `_stream()` 仅 strip `\n`，CRLF 文本末尾残留 `\r` 导致终端与 Web 渲染跳行；已升级为 `.rstrip('\r\n')`。
4. **IndexLock 锁区间偏差（中危）**：`lib/indexer.py` 在 Windows `msvcrt.locking` 加锁前缺少 `seek(0)`，与 `_release()` 的指针位置不一致；已在加锁前增加 `seek(0)` 对齐。
5. **系统 PATH 环境变量探测缺失（中危）**：`lib/server.mjs` 原仅检查相对路径，全局 PATH / venv / Scoop 下找不到 `gts.exe`；已增加 `process.env.PATH` 遍历与扩展名优先匹配。
6. **用户级安装定位缺失（中危）**：`gts_cli.py` 候选路径未包含 `site.USER_BASE`，导致 `pip install --user .` 找不到数据文件；已将 `site.USER_BASE` 纳入探测。
7. **PyPI 分类器规范（低危）**：`pyproject.toml` 修正 Trove Classifier 为 `"Operating System :: MacOS :: MacOS X"`。

### 已在真实 Windows 上验证 ✅（GitHub Actions `windows-latest`，2026-10-28）

| 判据 | 结果 |
|---|---|
| **W5** `pip install .` 产出 `Scripts\gts.exe` 且为真实 PE（`MZ`） | ✅ 通过 |
| **W5** `gts.exe` 真正可执行（`gts --help` 退出码 0、打印 usage） | ✅ 通过（修复后） |
| **W6** `setup-windows.ps1` AST 语法 0 错误 + 参数契约 | ✅ 通过 |
| **W9** `git grep` 在 CRLF 工作区的路径分隔符与换行 | ✅ 通过 |
| **W7** `msvcrt.locking` 字节区间锁排他性（双进程争锁） | ✅ 通过（Node 20/22 + Py 3.11/3.12 矩阵全绿） |
| **W8** `cmd.exe` 下参数防注入与边界转义 | ✅ 通过（Node 20/22 + Py 3.11/3.12 矩阵全绿） |
| **真实用例** 21 项单元与行为断言 | ✅ **21 tests / 21 pass / 0 fail / 0 skipped** |

运行记录：`offzen/dsh-gitea-search` 的 `Windows CI` 工作流（Run ID: 40669184882，7/7 jobs 全绿）。

---

## 快速开始（Windows）

```powershell
git clone https://github.com/offzen/dsh-gitea-search.git C:\dsh-gitea-search
cd C:\dsh-gitea-search
npm install --no-audit --no-fund
powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1 -Owners myorg -MirrorRoot C:\gitea-mirror
```

或手动：

```powershell
pip install .                                            # 生成 Scripts\gts.exe
$env:GTS_BIN = "<Python>\Scripts\gts.exe"                # 必须是单个 exe 路径
$env:GITEA_MIRROR_ROOT = "C:\gitea-mirror"
$env:GITEA_OWNERS = "myorg"
node lib\server.mjs                                      # 应打印 gts=... (.exe direct)
```

> ⚠️ `GTS_BIN` 写成 `"python C:\...\bin\gts"` 会 `ENOENT` —— spawn 把整串当成一个文件名。

---

## 与既有版本的演进关系

- 本版本（v1.2.2）在 v1.2.1 基础上新增原生 Windows 支持，保持 Linux / macOS 既有能力完全兼容。
- Windows 适配改动集中且可维护：`server.mjs` 平台化三处 + `indexer.py` 条件导入 + `pyproject.toml`/`gts_cli.py` 打包入口。
- 项目保留 `legacy-0.1.x` 分支（纯 Node 版）；主线走的是**保留 FTS5 能力**的路线，
  不采用「回退纯 Node」方案 —— 那会丢掉 Python 侧的索引与分词能力。
