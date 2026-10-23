# dsh-gitea-search 部署与运维手册（MANUAL）

> 适用范围：`dsh-gitea-search`（本地全量 bare 镜像 + **git grep 代码检索 + SQLite FTS5 全文检索** + DSH MCP 检索工具）。
> 本文是**正式部署与运维手册**：写明依赖、环境、部署、配置、运维与故障处置。
> 快速入门见 [`README.md`](README.md)。

---

## 1. 这是什么 / 解决什么

把私有 Gitea（`git.example.com`，owner `myorg`）的**全部仓库以 bare 镜像**拉到本机，提供**两层检索**并通过 DSH 的 MCP 客户端暴露成任意会话/子 agent 可调的工具：

1. **代码检索（git grep）**：跨仓、跨全部本地分支、文件路径、提交信息。
2. **文档/会话全文检索（SQLite FTS5 + Jieba 中文分词）**：毫秒级、带 BM25 加权与类型软分区（`doc`/`session`/`config`/`script`）。

**为什么必须自建检索面**（实测结论，勿再重复踩）：

- Gitea **单仓**网页代码搜索本来就能用（内置 git grep，不需 indexer）；
  **全站**搜索 `/explore/code` 才需 `REPO_INDEXER_ENABLED`——本机**未开**；
  且**即使开了也没有代码搜索 API**（swagger 308 路径无此端点）。
- 两层网页搜索**都只搜默认分支**；本镜像方案覆盖**全部分支**，且新增 FTS5 文档级检索。
- 本机散落的 `.git` 工作副本各自分叉、有未提交改动，**不构成可枚举的检索面**。

---

## 2. 组件与依赖

### 2.1 组件清单

| 组件 | 运行态路径 | 正本位置 | 作用 |
|---|---|---|---|
| 镜像 | `/srv/gitea-mirror/myorg/*.git` | （运行时数据，非仓） | 各仓 `--mirror` 克隆，含全部分支 |
| 同步脚本 | `/srv/gitea-mirror/sync.sh`（软链） | `scripts/sync.sh` | 增量刷新 / 新仓克隆 / 枚举 / 状态文件 |
| CLI | `/usr/local/bin/gts`（软链，兼容 `dsh-search`/`gitq`） | `bin/gts` | **git grep + FTS5 检索引擎**（`--reindex`/`--fts-stats`/`-D`） |
| 索引核心 | `lib/indexer.py` | `lib/indexer.py` | FTS5 + Jieba 索引构建与检索（`BlobReader` + `doc_rowid`） |
| MCP 服务 | `~/gitea-search/server.mjs` | `lib/server.mjs` | 暴露 4 个 MCP 工具 |
| MCP 启动器 | `.../bin/mcp-server` 或 `.../bin/mcp-server-gitea-search` | `bin/mcp-server` | 给 `@deepseek-ai/dsh-mcp-client` 用的入口 |

### 2.2 依赖的软件

| 依赖 | 用途 | 实测版本/路径 |
|---|---|---|
| `git` | 镜像与 grep / `cat-file --batch` | 系统 git（`core.quotePath=false` 必须显式，见 §6.1） |
| `node` | 跑 `server.mjs`（ESM） | Node **≥ 20.11**（`import.meta.dirname`） |
| `python3` + `python3-jieba` | FTS5 索引/检索、中文分词 | Debian 包（Python 3.13 下 `_get_jieba()` 已屏蔽 SyntaxWarning） |
| `sqlite3`（含 FTS5） | 文档/会话全文检索 | 系统（FTS5 需编译启用） |
| `@modelcontextprotocol/sdk` | MCP 协议 | 仓内 `node_modules`（`setup.sh` 自动 `npm install`） |
| `flock` | cron 防重叠 | 系统 |

> **Node 版本**：`server.mjs` 用 `import.meta.dirname`，需 **Node ≥ 20.11**；`lib/server.mjs` 亦需 ESM 支持。
> `bin/gts` 是 Python 脚本（调用 `lib/indexer.py`），与 Node 版本无关。

