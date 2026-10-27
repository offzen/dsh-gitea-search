# 使用指南（USAGE）

> 本文聚焦于**如何高效使用**：四个核心工具的功能定位、入参规范、省上下文调用法则以及真实工作流场景。  
> 生产部署与高可用运维参见 [MANUAL.md](../MANUAL.md)；MCP 工具名前缀机制解析参见 [TOOL-NAMING.md](TOOL-NAMING.md)。

---

## 1. 核心实践三法则

1. **先定位，再精读**：先使用 `search_*` 工具锁定目标所在的仓库、相对路径与大致行号，再使用 `read_gitea_file` 配合较小的 `limit` 读取目标代码片段。**严禁在未定位前直接尝试抓取全量文件**。
2. **多词与中文优先走倒排索引**：查找中文短语、故障排查记录或多词组合时，首选 `search_gitea_docs`，利用 SQLite FTS5 + jieba 分词实现毫秒级相关度召回。
3. **检索范围为已推送到镜像的内容**：索引基于本地 bare 镜像构建。**未推送到远端或未完成镜像同步的本机工作副本不在检索范围内**。若刚提交了代码，请先执行同步刷新。

> 💡 **能力公开边界说明**：  
> 本项目标准开源发布的公共契约包含 4 个核心基础工具（`search_gitea`、`search_gitea_docs`、`read_gitea_file`、`list_gitea_repos`）。若您所在的特定部署环境通过上层网关封装了混合召回（如各类 `*_hybrid` 向量融合工具），请以宿主实际注册的扩展工具清单为准。

---

## 2. 核心工具速查

以下调用名基于默认 `serverName: gitea-search`。若您的宿主配置了自定义名称，仅需替换中间的前缀标识即可。

### 2.1 `mcp__gitea-search__search_gitea` —— 跨仓代码检索

基于本地 bare 镜像执行极速 `git grep`，跨所有仓库检索代码内容、路径及提交信息，无需逐个 clone。

| 参数 | 类型 | 说明 |
|---|---|---|
| `query` | string | **必填**。检索关键词或短语；当 `regex: true` 时按扩展正则表达式（egrep）解析 |
| `mode` | string | 检索模式：`content`（默认，搜文件内容）/ `path`（搜文件名与路径）/ `message`（搜 commit 提交日志） |
| `repo` | string | 限定仓库范围，支持逗号分隔与 `*` 通配符（如 `"zaoz/dsh-*"`） |
| `branch` | string | 限定分支；省略时检索全部本地分支；`HEAD` 代表仅检索默认分支 |
| `files_only` | boolean | **省上下文利器**。为 `true` 时仅返回匹配的文件名列表，不返回匹配行文本 |
| `regex` | boolean | 是否开启正则表达式匹配（默认 `false`） |
| `case_sensitive`| boolean | 是否区分大小写（默认 `false`） |
| `max_results` | integer | 返回结果条数上限，**默认 5**（最大被系统硬夹至 200 条） |
| `sync` | boolean | 搜索前先执行增量镜像同步（视仓库规模增加 10~30 秒耗时，默认 `false`） |

> **智能降级机制**：在 `content` 模式下，当多词短语字面量匹配返回 0 命中时，引擎会自动回退为“同一行内多词 AND”模式，输入 `"caddy 证书"` 时无需手动拆分关键词。

---

### 2.2 `mcp__gitea-search__search_gitea_docs` —— 文档与历史会话全文检索

基于 SQLite FTS5 引擎与 jieba 中文分词，支持 BM25 智能相关度排序与高亮摘要提取。**所有中文查询、历史故障复盘与自然语言场景强烈推荐首选此工具**。

| 参数 | 类型 | 说明 |
|---|---|---|
| `query` | string | **必填**。自然语言关键词或短语（如 `"反向代理 502 证书配置"`） |
| `repo` | string | 限定仓库名称（支持通配符，如 `"session*"`） |
| `kind` | string | 资产类型软分区，支持逗号分隔：`doc` / `session` / `config` / `script`。**省略则全库检索** |
| `max_results` | integer | 返回匹配文档数上限，**默认 5** |

**四类内容分区（`kind`）典型用途**：
- `session`：检索导出的历次长会话记录，定位历史排障经验、技术决策背景与踩坑教训；
- `doc`：检索技术手册、设计文档、README 与 Runbook；
- `config`：检索 `*.yml`、`*.conf`、`*.env` 等配置文件；
- `script`：检索 `*.sh`、`*.service`、`*.py` 等运维脚本。

---

### 2.3 `mcp__gitea-search__read_gitea_file` —— 切片读取文件内容

精准读取指定仓库与分支下的文件正文，内置分段机制与超大截断保护。

| 参数 | 类型 | 说明 |
|---|---|---|
| `repo` | string | **必填**。目标仓库名称（如 `"dsh-toolkit"`） |
| `path` | string | **必填**。仓内相对路径（如 `"deploy/docker-compose.yml"`） |
| `ref` | string | 分支名、Tag 或 Commit SHA；省略时取该仓默认分支 |
| `offset` | integer | 起始行号（基于 0 开始，默认为 `0`） |
| `limit` | integer | 返回行数。**默认 400 行**；若需获取全文可显式传 `0`（受单次字节上限约束） |

