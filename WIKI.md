# dsh-gitea-search Wiki

> 本页只保留跨版本仍有用的判别方法。一次性 run id、主机路径和开发现场记录属于私有审计 ledger，不进入发布树。每条证据以文件/测试合同为主；文件结构或合同改变时必须重审。

## 根因与判别方法

| 结论 | 证据 | 适用范围与失效条件 |
|---|---|---|
| placeholder owner（如 `myorg`）不能作为运行时默认值；缺真实 owner 应 fail closed。 | `test/config-contract.test.mjs` 覆盖 missing/placeholder 与 Python/Node parity。 | 当前 effective-config resolver；若未来引入认证 API 自动推导 owner，需重审。 |
| 显式配置或 `GTS_BIN` 不是“提示”，而是硬合同：缺失/畸形/不可执行必须报结构化错误，不能静默回落。 | `lib/config.py`、`lib/config.mjs`、`lib/server.mjs` 与 `test/config-contract.test.mjs`。 | rc7 resolver/launcher；若新增明确的 fallback 开关，需更新。 |
| profile 接入不能默认写 `web` 或同时写全部 profile；`Auto` 只接受唯一候选。 | `scripts/profile-patch.py`、`scripts/setup-*.{sh,ps1}`、`test/profile-patch.test.mjs` 与 `test/profile-yaml.test.py`。 | desktop/web/headless 风格 DSH profile；配置 schema 改变后需迁移。 |
| MCP 工具前缀不是协议固定格式。 | `docs/TOOL-NAMING.md` 区分裸名、宿主注册名与 GUI 名。 | 所有 MCP 宿主；具体调用名仍以实时工具表为准。 |
| FTS 文档搜索覆盖默认分支，不等于 bare mirror 全部 refs。 | `docs/BOUNDARIES.md`、`docs/USAGE.md` 与 `lib/server.mjs` 的 scope/fallback。 | 当前 indexer；实现可配置 ref scope 后失效。 |
| `verified` 不等于独立可信。 | `scripts/release-preflight.py` 固定 `TRUST_CEILING="same-party"`，并要求 receipts/evidence/artifacts/external anchor/exact ref。 | 当前 release contract；只有引入真正独立签名/attestation 才可抬高。 |
| MCP 把查询字符串放到 argv 尾部并不自动使它成为“数据”；`argparse` 仍会把 `--sync` / `--reindex` 等识别为控制选项。 | `lib/server.mjs` 的 `buildSearchArgs` / `buildDocsArgs` 在查询前加入 `--`，值参数使用 `--name=value`；`test/next-actions.test.mjs` 的 C1 sentinel fixture 证明旧 argv 会执行 sync，而新 argv 不产生 marker/state。 | v1.2.3 的 Node→Python `gts` 调用边界；若 CLI parser 或 transport 不再使用 argparse/argv，需重审。 |
| bare mirror fetch 成功不等于搜索面同步成功；索引失败必须拥有独立状态与错误归因。 | `scripts/sync.sh` 写入 `index_status/index_rc`，仅在 fetch 与 index 都成功时刷新 `last_ok`；`test/sync-index-failure.test.mjs` 用成功 fake git + indexer rc=42 证明失败可见、退出非零且旧 `last_ok` 内容/mtime 不变；`test/mirror-state.test.mjs` 固定 Python/Node 解释一致。 | v1.2.3 R1 的 `sync.sh → gts → MCP` 链；若同步与索引被拆成独立事务/API，需重新定义整体成功与 partial-success 契约。 |
| `git grep` 的 rc=1 才是 no-match；refs/log/ls-tree 的非零和 grep 的 rc≥2 都是“不可信结果”，不能归并成全库 0 命中。 | `bin/gts` 的 `_stream(..., ok_rcs=(0, 1))` 只用于 grep；`test/git-error-not-zero-hit.test.mjs` 对照断言 clean no-hit rc=1、invalid ERE/Git failure rc=2，并由 MCP 归因 `GIT_SEARCH_FAILED`。 | v1.2.3 C2 的 Git-backed content/path/message 搜索；若 Git CLI 退出合同或底层搜索引擎改变，需重审各 stage 的允许 rc。 |
| `git remote update --prune` 成功不表示 bare mirror 的 symbolic `HEAD` 已跟随上游默认分支；只有远端 symref、远端 OID、已抓取本地 ref/OID 四者一致后才能更新 `HEAD`。 | `scripts/sync.sh:converge_default_head` 先解析 `ls-remote --symref` 的 exact `HEAD` 记录，再用 `show-ref --verify --hash` 对齐本地 OID；`test/sync-default-head.test.mjs` 用多分支 `main→trunk`、post-fetch 不可达、既有/新建空 remote 和凭据 canary 固定 fail-closed 合同。 | v1.2.3 C4 的单远端 bare mirror；若未来允许多远端权威选择、改变 Git transport 或不再以 symbolic `HEAD` 定义默认分支，需重审。 |
| `ls-tree` 失败不能表示“仓库现在没有可索引文件”，blob 读取失败也不能静默跳过；否则旧行会被删、head gate 会前移，甚至在第 201 个文件失败时留下前 200 行的部分提交。 | `lib/indexer.py:index_repo` 用单仓 savepoint 包住批次写入、删除、stale-ref 清理和 head gate；`test/owner-qualified-repos.test.py:C3FailClosedReadTests` 的产品改动前红态证明 tree failure 清空行且 late-blob failure 留下已提交批次，修复后完整 snapshot 保持且移除注入可重试。 | v1.2.3 C3 的增量索引；显式 `--full` 仍先删除旧 DB，需未来使用临时 DB 原子替换后才能把“保留旧索引”扩展到 full rebuild。 |
| Git 路径是字节序列，不能用 `text=True` + `splitlines()`/空白 `split()` 解析 `ls-tree`；U+2028/U+0085 或前导/纯空白路径会被拆行或改名。 | C3 第二次 Claude High 审查给出具体反例；产品改为 `git ls-tree -r -z`、按 NUL/TAB 解析，真实 ` lead.md`、U+3000、U+2028、U+0085 fixture 全绿，缺 TAB 的假记录仍失败关闭。 | 当前 Git CLI `ls-tree -z` 字节合同；若 Git backend 或路径身份存储改为原始 bytes，需重审 UTF-8 replacement 的碰撞债务。 |
| “本轮没看到仓库”只有在范围与枚举都可信时才是删除证据；局部同步、cache/disk 降级、任一仓 fetch/HEAD 失败或 C3 读取失败都不能授权全局 prune。 | `lib/indexer.py` 只在 unfiltered、所有 owner 可枚举且全仓索引成功后比较 `seen_repo_ids`；`scripts/sync.sh` 对 `-r`、`DEGRADED!=0`、`fail>0` 显式传 `--no-global-prune`；`R2StaleRepositoryPruneTests` 与 `test/sync-index-failure.test.mjs` 固定 trusted/partial/degraded 对照。 | v1.2.3 R2 的磁盘镜像→SQLite 派生索引；若未来同步器能原子提供权威远端 manifest、删除本地 mirror 或引入多 owner 编排，需重新定义 trust token 与 owner scope。 |
| macOS 自带 Bash 3.2 在 `set -u` 下会把未加花括号、紧邻 UTF-8 文字的变量引用误解析为更长变量名，报出形如 `JOBS�: unbound variable`；这不是变量未赋值。 | `scripts/sync.sh` 对这类引用统一使用 `${name}`；`test/sync-stage6-contract.test.mjs` 用静态回归门拒绝 `\$name` 直接邻接非 ASCII 字节，并与真实 sync fixtures 一起通过。 | 当前由 Bash 3.2 解释的 UTF-8 shell 脚本；若最低 macOS shell 升级且实测解析合同改变，静态门可重审，但花括号写法仍兼容。 |

