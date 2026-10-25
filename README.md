# dsh-gitea-search

[![License](https://img.shields.io/badge/License-Apache--2.0-C21325)](LICENSE)
[![CI](https://github.com/offzen/dsh-gitea-search/actions/workflows/ci.yml/badge.svg)](https://github.com/offzen/dsh-gitea-search/actions/workflows/ci.yml)

DeepSeek Harness (DSH) 高性能 Gitea 代码与文档全文检索 MCP 插件。

> SPDX-License-Identifier: Apache-2.0

## 特性

- **毫秒级文档与历史会话检索**：基于 SQLite FTS5 引擎与 Jieba 中文分词，支持 BM25 智能加权与类型软分区（`doc`, `session`, `config`, `script`），完整索引用户提问与助手回复正文。
- **极速代码搜索与智能多词回退**：支持全分支内容、文件路径、提交信息的毫秒级 `git grep` 检索；当多词短语字面量连写 0 命中时，自动回退为「同行多词 AND」检索（`--and`）。
- **低开销增量索引**：内置 `head_commit` 变更门控，未发生提交的仓库自动跳过遍历，日常增量扫描耗时压至 1 秒级。
- **安全四层护栏与行窗口**：严格防范单行溢出（2000B）、总体缓冲区溢出（128KB）、默认返回 5 条命中、`read_gitea_file` 默认 400 行窗口与 UTF-8 字符跨块截断乱码（U+FFFD）。
- **多主体自适应**：支持按 Token 自适应探测可访问仓库，或显式指定多租户 `GITEA_OWNERS` 白名单。
- **细粒度会话归档**：配套 `session-export-incremental.py` 会话增量导出工具（支持 v1~v4，完整提取用户消息与助手文本块并跳过思维链，基于 `os.scandir(follow_symlinks=False)` 防软链环死循环），单会话独立文件，精准追踪历史故障排查记录。

- **耗时与磁盘 I/O 自动监测与阈值判定**：内置 `IoTracker` 与 `evaluate_perf_budget`（采集墙钟耗时、CPU 时间、`/proc/self/io` 的 `rchar`/`wchar`/`read_bytes`/`write_bytes`/`syscr`/`syscw`），落盘 `state/perf-metrics.jsonl` 并自动判定 `OK` / `WARN` / `CRITICAL`。
- **宿主 DSH 会话存储代数预检与无损迁移（零修改 DSH 官方代码）**：提供 `scripts/dsh-session-health.mjs`（`--check` / `--fix`）与 `gts --doctor`，在安装期与运行期主动识别旧版（`< v4`）会话文件、`session-query.sqlite` 长 `revision`、WAL 膨胀（>32MB 自动在线 `wal_checkpoint(TRUNCATE)`）及 `node_modules/.pnpm` 软链环风险，防止 `historicalCorpusRevision` 冷迁移风暴。

## 目录结构

```text
dsh-gitea-search/
├── bin/
│   ├── gts                   # CLI 命令行检索工具（兼容别名 dsh-search / gitq）
│   └── mcp-server            # MCP Server 启动入口
├── lib/
│   ├── indexer.py            # FTS5 + Jieba 索引、搜索与 I/O 监测核心
│   └── server.mjs            # MCP Server 标准实现（含慢调用监测）
├── scripts/
│   ├── setup.sh                        # 一键环境检查、依赖预检与宿主会话健康检查
│   ├── dsh-session-health.mjs          # DSH 会话存储代数 (v1~v4) 与 SQLite 索引预检/无损修复工具
│   ├── sync.sh                         # Gitea 裸镜像并发同步（支持 API/SSH 双通道与自动增量索引）
│   ├── probe-stdio.mjs                 # MCP stdio 自动化测试探针
│   ├── respawn-mcp.sh                  # MCP 进程安全平滑重启脚本
│   ├── session-export-incremental.py   # DSH 会话 per-session 增量导出器（含 <v4 会话与 I/O 监测）
│   ├── run-e2e-test.sh                 # 自建合成测试集端到端验收脚本（28 项断言）
│   └── run-realdata-test.sh            # 真实镜像与真实会话全链路验收脚本（34 项断言）
├── MANUAL.md                           # 正式部署与运维手册
└── config.example.json                 # 配置示例模板
```

## 快速上手

### 1. 环境准备
```bash
bash scripts/setup.sh
```

### 2. 构建/更新索引
```bash
# 增量更新 FTS5 索引（未变更仓毫秒级跳过）
python3 lib/indexer.py --build

# 查看索引库状态
python3 lib/indexer.py --stats
```

### 3. 命令行检索 (CLI)
```bash
# FTS5 全文检索文档与会话
bin/gts -D "沙盒机制"

# 按内容类型过滤检索配置或脚本
bin/gts -D --kind config,script "caddy"

# 跨镜像全量仓库检索源码
bin/gts "function handleGetData"
```

### 4. 接入 DSH MCP

在 DSH 的 profile patch（`cordis.patch.yml`）中插入一条 `@deepseek-ai/dsh-mcp-client` 条目。

> ⚠️ **配置格式（实测，勿凭记忆写）**
> `cordis.patch.yml` 的顶层**必须是 YAML 数组**，每项是一个 loader patch。
> `dsh-mcp-client` 的 `config` 是**单个 MCP 实例的扁平配置**，按 `transport` 字段区分
> stdio / streamable-http（`serverName` + `command` + `args` …），**没有 `servers` 这一层**。
> 写成顶层 `mcpServers:` 或 `plugins: → servers:` 映射都会在启动时被拒：
> `overlay ... must be a top-level YAML array of loader patch entries`。

```yaml
# ~/.dsh/profiles/<profile>/cordis.patch.yml
- insert:
    - id: mcp-gitea-search
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: gitea_search          # 工具名形如 mcp__gitea_search__search_gitea_docs
        transport: stdio                  # 必填；缺失则 transport 解析为 undefined、连接必崩
        command: node
        args: ["/path/to/dsh-gitea-search/lib/server.mjs"]
        cwd: /path/to/dsh-gitea-search
        env:
          GITEA_MIRROR_ROOT: /data/gitea-mirror
          GITEA_OWNERS: "my-org,my-user"
```

要点：

- `transport` **必须显式写**（`stdio` 或 `streamable-http`）。省略不会被 schema 拒绝，但会得到
  `transport === undefined`，在连接阶段失败——这是最常见的「配置看着没问题却连不上」。
- `serverName` 决定工具名前缀，需匹配 `[A-Za-z0-9_-]{1,32}` 且在同一 scope 内唯一。
- 一个 MCP Server 对应一条实例；接多个 Server 就重复插入多条（各自 `id` / `serverName` 不同）。
- 改完 `cordis.patch.yml` 后：未禁用 `hmr` 的实例 `touch` 即热生效；若已禁用 `hmr`，需重启
  `dsh.service`。仅更新 `lib/server.mjs` 代码时可用 `bash scripts/respawn-mcp.sh` 平滑热替换子进程。

验证接线是否成功（打印合成后的 entry tree，不启动服务）：

```bash
dsh --profile <profile> --dump-config | grep -A12 mcp-gitea-search
```

更多部署细节见 [`MANUAL.md §4.2`](MANUAL.md)。

## 使用示例与 Agent 交互实测

### 1. Agent 工具调用场景（MCP Tool Call）

当用户在 DSH 会话中向 Agent 询问跨仓源码或历史排障经验时，Agent 会按需自动发起 MCP 工具调用：

#### 场景 A：排查历史会话与架构决策（`search_gitea_docs`）
- **用户 Prompt**：“查一下之前我们在测试环境中排查 Caddy 证书与沙盒机制兼容性时的结论”
- **Agent Tool Call**：
  ```json
  {
    "name": "mcp__gitea_search__search_gitea_docs",
    "arguments": {
      "query": "Caddy 证书 沙盒机制",
      "kind": "session"
    }
  }
  ```
- **返回结果摘要**（内置 FTS5 BM25 智能加权，毫秒级响应）：
  ```text
  [dsh-session-archive@main] sessions/session-env-arch.md (DSH 会话: session-arch01)
    ...**user**: 请排查 Caddy 证书自动续期与沙盒机制的兼容性问题。
    ...**assistant**: 已核查 cpm-deploy-guide 中的 docker-compose.yml，证书续期工作正常。
  —— 命中 1 篇文档（FTS5 相关度排序）
  ```

#### 场景 B：跨仓定位微服务配置（`search_gitea`）
- **用户 Prompt**：“搜索所有仓库里关于 cpm-panel 服务的 docker-compose 配置”
- **Agent Tool Call**：
  ```json
  {
    "name": "mcp__gitea_search__search_gitea",
    "arguments": {
      "query": "cpm-panel",
      "mode": "content"
    }
  }
  ```
- **返回结果摘要**（多词连写 0 命中时自动回退同行多词 AND）：
  ```text
  cpm-deploy-guide:deploy/docker-compose.yml:4:    image: registry.local/cpm-panel:v2.4.0
  —— 命中 1 行 / 1 仓
  ```

#### 场景 C：按行窗口读取目标文件（`read_gitea_file`）
- **Agent Tool Call**：
  ```json
  {
    "name": "mcp__gitea_search__read_gitea_file",
    "arguments": {
      "repo": "cpm-deploy-guide",
      "path": "deploy/docker-compose.yml",
      "offset": 0,
      "limit": 10
    }
  }
  ```
- **返回结果**（受 400 行与 60KB 护栏保护，避免单次返回过大冲垮 Agent 上下文）：
  ```yaml
  version: "3.9"
  services:
    cpm-panel:
      image: registry.local/cpm-panel:v2.4.0
      restart: always
      ports:
        - "127.0.0.1:8080:8080"
  ```

### 2. 命令行 (CLI) 终端效果

在服务器终端中直接使用 `gts` 检索：
```bash
$ gts -D "沙盒机制"
[ops-infra-runbook@main] README.md (核心基础设施运维手册)
  ...1. **【沙盒】【机制】与权限隔离**：在 danger-full-access 之外的模式中，通过只读挂载保护宿主机配置...

[cpm-deploy-guide@main] README.md (CPM 部署与证书排障指南)
  ...当遇到 Caddy 证书续期失败或 503 错误时，请先检查 deploy/docker-compose.yml...
—— 命中 2 篇文档（FTS5 相关度排序）
```

完整运维手册见 [`MANUAL.md`](MANUAL.md)。
