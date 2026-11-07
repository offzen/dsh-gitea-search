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

# rc7 A8: every git call below runs with ambient GIT_* stripped (GIT_DIR, GIT_INDEX_FILE,
# GIT_WORK_TREE, GIT_CONFIG_* could otherwise point it at some other repository), without a
# pager, without fsmonitor, and with diff drivers disabled.
git_safe() {
  local -a unset_args=()
  local key
  while IFS= read -r key; do
    unset_args+=("-u" "$key")
  done < <(env | sed -n 's/^\(GIT_[A-Za-z0-9_]*\)=.*/\1/p')
  env ${unset_args[@]+"${unset_args[@]}"} git --no-pager -c core.fsmonitor= -c core.quotepath=false -C "$ROOT_DIR" "$@"
}

ACTUAL_SHA="$(git_safe rev-parse HEAD)"
if [[ "$ACTUAL_SHA" != "$REQUESTED_SHA" ]]; then
  echo "requested SHA does not match checkout HEAD" >&2
  exit 65
fi
if ! git_safe diff --no-ext-diff --no-textconv --quiet || ! git_safe diff --no-ext-diff --no-textconv --cached --quiet; then
  echo "tracked checkout is dirty" >&2
  exit 66
fi
# rc7 A8: untracked AND ignored files must not exist before any build action. This can only be
# checked here, at the very start: `npm ci` below creates node_modules (ignored), so the same
# check at the end would always fail.
if [[ -n "$(git_safe status --porcelain --ignored)" ]]; then
  echo "checkout has untracked or ignored files before the gate started; use a fresh checkout" >&2
  exit 66
fi

# rc7 A3: workflow identity comes from the measured environment, never from constants. On a
# forge (RUN_ID != local-offline) the expected workflow_ref is recomputed here from
# repository + workflow path + ref and must equal GITHUB_WORKFLOW_REF exactly.
RUN_ID="${GITHUB_RUN_ID:-local-offline}"
GATE_WORKFLOW=".gitea/workflows/ci.yml"
WORKFLOW_REF="${GITHUB_WORKFLOW_REF:-}"
RUN_ATTEMPT="${GITHUB_RUN_ATTEMPT:-}"
if [[ "$RUN_ID" != "local-offline" ]]; then
  EXPECTED_WORKFLOW_REF="${GITHUB_REPOSITORY:-}/${GATE_WORKFLOW}@${GITHUB_REF:-}"
  if [[ -z "${GITHUB_REPOSITORY:-}" || -z "${GITHUB_REF:-}" || "$WORKFLOW_REF" != "$EXPECTED_WORKFLOW_REF" ]]; then
    echo "GITHUB_WORKFLOW_REF '$WORKFLOW_REF' does not equal the computed '$EXPECTED_WORKFLOW_REF'" >&2
    exit 69
  fi
  if [[ ! "$RUN_ATTEMPT" =~ ^[1-9][0-9]*$ ]]; then
    echo "GITHUB_RUN_ATTEMPT is missing or not a positive integer" >&2
    exit 69
  fi
else
  # Offline run: there is no forge identity to measure. Such a receipt is rejected by the
  # verifier (provider=local-offline); the placeholders only keep the JSON well-formed.
  WORKFLOW_REF="offline"
  RUN_ATTEMPT=0
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
git_safe diff --no-ext-diff --no-textconv --check
git_safe diff --no-ext-diff --no-textconv --quiet
git_safe diff --no-ext-diff --no-textconv --cached --quiet

TREE_SHA="$(git_safe rev-parse 'HEAD^{tree}')"
ENDED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
NODE_VERSION="$(node --version)"
NPM_VERSION="$(npm --version)"
PYTHON_VERSION="$(python3 -c 'import platform; print(platform.python_version())')"
GIT_VERSION="$(git --version | awk '{print $3}')"

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
export GATE_RUN_ID="$RUN_ID" GATE_WORKFLOW_REF="$WORKFLOW_REF" GATE_RUN_ATTEMPT="$RUN_ATTEMPT"
export GATE_ROOT_DIR="$ROOT_DIR" GATE_WORKFLOW="$GATE_WORKFLOW"
export GATE_TEST_COUNTS="$TEST_COUNTS"
export GATE_EVENT="${GITHUB_EVENT_NAME:-workflow_dispatch}"
export GATE_REF="${GITHUB_REF:-refs/heads/local-offline}"
export GATE_REPOSITORY="${GITHUB_REPOSITORY:-offzen/dsh-gitea-search}"
python3 - "$RECEIPT_TMP" <<'PY'
import datetime as dt, hashlib, json, os, pathlib, platform, sys

started = dt.datetime.fromisoformat(os.environ["GATE_STARTED_AT"].replace("Z", "+00:00"))
ended = dt.datetime.fromisoformat(os.environ["GATE_ENDED_AT"].replace("Z", "+00:00"))
counts_path = pathlib.Path(os.environ["GATE_TEST_COUNTS"])
counts = json.loads(counts_path.read_text(encoding="utf-8")) if counts_path.exists() else {}

def strict(value):
    # rc7 A6: bool is an int subclass; reject it, and reject negatives.
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


NODE_KEYS = ("node_total", "node_pass", "node_fail", "node_cancelled", "node_skipped", "node_todo")
for key in NODE_KEYS + ("python_suites",):
    if not strict(counts.get(key)):
        raise SystemExit(f"test_counts.{key} missing or not a non-negative integer in {counts_path}")
e2e = counts.get("e2e")
if not isinstance(e2e, dict) or not strict(e2e.get("pass")) or not strict(e2e.get("fail")):
    raise SystemExit(f"test_counts.e2e must be an object with integer pass/fail in {counts_path}")
if counts["node_fail"] != 0:
    raise SystemExit(f"test_counts reports {counts['node_fail']} node failures")
if sum(counts[key] for key in NODE_KEYS[1:]) != counts["node_total"]:
    raise SystemExit("test_counts arithmetic is inconsistent (pass+fail+cancelled+skipped+todo != total)")


def sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


# rc7 A5: measured, never constants. The receipt names the exact bytes its numbers came from.
root = pathlib.Path(os.environ["GATE_ROOT_DIR"])
inputs_sha256 = {
    "test-counts.json": sha256(counts_path),
    "collect-test-counts.py": sha256(root / "scripts" / "collect-test-counts.py"),
    "linux-gate.sh": sha256(root / "scripts" / "ci" / "linux-gate.sh"),
}

receipt = {
    "schema": 3,
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
    "workflow": os.environ["GATE_WORKFLOW"],
    "workflow_ref": os.environ["GATE_WORKFLOW_REF"],
    "run_attempt": int(os.environ["GATE_RUN_ATTEMPT"]),
    "event": os.environ["GATE_EVENT"],
    "ref": os.environ["GATE_REF"],
    "inputs_sha256": inputs_sha256,
    "test_counts": {
        **{key: counts[key] for key in NODE_KEYS + ("python_suites",)},
        "e2e": e2e["pass"],
    },
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
        "clean-checkout-incl-ignored-at-start",
        "workflow-ref-recomputed",
    ],
}
path = pathlib.Path(sys.argv[1])
path.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
PY
chmod 600 "$RECEIPT_TMP"
mv -f -- "$RECEIPT_TMP" "$RECEIPT_PATH"
printf 'LINUX_GATE_OK sha=%s receipt=%s\n' "$ACTUAL_SHA" "$RECEIPT_PATH"
