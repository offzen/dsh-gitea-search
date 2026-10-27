# dsh-gitea-search 部署与运维手册（MANUAL）

> 适用范围：`dsh-gitea-search`（本地全量 bare 裸镜像 + **git grep 代码检索 + SQLite FTS5 全文检索** + DSH MCP 检索工具）。  
> 本文是**正式部署与运维手册**：详细记录系统架构、多环境部署、高可用降级、安全护栏及故障处置方案。  
> 快速入门见 [`README.md`](README.md)；日常检索实操见 [`docs/USAGE.md`](docs/USAGE.md)；工具名命名机制见 [`docs/TOOL-NAMING.md`](docs/TOOL-NAMING.md)。

---

## 1. 架构定位与解决的核心痛点

本项目将 Gitea 远端托管的**所有仓库以 bare 裸镜像**拉取并维护在宿主机本地，提供**两层极速检索引擎**，并通过标准 Model Context Protocol (MCP) 服务暴露给 DeepSeek Harness (DSH) Agent：

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
| **MCP SDK** | MCP 协议通信 | `@modelcontextprotocol/sdk` | 仓内 `package.json` 声明，通过 `setup.sh` 自动安装。 |

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
| **macOS 上运行 `sync.sh` 卡在 timeout 管道** | 系统缺少 GNU `timeout` 且 fallback 逻辑受阻塞 | 更新代码至 `v1.2.0`（PR #2 已修复子进程管道阻塞问题）。 |
| **修改了 `server.mjs` 却未生效** | MCP 属于被宿主长期持有并在后台常驻的子进程 | 执行 `bash scripts/respawn-mcp.sh` 平滑重启 MCP 进程（宿主 DSH 会自动重连）。 |
| **`session-query.sqlite-wal` 膨胀至数百 MB** | 大会话读写或非正常退出导致 WAL 未回收 | 运行 `node scripts/dsh-session-health.mjs --fix` 执行在线 `PRAGMA wal_checkpoint(TRUNCATE)`。 |

---

## 9. 质量验收与自动化测试基线

### 9.1 自建合成回归测试集（32 项完整断言）

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
