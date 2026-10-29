# dsh-gitea-search 部署与运维手册（MANUAL）

> 适用范围：`dsh-gitea-search`（本地全量 bare 裸镜像 + **git grep 代码检索 + SQLite FTS5 全文检索** + DSH MCP 检索工具）。  
> 本文是**正式部署与运维手册**：详细记录系统架构、多环境部署、高可用降级、安全护栏及故障处置方案。  
> 快速入门见 [`README.md`](README.md)；日常检索实操见 [`docs/USAGE.md`](docs/USAGE.md)；工具名命名机制见 [`docs/TOOL-NAMING.md`](docs/TOOL-NAMING.md)。

---

## 1. 架构定位与解决的核心痛点

可以将远端托管的**所有仓库以 bare 裸镜像**拉取并维护在宿主机本地，提供**两层极速检索引擎**，并通过标准 Model Context Protocol (MCP) 服务暴露给 DeepSeek Harness (DSH) Agent：

1. **代码检索引擎（git grep）**：跨全量仓库、全部分支、文件路径及提交记录的毫秒级检索；支持多词短语连写 0 命中时自动回退为「同行多词 AND（`--and`）」模式。
2. **文档与会话全文检索（SQLite FTS5 + jieba 中文分词）**：毫秒级响应，支持 BM25 智能加权与内容类型分区（`doc`、`session`、`config`、`script`），解决 Agent 历史长上下文与排障经验的召回问题。
3. **零服务本地快速体验模式 (Zero-Gitea Mode)**：支持将本地任意普通 Git 仓库目录一键裸镜像化并构建倒排索引，无需远端 Gitea 服务即可作为 MCP 检索端点。

### 为什么必须自建本地检索引擎（实测经验）：
- **Gitea 网页搜索局限**：单仓内置搜索无需索引器但无法跨仓；全站搜索 `/explore/code` 需要管理员启用 `REPO_INDEXER_ENABLED`（通常未开），且 **Gitea API 无代码搜索端点**（官方 Swagger 规范中不存在该路径）。
- **分支覆盖局限**：Gitea 网页与内置索引仅覆盖默认分支；本方案裸镜像覆盖全量本地分支。
- **Agent 上下文保护**：直连原始检索若缺乏有界化约束，单条大输出（如大文本/Base64）极易打爆 LLM 上下文；自建 MCP 接入层提供四层安全护栏。

---

## 2. 组件与依赖矩阵

### 2.1 组件清单

| 组件 | 运行态路径 | 核心职责 |
|---|---|---|
| **裸镜像库** | `/srv/gitea-mirror/*/*.git` | `--mirror` 克隆的裸仓库，含全部远端分支及 commit 历史 |
| **同步器** | `scripts/sync.sh` | 增量刷新 / 新仓探测 / 双通道枚举 / 自动触发索引 |
| **CLI 引擎** | `bin/gts`（或软链 `/usr/local/bin/gts`） | 命令行统一检索入口（兼具代码与 FTS5 文档检索） |
| **索引核心** | `lib/indexer.py` | FTS5 倒排索引构建、jieba 分词、`BlobReader` 与 I/O 遥测 |
| **MCP 服务** | `lib/server.mjs` | 标准 MCP stdio 服务端，集成四层有界护栏与配置寻径 |
| **MCP 启动器** | `bin/mcp-server` | 标准入口（自动解析环境并拉起 Node MCP 服务） |
| **本地快速启动器** | `scripts/quickstart-local.sh` | Zero-Gitea 纯本地单机免服务快速体验脚本 |
| **macOS 安装器** | `scripts/setup-macos.sh` | macOS / BSD 平台一键环境配置与兼容支持 |
| **会话维护工具** | `scripts/dsh-session-health.mjs` | DSH 会话代数健康诊断与 WAL 在线截断工具 |

> **MCP 工具集的公开边界说明**：  
> 本仓公开标准契约是 `lib/server.mjs` 中定义的 **4 个基础工具**（`search_gitea` / `read_gitea_file` / `list_gitea_repos` / `search_gitea_docs`）。该服务允许被下游进一步包装扩展（例如在外部集成向量语义召回与 RRF 融合），下游的自定扩展不属于本仓的基础范围。

### 2.2 运行依赖要求

