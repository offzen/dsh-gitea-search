# dsh-gitea-search

[![License](https://img.shields.io/badge/License-Apache--2.0-C21325)](LICENSE)
[![CI](https://github.com/offzen/dsh-gitea-search/actions/workflows/ci.yml/badge.svg)](https://github.com/offzen/dsh-gitea-search/actions/workflows/ci.yml)
[![Version](https://img.shields.io/badge/Version-v1.2.0-blue)](RELEASE-NOTES.md)
[![npm](https://img.shields.io/npm/v/dsh-gitea-search?color=red)](https://www.npmjs.com/package/dsh-gitea-search)
[![Archive](https://img.shields.io/badge/Archive-v0.1.1%20(Pure%20Node)-orange)](https://github.com/offzen/dsh-gitea-search/tree/legacy-0.1.x)
[![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20macOS-brightgreen)](scripts/setup-macos.sh)
[![Node](https://img.shields.io/badge/Node-%3E%3D20.11-success)](package.json)

DeepSeek Harness (DSH) 高性能跨仓 Git 代码检索与文档/历史会话全文检索 MCP 插件。

> SPDX-License-Identifier: Apache-2.0

---

## 架构演进与历史版本 (v0.1.x Archive)

本项目经历了从早期纯 Node.js 插件到高性能 Python+Node 混合索引引擎的重大演进，两代成果均完整公开：

- **v1.x (当前分支 / main)**：全新一代高性能架构。采用 Python + SQLite FTS5 + Jieba 实现跨仓全量倒排索引与毫秒级中文分词搜索，Node.js 负责轻量级 MCP stdio 传输并具备跨平台兼容与安全护栏。
- **v0.1.x (历史归档 / legacy-0.1.x)**：第一代纯 Node.js 实现（`mcp__gitea_search__*` 命名空间），对应 npm 官方包 [`dsh-gitea-search@0.1.1`](https://www.npmjs.com/package/dsh-gitea-search/v/0.1.1)。完整源码与 7 个历史提交已无损保存在 [`legacy-0.1.x`](https://github.com/offzen/dsh-gitea-search/tree/legacy-0.1.x) 分支及 [v0.1.1 Release](https://github.com/offzen/dsh-gitea-search/releases/tag/v0.1.1) 中，供参考学习及轻量级无 Python 环境复用。

---

## 核心特性

- **毫秒级文档与历史会话全文检索**：基于 SQLite FTS5 引擎与 Jieba 中文分词，支持 BM25 智能相关度加权与多类型软分区（`doc`、`session`、`config`、`script`），精准索引用户提问与助手回复正文，彻底解决大模型内置检索在多词与中文语义下的匹配难题。
- **全分支代码检索与同行相交智能回退**：基于裸镜像（Bare Mirrors）执行高效 `git grep`，覆盖全部仓库与所有本地分支；当多词短语字面量连写 0 命中时，自动触发「同行多词 AND」（`--and`）回退匹配。
- **Zero-Gitea 本地单机快速启动**（v1.2.0 新增）：内置 `quickstart-local.sh`，零服务端依赖，支持一键将本地任意普通 Git 仓库目录扫描并转化为 MCP 镜像，实现本地开箱即用。
- **原生 macOS 与 BSD 跨平台兼容 (PR #2)**（v1.2.0 新增）：配套 `setup-macos.sh`，原生兼容 macOS 默认 Bash 3.2、Homebrew 环境、BSD `stat`/`awk` 以及非 GNU `timeout` 管道防阻塞机制。
- **Node.js 跨版本优雅降级**（v1.2.0 新增）：优先适配 Node.js 22.15+ LTS 原生 `node:sqlite` 与 `zlib.zstdCompressSync`；在 Node.js `20.11 ~ 22.14` 环境下自动降级调用系统 `sqlite3` 与 `zstd` CLI，杜绝运行期模块崩溃。
- **多途径配置自动化 (`config.json`)**（v1.2.0 新增）：支持无需注入复杂环境变量，直接在工作目录读取 `config.json` 自动解析，同时完全向前兼容 `GITEA_*` 与 `GITQ_*` 环境变量规范。
- **Agent 上下文安全四层护栏**：严格防范单行长文本溢出（2000B）、总体搜索缓冲区溢出（128KB）、默认返回 Top 5 结果（硬上限 200 条）、`read_gitea_file` 默认 400 行窗口及 UTF-8 跨数据包拼接防乱码机制。
- **宿主会话存储代数预检与无损维护**：配套 `dsh-session-health.mjs`，主动识别 DSH `< v4` 旧版会话、`session-query.sqlite` 长 revision 与 WAL 膨胀（>32MB 在线截断），保障长效运行稳定。

---

## 目录结构

```text
dsh-gitea-search/
├── bin/
│   ├── gts                   # CLI 命令行检索引擎（兼具代码与 FTS5 全文搜索）
│   └── mcp-server            # 标准 MCP Server 启动入口
├── lib/
│   ├── indexer.py            # FTS5 + Jieba 索引、搜索核心与 I/O 遥测
│   └── server.mjs            # MCP Server 实现（含四层安全护栏与 config.json 自动解析）
├── scripts/
│   ├── quickstart-local.sh             # [新] Zero-Gitea 本地免服务快速启动体验脚本
│   ├── setup-macos.sh                  # [新] macOS / BSD 平台一键环境安装与兼容脚本 (PR #2)
│   ├── setup.sh                        # Linux 环境一键依赖预检与安装脚本
│   ├── sync.sh                         # Gitea 镜像并发同步脚本（支持 API/SSH 双通道与增量索引）
│   ├── dsh-session-health.mjs          # DSH 会话存储代数诊断与 WAL 在线优化工具
│   ├── respawn-mcp.sh                  # MCP 进程平滑重载运维脚本
│   ├── probe-stdio.mjs                 # MCP stdio 协议自动化测试探针
│   ├── session-export-incremental.py   # DSH 历史会话增量导出器（过滤思维链，防软链环）
│   ├── run-e2e-test.sh                 # 自建合成测试集端到端测试（32 项完整断言）
│   └── run-realdata-test.sh            # 真实大规模镜像与生产会话全链路验收脚本
├── config.example.json                 # 配置文件模板
├── CONTRIBUTING.md                     # 开源社区贡献规范与双向同步准则
├── CHANGELOG.md                        # 版本演进详细变更记录
├── RELEASE-NOTES.md                    # 发版说明与更新摘要
├── MANUAL.md                           # 生产部署、配置与运维排障完整手册
└── WIKI.md                             # 架构设计取舍、排坑实录与反直觉事实归档
```

---

## 快速上手

### 模式 A：Zero-Gitea 本地单机快速体验（零外部服务）

无需搭建 Gitea 服务或配置 SSH 密钥，只要本地有任意一个或多个 Git 仓库，即可一键体验：

```bash
# 自动扫描指定目录下的 Git 仓库并构建 MCP 镜像环境
bash scripts/quickstart-local.sh /path/to/your/git-projects
```

启动后会生成本地镜像目录、构建 FTS5 倒排索引，并打印可直接接入 DSH 的配置片段。

---

### 模式 B：标准部署（连接 Gitea / Gitorca 远端镜像）

#### 1. 环境准备与依赖安装

- **运行要求**：
  - **Node.js `>= 20.11.0`**（推荐 Node.js `22.15+` LTS；低版本自动降级 CLI）。
  - **Python 3 (`>= 3.10`)** + `jieba` + 支持 FTS5 的 `sqlite3` + `git`。

```bash
# Linux (Debian / Ubuntu / RHEL)
bash scripts/setup.sh

# macOS (自动通过 Homebrew 补齐依赖并处理 Bash 3.2 兼容)
bash scripts/setup-macos.sh
```

#### 2. 构建与增量刷新索引

```bash
# 增量构建 FTS5 索引（未变更仓库毫秒级跳过）
python3 lib/indexer.py --build

# 查看索引库统计数据
python3 lib/indexer.py --stats
```

#### 3. 命令行体验 (CLI)

```bash
# FTS5 全文搜索文档、设计稿与历史会话
bin/gts -D "沙盒机制"

# 按分类过滤配置与运维脚本
bin/gts -D --kind config,script "caddy"

# 跨所有镜像仓库毫秒级搜索源码
bin/gts "function handleGetData"
```

---

## 接入 DeepSeek Harness (DSH)

在 DSH 的配置文件 `cordis.patch.yml` 中添加如下插件配置（顶层必须是 YAML 数组，且 `transport: stdio` 必填）：

### 方式 1：通过环境变量配置（标准推荐）

```yaml
# ~/.dsh/profiles/<profile>/cordis.patch.yml
- insert:
    - id: mcp-gitea-search
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: gitea_search          # 工具名形如 mcp__gitea_search__search_gitea_docs
        transport: stdio                  # 必填项，省略会导致连接失败
        command: node
        args: ["/opt/dsh-gitea-search/lib/server.mjs"]
        cwd: /opt/dsh-gitea-search
        env:
          GITEA_MIRROR_ROOT: "/srv/gitea-mirror"
          GITEA_OWNERS: "my-org,my-team"
```

> **兼容说明**：系统完全兼容 `GITQ_MIRROR_ROOT` / `GITQ_OWNER` 等历史变量及 `serverName: gitorca_search`。

### 方式 2：通过 `config.json` 配置文件

在插件根目录下创建 `config.json`（参考 `config.example.json`）：

```json
{
  "mirrorRoot": "/srv/gitea-mirror",
  "owners": ["my-org", "my-team"],
  "enumSource": "auto",
  "apiBase": "https://git.example.com"
}
```

此时 `cordis.patch.yml` 中的 `env` 项可完全省略，`server.mjs` 会自动向上查找并加载配置。

---

## Agent 工具调用实测场景

配置接入后，Agent 会按需自动发起 MCP 工具调用：

### 场景 1：排查历史故障与架构决策 (`search_gitea_docs`)
- **用户 Prompt**：“查一下之前关于 Caddy 证书续期失败与沙盒隔离机制的讨论和排障记录”
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
- **返回结果**（FTS5 BM25 毫秒级命中并返回带高亮的上下文）：
  ```text
  [dsh-session-archive@main] sessions/session-20261022.md (DSH 会话归档)
    ...**user**: 请排查 Caddy 证书自动续期在沙盒模式下的只读报错。
    ...**assistant**: 经定位，沙盒只读挂载了 /etc/caddy，已在 docker-compose 中挂载独立证书卷修复。
  —— 命中 1 篇文档（FTS5 相关度排序）
  ```

### 场景 2：跨全量仓库定位配置与微服务 (`search_gitea`)
- **用户 Prompt**：“查找所有包含 cpm-panel 镜像的 docker-compose 编排”
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
- **返回结果**（多词连写 0 命中时自动回退同行多词 AND）：
  ```text
  cpm-deploy-guide:deploy/docker-compose.yml:4:    image: registry.local/cpm-panel:v2.4.0
  —— 命中 1 行 / 1 仓
  ```

### 场景 3：受控安全读取目标文件 (`read_gitea_file`)
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
- **返回结果**（受 400 行与 60KB 护栏保护，避免单次返回巨量数据破坏 Agent 上下文）。

---

## 质量保障与自动化测试

本项目内置完善的自动化回归测试体系，并在 GitHub Actions CI 中对 Node 20 / 22 及 Python 3.11-3.13 运行多矩阵验证：

```bash
# 运行完整的自建合成测试集（共 32 项自动化断言）
bash scripts/run-e2e-test.sh
```

- **Phase 1**：FTS5 首次建库与四表一致性校验
- **Phase 2**：增量索引与 `head_commit` 毫秒级跳过
- **Phase 3**：CLI `gts` 检索模式与多词 AND 回退验证
- **Phase 4**：MCP stdio 四工具协议调用与输出护栏拦截
- **Phase 5**：`config.json` 自动寻径与 Zero-Gitea 本地快速启动测试

---

## 开源治理与贡献

本项目采用 Apache-2.0 许可证，欢迎社区开发者提交 Issue、反馈与 Pull Request！
详细的本地开发测试指引与双向同步准则请查阅 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

- 完整部署与运维手册：[`MANUAL.md`](MANUAL.md)
- 版本历史与变更日志：[`CHANGELOG.md`](CHANGELOG.md)
- 发版说明：[`RELEASE-NOTES.md`](RELEASE-NOTES.md)
