# dsh-gitea-search 手册

进阶使用与维护说明。快速上手看根目录的 `README.md`。

## 1. 它怎么工作

```
Gitea / Forgejo HTTP API  ──sync──▶  本地 bare 镜像（<mirror>/<owner>/<repo>.git）
                                        │
search / read 只读本地镜像 ◀──git grep / git show──┘
        │
MCP stdio（三个工具）◀──npx 启动──▶ DSH 会话
```

- **同步面**：只有 `sync` 联网。它枚举 token 能读到的仓，逐个 `git clone --mirror`（之后
  `git remote update --prune`），并把最新同步时间写进 `<mirror>/last_sync`、仓名缓存写进
  `<mirror>/state/repos.json`。
- **搜索面**：`search_gitea` / CLI `search` 只对镜像目录做操作：列出所有 bare 仓 → 每仓枚举
  本地分支（`-A` 时连 tag）→ 对每个 ref 跑 `git grep` / `git ls-tree` / `git log`。
- **读面**：`read_gitea_file` / CLI `read` 用 `git show <ref>:<path>` 取单文件。

搜索与读文件全程无网络，这也是"服务端没开代码索引"的替代方案。

## 2. 安装细节

### 2.1 bundle 机制

`package.json` 声明 `dsh.bundle.patch: "./cordis.patch.yml"`；`cordis.patch.yml` 插入一行
`@deepseek-ai/dsh-mcp-client`。`dsh plugin --profile <p> add dsh-gitea-search` 会：
装依赖（pnpm 在 profile 目录里跑）→ 把 `dsh-gitea-search` 追加到 profile 的
`dsh.profile.bundles` → 该 patch 成为一层配置。

- `--profile` 必填；`dsh plugins list`（复数）不存在。
- 层序：bundle patch → 你自己 profile 的 `cordis.patch.yml` → 全局 `$DSH_HOME` 的 patch →
  命令行 `--patch` 覆盖。**后面的层按 id 赢，且是整份替换 `config`，不做深合并**。
- profile 关闭 HMR 时，`add` 之后要重启 `dsh` 才生效。

### 2.2 从 git 源码安装（离线场景）

`github:` 安装拉的是源码且不会自动构建，还要求用户先允许该包的 `prepare` 脚本（`allowBuilds`）。
如果不用 npm 分发，把 bundle 行自己写成：

```yaml
- id: gitea-search
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: gitea_search
    transport: stdio
    command: node
    args: ['/绝对/路径/dsh-gitea-search/bin/mcp-server.mjs']
    env:
      GITEA_MIRROR_DIR: !!js process.env.GITEA_MIRROR_DIR ?? ''
      GITEA_OWNER: !!js process.env.GITEA_OWNER ?? ''
    failOnStartupError: true
```

Windows 下 `npx` 可能要写成 `npx.cmd`。

### 2.3 为什么环境变量要 `!!js` 转一圈

`dsh-mcp-client` 为子进程提供的是**擦洗过的环境**——shell 里 export 的变量到不了服务端。
所以 patch 里用 Loader 的 `!!js` 表达式把 `dsh` 进程自己的环境变量显式转交。对 `sync` 命令
（CLI，不在 DSH 里跑）不需要这一层：它直接读你 shell 的环境。

## 3. 配置参考

| 变量 | 默认 | 说明 |
|---|---|---|
| `GITEA_MIRROR_DIR` | `~/.local/share/gitea-search/mirror` | 镜像根目录 |
| `GITEA_BASE_URL` | 无 | `sync` 必需，例如 `https://gitea.example.com` |
| `GITEA_TOKEN` | 无 | 只读 token；建议 Gitea scope `read:repository,read:user` |
| `GITEA_OWNER` | 全部可读 | 逗号分隔的 owner（用户或组织），只镜像这些 |
| `GITEA_MAX_RESULTS` | `200` | 单次 search 返回条数硬夹（≤1000） |
| `GITEA_MAX_SEARCH_BYTES` | `131072` | 单次 search 输出字节总预算（硬承诺） |
| `GITEA_MAX_LINE_BYTES` | `2000` | 单行截断上限（标注计入配额） |
| `GITEA_MAX_READ_BYTES` | `60000` | 单文件读取上限 |
| `GITEA_JOBS` | `6` | 搜索并发仓数（≤32） |
| `GITEA_SYNC_JOBS` | `6` | 同步并发仓数（≤32） |
| `GITEA_TIMEOUT_MS` | `120000` | 单次 git 子进程超时 |

CLI 选项（`dsh-gitea-search --help`）：