| 依赖软件 | 作用 | 最低要求 | 推荐与降级行为 |
|---|---|---|---|
| **Git** | 镜像拉取与代码 `git grep` | `>= 2.25` | 必须配置 `core.quotePath=false`（防中文文件名乱码） |
| **Node.js** | 运行 `server.mjs` 与诊断工具 | `>= 20.11.0` | **推荐 `>= 22.15 LTS`**（原生支持 `node:sqlite` 与 `zlib.zstdCompressSync`）。在 `20.11 ~ 22.14` 环境下，系统自动降级调用 `sqlite3` CLI 与 `zstd` CLI。 |
| **Python 3** | FTS5 索引核心、CLI 及分词 | `>= 3.10` | 需系统提供带 FTS5 模块的 `sqlite3`，安装 `jieba` 分词库。 |
| **macOS 适配** | 跨平台环境与 BSD 工具链 | macOS 12+ | 支持默认 Bash 3.2，自动适配 BSD `stat`/`awk`，内置非阻塞 `timeout()` 替代方案。 |
| **Windows 适配** | 原生运行环境与 MCP 检索支持 | Windows 10+ / Server 2019+ | 原生 PE 启动器 (`gts.exe`)、`msvcrt.locking` 字节区间锁、PowerShell 自动化部署与命令行注入防护。 |
| **MCP SDK** | MCP 协议通信 | `@modelcontextprotocol/sdk` | 仓内 `package.json` 声明，通过 setup 脚本自动安装。 |

---

## 3. 多场景部署流程

### 3.1 场景 A：Zero-Gitea 本地免服务快速启动（单机开发与演示）

无需搭建任何 Gitea 远端服务、无需配置 Token 或 SSH 密钥，仅需本地 Git 仓库：

```bash
# 自动扫描并建立本地检索镜像
bash scripts/quickstart-local.sh /path/to/your/projects

# 验证命令行检索效果
bin/gts -D "关键词"
```

该脚本将会在指定目录下自动完成：
1. 递归探测目录下的所有 Git 仓库；
2. 在本地缓存区创建 bare 镜像；
3. 初始化并构建 SQLite FTS5 索引库；
4. 输出可以直接复制到 DSH `cordis.patch.yml` 的配置片段。

---

### 3.2 场景 B：标准生产环境部署（连接私有 Gitea）

#### 步骤 1：一键安装系统依赖与环境预检

- **Linux (Debian / Ubuntu / RHEL)**：
  ```bash
  bash scripts/setup.sh
  ```
- **macOS**：
  ```bash
  bash scripts/setup-macos.sh
  ```

#### 步骤 2：安全凭据与权限配置（生产最小特权原则）

在生产环境中连接私有 Gitea 服务时，**强烈建议使用只读权限的 Access Token 或只读 Deploy Key**：
- 在 Gitea 用户设置 -> 应用（Applications）中生成 Token，权限仅需勾选：
  - `repo:read`（读取仓库代码）
  - `organization:read`（读取组织下仓库列表，可选）
- 严禁赋予 `repo:write`、`admin` 或任何写入权限，遵循最小特权安全隔离原则。

#### 步骤 3：全量构建 FTS5 检索库

```bash
# 构建全量倒排索引
python3 lib/indexer.py --build

# 检查四表一致性与索引统计
python3 lib/indexer.py --stats
```

#### 步骤 4：配置周期性同步（Cron）

在宿主机配置 Cron 定时增量刷新镜像并自动触发增量索引构建（推荐每小时）：

```cron
17 * * * * /usr/bin/flock -n /var/lock/gitea-mirror.lock /path/to/dsh-gitea-search/scripts/sync.sh -q >> /var/log/gitea-mirror.log 2>&1
```

> **说明**：`sync.sh` 增量同步完成后会自动调用 `python3 lib/indexer.py --build`；未变更的仓库将在毫秒级跳过，整体同步轻量高效。

---

### 3.3 场景 C：原生 Windows 环境部署

Windows 的进程创建机制（`CreateProcess`）只认 PE 二进制文件（`.exe` / `.com`），不支持 POSIX shebang 机制；因此无扩展名的 Python 脚本 `bin\gts` 无法被直接执行。v1.2.2 提供了完善的原生 Windows 架构支持，系统提供三种安装接入方式：