### 2.3 依赖的服务 / 网络

| 服务 | 用途 | 不可达时的影响 |
|---|---|---|
| **Gitea HTTP API** `https://git.example.com`（+只读 token） | 标准枚举（`GITQ_ENUM=api/auto`，自动兼容 User 与 Organization）与 https 克隆/更新 | 无 token 时可改走 ssh 通道 |
| **Gitea SSH 远端** `git.example.com`（ssh 别名 `gitea`，key `~/.ssh/gitea`） | clone/fetch 镜像（ssh 通道） | 无 ssh 时可用 token 走 https 通道 |
| **Gitea 宿主机 SSH**（可选，`GITQ_SSH_HOST`） | 无 API token 时的备选目录枚举 | 未配置时默认走 Gitea HTTP API 或本地缓存 |
| **cron / systemd timer** | 定时增量刷新 + 索引 | 镜像/索引变陈旧；可手工跑 `sync.sh` 或给工具加 `sync:true` |

> **token 与 ssh 只需其一**（详见 §5.1b）：具备只读 API token 即可通过 HTTPS 完成枚举与镜像同步；也可使用 SSH 私钥配合 API 或 SSH 目录枚举。

### 2.4 依赖的凭据

| 凭据 | 存放 | 用途 | 缺失后果 |
|---|---|---|---|
| `GITEA_TOKEN` / `GITQ_API_TOKEN`（只读） | 环境变量 / `.sync-token` / Secret Manager | API 枚举仓名与 HTTPS 克隆 | 无 token 时需配置 SSH 通道 |
| ssh key `~/.ssh/gitea`（可选） | 文件 | 以 `git` 用户拉取镜像 | 无 SSH 时自动回退 HTTPS + token |
| `flock` 锁 `/var/lock/gitea-mirror.lock` | — | cron 防重叠 | 重叠会串日志/计数 |

> 🔒 **凭据安全规范**：API token 通过环境变量或受控凭据文件（权限 `600`）注入，**切勿硬编码进代码仓库**。
> 镜像目录是 `--mirror` 克隆，保持只读。

---

## 3. 典型部署形态

| 部署场景 | DSH 形态 | 推荐枚举与同步方式 | 镜像/索引路径 | 说明 |
|---|---|---|---|---|
| **标准单机 / 容器环境** | 系统级或用户级 `dsh.service` | `GITQ_ENUM=api` + `GITEA_TOKEN` | `/srv/gitea-mirror` | 最简通用配置，仅需 Gitea URL 与只读 Token |
| **内网 SSH 免密环境** | 系统级或源码运行 | `GITQ_ENUM=auto` + SSH key | `/srv/gitea-mirror` | 优先走 SSH 拉取镜像，API 或 SSH 枚举仓名 |
| **沙箱 / CI 验收环境** | 独立沙箱测试 | 本地合成或副本镜像 | `/tmp/gts-e2e-testbed` | 零外部网络依赖，使用 `scripts/run-e2e-test.sh` 验证 |

---

## 4. 部署（DSH 接线）

### 4.1 标准安装步骤（从零到跑通 MCP 检索）