## 软 timeout 的 stdin 合同

后台启动命令默认的 stdin 行为不能当作透明 timeout：Bash 3.2 会丢失管道和直接重定向的 stdin，Bash 5.2 的管道行为不同但直接重定向仍会丢失。`scripts/sync.sh` 显式使用 `"$@" <&0 &` 保留调用方 fd 0；watcher 仍独立使用 `</dev/null`。`test/sync-stage6-contract.test.mjs` 从真实源码提取函数，验证 here-string、管道和子进程 rc=7，不执行同步脚本的其他副作用。范围限非交互 Bash fallback；调用方若不应消费输入，需自行隔离 stdin。该功能修复不补录旧 CI 缺失的历史遥测，也不证明旧失败的精确归因。

## 反直觉事实

1. **Node 能运行 `.mjs`，不代表 Windows `spawn(path.mjs)` 能运行。** 脚本需通过 `process.execPath` 启动；见 `scripts/launch-spec.mjs` 与 `test/launch-spec.test.mjs`。
2. **SQLite 查询本体毫秒级，不代表 MCP 端到端毫秒级。** Python/jieba 冷启动仍计入调用耗时；见 `docs/BOUNDARIES.md`。
3. **PowerShell 文件是 UTF-8，不代表 PowerShell 5.1 会按 UTF-8 解析。** Windows 入口的 BOM 合同由 `test/windows-adapt.test.mjs` 固定。
4. **wheel/sdist 构建成功或 `gts --help` 成功，不证明真实命令可用。** `scripts/test-python-package.py` 必须在源码树外继续验证 config/indexer。
5. **Windows Git Bash 能运行 Node/Python，不代表 Windows 路径能安全拼进 `python -c`。** 路径应通过 argv 传递；`scripts/run-e2e-test.sh` 采用该合同。
6. **`.cmd/.bat` 的逐参数引号不能消除 `cmd.exe` 二次解析。** rc7 在 shell 前拒绝 `% ! ^ & | < >` 与 CR/LF/NUL；原生 `.exe/.com` 才是首选入口。证据：`lib/server.mjs` 与 `test/windows-quote.test.mjs`。
7. **本地全套测试通过不等于 exact-P release ready。** receipt 还必须绑定 P/tree/ref/run，remote evidence 必须深验，GitHub artifact 必须绑定 digest；见 `docs/DEVELOPMENT-AND-RELEASE.md`。
8. **preflight shell exit 0 不等于 `verified`。** `configured-unverified` 会返回完整诊断并正常退出；调用方必须解析 JSON 的 `status` 与 `blocked_reasons`。证据：`scripts/release-preflight.py` 的返回合同及其测试。
9. **tracked tree 之外的本地 `node_modules` / `__pycache__` 不是发布冗余，但 exact-P gate 会在开始时拒绝非白名单 untracked/ignored 面。** 正式范围应以 `git ls-tree P` 判断；见 `scripts/ci/linux-gate.sh` 与 `release-preflight.py`。
10. **文档改动也会让旧 P 失效。** D/P 合同绑定整个 tracked tree；任何手册、索引或 release guide 修订都必须形成新 D 和 provenance-only P，再跑 exact-P gates。
11. **临时 mirror 不等于源码配置已隔离。** C9 修订后的 `scripts/run-e2e-test.sh` 为 quickstart 和 MCP probe 显式传入测试目录中的 `GITEA_SEARCH_CONFIG`，不再写删项目根配置；`quickstart-local.sh` 对不同内容、软链接（含悬空链接）及非普通文件拒绝覆盖，相同内容的普通文件重跑保持字节、inode、权限与 mtime 不变。证据：`test/quickstart-config-preservation.test.mjs` 的成功、失败、并发出现配置与软链接回归。完整 E2E 仍应在一次性源码副本和测试 mirror 运行，因为配置保护不等于所有测试写入都适合生产目录；不得用 C9 局部结论宣称整个脚本无副作用。
12. **能跑 Python preflight 的解释器不一定能跑 E2E。** preflight 的 `test/profile-yaml.test.py` 需要 PyYAML，而 E2E 的 `gts --reindex` 又由 `lib/indexer.py` 对缺失 jieba fail closed；因此两层应各用满足自身声明依赖的隔离环境，不能仅凭其中一层通过复用解释器。证据：`CONTRIBUTING.md` 的 `jieba PyYAML` 依赖合同、`test/dependency-missing.test.py` 与 `scripts/run-e2e-test.sh` 的真实 `--reindex` 路径。适用于当前分离的 Node/Python/E2E 测试链；若统一测试环境并机械安装全依赖后失效。
13. **捕获子进程 stderr 不等于把它接到 `PIPE` 就结束。** 若父进程只逐行读 stdout 而不并发 drain stderr，大量错误输出可能填满 pipe 并死锁；C2 用临时文件收 stderr，等 child 结束后再有界读取。证据：`bin/gts:_stream` 与 `test/git-error-not-zero-hit.test.mjs` 的 Git failure 路径。适用于当前同步 stdout 流式消费；若改成 `communicate()` 或异步双流读取，需重审。
14. **成功 fetch 不会自动修正 bare mirror 的 `HEAD`。** 上游从 `main` 改到 `trunk` 后，`remote update --prune` 可以 rc=0，但本地 `HEAD` 仍指向旧分支甚至悬空；C4 必须另做远端 symref/OID 与本地 ref/OID 的闭环验证。证据：`test/sync-default-head.test.mjs` 在产品改动前精确复现，修复后多分支 fixture 通过。适用于当前 Git bare mirror；若 Git 原生同步命令未来显式承诺更新 symbolic `HEAD`，需重新实测。
15. **本地 owner 目录为空不等于枚举失败。** 对直接索引而言，已存在且成功列举为空的 owner 是“最后一个 mirror 已移走”的必要证据；把空集合一律当 degraded 会让 canonical stale-repo 缺陷永远无法收敛。相反，缺失 owner、`listdir` 异常或 sync 的 cache/disk fallback 才必须禁用全局 prune。证据：`test_r2_trusted_empty_owner_prunes_last_vanished_repo` 与 missing-owner/listdir/sync-degraded 对照测试。适用于 v1.2.3 本地目录枚举；若 owner 是可能静默卸载的 mountpoint，空目录与未挂载仍有歧义，需未来引入显式 mount/manifest 信号。
16. **跨平台测试里的 fake CLI 也必须遵守被测平台的 launcher 合同。** 在 Windows 上生成无扩展名 `#!/bin/sh` stub 会被 `lib/server.mjs` 正确拒绝，不能据此放宽产品的 fail-closed；`test/fts-pagination-metadata.test.mjs` 改为 POSIX `gts`、Windows `gts.cmd`，两端都从独立 `payload.json` 原样输出，避免 JSON 进入 shell/cmd 解析层。适用于当前 `.exe/.com` direct、`.cmd/.bat` shell 合同；若 Windows launcher 类型改变，需同步重审测试夹具。

## 固定动作序列

1. 开发完成后先跑默认测试；把产品、测试、workflow 和文档一起提交为 D。
2. 以 D 生成 `PROVENANCE.json`，只提交该文件为 P；核对 `P^=D`、单文件 diff 与 manifest。
3. 所有 receipt/evidence/artifact/anchor 放在 checkout 外，并绑定 exact P 和 expected ref。
4. 私有预发布把同一本地 P 分别送往 canonical 与 prerelease 两个私有阶段，逐端读回 SHA；禁止把派生树回灌正本。
5. 候选失败时保留旧 ref 作为证据，创建新候选；不 force、不移动既有 tag。
6. 没有 fresh GitHub-hosted Linux/macOS/Windows exact-P evidence 时，不建议创建 stable tag，也不把历史 native/self-hosted 结果冒充当前验证。

## 有意不做

- v1.2.x 不实现 all-ref FTS：会扩大索引、重复结果、ref 清理与迁移面；留待结构性版本。
- v1.2.x 不实现常驻 jieba worker：涉及进程生命周期、并发和崩溃恢复；留待结构性版本。
- 可选 skill 不自动安装、不写 memory：避免覆盖用户内容与隐藏副作用。
