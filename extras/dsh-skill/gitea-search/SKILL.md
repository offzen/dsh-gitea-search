---
name: gitea-search
description: Search mirrored Git/Gitea repositories through the installed MCP server.
---

# Gitea Search

1. Inspect the host's actual tool registry first. MCP `tools/list` returns bare server names, while a host may prefix, sanitize, or rename them.
2. Use the code search tool for source, paths, commit messages, and non-default branches.
3. Use the document search tool for Markdown, sessions, configuration, and scripts. Its FTS index covers each repository's **default branch** only.
4. Start with a narrow query and repository filter, then read the selected file by repository, path, and ref.
5. If document search returns no match, shorten the query or use code search across heads; read a non-default ref explicitly when needed.
6. Do not unpack complete session archives merely because a focused query returned no result.

This skill is optional. Install it explicitly; installers must not overwrite an existing skill or write memory as a side effect.