```bash
# 步骤 1：克隆插件仓库并执行一键环境预检与初始化
# （自动检查 python3-jieba、Node.js >= 20.11、SQLite FTS5，执行 npm install，
#   注册 /usr/local/bin/gts 软链，并预检宿主 DSH 会话格式与 SQLite 索引健康度）
git clone https://git.example.com/myorg/dsh-gitea-search.git /opt/dsh-gitea-search
cd /opt/dsh-gitea-search
bash scripts/setup.sh

# 步骤 2：配置凭据（推荐存放在镜像根目录下 600 权限的 .sync-token，避免写进命令行历史）
sudo mkdir -p /srv/gitea-mirror
echo "<your_readonly_gitea_token>" | sudo tee /srv/gitea-mirror/.sync-token >/dev/null
sudo chmod 600 /srv/gitea-mirror/.sync-token

# 步骤 3：首次同步 Gitea 裸镜像并构建 SQLite FTS5 全文索引
GITEA_MIRROR_ROOT="/srv/gitea-mirror" \
GITEA_URL="https://git.example.com" \
GITEA_OWNERS="myorg" \
bash scripts/sync.sh

# 手工触发增量索引与健康诊断（sync.sh 结束时也会自动增量更新）
GITEA_MIRROR_ROOT="/srv/gitea-mirror" GITEA_OWNERS="myorg" python3 lib/indexer.py --build
GITEA_MIRROR_ROOT="/srv/gitea-mirror" GITEA_OWNERS="myorg" bin/gts --fts-stats
GITEA_MIRROR_ROOT="/srv/gitea-mirror" GITEA_OWNERS="myorg" bin/gts --doctor

# 步骤 4：（可选）若宿主存在旧版 (<v4) DSH 会话或 WAL 膨胀，执行在线无损迁移与对齐（零修改 DSH 官方代码）
node scripts/dsh-session-health.mjs --fix --dsh-home ~/.dsh

# 步骤 5：在 DSH profile 挂 MCP（见下 4.2），并用 stdio 探针一键验证
GITEA_MIRROR_ROOT="/srv/gitea-mirror" GITEA_OWNERS="myorg" node scripts/probe-stdio.mjs
```

### 4.2 DSH profile 接线（两种写法）

**写法 A（推荐，`cordis.patch.yml` 的 `plugins` 下挂 `servers`）**：

```yaml
plugins:
  "@deepseek-ai/dsh-mcp-client":
    servers:
      gitea_search:                        # serverName
        command: node
        args: ["/path/to/dsh-gitea-search/lib/server.mjs"]
        env:
          GITEA_MIRROR_ROOT: /srv/gitea-mirror
          GITEA_OWNERS: myorg
        enabled: true
```

**写法 B（legacy `- insert` 形式）**：

```yaml
- insert:
    - id: mcp-gitea-search
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: gitea_search
        transport: stdio
        command: /path/to/dsh-gitea-search/bin/mcp-server-gitea-search
        cwd: /path/to/dsh-gitea-search
        env:
          GITEA_MIRROR_ROOT: /srv/gitea-mirror
          GITEA_OWNERS: myorg
```

加载：`dsh-mcp-client` 启动时自动拉起子进程。修改 `cordis.patch.yml` 后需重启 DSH 服务生效；仅更新 `server.mjs` 代码时可直接运行 `bash scripts/respawn-mcp.sh` 平滑热替换 MCP 子进程。

工具名：

- `mcp__gitea_search__search_gitea_docs`（FTS5 全文检索，推荐）
- `mcp__gitea_search__search_gitea`（git grep 代码检索）
- `mcp__gitea_search__read_gitea_file`
- `mcp__gitea_search__list_gitea_repos`

---

## 5. 配置参考

### 5.1 环境变量

