# 为什么 MCP 工具名不能只写裸名

> 适用对象：任何通过 MCP（Model Context Protocol）接入工具服务器的 Agent 开发者与使用者。  
> 本文以 `dsh-gitea-search` 为例展开，但其机制与设计原则对**所有**合规的 MCP 服务器及宿主环境均成立。

---

## 1. 一句话核心结论

**裸工具名（如 `search_gitea`）仅是服务内部的主键，并非全局唯一的调用标识。**  
宿主（如 DeepSeek Harness、Claude Desktop/Code）在聚合多个 MCP 服务后，真正呈现给模型并支持调用的名称是 **`mcp__<serverName>__<toolName>`**。  
在 Prompt、Skill 指令或系统规则中直接写裸名，能否正常执行完全取决于宿主是否存在兼容降级，协议本身**并不提供裸名跨服务调用的全局保证**。

---

## 2. 核心机制：工具名在哪个层级被添加前缀

MCP 官方规范对工具声明中 `name` 字段的原文定义为：

> `name`: **A unique identifier for the tool within the server's namespace.**  
> —— [MCP Specification · Tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)

规范的核心语义是 **within the server's namespace（在服务端命名空间内唯一）**。换言之，协议只保证单个服务端内部不重名，绝不保证跨服务唯一。服务 A 可以声明 `search` 工具，服务 B 同样可以声明 `search` 工具，二者完全合规。

但在实际通信中，JSON-RPC 的 `tools/call` 请求仅包含 `params.name` 与 `params.arguments`，**报文内没有任何字段指明本次调用应路由至哪一个服务进程**。服务与连接的映射关系仅维护在客户端/宿主的内存注册表中。

因此，当宿主聚合管理多个独立的 MCP 服务时，必须对同名空间进行隔离，主流宿主均采用**前缀命名空间映射**：

```
     MCP 服务内部（声明层）              宿主呈现给 LLM（统一注册表层）
     ────────────────────                ──────────────────────────────
     search_gitea                ──►     mcp__gitea-search__search_gitea
     read_gitea_file             ──►     mcp__gitea-search__read_gitea_file
     search_gitea_docs           ──►     mcp__gitea-search__search_gitea_docs
     list_gitea_repos            ──►     mcp__gitea-search__list_gitea_repos
                                                      ▲
                                             前缀 = 配置中的 serverName
```

**工具在模型视角的标准全名为：`mcp__` + 配置中的 `serverName` + `__` + 裸工具名。**

---

## 3. 命名层级混淆导致的四种典型失败形态

一旦用户或 Agent 在错误的抽象层级使用了工具名，通常会引发以下四类故障：

### 3.1 `Unknown tool` 错误（最常见）

