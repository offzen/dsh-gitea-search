#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
usage() {
  cat <<'EOF'
Usage: bash scripts/doctor-mcp.sh [--verify] [--query WORD]
Checks the standalone MCP stdio server and performs a read-only SQLite FTS5 user_version check;
it never modifies config or rebuilds indexes, and cannot inspect the host's registered tools.
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
GTS="${GITEA_SEARCH_CLI:-${GTS_BIN:-$(command -v gts || true)}}"
if [[ -z "$GTS" ]]; then GTS="$ROOT_DIR/bin/gts"; fi
export GITEA_SEARCH_CLI="$GTS"
export GTS_BIN="$GTS"
printf 'MCP bare tool names:\n  search_code\n  search_docs\n  read_file\n  list_repositories\n'
printf 'Legacy tool aliases accepted on tools/call: search_gitea*, search_gitorca*\n'
printf 'Host naming examples (not guaranteed): mcp__gitea-search__search_code\n'
printf 'Checking standalone server; this does not verify host registration.\n'

# Read-only SQLite FTS5 index PRAGMA user_version precheck (never mutates files).
MIRROR_ROOT_CHECK="${GITEA_MIRROR_ROOT:-${GITEA_MIRROR_PATH:-${GITQ_MIRROR_ROOT:-}}}"
if [[ -z "$MIRROR_ROOT_CHECK" ]] && [[ -x "$GTS" ]]; then
  MIRROR_ROOT_CHECK="$("$GTS" --show-effective-config 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("mirror_root",""))' 2>/dev/null || true)"
fi
if [[ -n "$MIRROR_ROOT_CHECK" ]] && [[ -f "$MIRROR_ROOT_CHECK/state/gitea-fts.sqlite" ]]; then
  python3 - "$MIRROR_ROOT_CHECK/state/gitea-fts.sqlite" <<'PY'
import pathlib, sqlite3, sys
db_path = pathlib.Path(sys.argv[1]).resolve()
try:
    con = sqlite3.connect(f"file:{db_path.as_uri().removeprefix('file:')}?mode=ro", uri=True, timeout=2.0)
    version = int(con.execute("PRAGMA user_version").fetchone()[0])
    try:
        has_docs = con.execute("SELECT 1 FROM docs_fts LIMIT 1").fetchone() is not None
    except sqlite3.OperationalError:
        has_docs = False
    con.close()
    if version != 2 and has_docs:
        print(f"WARN: SQLite FTS5 index {db_path} has PRAGMA user_version={version} (expected 2); 需执行一次全量重建：gts --reindex --full")
    else:
        print(f"SQLite FTS5 index identity: user_version={version} ({'OK' if version == 2 else 'empty'}) [{db_path}]")
except Exception as exc:
    print(f"WARN: unable to inspect SQLite FTS5 index {db_path} read-only: {exc}")
PY
else
  printf 'SQLite FTS5 index check: no local index found (read-only check skipped).\n'
fi

node "$ROOT_DIR/scripts/probe-stdio.mjs" "$ROOT_DIR/bin/mcp-server" "$QUERY"
printf '\nIf the probe passes, reload your MCP host (Claude Desktop / Cursor / Cline / DSH) and verify its actual tool table.\n'