| 变量 | 默认 | 作用 | 生效处 |
|---|---|---|---|
| `GITEA_MIRROR_ROOT` / `GITQ_MIRROR_ROOT` | `/srv/gitea-mirror` | 镜像根目录 | `sync.sh` / `server.mjs` / `indexer.py` / `gts` |
| `GITEA_OWNERS` / `GITQ_OWNER` | `myorg` | Gitea 用户名或组织名（支持逗号分隔多 owner） | 同上 |
| `GITEA_URL` / `GITQ_API_BASE` | `https://git.example.com` | Gitea HTTP API 基址 | `sync.sh` |
| `GITEA_TOKEN` / `GITQ_API_TOKEN` | — | 只读 API token（用于 API 枚举与 HTTPS 克隆） | `sync.sh` |
| `GITEA_REMOTE` / `GITQ_REMOTE` | `gitea` | SSH 别名 | `sync.sh` |
| `GITQ_SSH_HOST` | — | 可选：SSH 目录枚举主机（未设则默认走 HTTP API） | `sync.sh` |
| `GITQ_JOBS` | `6` | 同步并发数 | `sync.sh` |
| `GITQ_ENUM` | `auto` | 仓名枚举：`auto`(api→ssh) / `api` / `ssh` | `sync.sh` |
| `GITQ_TRANSPORT` | `auto` | 克隆通道：`auto`(先 ssh，失败有 token 则 https) / `ssh` / `https` | `sync.sh` |
| `GITQ_REPOS_CACHE` | `$ROOT/state/repos.cache` | 枚举缓存路径 | `sync.sh` |
| `GITQ_CACHE_MAXAGE_H` | `24` | 缓存救急最长可用小时数 | `sync.sh` |
| `GITEA_MAX_RESULTS` / `GITQ_MAX_RESULTS` | `200`（上限 1000） | **命中条数硬夹** | `server.mjs` 护栏 |
| `GITEA_MAX_LINE_BYTES` / `GITQ_MAX_LINE_BYTES` | `2000` | **单行字节上限** | `server.mjs` 护栏 |
| `GITEA_MAX_SEARCH_BYTES` / `GITQ_MAX_SEARCH_BYTES` | `131072`(128KB) | **搜索总量预算** | `server.mjs` 护栏 |
| `GITEA_MAX_READ_BYTES` / `GITQ_MAX_READ_BYTES` | `60000` | `read_gitea_file` 单文件截断 | `server.mjs` 护栏 |
| `GTS_BIN` / `GITQ_BIN` | 自动探测 | 指定 gts 二进制路径 | `server.mjs` |
| `DSH_MCP_NODE` | 自动探测 | 指定 node 路径 | 启动器 |

### 5.1b 双通道通用性：token 与 ssh 只需其一

| 能力 | token 路（推荐通用方式） | ssh 路 |
|---|---|---|
| 枚举仓名 | `GET /api/v1/users/OWNER/repos` 或 `/api/v1/orgs/OWNER/repos`（分页） | `ssh $GITQ_SSH_HOST ls …`（需显式配置 `GITQ_SSH_HOST`） |
| 克隆/更新 | `https://git:<token>@host/OWNER/x.git` | `git clone --mirror gitea:OWNER/x.git` |

- `GITQ_ENUM=auto`：优先调用 Gitea HTTP API（自动兼容 User 与 Organization）；若配置了 `GITQ_SSH_HOST` 可回退 SSH 枚举。
- `GITQ_TRANSPORT=auto`：先尝试 SSH，若该仓失败且配置了 Token 则自动切换 HTTPS。
### 5.1c 枚举缓存与降级级联

```
api（有 token） → ssh（配置了 GITQ_SSH_HOST） → state/repos.cache → 本地镜像目录
```

`last_status` 关键字段：`enum_source`（api/ssh/cache/disk）、`cache_stale`、`transport`、
`degraded`、`last_ok_age_h`。

### 5.2 定时同步（cron 示例）

```cron
17 * * * * /usr/bin/flock -n /var/lock/gitea-mirror.lock \
    /path/to/dsh-gitea-search/scripts/sync.sh -q >> /var/log/gitea-mirror.log 2>&1   # 每小时第 17 分增量刷新
```

> `sync.sh` 会内联自动跑 `indexer.py --build` 增量索引（sync 完成后索引自动刷新）。

### 5.3 生效条件（重要）

- 改 `sync.sh` / `indexer.py` / `gts`：**立即或下轮 cron 生效**（`indexer.py`/`gts` 每次调用新起进程，立即生效）。
- 改 `server.mjs`：改的是被 import 的模块 ⇒ **需 respawn 该 MCP 进程**。
  用 `scripts/respawn-mcp.sh`（精准 `kill <PID>`，`dsh-mcp-client` 自动 respawn，**无需重启 dsh.service**）。

---

## 6. 护栏（输出有界化）

`server.mjs` 对 `search_gitea` / `search_gitea_docs` / `read_gitea_file` 的输出做**四层有界化**，防止单条巨型命中打穿上下文（例如单行超大压缩/base64 产物）：

