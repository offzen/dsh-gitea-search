# dsh-gitea-search Wiki

## rc3：安装、配置与发布边界（2026-11-01）

### 根因与判别方法

| 结论 | 证据 | 适用范围与失效条件 |
|---|---|---|
| placeholder owner（例如 `myorg`）不能作为运行时默认值；缺少真实 owner 应 fail closed。 | `test/config-contract.test.mjs:81-90` 覆盖 missing/placeholder；Linux `npm test` 通过（2026-11-01，精确计数见当次 CI/验收记录）。 | v1.2.2-rc3 配置 resolver；若未来引入经认证 API 自动推导 owner，需重审。 |
| Windows 安装器不能猜 `gts.exe` 路径；必须向当前 Python 查询 scripts 路径并校验身份。 | `scripts/setup-windows.ps1:37-58` 查询 active/user scripts path，并对候选执行 `--help` 身份校验。 | 原生 Windows + setuptools console script；若 launcher 机制更换则失效。 |
| profile 接入不能默认写 `web` 或同时写全部 profile。 | `scripts/setup-windows.ps1:60-77` 的 `Auto` 只接受唯一候选；`test/profile-patch.test.mjs:18-43` 验证同 id 唯一且 check mode 零写入。 | desktop/web/headless 风格 DSH profile；若 DSH 配置 schema 改变需迁移 patcher。 |
| MCP 工具前缀不是协议固定格式。 | `docs/TOOL-NAMING.md:3-29` 区分裸名、宿主注册名与 GUI 名，并要求以实际工具表为准。 | 所有 MCP 宿主；具体宿主版本仍须读取实际工具表。 |
| FTS 文档搜索覆盖默认分支，不等于 bare mirror 的全部 refs。 | `lib/server.mjs:608-618,688-696` 输出 `scope: default-branches` 并给出 `search_gitea` 回退。 | rc3 当前 indexer；未来若实现可配置 ref scope 则需更新。 |

### 反直觉事实

