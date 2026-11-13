#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# quickstart-local.sh: Zero-Gitea Quickstart
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
TARGET_DIR="${1:-/tmp/local-git-mirror}"
OWNER="local"
MIRROR_PATH="${TARGET_DIR}/${OWNER}"
# Reuse the resolver's explicit-config path; direct first runs keep the project default.
CONFIG_FILE="${GITEA_SEARCH_CONFIG:-${PROJECT_DIR}/config.json}"

config_publish() {
  python3 - "$1" "$CONFIG_FILE" "$TARGET_DIR" "$OWNER" << 'PY'
import json
import os
import stat
import sys

mode, path, mirror, owner = sys.argv[1:5]
body = (json.dumps({"mirror_root": mirror, "owners": [owner]}, indent=2) + "\n").encode("utf-8")

def refuse(reason):
    print(f"ERROR: refusing to overwrite existing config: {path} ({reason})", file=sys.stderr)
    print("Set GITEA_SEARCH_CONFIG to a new path; the existing config is left untouched.", file=sys.stderr)
    sys.exit(2)

def existing_same():
    try:
        current = os.lstat(path)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(current.st_mode):
        refuse("symlink or non-regular file")
    # Do not follow a symlink swapped in after lstat, or block on a raced FIFO.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                refuse("config changed concurrently")
            same = handle.read(len(body) + 1) == body
    except OSError:
        refuse("unreadable or changed concurrently")
    if not same:
        refuse("different content")
    return True

try:
    if mode == "check":
        if not existing_same() and not os.path.isdir(os.path.dirname(path) or "."):
            raise OSError("config directory does not exist")
    else:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError:
            if not existing_same():
                refuse("config changed concurrently")
            print(f"OK: config.json already up to date (unchanged): {path}")
        else:
            created = os.fstat(fd)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(body)
            except Exception:
                try:
                    current = os.lstat(path)
                    if (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
                        os.unlink(path)
                except OSError:
                    pass
                raise
            print(f"OK: config.json saved: {path}")
except OSError as error:
    print(f"ERROR: config handling failed: {error}", file=sys.stderr)
    sys.exit(1)
PY
}

# Refuse conflicting caller configuration before creating or updating a mirror.
config_publish check

echo "=================================================="
echo " dsh-gitea-search Local Mode (Zero-Gitea Quickstart)"
echo " Target: ${TARGET_DIR}"
echo "=================================================="

mkdir -p "${MIRROR_PATH}"

DEMO_REPO="${MIRROR_PATH}/demo-project.git"
if [[ ! -d "${DEMO_REPO}" ]]; then
  echo "[1/4] Creating demo bare repo (demo-project.git) ..."
  TMP_SRC="$(mktemp -d /tmp/demo-src-XXXXXX)"
  git -C "${TMP_SRC}" init -q -b main
  cat << 'MD' > "${TMP_SRC}/README.md"
# Demo Project for Local Code and Doc Search
Demo project for cross repo search without Gitea server.
MD
  cat << 'DOC' > "${TMP_SRC}/ARCHITECTURE.md"
# Architecture
- Engine: Bare Git and SQLite FTS5
- Tokenizer: Jieba and BM25
- Interface: MCP Stdio
DOC
  git -C "${TMP_SRC}" add .
  git -C "${TMP_SRC}" -c user.name="LocalUser" -c user.email="local@example.com" commit -q -m "init demo project"
  git clone -q --bare "${TMP_SRC}" "${DEMO_REPO}"
  rm -rf "${TMP_SRC}"
  echo "OK: demo bare repo ready: ${DEMO_REPO}"
else
  echo "[1/4] demo bare repo exists."
fi

date -u +"%Y-%m-%dT%H:%M:%SZ" > "${TARGET_DIR}/last_sync"

echo "[2/4] Generating config.json ..."
config_publish create

echo "[3/4] Building FTS5 index ..."
env GITEA_MIRROR_ROOT="${TARGET_DIR}" GITQ_MIRROR_ROOT="${TARGET_DIR}" GITEA_OWNERS="${OWNER}" GITQ_OWNER="${OWNER}" python3 "${PROJECT_DIR}/lib/indexer.py" --build

echo "[4/4] Local mode ready!"
echo "--------------------------------------------------"
echo "MCP Configuration:"
python3 - "$PROJECT_DIR" "$CONFIG_FILE" "$TARGET_DIR" "$OWNER" << 'PY'
import json
import os
import sys

project, config, mirror, owner = sys.argv[1:5]
print(json.dumps({"mcpServers": {"gitea-search": {
    "command": "node",
    "args": [os.path.join(project, "lib", "server.mjs")],
    "cwd": project,
    "env": {
        "GITEA_SEARCH_CONFIG": os.path.abspath(config),
        "GITEA_MIRROR_ROOT": os.path.abspath(mirror),
        "GITEA_OWNERS": owner,
    },
}}}, indent=2))
PY
echo "--------------------------------------------------"