> 📌 **文档分工（避免两边内容漂移）**
>
> | 文档 | 职责 | 唯一真源 |
> |---|---|---|
> | **本节（MANUAL §3.3）** | **操作步骤**：照着敲就能装上 | 安装命令、脚本参数 |
> | **[WINDOWS-PORT.md §五](WINDOWS-PORT.md)** | **技术原理**：为什么这么做 | 底层设计、安全护栏、CI 矩阵 |
>
> 两处都涉及 Windows 部署，但**本节只写"怎么做"**，原理与护栏一律链接到
> `WINDOWS-PORT.md`，不在本手册重复展开。若发现两处步骤不一致，
> 以 `scripts/setup-windows.ps1` 的实际参数为准（可用 `-?` 查看）。

#### 方式 A：PowerShell 一键自动化安装（推荐）

通过内置的 `scripts\setup-windows.ps1` 脚本，自动完成 Python/Node/Git 环境检查、pip 构建 `gts.exe`、镜像目录准备、FTS5 索引初始化以及 DSH `cordis.patch.yml` 自动配置：

```powershell
# 以管理员或普通用户身份在 PowerShell 中执行（绕过当前进程执行策略）
powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1 -Owners myorg -MirrorRoot C:\gitea-mirror -RegisterDsh
```

**参数说明**：
- `-MirrorRoot <Path>`：指定裸镜像存储根目录（默认为项目内 `mirror` 目录）；
- `-Owners <List>`：指定 Gitea 组织或用户名（逗号分隔）；
- `-RegisterDsh`：自动备份并将 MCP 配置追加到 DSH 的 `cordis.patch.yml`；
- `-SkipIndex`：仅安装环境与启动器，跳过初次 FTS5 索引构建。

#### 方式 B：标准 pip 安装（适合已配置 Python 环境的 Windows 开发者）

利用 `pyproject.toml` 的 setuptools 配置，编译生成 Windows 原生启动器：

```powershell
# 在项目根目录下执行（生成 <Python>\Scripts\gts.exe）
pip install .

# 若无系统管理员权限，可安全使用用户级安装（产物位于 %APPDATA%\Python\Scripts\gts.exe）：
pip install --user .
```

安装完成后，可通过 `gts --help` 验证启动器是否正常工作。

#### 方式 C：PyInstaller 单目录打包（适合目标机不想安装完整 Python 环境）

```powershell
pip install pyinstaller
pyinstaller --onedir --name gts --hidden-import=indexer --paths=lib bin\gts
# 产物：dist\gts\gts.exe（采用 --onedir 避免单文件模式每次运行解压数十兆开销）
```

#### Windows 下的环境变量与接入配置约束

在 Windows 环境下接入 DSH 或手动调试时，请特别注意 `GTS_BIN` 变量的配置规则：

```powershell
$env:GTS_BIN = "C:\path\to\Scripts\gts.exe"   # 必须是单个 .exe 的完整路径
$env:GITEA_MIRROR_ROOT = "C:\gitea-mirror"
$env:GITEA_OWNERS = "myorg"
```

> ⚠️ **关键陷阱**：`GTS_BIN` **必须是单个可执行文件的实际路径**。严禁写成 `"python bin\gts"` 或带空格的参数组合，否则子进程创建会直接触发 `ENOENT` 致命报错。
> 另外，`lib/server.mjs` 会自动遍历系统 `PATH` 环境变量识别 `gts.exe`；若已执行 `pip install .` 且 Python Scripts 目录在系统 PATH 中，则无需显式指定 `GTS_BIN`。

---

## 4. DSH Profile 接线配置规范

在 DeepSeek Harness 的配置文件 `cordis.patch.yml` 中添加 `@deepseek-ai/dsh-mcp-client` 条目。

> ⚠️ **配置硬性规范（务必遵守）**：
> 1. `cordis.patch.yml` 顶层**必须是 YAML 数组**（`- insert:`）。
> 2. `dsh-mcp-client` 的 `config` 是单个 MCP 实例的扁平对象，**严禁写成 `servers:` 嵌套结构**。
> 3. `transport: stdio` **必须显式声明**。若省略，schema 校验虽能解析通过，但内部 `createTransport` 会返回 `undefined`，导致启动阶段静默失败。

### 标准配置模板