1. **条数硬夹** `GITQ_MAX_RESULTS`（默认 200，上限 1000）。
2. **单行上限** `GITQ_MAX_LINE_BYTES`（默认 2000B），UTF-8 边界安全（退续位字节）。
3. **总量预算** `GITQ_MAX_SEARCH_BYTES`（默认 128KB）。
4. `read_gitea_file` 单文件截断 `GITQ_MAX_READ_BYTES`（默认 60000B）。

### 6.1 传输层 UTF-8 完整性

`run()` 收 `outChunks` / `errChunks`，结束时一次性 `Buffer.concat(chunks).toString('utf8')`（避免按块解码在多字节字符边界产生 U+FFFD）。

### 6.2 变更验证与平滑生效流程

**固定动作序列（修改 `server.mjs` 后建议走完）**：
1. 语法检查：`node --check lib/server.mjs`；
2. 端到端回归：`bash scripts/run-e2e-test.sh`；
3. 平滑生效：`bash scripts/respawn-mcp.sh [部署目录]`（先 `--dry-run`；**切勿使用宽泛 `pkill -f`**）；
4. 探针核验：`node scripts/probe-stdio.mjs`。

---

## 7. FTS5 索引运维

### 7.1 构建与查看

```bash
# 增量更新（未变更仓毫秒级跳过）
python3 lib/indexer.py --build        # 或 gts --reindex

# 强制全量重建
python3 lib/indexer.py --full

# 查看索引统计与健康诊断
python3 lib/indexer.py --stats        # 或 gts --fts-stats
bin/gts --doctor                      # 联合诊断 FTS5 索引、性能指标与 DSH 会话健康度

# 限定单个/通配仓
python3 lib/indexer.py --build -r 'myorg/dsh-*'
```

### 7.2 内部机制（排障必读）

- **`BlobReader`**（`lib/indexer.py`）：每仓懒启动一个常驻 `git cat-file --batch` 子进程，
  批量读 blob，避免逐文件 `git show` 子进程风暴。
- **`doc_rowid`**（`lib/indexer.py`）：普通 B-Tree 主键表 `(repo,ref,path) → rowid`，
  FTS5 删除/更新一律按 `rowid` 精确删（`DELETE WHERE rowid=?`，$O(\log N)$），
  避免 FTS5 虚表全表扫描（`DELETE WHERE col=?` 是 $O(N)$）。
- **`head_commit` 门控**：每仓只读 `HEAD` 符号引用 + 一次 `rev-parse`，无变更仓毫秒级跳过。
- **类型软分区**：`classify_kind()` 按路径/扩展名分 `doc/session/config/script/code`，检索可 `--kind` 过滤。
- **会话导出双角色覆盖**：`scripts/session-export-incremental.py` 同时导出 `user/message`（真用户提问）与 `assistant/message`（提取 `data.message.content` 中的 `text` 块，跳过 `reasoning` 思维链块），并使用 `os.scandir(follow_symlinks=False)` 杜绝软链死循环。
- **代码搜索多词同行 AND 自动回退**：`gts`（及 MCP `search_gitea`）在 `content` 字面量模式下，若含空格多词短语精确连写返回 0 命中，自动拆分为 `git grep -F -e w1 --and -e w2 ...` 同行多词与匹配，并在 JSON 输出标注 `"match_mode": "line_and_fallback"`。
- **宿主会话健康与 WAL 在线治理（零修改 DSH 官方文件）**：`scripts/dsh-session-health.mjs` 检测 `<v4` 会话、长 `revision`、未入库会话差值、`session-query.sqlite-wal` 体积（`--fix` 在 WAL > 32MB 时自动执行 `PRAGMA wal_checkpoint(TRUNCATE)`）以及 `node_modules/.pnpm` 软链环目录提示。

### 7.3 一致性不变量

健康库必须满足：`file_blobs == docs_fts == doc_rowid == (doc_rowid⋈docs_fts)`。
可通过 `bin/gts --doctor` 或 `scripts/run-realdata-test.sh` Phase 1 自动核验；不满足时执行 `python3 lib/indexer.py --full` 重建。

---

## 8. 降级与故障处置

