# Windows 平台原生支持技术规范与移植架构

> **适用版本**：`dsh-gitea-search` v1.2.2-rc / v1.2.2+  
> **项目主页**：https://github.com/offzen/dsh-gitea-search  
> **开源协议**：Apache-2.0

---

## 一、架构概述：为何需要原生 Windows 支持

`dsh-gitea-search` 为 DeepSeek Harness (DSH) 及任意遵循 Model Context Protocol (MCP) 的 Agent 提供跨代码仓、文档库与运维台账的高性能混合检索能力。

在 `v1.2.1` 及早期版本中，项目主要面向 POSIX 环境（Linux / macOS）设计与部署。在原生 Windows（非 WSL）环境下，由于底层进程模型、文件系统语义与标准库实现的巨大差异，MCP 服务的 4 个核心工具中有 3 个完全无法运行（`read_gitea_file` 因直接调用 `git` 侥幸可用，而 `search_gitea`、`search_gitea_docs`、`list_gitea_repos` 全部中断）。

自 `v1.2.2` 起，本项目正式引入了生产级**原生 Windows 跨平台架构**，在不破坏 Linux / macOS 既有兼容性与性能的前提下，彻底打破了 Windows 运行环境的底层壁垒。

---

## 二、Windows 原生环境的四大技术壁垒与方案

| # | POSIX 既有设计 | Windows 底层壁垒 | v1.2.2 原生架构解决方案 |
|---|---|---|---|
| **1** | `bin/gts` 为无扩展名 Python 脚本，依靠 `#!/usr/bin/env python3` shebang 启动 | Windows 的 `CreateProcessW` **只认 PE 二进制**（或经 shell 关联的已知扩展名），完全无内核级 shebang 机制 ⇒ 直接抛 `ENOEXEC` | 通过标准 `pyproject.toml` 与 `[project.scripts]` 配置，构建生成原生 PE 二进制启动器 `Scripts\gts.exe`（MZ 头），支持免提权部署 |
| **2** | Node.js `spawn('gts', ...)` 默认 `shell: false` 避免参数注入风险 | 若无 PE 可执行文件，直接调用 `.cmd` / `.bat` 批处理脚本必须开启 `shell: true`，但未经严格转义的 shell 模式极易引发任意命令注入 | 建立双模式执行管道：优先使用 `.exe` PE 直执行（`shell: false`）；在批处理降级模式下引入 `winQuoteArg` 严格参数定界护栏 |
| **3** | `fs.accessSync(c, X_OK)` 用于验证 CLI 是否可执行 | Windows 文件系统无 POSIX 执行权限位（Mode `+x`），`X_OK` 在 Windows 下退化为单纯的文件存在性检查，导致无扩展名脚本被误判为“可执行” | 引入平台化分类器 `classifyWinExecutable()`：根据 `.exe`/`.com`/`.cmd`/`.bat` 明确扩展名判定执行策略，直接判定 `.py` 与无后缀文件不可执行并给出精准诊断指引 |
| **4** | `lib/indexer.py` 顶层依赖 POSIX 专有模块 `fcntl`（文件锁）与 `resource`（CPU 遥测） | Windows 运行环境缺失 `fcntl` 与 `resource`，导入即抛 `ModuleNotFoundError`，导致全文索引器与后台守护进程启动崩溃 | 采用条件导入与等价跨平台抽象层：文件锁无缝桥接至 Windows `msvcrt.locking` 字节区间排他锁；资源遥测提供安全零值回退 |

---

## 三、核心稳健性设计与跨平台安全防护

为了确保在复杂的 Windows 生产环境（不同终端代码页、混合行尾、权限隔离与多元开发工具链）中长期稳定运行，`v1.2.2` 构建了八重核心防护机制：