宿主向模型暴露了 `mcp__gitea-search__search_gitea`，但若在调用时将带前缀的完整名称原封不动透传给后端 MCP 进程，而后端进程仅识别自身声明的 `search_gitea`，便会直接抛出协议错误：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32602,
    "message": "Unknown tool: search_gitea"
  }
}
```

这是 MCP 规范定义的典型错误码（`-32602` = Invalid params）。**遇到此类报错时，应优先核验工具名所处的命名空间层级，而非反复调试参数**。

### 3.2 工具注册被静默覆盖

当多个 MCP 服务使用了相同的裸名且宿主未实施有效前缀隔离时，宿主注册表会因键名冲突出现“后入为主”的覆盖现象。服务端明明显式声明了 4 个工具，但在 Agent 工具箱中仅能检索到 3 个，且无任何警报日志。

### 3.3 请求被静默路由至错误服务（破坏性最大）

两个提供同名工具的服务同时挂载，模型发起调用且执行成功，返回的数据结构看似正常，但实际数据却来自完全无关的另一套系统。此类静默失败极难被日志捕捉，往往在脏数据入库或数周后的审计中才被察觉。

### 3.4 入参 Schema 错位

模型读取了服务 A 的 `inputSchema` 并生成参数，但调用请求却被宿主路由到了服务 B 的同名工具，导致参数解析失败或因字段重叠而产生非预期的错误响应。

---

## 4. 命名长度的约束考量

前缀机制并非没有代价：

- **模型接口的长度限制**：主流模型（如 OpenAI Function Calling）对 `name` 字段通常限制在 **64 字符以内**（`^[a-zA-Z0-9_-]+$`）。宿主拼接 `mcp__<serverName>__` 已经消耗了部分字符额度，服务内部的裸工具名建议严格控制在 **30 字符以内**，避免总长度超限导致注册失败。
- **配置持久化与破坏性变更**：一旦带有特定前缀的工具名进入生产 Agent 的 System Prompt、工作流脚手架或评测基线，改动 `serverName` 将直接导致依赖链路断裂。

---

## 5. 最佳实践指南

### 5.1 规则与指令应使用参数化模板

在文档、Runbook 及自动化脚手架中，**推荐使用动态模板描述工具名称**：

```text
mcp__<serverName>__<toolName>
```

并在显著位置注明默认值：

```text
serverName 默认为 gitea-search
⇒ 默认完整调用名为 mcp__gitea-search__search_gitea_docs
```

#### 📌 Agent System Prompt / Skill 指令的标准配置示范

当您需要为 Agent 编写指导规则（如 `AGENTS.md`、`SOPS.md` 或自定义 Skill）时，推荐采用如下显式、严谨的书写规范：

```markdown
### 代码与知识检索规则
当需要检索内部代码仓库或历史运维经验时，请调用相应的 MCP 检索工具：
1. **代码与配置检索**：使用 `mcp__gitea-search__search_gitea`（支持文件内容、路径及 commit 搜索）。
2. **文档与故障排查经验检索**：优先使用 `mcp__gitea-search__search_gitea_docs`（支持 FTS5 中文分词与相关度排序）。
3. **文件精准读取**：通过上述工具锁定仓库与路径后，使用 `mcp__gitea-search__read_gitea_file` 进行切片读取。

注：若宿主配置的 serverName 非默认的 `gitea-search`，请将上述前缀替换为实际注册的 serverName。
```

### 5.2 注册排查三步自检法

遇到工具找不到或调用异常时，可按由浅入深的步骤核实：

```bash
# 步骤 1：查看宿主配置文件中实际定义的 serverName
grep -A3 'serverName' /path/to/cordis.patch.yml

# 步骤 2：使用官方 MCP Inspector 直连服务进程，核对 tools/list 返回的原始裸名
npx @modelcontextprotocol/inspector node lib/server.mjs

# 步骤 3：在会话中直接要求 Agent 报告当前可用工具的完整列表以验证映射结果
# 提问示例："请直接输出你当前已加载的全部包含 gitea 或 search 关键词的工具完整名称"
```

### 5.3 避免同名服务冲突

- 当环境内挂载多个检索类服务时，必须通过差异化的 `serverName`（如 `gitea-search-prod` 与 `gitea-search-dev`）建立清晰的命名空间隔离。
- 严禁在同一宿主中挂载两个相同功能、相同名称的同构服务，避免造成路由竞态。

---

## 6. 编写 MCP 文档的规范约定

为保障下游开发者与 Agent 能准确调用，文档编写应遵循以下约定：

1. **首次介绍工具时，必须同时呈现裸名与带宿主前缀的标准全名**，并清晰解释两者关系。
2. **严禁在调用示例中仅书写裸名**（裸名仅适用于 CLI 命令行环境，如 `bin/gts` 子命令，不可直接用于 MCP JSON-RPC 调用）。
3. **明示 `serverName` 的变量属性**，提供开箱即用的默认值并指出自定义替换路径。

---

## 7. 参考文献与规范出处

- [MCP Specification · Tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools) —— 工具命名空间定义与 `-32602` 标准错误码说明。
- [MCP Architecture Overview](https://modelcontextprotocol.io/docs/learn/architecture) —— 宿主聚合与多服务管理架构规范。
- [OpenAI Function Calling Guide](https://platform.openai.com/docs/guides/function-calling) —— 函数名称 64 字符上限及合规字符集说明。