### 8.1 枚举降级（关键）

仓名枚举 `api → ssh → cache → disk` 逐级回退（`GITQ_ENUM=auto`）：

| 级别 | `enum_source` | 后果 |
|---|---|---|
| 正常 | `api` / `ssh` | 全量，正确 |
| 缓存救急 | `cache` | 用上次成功枚举列表；可能漏新增仓；`degraded=1`（>24h 加 `cache_stale=1`） |
| 磁盘兜底 | `disk` | 只同步已有镜像；新仓不被发现；`degraded=1` |

### 8.2 状态文件（巡检用）

| 文件 | 语义 |
|---|---|
| `state/last_status` | 每轮写：`time/host/ok/fail/total/dur/degraded/enum_source/cache_stale/transport/last_ok_age_h` |
| `state/last_ok` | **仅本轮全成功**时刷新 |
| `state/repos.cache` | 最近一次成功枚举的仓名 |
| `state/indexer.lock` | 索引 `flock` 互斥锁（0 字节正常） |

### 8.3 常见故障

| 现象 | 原因 | 处置 |
|---|---|---|
| 搜不到确定写过的内容 | 未 push 到 Gitea | 确认是否已推；工作区内容用 DSH grep/glob |
| 新仓搜不到 | 枚举降级，未发现新仓 | 检查 `GITEA_URL`/`GITEA_TOKEN` 后重跑 `sync.sh` |
| FTS5 搜中文没命中 | 索引未建 / 需重建 | `gts --reindex`；`indexer.py --full` |
| FTS5 报 `no such table: doc_rowid` | 旧版索引库结构 | 运行 `python3 lib/indexer.py --full` 重建 |
| 检索慢（>5s） | 索引库碎片或大仓未门控 | 运行 `gts --doctor` 检查耗时与 I/O 统计 |
| 搜索超时/卡 | git 远端连接缓慢 | 已有 `GIT_SSH_COMMAND` ConnectTimeout=8；`sync.sh -r <仓>` 单仓重试 |
| 中文路径读不出 | `core.quotePath` 被改回默认 | gts/server.mjs 保留 `-c core.quotePath=false` |
| 改了代码没生效 | `server.mjs` 被 import 缓存 | respawn MCP 子进程（§5.3） |

---

## 9. 测试与验收

### 9.1 自建合成测试集

```bash
bash scripts/run-e2e-test.sh /tmp/gts-e2e-$$    # 写 /tmp 自动清理
```

覆盖：首次/增量索引、head_commit 跳过、删空末篇文档回归、doc_rowid 1:1 对齐、CLI 全模式、审计修复回归项、MCP 4 工具、性能监测与会话健康诊断。

### 9.2 真实镜像 + 真实会话全链路

```bash
bash scripts/run-realdata-test.sh /srv/gitea-mirror
# 如需显式指定 DSH_HOME：
DSH_HOME_DIR=~/.dsh bash scripts/run-realdata-test.sh /srv/gitea-mirror
```

覆盖：真实镜像仓库、四表 1:1、增量 <5s、CLI 真实语料检索、真实会话导出建索引、MCP 4 工具端到端、性能与健康诊断。

---

## 10. 边界（务必知情）

- **只搜已推送到 Gitea 的内容**；本机未 push 的工作副本不在检索面内。
- 镜像目录**只读**；改动会被下轮 `remote update --prune` 抹掉。
- 不搜二进制（`git grep -I`）；只搜**被 git 跟踪**的文件。
- 工具**只读**（无写操作）；若加写能力，务必参考 §11 写安全栏。

---

## 11. 若将来加写操作（写安全栏，备忘）

DSH **原生**有调用前批准机制：`tools/pre-execute` waterfall，宿主可 `allow`/`deny`/`cancel`/**`ask`**；
**无批准支持时 `ask` 自动降级为拒绝（fail-closed）**。若加写工具：优先挂 `tools/pre-execute` 监听，
插件内再加 `confirm:true` 纵深防御；难点在**幂等边界**（删除等防重放）。