```yaml
# ~/.dsh/profiles/<profile>/cordis.patch.yml
- insert:
    - id: mcp-gitea-search
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: gitea-search          # 工具注册名前缀：mcp__gitea-search__*
        transport: stdio                  # 必填项：必须显式声明
        command: node
        args: ["/opt/dsh-gitea-search/lib/server.mjs"]
        cwd: /opt/dsh-gitea-search
        env:
          GITEA_MIRROR_ROOT: "/srv/gitea-mirror"
          GITEA_OWNERS: "myorg,core-team"
```

### 极简配置（利用 `config.json` 自动解析）

若在插件根目录下维护了 `config.json`，则 `cordis.patch.yml` 中无需注入环境变量：

```yaml
- insert:
    - id: mcp-gitea-search
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: gitea-search
        transport: stdio
        command: node
        args: ["/opt/dsh-gitea-search/lib/server.mjs"]
        cwd: /opt/dsh-gitea-search
```

---

## 5. 配置参数全表参考

### 5.1 配置文件 `config.json` 规范

`lib/server.mjs` 会从当前工作目录起逐级向上寻找 `config.json`，完整配置字段如下：

```json
{
  "mirrorRoot": "/srv/gitea-mirror",
  "owners": ["myorg", "core-team"],
  "enumSource": "auto",
  "apiBase": "https://git.example.com",
  "token": "your_readonly_token_here",
  "maxResults": 200,
  "maxLineBytes": 2000,
  "maxSearchBytes": 131072,
  "maxReadBytes": 60000
}
```

### 5.2 环境变量对照表

配置加载优先级为：**CLI 参数 > 操作系统环境变量 > `config.json` > 默认值**。

| 环境变量 (推荐规范) | 兼容历史变量 | 默认值 | 作用与生效范围 |
|---|---|---|---|
| `GITEA_MIRROR_ROOT` | `GITQ_MIRROR_ROOT` | `/srv/gitea-mirror` | 裸镜像与倒排索引存储根目录 |
| `GITEA_OWNERS` | `GITQ_OWNER` | `myorg` | 索引与同步的属主（逗号分隔多 owner） |
| `GITEA_API_BASE` | `GITQ_API_BASE` | `https://git.example.com` | Gitea API 地址（用于 API 枚举与 https 克隆） |
| `GITEA_TOKEN` | `GITQ_API_TOKEN` | — | 只读 API Token（用于私有仓库枚举与拉取） |
| `GITEA_ENUM` | `GITQ_ENUM` | `auto` | 仓库枚举策略：`auto` (优先 API，失败回退 SSH) / `api` / `ssh` |
| `GITEA_TRANSPORT` | `GITQ_TRANSPORT` | `auto` | 克隆通道策略：`auto` (先 SSH，失败则 HTTPS) / `ssh` / `https` |
| `GITQ_MAX_RESULTS` | — | `200` (硬上限 1000) | **条数硬夹护栏**：单次检索返回的最大匹配条数 |
| `GITQ_MAX_LINE_BYTES` | — | `2000` | **单行上限护栏**：单行最大字符数，带 UTF-8 安全截断 |
| `GITQ_MAX_SEARCH_BYTES` | — | `131072` (128KB) | **总量预算护栏**：MCP 工具返回文本的总字节数上限 |
| `GITQ_MAX_READ_BYTES` | — | `60000` (60KB) | `read_gitea_file` 单次文件读取的最大字节数限制 |

---

## 6. 输出有界化安全护栏 (Output Guards)

为彻底杜绝超大输出击穿 Agent 上下文，`lib/server.mjs` 实现了四重安全拦截：

```
LLM / Agent 发起调用
      │
      ▼
┌────────────────────────────────────────────────────────┐
│ 1. 结果数量硬夹 (默认 5 条，CLI/MCP 参数最大夹至 200 条)   │
├────────────────────────────────────────────────────────┤
│ 2. 单行截断 (GITQ_MAX_LINE_BYTES: 2000B，退续位字节安全)  │
├────────────────────────────────────────────────────────┤
│ 3. 总体预算截断 (GITQ_MAX_SEARCH_BYTES: 128KB 总体上限)    │
├────────────────────────────────────────────────────────┤
│ 4. 窗口化安全读取 (read_gitea_file 默认 400 行 / 60KB 保护) │
└────────────────────────────────────────────────────────┘
      │
      ▼
UTF-8 完整性保证：Buffer.concat(chunks).toString('utf8')
（彻底杜绝流式单块解码导致的 U+FFFD 乱码）
```

