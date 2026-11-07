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
