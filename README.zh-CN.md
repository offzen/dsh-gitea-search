# dsh-gitea-search

自建 Gitea / Forgejo 的**跨仓代码搜索**，供 DeepSeek Harness 使用：先把服务端能读到的仓
bare-mirror 到本地，再对全部仓、全部分支做 `git grep`，最后通过三个 MCP 工具
（`mcp__gitea_search__*`）交给模型。搜索与读文件**只读本地镜像、不联网**。

自建 Gitea 通常**没开服务端代码索引**：网页端与 API 都搜不到文件内容。本插件补的就是这一块。

- `search_gitea` —— 文件内容 / 文件路径 / 提交信息，跨全部仓与全部分支
- `read_gitea_file` —— 直接读某个文件，不必 clone
- `list_gitea_repos` —— 镜像里有哪些仓，可按名字过滤

## 前置要求

- DeepSeek Harness 0.2.x
- Node `^22.19.0 || >=24.0.0` 与 `git`（仅有这两个运行时依赖）
- 一台自建 Gitea / Forgejo
- 只读 API token（Gitea scope：`read:repository` 与 `read:user`），镜像私有实例时需要
- 磁盘空间与你要镜像的仓历史规模同量级

## 安装

```sh
dsh plugin --profile <profile> add dsh-gitea-search
```

`--profile` 是必填的。本包声明了 `dsh.bundle.patch`，所以 `dsh plugin add` 会装依赖、把
bundle 追加进 profile、并自动插入 MCP client 那一行——**不需要你手写 YAML**。若该 profile
关闭了配置热重载，装完需重启 `dsh`；之后工具以 `mcp__gitea_search__search_gitea`、
`mcp__gitea_search__read_gitea_file`、`mcp__gitea_search__list_gitea_repos` 出现。

bundle 用 `npx -y dsh-gitea-search mcp` 启动 MCP 服务端（包在 npm 上以预构建分发，因此不需要
安装期构建授权）。若你是从 git 源码安装的，请把那一行的 `command`/`args` 改成
`node <路径>/bin/mcp-server.mjs`，见「排障」。

## 配置

MCP client 在启动服务端前会**擦洗环境变量**，所以你在 shell 里 export 的值不会自动传进去。
bundle 的 patch 显式把它们转进去（`!!js process.env.GITEA_* ?? ''`），因此取值的来源是
**dsh 进程自己的环境**——例如 systemd 的 `Environment=`，或启动 `dsh` 的那个 shell。

| 变量 | 默认值 | 含义 |
|---|---|---|
| `GITEA_MIRROR_DIR` | `~/.local/share/gitea-search/mirror` | bare 镜像存放位置 |
| `GITEA_BASE_URL` | 无 | Gitea/Forgejo 地址，**`sync` 必需** |
| `GITEA_TOKEN` | 无 | 只读 API token，`sync` 私有实例必需 |
| `GITEA_OWNER` | token 能读到的全部仓 | 要镜像的 owner（用户/组织），逗号分隔 |
| `GITEA_MAX_RESULTS` | `200` | `search_gitea` 返回条数硬上限（最大 1000） |
| `GITEA_MAX_SEARCH_BYTES` | `131072` | 单次搜索的输出总预算 |
| `GITEA_MAX_LINE_BYTES` | `2000` | 单行上限，超长就地截断并标注 |
| `GITEA_MAX_READ_BYTES` | `60000` | 单次读文件上限 |
| `GITEA_JOBS` / `GITEA_SYNC_JOBS` | `6` | 搜索 / 同步的并发仓数 |
| `GITEA_TIMEOUT_MS` | `120000` | 单次 git 调用超时 |

以后要改值：要么给 `dsh` 进程设置环境变量，要么在你自己 profile 的 `cordis.patch.yml` 里按
`id`（`gitea-search`）覆盖那一行——注意覆盖是**整份 `config` 替换**，要保留的键必须全部重写。

## 建立镜像

```sh
export GITEA_BASE_URL=https://gitea.example.com
export GITEA_TOKEN=...            # 只读 token
npx -y dsh-gitea-search sync      # 首次建立，之后定期刷新
```

`sync` 用 Gitea HTTP API 枚举 token 能读到的所有仓并逐个镜像（`git clone --mirror`，之后
`git remote update --prune`）。镜像就是一个普通的 bare 仓目录：`<mirror>/<owner>/<repo>.git`，
或手工镜像单个 owner 时的扁平 `<mirror>/<repo>.git`。定时刷新交给 cron 或 systemd timer——
示例见 [docs/MANUAL.md](docs/MANUAL.md)。

## 命令行

同一套引擎也以 CLI 形式提供：
`dsh-gitea-search search <pattern> [-p|-m|-E|-s|-w|-l|-c] [-r repo] [-b branch] [-A] [-n N] [--json]`，
另有 `repos`、`read <repo> <path> [--ref]`、`sync [--transport https|ssh]`、`mirror`、`mcp`。
完整选项见 `dsh-gitea-search --help`。

## 如实说明的边界

- 检索面就是镜像：**只有已推送到服务器的内容**可搜；本地未推送的工作副本不在内。
- 默认是字面量匹配（不分大小写），需要时才用正则——这是 grep，不是语义/向量检索。
- 镜像只在你自己调度 `sync` 时刷新，插件不会自动触发。
- 输出是刻意有界的（每条记录/每行/每次调用/每次读文件四层），防止一行压缩产物打穿上下文。

## 隐私与安全

- 只有 `sync` 联网。token 以 `Authorization` 头访问 Gitea API，并以 `GIT_ASKPASS` 交给
  `git`——不进命令行，也不进日志（git 输出在记录前会先脱敏）。
- 搜索与读文件**只作用于本地镜像**。
- 读文件会校验 ref 与路径，并用 `git show --end-of-options`，因此构造出来的 ref 无法被当成
  git 选项使用（那是一条任意文件写入的通道）。
- token 由你保管：放在你惯用的密钥位置，停用时记得吊销。

## 排障

- **工具一直不出现。** 因为 `failOnStartupError: true`，MCP 连接失败会让这一行加载失败，而
  harness 本身照常启动。去 `dsh` 日志里找 `gitea-search`。常见原因：离线机器上 `npx` 取不到包
  （改用源码安装），或 Windows 上那一行需要写成 `npx.cmd`。
- **工具报 “no repositories under …”。** 镜像没建，或 `GITEA_MIRROR_DIR` 指错——先跑一次
  `sync`，再用 `dsh-gitea-search mirror` 核对。
- **首次搜索慢。** 大镜像冷启动可能超过 MCP 默认的 60s，bundle 已把
  `toolCallTimeoutMs` 设为 120000。可用 `repo` 收窄，或先用 `mode: "path"` 与 `files_only` 探路。

## 卸载

```sh
dsh plugin --profile <profile> remove dsh-gitea-search
```

之后删除镜像目录并吊销 token。

## 兼容性

已对 DSH `0.2.0-rc.2` 实测（面向 `0.2.x` 一线设计）。bundle 里的 `engines.dsh` 只是声明；
DSH 的安装准入看的是 `@deepseek-ai/dsh-*` 的 `peerDependencies`，本包是纯配置 bundle、不声明
该 peer，因此在任意 `0.2.x` 运行时都能装。

## 许可

MIT
