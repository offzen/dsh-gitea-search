# 能力范围与不支持事项

本文是用户侧功能边界速查；具体安装步骤见 [`POST-INSTALL.md`](POST-INSTALL.md)。

## 支持的范围

- `search_gitea` 检索已同步到本地 bare mirror 的仓库和本地 refs；尚未推送或尚未同步的内容不可见。
- `search_gitea_docs` 用 SQLite FTS5 检索当前已建索引的文档、会话、配置与脚本；默认仅覆盖每仓默认分支。中文查询依赖 jieba。
- `read_gitea_file` 按仓库、ref 和仓内路径读取；建议先搜索定位，再小窗口读取。
- `list_gitea_repos` 展示本地镜像仓，不会查询远端实时仓库清单。

## 不会自动发生的操作

- 安装器不会替你决定何时同步远端、何时重载宿主，也不会重启 DSH。
- `onboarding` 与 `doctor-mcp` 只做检查/打印建议；不修改 profile、建立索引或触发同步。
- `sync:true` 会触发镜像同步与索引更新，是写操作；不要在循环或并发 Agent 调用中使用。
- MCP stdio 探针检查的是单独启动的 server，不等于宿主配置已加载或 GUI 工具已注册。

## 明确不提供

- 不支持搜索未推送到镜像的本地工作区源码；请先推送并完成镜像同步。
- 不承诺宿主侧工具名、前缀或 GUI 展示名固定；以当前宿主工具表为准。
- Windows 原生 PowerShell 不直接运行 `scripts/sync.sh`；需要 Git Bash 或另行配置的镜像获取流程。
- npm install 不提供 Python CLI；需使用 Python 包安装方式。
- 不提供跨所有 refs 的 FTS 文档索引；需要非默认分支时使用代码搜索并按 ref 读取。
