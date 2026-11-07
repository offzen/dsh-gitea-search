#!/usr/bin/env bash
# Deterministic Linux CI gate. Receipts are operational artifacts and must stay
# outside the tracked checkout.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REQUESTED_SHA="${1:-${GITHUB_SHA:-}}"
RECEIPT_PATH="${2:-${GTS_CI_RECEIPT:-}}"

if [[ ! "$REQUESTED_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "usage: $0 <full-40-hex-sha> [receipt-path]" >&2
  exit 64
fi

ACTUAL_SHA="$(git -C "$ROOT_DIR" rev-parse HEAD)"
if [[ "$ACTUAL_SHA" != "$REQUESTED_SHA" ]]; then
  echo "requested SHA does not match checkout HEAD" >&2
  exit 65
fi
if ! git -C "$ROOT_DIR" diff --quiet || ! git -C "$ROOT_DIR" diff --cached --quiet; then
  echo "tracked checkout is dirty" >&2
  exit 66
fi

STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/dsh-gitea-search-linux-gate.XXXXXX")"
cleanup() {
  rm -rf -- "$SCRATCH"
}
trap cleanup EXIT

# The rc3 failure was caused by ambient config aliases. Strip all supported
# aliases for every child, then point E2E at a fresh, explicitly owned mirror.
for key in \
  GITEA_MIRROR_ROOT GITEA_MIRROR_PATH GITQ_MIRROR_ROOT \
  GITEA_OWNERS GITEA_OWNER GITQ_OWNER GITEA_SEARCH_CONFIG \
  GITEA_TOKEN GITQ_TOKEN GTS_CONFIG; do
  unset "$key" || true
done
export HOME="$SCRATCH/home"
export XDG_CACHE_HOME="$SCRATCH/xdg-cache"
export npm_config_cache="$SCRATCH/npm-cache"
export PIP_CACHE_DIR="$SCRATCH/pip-cache"
export PYTHONDONTWRITEBYTECODE=1
export GITEA_MIRROR_ROOT="$SCRATCH/mirror"
export GITEA_MIRROR_PATH="$SCRATCH/mirror"
export GITQ_MIRROR_ROOT="$SCRATCH/mirror"
export GITEA_OWNERS="test-owner"
export GITQ_OWNER="test-owner"
mkdir -p "$HOME" "$XDG_CACHE_HOME" "$npm_config_cache" "$PIP_CACHE_DIR"

cd "$ROOT_DIR"
npm ci --ignore-scripts --no-audit --no-fund
# rc6 F8: run the suites through the counter so the receipt can carry real numbers
# instead of an asserted `status: success`. Covers the same four suites as
# `npm run test:all` (node --test, the two python suites, and the e2e runner).
TEST_COUNTS="$SCRATCH/test-counts.json"
python3 scripts/collect-test-counts.py --output "$TEST_COUNTS"
python3 -c 'import jieba' >/dev/null
BUILD_ROOT="$SCRATCH/python-build"
if python3 -c 'import build' >/dev/null 2>&1; then
  python3 -m build --outdir "$BUILD_ROOT/dist"
elif python3 -m pip --version >/dev/null 2>&1; then
  python3 -m pip wheel --no-deps --no-build-isolation --wheel-dir "$BUILD_ROOT/dist" .
  python3 -m pip download --no-deps --no-build-isolation --no-binary=:all: --dest "$BUILD_ROOT/dist" .
else
  echo "python package build tooling is missing (install build or pip)" >&2
  exit 68
fi
python3 scripts/test-python-package.py "$BUILD_ROOT/dist"
python3 scripts/check-provenance.py
git diff --check
git diff --quiet
git diff --cached --quiet

TREE_SHA="$(git rev-parse 'HEAD^{tree}')"
ENDED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
NODE_VERSION="$(node --version)"
NPM_VERSION="$(npm --version)"
PYTHON_VERSION="$(python3 -c 'import platform; print(platform.python_version())')"
GIT_VERSION="$(git --version | awk '{print $3}')"
RUN_ID="${GITHUB_RUN_ID:-local-offline}"
WORKFLOW_REF="${GITHUB_WORKFLOW_REF:-offline}"

if [[ -z "$RECEIPT_PATH" ]]; then
  RECEIPT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/dsh-gitea-search-linux-receipt.XXXXXX")"
  RECEIPT_PATH="$RECEIPT_DIR/receipt.json"
fi
mkdir -p "$(dirname "$RECEIPT_PATH")"
RECEIPT_PATH="$(python3 - "$RECEIPT_PATH" <<'PY'
import pathlib, sys
print(pathlib.Path(sys.argv[1]).resolve())
PY
)"
case "$RECEIPT_PATH" in
  "$ROOT_DIR"|"$ROOT_DIR"/*)
    echo "receipt path must be outside the tracked checkout" >&2
    exit 67
    ;;
esac
if [[ -e "$RECEIPT_PATH" || -L "$RECEIPT_PATH" ]]; then
  echo "receipt path already exists; use a new path for this gate run" >&2
  exit 67
fi

RECEIPT_TMP="${RECEIPT_PATH}.tmp.$$"
export GATE_ACTUAL_SHA="$ACTUAL_SHA" GATE_TREE_SHA="$TREE_SHA"
export GATE_STARTED_AT="$STARTED_AT" GATE_ENDED_AT="$ENDED_AT"
export GATE_NODE_VERSION="$NODE_VERSION" GATE_NPM_VERSION="$NPM_VERSION"
export GATE_PYTHON_VERSION="$PYTHON_VERSION" GATE_GIT_VERSION="$GIT_VERSION"
export GATE_RUN_ID="$RUN_ID" GATE_WORKFLOW_REF="$WORKFLOW_REF"
export GATE_TEST_COUNTS="$TEST_COUNTS"
export GATE_EVENT="${GITHUB_EVENT_NAME:-workflow_dispatch}"
export GATE_REF="${GITHUB_REF:-refs/heads/local-offline}"
export GATE_REPOSITORY="${GITHUB_REPOSITORY:-offzen/dsh-gitea-search}"
python3 - "$RECEIPT_TMP" <<'PY'
import datetime as dt, json, os, pathlib, platform, sys

started = dt.datetime.fromisoformat(os.environ["GATE_STARTED_AT"].replace("Z", "+00:00"))
ended = dt.datetime.fromisoformat(os.environ["GATE_ENDED_AT"].replace("Z", "+00:00"))
counts_path = pathlib.Path(os.environ["GATE_TEST_COUNTS"])
counts = json.loads(counts_path.read_text(encoding="utf-8")) if counts_path.exists() else {}
for key in ("node_total", "node_pass", "node_fail", "python_suites", "e2e"):
    if not isinstance(counts.get(key), int):
        raise SystemExit(f"test_counts.{key} missing from {counts_path}")
if counts["node_fail"] != 0:
    raise SystemExit(f"test_counts reports {counts['node_fail']} node failures")

receipt = {
    "schema": 2,
    "gate": "gate2-linux",
    "provider": "gitea-actions" if os.environ["GATE_RUN_ID"] != "local-offline" else "local-offline",
    # rc6 F6: the gate runs on our own runner, so it is regression coverage rather
    # than independent trust. Say so instead of letting the receipt imply otherwise.
    "trust": "same-party",
    "runner_environment": "clean-linux-runner",
    "repository": os.environ["GATE_REPOSITORY"],
    "commit": os.environ["GATE_ACTUAL_SHA"],
    "tree": os.environ["GATE_TREE_SHA"],
    "status": "success",
    "started_at": os.environ["GATE_STARTED_AT"],
    "gate_started_at": os.environ["GATE_STARTED_AT"],
    "ended_at": os.environ["GATE_ENDED_AT"],
    "duration_seconds": int((ended - started).total_seconds()),
    "run_id": os.environ["GATE_RUN_ID"],
    "workflow": ".gitea/workflows/ci.yml",
    "workflow_ref": os.environ["GATE_WORKFLOW_REF"],
    "event": os.environ["GATE_EVENT"],
    "ref": os.environ["GATE_REF"],
    "test_counts": {key: counts[key] for key in ("node_total", "node_pass", "node_fail", "python_suites", "e2e")},
    "toolchain": {
        "os": platform.platform(),
        "python": os.environ["GATE_PYTHON_VERSION"],
        "node": os.environ["GATE_NODE_VERSION"],
        "npm": os.environ["GATE_NPM_VERSION"],
        "git": os.environ["GATE_GIT_VERSION"],
    },
    "checks": [
        "npm-ci-ignore-scripts",
        "node-unit-and-e2e",
        "python-wheel-and-sdist",
        "provenance",
        "git-diff-check",
        "test-counts-floors",
    ],
}
path = pathlib.Path(sys.argv[1])
path.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
PY
chmod 600 "$RECEIPT_TMP"
mv -f -- "$RECEIPT_TMP" "$RECEIPT_PATH"
printf 'LINUX_GATE_OK sha=%s receipt=%s\n' "$ACTUAL_SHA" "$RECEIPT_PATH"
