# dsh-gitea-search

Cross-repository code search for DeepSeek Harness over a **local mirror** of a self-hosted
[Gitea](https://gitea.io) / [Forgejo](https://forgejo.org) instance.

Self-hosted Gitea usually ships with the server-side code index **disabled**: the web UI and the API
cannot search file contents at all. This plugin mirrors every repository the token can read into a
local bare mirror, runs `git grep` over all of them on every branch, and exposes the results to the
model as three MCP tools (`mcp__gitea_search__*`). Searching and reading never touch the network.

- `search_gitea` — file contents / file paths / commit messages, across all repositories and branches
- `read_gitea_file` — one file from the mirror, without cloning
- `list_gitea_repos` — what is in the mirror, optionally filtered

## Requirements

- DeepSeek Harness 0.2.x
- Node `^22.19.0 || >=24.0.0` and `git` (the only runtime dependencies)
- A self-hosted Gitea/Forgejo instance
- A **read-only** API token (Gitea scopes `read:repository` and `read:user`) for syncing a private instance
- Disk space proportional to the repositories you mirror

## Install

```sh
dsh plugin --profile <profile> add dsh-gitea-search
```

`--profile` is required. The package declares `dsh.bundle.patch`, so `dsh plugin add` installs the
dependency, appends the bundle to the profile, and inserts the MCP client row for you — no YAML to
write. If the profile runs without configuration hot reload, restart `dsh` after installing; the
tools then appear as `mcp__gitea_search__search_gitea`, `mcp__gitea_search__read_gitea_file` and
`mcp__gitea_search__list_gitea_repos`.

The bundle starts the MCP server with `npx -y dsh-gitea-search mcp` (the package is distributed
prebuilt on npm, so no build permission is needed). Installing from a git checkout instead? Point
the row at `node <path>/bin/mcp-server.mjs` yourself — see the troubleshooting section.

## Configure

The MCP client **scrubs the ambient environment** before it spawns the server, so values exported
in your shell do not reach it automatically. The bundle patch re-exports the variables explicitly
(`!!js process.env.GITEA_* ?? ''`), which means the source is the environment of the **dsh process**
itself — a systemd `Environment=` entry, or the shell that launched `dsh`.

| Variable | Default | Meaning |
|---|---|---|
| `GITEA_MIRROR_DIR` | `~/.local/share/gitea-search/mirror` | where the bare mirrors live |
| `GITEA_BASE_URL` | — | Gitea/Forgejo base URL, **required by `sync`** |
| `GITEA_TOKEN` | — | read-only API token, required by `sync` against a private instance |
| `GITEA_OWNER` | all readable repos | comma-separated owners (users/orgs) to mirror |
| `GITEA_MAX_RESULTS` | `200` | hard cap on records returned by `search_gitea` (max 1000) |
| `GITEA_MAX_SEARCH_BYTES` | `131072` | total output budget for one search |
| `GITEA_MAX_LINE_BYTES` | `2000` | per-line cap; longer lines are truncated in place with a marker |
| `GITEA_MAX_READ_BYTES` | `60000` | per-file read cap |
| `GITEA_JOBS` / `GITEA_SYNC_JOBS` | `6` | parallel repositories during search / sync |
| `GITEA_TIMEOUT_MS` | `120000` | per git invocation timeout |

To change a value later, either export it for the `dsh` process, or override the row in your own
profile `cordis.patch.yml` by `id` (`gitea-search`) — note that an override **replaces the whole
`config` value**, so restate every key you want to keep.

## Build the mirror

```sh
export GITEA_BASE_URL=https://gitea.example.com
export GITEA_TOKEN=...            # read-only token
npx -y dsh-gitea-search sync      # first sync, then keep it fresh
```

`sync` enumerates every repository the token can read through the Gitea HTTP API and mirrors each
one (`git clone --mirror`, then `git remote update --prune`). The mirror is a plain directory of
bare repositories: `<mirror>/<owner>/<repo>.git`, or flat `<mirror>/<repo>.git` if you mirror a
single owner by hand. A cron entry or systemd timer keeps it fresh — examples are in
[docs/MANUAL.md](docs/MANUAL.md).

## Command line

The same engine ships as a CLI: `dsh-gitea-search search <pattern> [-p|-m|-E|-s|-w|-l|-c] [-r repo] [-b branch] [-A] [-n N] [--json]`,
plus `repos`, `read <repo> <path> [--ref]`, `sync [--transport https|ssh]`, `mirror` and `mcp`.
Run `dsh-gitea-search --help` for the full option list.

## Honest boundaries

- The search surface is the mirror: only content **already pushed to the server** is searchable;
  local, unpushed work is not.
- Matching is literal by default (case-insensitive), regular expressions on request — this is a
  grep, not a semantic or vector search.
- The mirror refreshes only when you schedule `sync`; the plugin never triggers it on its own.
- Results are deliberately bounded (per record, per line, per call, per read) so a single minified
  file cannot flood the model context.

## Privacy and security

- `sync` is the only networked step. The token goes to the Gitea API in an `Authorization` header
  and to `git` through `GIT_ASKPASS` — never on a command line, never in a log (git output is
  redacted before logging).
- Search and read operate **only on the local mirror**.
- File reads validate the ref and path, and invoke `git show --end-of-options`, so a crafted ref
  cannot be used as a git option (an arbitrary-file-write vector).
- The token is your responsibility: store it where your other secrets live, and revoke it when
  you stop using the mirror.

## Troubleshooting

- **Tools never appear.** With `failOnStartupError: true` a failed MCP connection makes the row fail
  to load while the harness still starts. Look for `gitea-search` in the `dsh` log. Common causes:
  `npx` cannot reach the registry (offline host — install from a git checkout instead), or on
  Windows `npx` may need to be written `npx.cmd` in the row.
- **Tools say "no repositories under …".** The mirror is missing or `GITEA_MIRROR_DIR` points
  elsewhere — run `sync` once, then check `dsh-gitea-search mirror`.
- **Slow first search.** A cold search over a large mirror can exceed the 60s MCP default, so the
  bundle already sets `toolCallTimeoutMs: 120000`. Narrow with `repo`, use `mode: "path"` or
  `files_only` to probe first.

## Uninstall

```sh
dsh plugin --profile <profile> remove dsh-gitea-search
```

Remove the mirror directory and revoke the token afterwards.

## Compatibility

Tested against DSH `0.2.0-rc.2` (and designed for the `0.2.x` line). The bundle declares an
`engines.dsh` range as documentation; DSH gates installation by `peerDependencies` on
`@deepseek-ai/dsh-*`, which this configuration-only bundle does not declare, so it installs on any
`0.2.x` runtime.

## License

MIT