1. **Node 能运行 `.mjs`，不代表 Windows `spawn(path.mjs)` 能运行。** E2E/probe 应通过 `process.execPath` 启动脚本；证据：`scripts/launch-spec.mjs` 与 `test/launch-spec.test.mjs`。
2. **SQLite 查询本体毫秒级，不代表 MCP 文档搜索端到端毫秒级。** 当前每次调用仍付 Python/jieba 冷启动成本；证据：`lib/server.mjs` tool description 与 `README.md` 性能边界。
3. **PowerShell 内容是 UTF-8，不代表 PowerShell 5.1 会按 UTF-8 解析。** 含中文的入口脚本仍需 BOM；证据：`test/windows-adapt.test.mjs:146-153` 断言前三字节 `EF BB BF`。
4. **wheel/sdist 构建成功、甚至 `gts --help` 成功，也不代表安装包可执行真实命令。** argparse 会在配置导入检查前处理 `--help`；`scripts/test-python-package.py` 因此从中立目录进一步验证 config 与 indexer。
5. **`new URL('..', import.meta.url).pathname` 在 POSIX 正常，不代表 Windows 正常。** 原生 Windows 会得到 `/C:/...`，再经 `path.resolve` 变成 `C:\\C:\\...`；测试定位仓根必须使用 `fileURLToPath(import.meta.url)`。rc3 真机测试已据此修正 config/profile/macOS-static 三组测试。
6. **Git Bash 里的路径变量可能仍来自 Windows Node/Python。** 把 `C:\\...` 直接插进 Python 源码会触发 `\U` unicode escape；路径必须经 `sys.argv` 传入，而不是拼进 `python -c` 源码。证据：`scripts/run-e2e-test.sh` 的 UTF-8 fixture 与 SQLite 路径均改用 argv，native Windows 已越过 Phase 2/3。
7. **Windows 可以由 Node 打开无扩展名脚本，但 ESM 动态 import 不能直接吃 `C:\\...` 字符串。** `bin/mcp-server` 先前 `await import(serverScript)` 报 `ERR_UNSUPPORTED_ESM_URL_SCHEME`；必须使用 `new URL('../lib/server.mjs', import.meta.url).href`。`scripts/launch-spec.mjs` 同时负责把无扩展名 MCP wrapper 交给 `process.execPath`。
8. **MCP server 可启动不代表 search_gitea 走到真实 CLI。** Windows 测试若不显式给隔离 venv 的 `GTS_BIN=<venv>\\Scripts\\gts.exe`，probe 会正确返回“找不到可执行的 gts CLI”，协议绿而搜索判据红；native harness 因而在 Git Bash E2E 期间临时注入该路径，结束后恢复环境。
9. **CLI 已强制 UTF-8 输出，不代表 Python 测试父进程会按 UTF-8 解码。** 首次 hosted Windows W5 中，`subprocess.run(text=True)` 的 reader 仍按 Windows locale 解码子进程 UTF-8，先在线程抛 `UnicodeDecodeError`，随后 `stdout=None` 引发 `TypeError`；package gate 必须显式使用 `encoding="utf-8", errors="replace"`。证据：private hosted run `41273247111`，W5 step 9。
10. **PowerShell 变量不区分大小写。** Workflow 的 `foreach ($home ...)` 会尝试覆盖只读自动变量 `$HOME`；应使用 `$isolatedHome`。证据：private hosted run `41273247111`，W6 runtime smoke 明确报 `Cannot overwrite variable HOME`。
11. **Windows Git Bash 能运行 Node/Python，不等于具备 SQLite CLI fallback 的同构依赖。** W10 首轮因强制 `DSH_FORCE_SQLITE_CLI=1` 而 runner 缺 `sqlite3`，旧 health helper 又静默吞掉异常，最终只表现为 `KeyError: 'engine'`；CI 必须显式安装 sqlite CLI，helper 必须把非秘密错误写入结构化结果。证据：private hosted run `41273247111`，W10 汇总 27 PASS / 1 FAIL。
12. **Windows 路径字符串不同不代表文件不同。** Hosted runner 的 `%TEMP%` 参数以 8.3 short path（`RUNNER~1`）进入 CLI，而 Python 解析结果是 long path（`runneradmin`）；package gate 应比较 canonical long path，而非字符串相等。证据：private hosted run `41273989314`，W5 step 9。
13. **`py -3` 与 Actions `python` 不保证是同一解释器。** setup-python 将 `python.exe` 置于 PATH，但系统 Python Launcher 可能选到另一安装；安装与 `gts.exe` 发现必须绑定同一解释器，优先 active `python`，并从该解释器的 `sys.executable`/distribution 元数据找 launcher。证据：private hosted run `41273989314`，W6 runtime smoke 在 pip 成功后旧探测仍找不到对应 `gts.exe`。

### 固定动作序列

1. 构建 Python 发布物时同时生成 wheel 与 sdist；分别安装到隔离 venv，并从源码树外、`PYTHONPATH` 未设置的目录运行三个入口。
2. `--help` 只能证明 argparse 可启动；还必须跑 `--show-effective-config` 与 `--fts-stats`，才能证明 `config.py` / `indexer.py` 已进入发布物。
3. profile 注册只能显式选择单个 profile；`Auto` 只在唯一候选时成立，`all` 必须由用户明确选择。自动化或用户环境 smoke 应传 `-DshHome <临时根>`，不得依赖 ambient `DSH_HOME` 误触现役配置。
4. rc3 不实现 all-ref FTS、常驻 jieba worker或自动安装 skill。
5. hosted gate 失败先按 job/step 取证：运行日志下载需要 Actions read 权限；若 job-log endpoint 受限，可下载 run-attempt ZIP 并只提取失败 job 的错误上下文，禁止凭 step 名盲修。

### 已排除方案

- rc3 不实现 all-ref FTS：会扩大索引、重复结果、ref 清理与迁移面；保留到 v1.3。
- rc3 不实现常驻 jieba worker：属于进程生命周期/并发/崩溃恢复结构性变化；保留到 v1.3。
- 可选 skill 不自动安装、不写 memory：避免覆盖用户内容与隐藏副作用。
