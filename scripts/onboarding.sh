#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
usage() {
  cat <<'EOF'
Usage: bash scripts/onboarding.sh [--verify]

--verify  Read-only checks and actionable next steps; default behaves the same.
This helper never syncs repositories, builds indexes, edits profiles, or reloads DSH.
EOF
}
case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  ""|--verify) ;;
  *) usage >&2; exit 2 ;;
esac

GTS="${GTS_BIN:-}"
if [[ -z "$GTS" ]]; then
  if command -v gts >/dev/null 2>&1; then GTS="$(command -v gts)"; else GTS="$ROOT_DIR/bin/gts"; fi
fi
if [[ ! -x "$GTS" ]]; then
  echo "FAIL: gts CLI is not executable: $GTS" >&2
  echo "Next: run the platform installer and ensure its Python environment is available." >&2
  exit 1
fi

CONFIG_JSON="$($GTS --show-effective-config 2>&1)" || { printf '%s\n' "$CONFIG_JSON" >&2; exit 1; }
MIRROR="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["mirror_root"])' <<<"$CONFIG_JSON")"
OWNERS="$(python3 -c 'import json,sys; print(",".join(json.load(sys.stdin)["owners"]))' <<<"$CONFIG_JSON")"
VALID="$(python3 -c 'import json,sys; print(str(json.load(sys.stdin)["valid"]).lower())' <<<"$CONFIG_JSON")"
if [[ "$VALID" != true ]]; then
  echo 'FAIL: effective config is invalid (owners missing or placeholder).' >&2
  echo 'Next: set GITEA_OWNERS and GITEA_MIRROR_ROOT in the host MCP profile, then reload that profile.' >&2
  exit 1
fi
printf 'Configuration: valid\nMirror root: %s\nOwners: %s\n' "$MIRROR" "$OWNERS"

REPOS_JSON="$($GTS --repos --json 2>&1)" || { printf '%s\n' "$REPOS_JSON" >&2; echo 'Next: check owner directories and mirror permissions.' >&2; exit 1; }
REPO_COUNT="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("count",0))' <<<"$REPOS_JSON")"
printf 'Mirrored repositories: %s\n' "$REPO_COUNT"
if [[ "$REPO_COUNT" == 0 ]]; then
  echo 'WARN: no mirrored repositories are available.'
  echo "Next (explicit write): bash '$ROOT_DIR/scripts/sync.sh' -q"
else
  echo 'Repository mirror: present'
fi

DOCTOR="$($GTS --doctor --json 2>&1)" || { printf '%s\n' "$DOCTOR" >&2; exit 1; }
python3 - "$DOCTOR" <<'PY'
import json, sys
report = json.loads(sys.argv[1])
fts = report.get("fts_index", {})
print("FTS index: " + ("present" if fts.get("exists") else "missing"))
if fts.get("exists"):
    print("FTS alignment: " + ("aligned" if fts.get("aligned_1to1") else "check required"))
PY
if [[ ! -f "$MIRROR/state/gitea-fts.sqlite" ]]; then
  echo 'Next (explicit write): run the installer index step or gts --reindex after confirming the mirror.'
fi
cat <<EOF

MCP check: bash "$ROOT_DIR/scripts/doctor-mcp.sh" --verify
Apply/reload: configuration changes take effect only after the DSH host reloads the selected profile.
Rollback: restore the timestamped cordis.patch.yml backup created by profile-patch.py, then reload.
No synchronization, indexing, profile mutation, or DSH restart was performed by this script.
EOF
