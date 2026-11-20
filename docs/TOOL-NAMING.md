# MCP 工具名的三层模型

MCP 只规定服务端 `tools/list` 返回的裸工具名；宿主如何把它注册给模型、GUI 如何显示，均由宿主决定。不要把某一种宿主前缀当成协议标准。

## 三个不同层级

1. **MCP server 裸名**：本服务 `v2.0.0` 在 `tools/list` 中声明 4 个通用中性裸名：`search_code`、`search_docs`、`read_file`、`list_repositories`。JSON-RPC 直连该 server 时用这些名称。
2. **宿主注册/函数名**：宿主为了避免多个 server 重名，可能加前缀、清洗 `serverName`、截断或哈希。例如某些 DSH 版本可能展示 `mcp__gitea-search__search_docs`，但这不是 MCP 规范保证的固定名称。
3. **GUI 友好显示名**：可以是中文或自然语言描述，只用于展示，不一定可调用。

## 向后兼容别名（v2.x 过渡期，保留至 v3.0.0 前）

`tools/list` 严格只返回 4 个标准中性裸名（不污染客户端工具表），而 `tools/call` 同时透明转发两族旧裸名并在 stderr 打印废弃告警：

| v2.0.0 标准裸名 | v1.x 兼容别名 | 历史 `gitorca` 兼容别名 |
|---|---|---|
| `search_code` | `search_gitea` | `search_gitorca` |
| `search_docs` | `search_gitea_docs` | `search_gitorca_docs` |
| `read_file` | `read_gitea_file` | `read_gitorca_file` |
| `list_repositories` | `list_gitea_repos` | `list_gitorca_repos` |

> 注意：服务端内部别名只能承接裸工具名变化，不能抵消你在宿主配置中修改 `serverName` 引起的前缀变化。生产 DSH 两步切换步骤见 [`UPGRADING.md`](../UPGRADING.md)。

## 可移植规则

- 安装后先查看宿主的实际工具表，再把实际注册名写入 Agent 指令或 skill。
- 直连 MCP Inspector 或 `node bin/mcp-server --dump-schema` 时核对的是裸名；在宿主会话里核对的是宿主注册名。
- `serverName` 可配置，因此示例前缀不可硬编码成普遍真理。
- 改 `serverName` 可能破坏已有 prompt、workflow 和评测基线，应视为兼容性变更。

## DSH 示例（仅示例）

当某个 DSH 版本采用 `mcp__<serverName>__<toolName>` 映射，且配置为 `serverName: gitea-search` 时，可能得到：

```text
search_code        -> mcp__gitea-search__search_code
search_docs        -> mcp__gitea-search__search_docs
read_file          -> mcp__gitea-search__read_file
list_repositories  -> mcp__gitea-search__list_repositories
```

实际名称必须以当前宿主工具注册表为准。

## 排查顺序

1. 用 MCP Inspector、`node bin/mcp-server --dump-schema` 或原始 `tools/list` 核对四个裸名；
2. 查看宿主配置里的 `serverName`；
3. 在宿主会话/调试接口中读取实际注册名；
4. 若报 `Unknown tool`，先确认调用发生在哪一层，不要反复修改业务参数。

参考：MCP 规范把 `name` 定义为 “a unique identifier for the tool within the server's namespace”。它没有定义跨 server 或跨宿主的全局前缀格式。