### 6.2 跨平台运行时安全护栏 (Cross-Platform Execution Guards)

在 v1.2.2 跨平台适配中，`lib/server.mjs` 与 `lib/indexer.py` 增设了底层安全护栏：

1. **`cmd.exe` 参数防注入定界**：
   - Windows 下若必须经由 `cmd.exe` 启动（如批处理脚本），系统采用严格引号定界：显式构造 `cmd /d /s /c "<已引号化命令>"` 并对每个输入参数单独加引号（`/s` 确保 cmd 先剥掉最外层引号，使逐参数引号真正起作用），彻底防御搜索词中恶意注入 `& | < > ^` 触发多指令逃逸执行。
   - 对原生 `.exe` / `.com` 二进制，一律使用 `shell: false` 直执行，完全绕过 shell 解析层。
2. **`IndexLock` 跨平台排他锁对齐**：
   - 在 POSIX 平台上采用 `fcntl.flock`；在 Windows 平台上采用 `msvcrt.locking` 字节区间锁。
   - Windows 平台在加锁前显式执行 `seek(0)`，与 `_release()` 保持严格对称的字节位置，确保多进程并发建索引与检索时的强排他性。
3. **Git 路径正斜杠自动归一化**：
   - Git tree object 在所有平台上均只接受正斜杠 `/`。MCP 服务与 CLI 在接收输入后自动执行反斜杠转义（`replace(/\\/g, '/')`），杜绝因系统路径反斜杠导致 `fatal: path '...' does not exist in 'HEAD'` 异常中断。
4. **系统 PATH 目录引号清洗**：
   - 自动遍历 Windows `PATH` 环境变量时，显式清洗带引号的目录项（去除首尾 `"`），防止畸形拼接引起子进程启动失败。

---

## 7. FTS5 检索引擎运维原理

### 7.1 索引性能关键机制

1. **`BlobReader` 常驻子进程 (`lib/indexer.py`)**：  
   对每个仓库启动单个常驻 `git cat-file --batch` 管道批量读取 blob，替代频繁启动 `git show` 子进程，将全库扫描耗时由数十分钟压减至秒级。
2. **`doc_rowid` B-Tree 映射表**：  
   维护独立的普通表 `(repo, ref, path) → rowid`。更新或删除文档时，使用精确的 `DELETE WHERE rowid=?`（$O(\log N)$），避免 FTS5 全虚表无主键扫描（$O(N)$）。
3. **`head_commit` 变更门控**：  
   每次增量扫描仅对比 `HEAD` 提交 SHA，未发生变更的仓库耗时 < 1ms 直接跳过。稳态下 200+ 仓库增量扫描仅需 1~3 秒。

### 7.2 一致性不变量判定

健康的索引数据库必须满足四表数量严格对齐：
$$\text{file\_blobs} == \text{docs\_fts} == \text{doc\_rowid} == (\text{doc\_rowid} \bowtie \text{docs\_fts})$$
可随时运行 `python3 lib/indexer.py --stats` 核查。若出现不一致，可执行 `python3 lib/indexer.py --full` 进行强制重建。

---

## 8. 常见故障排查指南