> **内置安全护栏**：单次读取受 `GITQ_MAX_READ_BYTES`（默认 60KB）强约束，超出部分自动截断并附带状态标注。文件路径严格区分大小写，遇路径疑义可先通过 `search_gitea(mode: "path")` 探路。

---

### 2.4 `mcp__gitea-search__list_gitea_repos` —— 镜像仓库资产盘点

| 参数 | 类型 | 说明 |
|---|---|---|
| `filter` | string | 仓库名称子串或通配符（如 `"zaoz/dsh-*"`）；省略则列出镜像库中所有仓库 |

适用于资产梳理、确认新建仓库是否已被同步，或在模糊记忆下进行仓库名称定位。

---

## 3. 典型实战场景示范

### 场景 1：查找某个配置项在哪个仓库中定义（标准两步法）

> **用户提问**：“查一下哪个仓库的 docker-compose 里面使用了 cpm-panel 这个容器镜像？”

```jsonc
// 步骤 1：探路。设置 files_only: true 仅返回文件名，消耗极低 Token
{
  "name": "mcp__gitea-search__search_gitea",
  "arguments": {
    "query": "cpm-panel",
    "files_only": true
  }
}

// 返回示例：命中 repo="cpm-deploy", path="docker/compose.yml"

// 步骤 2：精读。针对性读取该文件前 50 行，避免全量输出
{
  "name": "mcp__gitea-search__read_gitea_file",
  "arguments": {
    "repo": "cpm-deploy",
    "path": "docker/compose.yml",
    "offset": 0,
    "limit": 50
  }
}
```

---

### 场景 2：跨会话复盘既往故障解决过程

> **用户提问**：“上周 Caddy 泛域名证书续期报 429 频率限制时，我们是怎么处理的？”

```jsonc
// 检索历史会话导出，快速召回当时的决策和临时绕过命令
{
  "name": "mcp__gitea-search__search_gitea_docs",
  "arguments": {
    "query": "Caddy 证书 429 续期",
    "kind": "session",
    "max_results": 3
  }
}
```

利用 FTS5 + jieba 分词直接命中相关排障会话的高亮片段，无需 Agent 盲目尝试搜索或让用户重复描述背景。

---

### 场景 3：全局重构或接口影响面摸排

> **用户提问**：“排查哪些仓库还在调用 `validateCustomForm` 这个旧方法？”

```jsonc
// 使用扩展正则并开启 files_only 进行全局影响面探测
{
  "name": "mcp__gitea-search__search_gitea",
  "arguments": {
    "query": "validateCustomForm\\(",
    "regex": true,
    "files_only": true
  }
}
```

**关键收益**：在多达数十个匹配点的情况下，`files_only: true` 仅返回仓与路径列表，有效防止几百行匹配代码瞬间灌爆上下文窗口。

---

### 场景 4：搜索结果为空时的排查阶梯

当检索没有返回预期结果时，请按如下顺序递进排查：

1. **确认代码是否已推送**：裸镜像仅抓取 Git 远端已 push 的分支，本地未提交或未推送的工作区不在索引中。
2. **切换分词索引引擎**：若使用 `search_gitea`（纯文本匹配）搜中文短语无结果，立即换用 `search_gitea_docs`。
3. **缩小关键词长度**：长句可能因标点或连字差异导致无法匹配，建议精简至 2~3 个核心技术名词。
4. **验证仓库范围**：调用 `list_gitea_repos` 确认目标仓库是否已被纳入同步清单。
5. **触发镜像同步**：若目标是刚刚推送的提交，可添加 `sync: true` 触发增量拉取。

---

## 4. 命令行 CLI 门面（`bin/gts`）

所有底层能力均提供原生 CLI 支持，开发者在脱机或无 Agent 环境下同样可享受极致的跨仓检索体验：

```bash
# 代码内容检索
bin/gts dockge

# 路径匹配检索
bin/gts -p "docker-compose.yml"

# 提交日志信息检索
bin/gts -m "fix(auth)" -r "backend-api"

# 仅输出命中文件列表
bin/gts -l "JWT_SECRET"

# FTS5 文档与会话全文检索
bin/gts -D "沙盒隔离机制"

# 指定内容分区过滤检索
bin/gts -D --kind doc,script "keepalived"

# 查看索引库四表统计与健康状态
bin/gts --fts-stats

# 触发增量扫描与索引构建
bin/gts --reindex
```

常用参数概览：`-s`（区分大小写）、`-w`（全词匹配）、`-b <branch>`（指定分支）、`-j <concurrency>`（并发扫描数，默认 8）、`--json`（输出结构化 JSON）。

---

## 5. Agent 省上下文实战核查清单

无论是由人类制定 Agent 提示词，还是编写自动化执行脚本，建议严格遵循以下准则：

- [ ] **严禁滥用 `max_results`**：保持默认值 `5`，先审阅 Top 5 结果；仅在明确需要遍历时才逐步放宽。
- [ ] **探路阶段必开 `files_only`**：摸底资产范围时只取路径名，不取正文。
- [ ] **切片读取按需索取**：调用 `read_gitea_file` 时显式指定 `limit: 30~50`，确认核心段落后再递增 `offset`。
- [ ] **中文检索首选 FTS5 引擎**：避免用 `search_gitea` 盲目轮询不同词形变体消耗会话轮次。