```
search <pattern> [-p|--path|-m|--msg] [-E|--regex] [-s|--case-sensitive] [-w|--word]
                 [-l|--files-only] [-c|--count] [-r|--repo <pat>]... [-b|--branch <b>]
                 [-A|--all-refs] [-n|--max <n>] [-j|--jobs <n>] [--json]
repos [-r <filter>] [--json]
read <repo> <path> [--ref <ref>]
sync [--transport https|ssh] [--jobs <n>] [-q|--quiet]
mirror [--json]
mcp
```

## 4. 镜像布局与 sync 语义

### 4.1 布局

两种都被接受：

- `<mirror>/<owner>/<repo>.git` —— 多 owner（推荐）
- `<mirror>/<repo>.git` —— 扁平（手工镜像单 owner 时）

仓的地址：多 owner 布局里是 `owner/repo`，扁平布局里是 `repo`。`-r`/`repo`/`filter` 过滤
同时匹配完整 id 与裸名；含 `*?[` 时按 glob，否则按子串。

### 4.2 枚举

- 设了 `GITEA_OWNER`：对每个 owner 先试 `/api/v1/users/<o>/repos`，404 时回退
  `/api/v1/orgs/<o>/repos`。
- 没设：`/api/v1/repos/search`（token 能读到的全部仓）。
- 每页 50，翻到空页为止；枚举成功会把仓名列表写进 `<mirror>/state/repos.json`。
- 整个 `sync` 零失败时才更新 `<mirror>/last_sync`。

### 4.3 传输与凭据

- 默认 https：`GITEA_BASE_URL/<owner>/<repo>.git`，token 通过 `GIT_ASKPASS` 交给 git
  （临时 askpass 脚本，mode 0700，token 经子进程环境传递，**不进 argv、不进历史、不进日志**）；
  同时 `GIT_TERMINAL_PROMPT=0` 防止 git 停下来问密码。
- `--transport ssh`：改用仓的 `ssh_url`（需要你自己配好 ssh key），并设
  `GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=10"`。
- git 的任何报错在落日志前会先脱敏（`redact` 掉 `//user:pass@`）。

### 4.4 定时刷新

cron（按需替换路径）：

```
17 * * * * /usr/bin/flock -n /tmp/gitea-search-sync.lock -c 'export GITEA_BASE_URL=... GITEA_TOKEN=...; /路径/npx -y dsh-gitea-search sync >> /var/log/gitea-search-sync.log 2>&1'
```

systemd timer（`%h` 指用户家目录）：

```ini
# /etc/systemd/system/gitea-search-sync.service
[Unit]
Description=Mirror Gitea repositories for dsh-gitea-search
After=network-online.target

[Service]
Type=oneshot
Environment=GITEA_BASE_URL=https://gitea.example.com
EnvironmentFile=%h/.config/gitea-search/sync.env
ExecStart=%h/.local/share/gitea-search/bin/dsh-gitea-search sync
```

```ini
# /etc/systemd/system/gitea-search-sync.timer
[Unit]
Description=Daily Gitea mirror refresh

[Timer]
OnCalendar=*-*-* 04:17:00
Persistent=true

[Install]
WantedBy=timers.target
```

## 5. 三个 MCP 工具

| 工具 | 参数 | 输出 |
|---|---|---|
| `search_gitea` | `query`(必填), `mode`(content\|path\|message), `repo`, `branch`, `regex`, `case_sensitive`, `files_only`, `max_results` | 命中行 `repo@ref:path:line:text`；首行统计；结尾护栏提示 |
| `read_gitea_file` | `repo`(必填), `path`(必填), `ref` | `# repo@ref:path` + 正文；超 `GITEA_MAX_READ_BYTES` 截断并标注 |
| `list_gitea_repos` | `filter` | 计数 + 仓名单 |

要点：

- `repo` 支持逗号分隔与 `*?[` glob；`branch` 支持逗号分隔，`HEAD` 表示默认分支。
- `mode:"path"` 走 `git ls-tree`，`mode:"message"` 走 `git log --all`。
- 参数缺失、未知工具、非法 ref/path 都返回 `isError: true` 与一句人话，不会让模型看到内部
  异常栈。

## 6. 输出护栏（四层）

| 层 | 默认 | 作用 |
|---|---|---|
| `max_results` | 200（MCP 侧 40） | 限制返回条数 |
| `max_line_bytes` | 2000 | 超长行就地截断，**截断标注计入该行配额** |
| `max_search_bytes` | 131072 | 单次输出总预算，**护栏提示行也计入预算**（"最终输出 ≤ 预算"是硬承诺） |
| `max_read_bytes` | 60000 | 单文件读取上限 |

