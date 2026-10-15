# Changelog

## 0.1.0

First public release.

- DSH bundle (`dsh.bundle.patch`) that connects the MCP server through
  `@deepseek-ai/dsh-mcp-client`, so the tools appear as `mcp__gitea_search__*`.
- `search_gitea`, `read_gitea_file` and `list_gitea_repos` MCP tools, plus the
  `dsh-gitea-search` command line with `search`, `repos`, `read`, `sync` and `mirror`.
- Multiple owners under one mirror, or a flat single-owner mirror; every local
  branch and optionally tags are searched.
- Layered output bounds (per result, per line, per tool call, per file read) so a
  single minified line cannot flood the model context.
- Mirror synchronisation over the Gitea/Forgejo HTTP API; the token is passed to
  git through `GIT_ASKPASS`, never on a command line, and git output is redacted
  before it is logged.
- File reads use `git show --end-of-options` with validated refs and paths.