### 1. Git 树对象路径统一归一化 (Path Normalization)
Git 底层对象模型（如 `git cat-file`、`git show` 与 `git ls-tree`）严格要求相对路径使用 POSIX 正斜杠 `/` 分隔。在 Windows 环境下，外部传入或自动补全的路径极易混入 `\`。  
`lib/server.mjs`（`normalizeGitPath` / `gitDir`）与 `lib/indexer.py`（`classify_kind` / `_normalize_repo_pattern`）实施统一拦截：
- **仅 Windows 侧**将路径中的 `\` 归一化为 `/`；
- 剥除首部多余斜杠，对齐 Git 的相对路径语义。

> ⚠️ **为什么必须按平台分流（重要）**
>
> 反斜杠在两类文件系统上语义完全相反：
>
> | 平台 | `\` 的含义 | 后果 |
> |---|---|---|
> | Windows (NTFS) | 保留分隔符，文件名中不可能出现 | 见到即转换，安全 |
> | Linux / macOS (ext4 / APFS) | **合法文件名字符** | 无脑转换会读错文件 |
>
> 在 POSIX 上 `a\b.md` 是一个真实存在的**单文件名**，Git 会如实保留它。若把它改成
> `a/b.md`，`git show HEAD:a/b.md` 会**静默返回另一个文件的内容且不报错**——
> 调用方（含 LLM）会拿着错误内容继续推理。同理，`foo\.md` 变成 `foo/.md` 后
> `os.path.splitext` 会把它当成隐藏文件，扩展名判为 `''`，`kind` 落入 `code` 而非
> `doc`，检索时漏召回。
>
> 因此归一化入口统一为 `normalizeGitPath()`（Node 侧）与 `IS_WINDOWS` 分支
> （Python 侧），POSIX 分支保持路径原样。回归用例见 `test/posix-path.test.mjs`。

**注意**：本节**不做**目录穿越（`..`）过滤。`..` 的拦截依赖 `gitDir()` 里的仓库名白名单
正则（`/^[A-Za-z0-9._-]+$/`）与 Git 自身的路径解析，路径参数侧没有、也不应声称有
`..` 过滤逻辑——早期版本曾在此处写下该声明，与实现不符，现已更正。

### 2. 命令注入主动防御协议 (Command Injection Defense & winQuoteArg)
检索模式（`pattern`）由大模型生成或用户输入，属于不可信字符串。若未经处理直接传入 `cmd.exe`，包含 `& | < > ^` 的参数会截断命令并执行恶意指令（如 `test & calc.exe`）。  
`winQuoteArg` 严格遵循 Windows 命令行解析规则（`CommandLineToArgvW` 规范）：
- 强制为每个参数首尾包裹双引号；
- 内部出现的双引号翻倍转义（`"say ""hi"""`）；
- **紧贴收尾双引号的反斜杠成倍转义**（如 `C:\dir\` 转义为 `"C:\dir\\"`），避免尾部反斜杠转义掉收尾引号引发语法失配。

### 3. CRLF / LF 混合工作区文本流清洗 (CRLF Stream Sanitation)
Git for Windows 默认启用 `core.autocrlf=true`，检出代码与文档通常携带 CRLF（`\r\n`）。若直接切分行，残留的 `\r` 会导致控制台光标跳回行首覆盖前序内容，或在 Web 界面渲染出乱码。  
- `bin/gts` 的逐行输出解析改为**只剥除行终止符本身**（先去尾部 `\n`、再去尾部 `\r`）。
  注意不能用 `rstrip('\r\n')`：那会把行内容尾部的其它 CR 一并吃掉
  （`'abc\r\r\n'` → `'abc'`），使 JSON 字段内容静默改变；
- `lib/indexer.py` 摘要生成器（`make_snippet`）使用正则 `re.sub(r'[\r\n]+', ' ', ...)` 彻底清除行内孤立 `\r` 与 `\n`。

### 4. `msvcrt.locking` 字节区间排他锁对称性设计 (Lock Symmetry)
POSIX 环境使用 `fcntl.flock` 实现进程间排他写入锁，而 Windows 基于 `msvcrt.locking` 提供字节区间加锁能力。  
为了保证加锁与解锁操作在 Windows 底层作用于完全一致的文件偏移区间：
- 在加锁（`_acquire()`）与解锁（`_release()`）前均执行显式 `self.fd.seek(0)` 原子复位；
- 确保锁范围始终严格锁定首字节（`length=1`），在多进程并发写入场景下达到 100% 互斥，进程退出时由操作系统内核安全释放。

### 5. 宿主 PATH 与虚拟环境智能穿透 (PATH Environment Discovery)
Windows 开发者的环境拓扑极其多样（全局 Python、venv、Conda、Scoop、Poetry 等）。  
`lib/server.mjs` 中的 `findSearchCli()` 实现了分级探测链：
1. 优先检查环境变量 `GTS_BIN` 或显式配置路径；
2. 自动检查项目内置路径（`bin/gts.exe`、打包目录 `dist/gts/` 下的 `gts.exe`）；
3. 动态解析 `process.env.PATH`，自动尝试 `.exe`、`.com`、`.cmd`、`.bat` 后缀，支持免配置直接发现位于系统路径中的 CLI。

### 6. 免提权用户目录安装与资源动态定位 (User-Base Fallback)
针对受限开发机或未提权的普通用户，`pip install --user .` 会将数据文件装入 `%APPDATA%\Python\PythonXX\share\...`。  
- `pyproject.toml` 将数据文件独立分配至 `share/dsh-gitea-search/` 目录，彻底杜绝数据文件覆盖 `Scripts\gts.exe` 入口引发的 `RecursionError` 无限递归；
- `gts_cli.py` 在加载时动态包含 `site.USER_BASE` 候选路径，保证普通用户权限下安装即用。

### 7. 部署脚本安全字符串注入 (Configuration Escaping Guard)
在执行 `setup-windows.ps1` 将 MCP 配置注入 DSH 配置文件（`cordis.patch.yml`）时：
- 对绝对路径统一调用 `.Replace('\', '/')` 转换为正斜杠注册；
- 避免反斜杠在 YAML 解析器中被识别为无效转义符（如 `\U`、`\b`、`\x`）导致宿主服务配置解析异常。

### 8. 控制台编码自动重平衡 (UTF-8 Console Protection)
Windows 中文控制台默认代码页为 CP936 (GBK)，英文控制台常为 CP437/CP1252。当输出包含多语言排障记录或中文检索词时，极易抛出 `UnicodeEncodeError: 'charmap' codec can't encode characters`。  
- `bin/gts` 与 `lib/indexer.py` 在入口均显式调用 `_force_utf8_stdio()`；
- 利用 Python 3.7+ 的 `TextIOWrapper.reconfigure()` 将标准输出与标准错误重设为 UTF-8 编码，并以 `backslashreplace` 作为极限安全兜底。

---

## 四、真实 Windows CI 验证矩阵

全套 Windows 适配方案在 GitHub Actions 官方托管的 **Windows Server 2022**（`windows-latest` 完整 Desktop Experience）上配置了严苛的 CI 判据矩阵：

| 判据任务 | 验证目标 | 运行环境 | 验证结果 |
|---|---|---|---|
| **W5: PE Binary** | 验证 `pip install .` 产出真实 PE 二进制，`Get-Item` 验证 `4D 5A` (MZ) 头；排除自我递归死循环 | Win Server 2022 / Py 3.12 | ✅ **SUCCESS** |
| **W6: PowerShell AST** | `setup-windows.ps1` 经 PowerShell 官方 AST Parser 语法解析，验证参数契约与 0 语法警告 | Win Server 2022 / Pwsh 7+ | ✅ **SUCCESS** |
| **W7: Concurrency Lock** | 真实双进程并发争锁测试，断言 `msvcrt.locking` 互斥生效 | Py 3.11/3.12 × Node 20/22 | ✅ **4/4 矩阵全绿** |
| **W8: Injection Defense** | 测试 `cmd.exe` 参数转义往返一致性与 `& \| < > ^` 注入拦截 | Py 3.11/3.12 × Node 20/22 | ✅ **4/4 矩阵全绿** |
| **W9: CRLF & Git Grep** | 在真实 bare 仓库与 CRLF 换行环境下验证路径形态与换行清洗 | Win Server 2022 | ✅ **SUCCESS** |
| **平台单测集** | 覆盖 Windows 可执行分类、引号转义、路径归一化等 21 项测试断言 | Win Server 2022 | ✅ **21 / 21 PASS (0 fail, 0 skip)** |

---

## 五、Windows 环境部署与快速上手

### 方案 A：PowerShell 一键自动化安装（推荐）

打开 PowerShell（推荐以管理员或普通开发人员身份）：

```powershell
# 1. 克隆代码仓库
git clone https://github.com/offzen/dsh-gitea-search.git C:\dsh-gitea-search
cd C:\dsh-gitea-search

# 2. 安装 Node.js 依赖
npm install --no-audit --no-fund

# 3. 运行自动化配置脚本
powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1 -Owners myorg -MirrorRoot C:\gitea-mirror
```

脚本会自动检测 Python / Git / Node.js 环境，调用 `pip install .` 生成 `Scripts\gts.exe`，并完成镜像目录初始化。

---

### 方案 B：手动构建与配置

若需要在自定义虚拟环境或特定目录中部署：

```powershell
# 1. 生成 PE 启动器
pip install .

# 2. 验证 gts.exe 正常可用
gts --help

# 3. 配置环境变量并启动 MCP 服务
$env:GTS_BIN = (Get-Command gts).Source
$env:GITEA_MIRROR_ROOT = "C:\gitea-mirror"
$env:GITEA_OWNERS = "myorg"

node lib\server.mjs
# 正确启动将输出: started; mirror=C:\gitea-mirror; platform=win32; gts=... (direct)
```

> ⚠️ **避坑提示**：`$env:GTS_BIN` 必须指向单个具体可执行文件路径，**切勿**填写为类似 `"python C:\...\bin\gts"` 的复合命令字符串，否则 Node.js `spawn` 会将其视作单一文件名引发 `ENOENT`。

---

## 六、已知运行时限制与规范

1. **CPU 遥测指标**：POSIX 特有的 `resource.getrusage` 在 Windows 下返回全 0（`cpu_ms=0`），不影响搜索、读取与索引构建的核心功能与准确性；
2. **cmd.exe 环境变量预展开**：在经由 `cmd.exe` 执行时，形如 `%NAME%` 的参数会被 Windows 命令解释器优先展开为环境变量值（属于 Windows 既定语义，已通过 `W8-8` 用例测试固化）。推荐优先使用默认的 `.exe` 直执行方式。