背景：压缩/打包产物的一行可达数百 KB，直接回传曾单次打出约 35 万 token。护栏的语义经过专门
加固：截断标注不计在配额之外、提示行不超出预算、极小预算退化为一条最小提示。相关行为由
`test/limits.test.mjs` 锁定。

## 7. 安全模型

- **不拼接用户输入进文件系统路径**：仓集合来自镜像目录扫描，请求不存在的仓就是"没有"。
- **ref 校验**：`assertRef` 拒绝以 `-` 开头、含 `..`、含空格/冒号/反斜杠的 ref；再用
  `git show --end-of-options` 调用——否则 `--output=/任意路径` 会被当成 git 选项，构成任意
  文件写入原语（有对照测试证明该原语真实存在且已被挡住）。
- **path 校验**：`assertRelPath` 拒绝空段、`.`、`..`、NUL 与绝对路径（前导 `/` 剥掉）。
- **仓库外读取**：git 本身拒绝 `..` 越出工作树；`test/safety.test.mjs` 锁死这两条负例。
- **凭据**：token 只在 `sync` 时使用，经 `Authorization` 头与 `GIT_ASKPASS` 传递；日志脱敏。
- **搜索不联网**：镜像只读；唯一网络面是 `sync`。

## 8. 性能调优

- 大镜像（数百仓）首次冷搜索约秒级到十秒级：调 `GITEA_JOBS`（≤32）与 `GITEA_TIMEOUT_MS`；
  先用 `repo` 收窄，或用 `files_only`/`mode:"path"` 探路再 `read_gitea_file` 取全文。
- `sync` 并发由 `GITEA_SYNC_JOBS` 控制；首次全量镜像耗磁盘与时间，之后增量 `remote update
  --prune` 很快。cron 加 `flock -n` 防重入。
- 磁盘：裸镜像 ≈ 服务端仓库 `.git` 的体积之和。

## 9. 故障排查树

1. **工具没出现**：`dsh` 日志找 `gitea-search`。
   - `npx` 取不到包 → 离线环境改用源码安装（§2.2）。
   - Windows 启动失败 → 把 `command` 写成 `npx.cmd` 或 `node <绝对路径>`。
   - 其它连接错误 → 检查 node 版本与 `git` 是否在 PATH。
2. **工具报 "no repositories under …"**：镜像目录空/路径错 → 跑 `sync`，`dsh-gitea-search mirror` 核对。
3. **`sync` 报 HTTP 4xx**：token 失效或 scope 不足（需要 `read:repository`；`read:user` 用于枚举用户仓）。
4. **`sync` 部分仓失败**：私仓 token 无权限、仓已删除等——逐个失败仓在 stderr 打印，可
   `--transport ssh` 换通道重试。
5. **搜索慢/超时**：收窄 `repo`/`branch`，或用 `mode:"path"`/`files_only` 探路（§8）。
6. **中文路径/内容乱码**：应不会发生——所有 git 调用固定 `core.quotePath=false`，且输出按
   完整行/整块解码（`test/utf8.test.mjs` 锁死该行为）。若出现 `\uFFFD`，请带上完整 `dsh`
   日志报 issue。

## 10. 开发与发布

```sh
npm install
npm test                 # node --test，全离线（fixture 本地造 bare 仓 + 本地假 Gitea）
npm pack --dry-run       # 核对 files 清单：cordis.patch.yml / locale / icon / bin / lib 必须齐全
```

- CI（`.github/workflows/ci.yml`）：node 22/24 矩阵跑测试，并实读 `npm pack` 产物列表断言
  bundle 必备文件在包内、`icon.svg` ≤256 KiB、`dsh.bundle.patch` 存在。
- 发布：`npm publish`（预构建，免用户 `allowBuilds`）；GitHub 仓打 `dsh-plugin` topic；向社区
  目录提交（`awesome-dsh-plugin` 一个 `data/plugins/<owner>__<repo>.yml` 的 PR，
  `deepseek-harness-plugin.com` 的 `/submit/`）。
- 兼容：peer 不声明（config-only bundle 无 dsh 代码依赖），兼容面靠 `engines.dsh` 声明 +
  README 实测表；DSH rc/alpha 期 manifest 形态会漂移，升级后重跑 §10 的测试与 pack 校验。
- 发版前检查：对 `README*`、`docs/`、`bin/`、`lib/` 跑一次"内网痕迹"扫描（私有实例域名、内部
  组织名、宿主机绝对路径、内网主机名、token 与凭据路径）应零命中；本仓的这把扫描是逐条模式匹配，
  不把特征串写进文档本身。