| 故障现象 | 潜在原因 | 排查与处置方法 |
|---|---|---|
| **搜索确定推送到远端的内容无命中** | 尚未执行镜像同步或索引刷新 | 手动执行 `scripts/sync.sh` 并运行 `python3 lib/indexer.py --build`。 |
| **中文关键词检索 0 命中** | FTS5 分词器未构建，或 `jieba` 未正确载入 | 运行 `bin/gts --fts-stats` 检查词元分布；重新执行 `python3 lib/indexer.py --full`。 |
| **Node.js 运行报错 `Cannot find module 'node:sqlite'`** | Node 版本在 `20.11 ~ 22.14` 且系统未装 `sqlite3` CLI | 运行 `bash scripts/setup.sh` 安装系统 `sqlite3` 命令行，系统将自动回退。 |
| **macOS 上运行 `sync.sh` 卡在 timeout 管道** | 系统缺少 GNU `timeout` 且 fallback 逻辑受阻塞 | 更新代码至 `v1.2.0`（[PR #2](https://github.com/offzen/dsh-gitea-search/pull/2) 已修复子进程管道阻塞问题）。 |
| **Windows 报错 `找不到可执行的 gts CLI`** | Windows `CreateProcess` 无法执行无扩展名 shebang 脚本 | 执行 `pip install .` 生成 `Scripts\gts.exe`；或显式指定环境变量 `$env:GTS_BIN="C:\path\to\gts.exe"`。注意严禁带 `"python ..."` 前缀。 |
| **Windows 执行 PowerShell 脚本报策略错误** | 系统默认执行策略（Restricted）阻止脚本运行 | 在命令中加入参数绕过限制：`powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1`。 |
| **Windows 读取文件报 `path '...' does not exist in 'HEAD'`** | Git tree object 仅接受正斜杠 `/` 分隔符 | 更新至 `v1.2.2`（已自动将反斜杠 `\` 归一化为 `/`）。在 CLI 中手动执行时亦可使用 `gts --read` 自动归一化。 |
| **修改了 `server.mjs` 却未生效** | MCP 属于被宿主长期持有并在后台常驻的子进程 | 执行 `bash scripts/respawn-mcp.sh` 平滑重启 MCP 进程（宿主 DSH 会自动重连）。 |
| **`session-query.sqlite-wal` 膨胀至数百 MB** | 大会话读写或非正常退出导致 WAL 未回收 | 运行 `node scripts/dsh-session-health.mjs --fix` 执行在线 `PRAGMA wal_checkpoint(TRUNCATE)`。 |

---

## 9. 质量验收与自动化测试基线

### 9.1 自建合成回归测试集（31 项完整断言）

无需任何外部依赖或现存代码库，在 `/tmp` 独立沙箱中完整验证全生命周期：

```bash
bash scripts/run-e2e-test.sh /tmp/gts-ci-testbed
```

- **Phase 1**：环境与 FTS5 建库完整性（四表一致性、Blob 抓取）；
- **Phase 2**：增量索引优化（`head_commit` 跳过判定、删除/修改感知）；
- **Phase 3**：CLI `gts` 全场景（中文分词、分类过滤、多词 AND 自动回退）；
- **Phase 4**：MCP stdio 接口协议（4 个工具注册、输出预算与单行截断护栏拦截）；
- **Phase 5**：`config.json` 自动解析机制与 Zero-Gitea 本地模式端到端连通性。

### 9.2 真实大规模数据全链路验收（34 项断言）

```bash
bash scripts/run-realdata-test.sh /srv/gitea-mirror
```

验证包含数百个真实裸镜像、万篇文档及数千个真实会话文件下的索引吞吐与检索延迟。

### 9.3 跨平台回归测试套件

基于 Node.js 原生测试运行器（`node --test`），断言平台适配、进程加锁、参数转义安全
与 POSIX 路径语义：

```bash
npm test          # 单元测试：自动发现 test/**/*.test.mjs（三平台通用）
npm run test:e2e  # 端到端：31 项合成回归断言（Linux / macOS）
npm run test:all  # 两者都跑（CI 使用的完整组合）
```

> ⚠️ **两个测试集此前是割裂的**：v1.2.2 的 `npm test` 只跑 3 个 Windows 用例文件，
> 而 e2e（31 项）单独跑；Linux CI 不跑单测、Windows CI 不跑 e2e。
> v1.2.2 的 POSIX 回归正是从这道缝里漏过去的。v1.2.3 起统一为 `npm run test:all`。

| 测试文件 | 覆盖范围 |
|---|---|
| `test/windows-adapt.test.mjs` | Windows 可执行文件扩展名分流（`.exe`/`.com` 直执行 vs `.cmd`/`.bat` 走 shell）、反斜杠路径归一化、POSIX 模块安全降级 |
| `test/windows-lock.test.mjs` | W7 锁排他性：Windows `msvcrt.locking` 下多进程争锁互斥与释放 |
| `test/windows-quote.test.mjs` | W8 命令行注入防护：双引号翻倍、尾部反斜杠转义、`& | < > ^` 元字符隔离 |
| `test/posix-path.test.mjs` | **POSIX 路径语义回归**（v1.2.3 新增）：反斜杠文件名的读取不被张冠李戴、`classify_kind` 扩展名判定、行尾 CR 不被吞掉、归一化按平台分流。Windows 上自动跳过 |

`test/posix-path.test.mjs` 的 5 项用例在 v1.2.2 上**全部失败**、在 v1.2.3 上全部通过，
可直接用于验证某个构建是否带该回归。
