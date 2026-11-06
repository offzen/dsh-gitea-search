#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
usage() {
  cat <<'EOF'
Usage: bash scripts/doctor-mcp.sh [--verify] [--query WORD]
Checks the standalone MCP stdio server; it cannot inspect the host's registered tools.
EOF
}
QUERY=gitea
while (($#)); do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --verify) shift ;;
    --query) (($# >= 2)) || { echo '--query requires a value' >&2; exit 2; }; QUERY="$2"; shift 2 ;;
    *) usage >&2; exit 2 ;;
  esac
done
GTS="${GTS_BIN:-$(command -v gts || true)}"
if [[ -z "$GTS" ]]; then GTS="$ROOT_DIR/bin/gts"; fi
export GTS_BIN="$GTS"
printf 'MCP bare tool names:\n  search_gitea\n  search_gitea_docs\n  read_gitea_file\n  list_gitea_repos\n'
printf 'Host naming examples (not guaranteed): mcp__gitea-search__search_gitea\n'
printf 'Checking standalone server; this does not verify DSH host registration.\n'
node "$ROOT_DIR/scripts/probe-stdio.mjs" "$ROOT_DIR/bin/mcp-server" "$QUERY"
printf '\nIf the probe passes, reload the selected DSH profile and verify its actual tool table.\n'
