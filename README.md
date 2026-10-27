# dsh-gitea-search

[![License](https://img.shields.io/badge/License-Apache--2.0-C21325)](LICENSE)
[![CI](https://github.com/offzen/dsh-gitea-search/actions/workflows/ci.yml/badge.svg)](https://github.com/offzen/dsh-gitea-search/actions/workflows/ci.yml)
[![Version](https://img.shields.io/badge/Version-v1.2.0-blue)](RELEASE-NOTES.md)
[![npm](https://img.shields.io/npm/v/dsh-gitea-search?color=red)](https://www.npmjs.com/package/dsh-gitea-search)
[![Node](https://img.shields.io/badge/Node-%3E%3D20.11-success)](package.json)

给 DeepSeek Harness (DSH) 及任意支持 Model Context Protocol (MCP) 的 Agent 装上**跨仓极速检索能力**：一条命令将 Gitea 上全部仓库镜像到本地，Agent 用一次工具调用即可毫秒级搜遍所有代码、文档、配置文件与历史排障记录——不用逐个 clone、不把大文件塞进上下文、彻底消除大模型在海量信息翻找中的高额开销。

> SPDX-License-Identifier: Apache-2.0 · 早期纯 Node.js 版本完整保留在 [`legacy-0.1.x`](https://github.com/offzen/dsh-gitea-search/tree/legacy-0.1.x) 分支。

---

## 为什么值得装：省下的是上下文和步数

Agent 查一次资料，真正的代价从来不是“查没查到”，而是**消耗了多少对话轮数、塞入了多少 Token**。没有本地索引时，每次检索都在严重透支模型宝贵的上下文窗口预算：

| 没有它（Agent 原生手段翻找） | 有它（本地两层索引直达） |
|---|---|
| 逐个 clone 仓库或抓取网页寻找配置 | 一次工具调用覆盖**全量仓库 + 全部本地分支** |
| 网页搜索引入的 HTML 与无关噪声全量灌入上下文 | 只返回匹配精准行，默认 Top 5（先定位、再精读） |
| 遇到大文件不得不整篇塞入上下文 | 默认仅读取前 400 行，支持 `offset`/`limit` 按需切片续读 |
| 撞上编译打包产物或超长文本，数十万 Token 瞬间爆窗 | 单行 ≤ 2000B、单次 ≤ 128KiB **原地安全截断** |
| 中文/多词查询历史排障，内置分词极易 0 命中 | SQLite FTS5 + jieba 分词，中文短语、多词跨行精准召回 |
| 反复摸索排查留下的巨量冗余过程记录 | 过程干净零噪声，直接收敛至目标事实 |

> 📊 **量化对比实测**：
> 让 Agent 在多仓项目中排查一个配置或历史故障，传统方式通常需 5~15 轮工具交互、消耗 **20,000 ~ 80,000 Tokens**，极易导致早期上下文被稀释甚至模型失忆；
> 使用本插件的「两步检索法（先查命中行定位 + 按需切片精读）」，整轮任务通常只需 1~2 步调用，Token 消耗压缩至 **1,500 ~ 3,000 Tokens**，节省 85% 以上预算。

一句话：**它把 Agent 的“地毯式翻找”变成了“精准点查”**，保护上下文预算，显著降低推理成本与等待延迟。

---

## 30 秒上手（Zero-Gitea 本地免服务模式）

无需搭建 Gitea 远端服务，无需 API Token 或 SSH 密钥，只要本地有 Git 仓库即可一键体验：

```bash
git clone https://github.com/offzen/dsh-gitea-search.git && cd dsh-gitea-search

# 扫描本地任意 Git 项目目录，自动创建镜像 + 构建 FTS5 索引 + 打印 DSH 配置片段
bash scripts/quickstart-local.sh /path/to/your/git-projects
```

脚本运行完毕后会直接输出开箱即用的 DSH 配置片段。

想直接在终端测试检索效果？命令行门面工具 `bin/gts` 即开即测：

```bash
bin/gts dockge                        # 跨仓代码内容检索（毫秒级）
bin/gts -D "反向代理配置"              # -D：基于 FTS5 + jieba 检索文档与历史会话
bin/gts -D --kind doc,script "caddy"  # 按分区过滤：只检索文档与脚本
```

> 完整命令参数参见 `bin/gts --help` 或运维手册 [MANUAL.md](MANUAL.md)。

---

## 接入 DeepSeek Harness (DSH)

在 DSH 配置文件（如 `cordis.patch.yml`）中添加如下片段即可挂载 MCP 服务（顶层必须是 YAML 数组）：

```yaml
- insert:
    - id: mcp-gitea-search
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: gitea-search        # 决定暴露给模型的工具名前缀（见下文说明）
        transport: stdio                # 必填项：缺失会导致连接静默失败
        command: node
        args: ["/opt/dsh-gitea-search/lib/server.mjs"]
        cwd: /opt/dsh-gitea-search
        env:
          GITEA_MIRROR_ROOT: "/srv/gitea-mirror"
          GITEA_OWNERS: "my-org,my-team"
```

> **提示**：如果不想在配置中书写 `env`，可以在插件根目录下放置 `config.json`（参考 `config.example.json`），`server.mjs` 会自动向上寻找并解析配置，两种方式完全等价。

### 💡 核心接入避坑指南

1. **必须显式声明 `transport: stdio`**：  
   若遗漏此配置项，配置文件 Schema 校验仍会通过，但客户端底层无法建立传输信道，表现为**静默不连接**——没有明确报错，但所有检索工具都不会出现在 Agent 列表中。
2. **实际调用名带有宿主服务前缀**：  
   MCP 协议中工具名在服务器内部是局部的，DSH 等客户端为了避免多服务命名冲突，会按 `mcp__<serverName>__<toolName>` 规则映射。  
   当配置 `serverName: gitea-search` 时，文档检索工具在 Agent 视角的完整调用名为 `mcp__gitea-search__search_gitea_docs`。若在指令中仅写裸名，会被宿主拒绝。原理解析与自检方法详见 **[docs/TOOL-NAMING.md](docs/TOOL-NAMING.md)**。

---

## 四个核心工具

当 `serverName` 采用推荐的 `gitea-search` 时，Agent 可直接调用的 4 个标准工具如下：

| 完整调用名 | 核心能力 | 典型应用场景 |
|---|---|---|
| `mcp__gitea-search__search_gitea` | 跨所有镜像仓检索代码内容 / 文件路径 / 提交信息 | 查找“某个函数、配置项、Dockerfile 在哪定义” |
| `mcp__gitea-search__search_gitea_docs` | SQLite FTS5 全文检索文档、历史会话、配置、脚本 | 查找“之前的故障排查记录、决策背景与架构设计” |
| `mcp__gitea-search__read_gitea_file` | 按仓库与相对路径直接读取文件（无需 clone） | 搜索定位到目标后，精确读取上下文内容 |
| `mcp__gitea-search__list_gitea_repos` | 列出已镜像的全部仓库名（支持通配符过滤） | 盘点仓库资产，或不确定具体仓名时快速探路 |

**核心实践法则：先定位，再精读。**  
通过 `search_*` 工具锁定目标文件与关键行号后，再使用 `read_gitea_file` 配合 `limit: 30~50` 进行小窗口切片精读，切忌盲目读取大文件全文。

完整实战场景示范与参数配置参见 **[docs/USAGE.md](docs/USAGE.md)**。

---

## 运行环境要求

- **Node.js ≥ 20.11**（推荐 22.15+ LTS，低版本自动降级调用系统 `sqlite3` 与 `zstd` CLI）；
- **Python 3 ≥ 3.10**（需安装 `jieba` 分词库与支持 FTS5 的系统 `sqlite3`）；
- **Git ≥ 2.25**。

系统提供一键化环境配置与预检脚本，自动适配 Linux 与 macOS（兼容 macOS 默认 Bash 3.2 及 BSD 工具链）：

```bash
bash scripts/setup.sh          # Linux (Debian / Ubuntu / RHEL)
bash scripts/setup-macos.sh    # macOS
```

---

## 文档导航

- **[docs/USAGE.md](docs/USAGE.md)** —— 四个工具参数速查、真实检索场景示范与防爆窗调用范例（**推荐首读**）
- **[docs/TOOL-NAMING.md](docs/TOOL-NAMING.md)** —— MCP 工具名前缀机制深度解析、四种典型冲突失败与命名规范
- **[MANUAL.md](MANUAL.md)** —— 生产级高可用部署、Cron 定时同步、四层安全护栏机制、参数全表与故障排查手册
- **[CHANGELOG.md](CHANGELOG.md)** / **[RELEASE-NOTES.md](RELEASE-NOTES.md)** —— 版本演进记录
- **[CONTRIBUTING.md](CONTRIBUTING.md)** —— 开发者贡献与双向同步准则

---

## 许可证

Apache-2.0 License。欢迎提交 Issue 与 Pull Request。
