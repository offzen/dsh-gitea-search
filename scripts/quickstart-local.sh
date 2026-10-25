#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# quickstart-local.sh: Zero-Gitea Quickstart
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
TARGET_DIR="${1:-/tmp/local-git-mirror}"
OWNER="local"
MIRROR_PATH="${TARGET_DIR}/${OWNER}"

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

CONFIG_FILE="${PROJECT_DIR}/config.json"
echo "[2/4] Generating config.json ..."
cat << JSON > "${CONFIG_FILE}"
{
  "mirror_root": "${TARGET_DIR}",
  "owners": ["${OWNER}"]
}
JSON
echo "OK: config.json saved: ${CONFIG_FILE}"

echo "[3/4] Building FTS5 index ..."
env GITEA_MIRROR_ROOT="${TARGET_DIR}" GITQ_MIRROR_ROOT="${TARGET_DIR}" GITEA_OWNERS="${OWNER}" GITQ_OWNER="${OWNER}" python3 "${PROJECT_DIR}/lib/indexer.py" --build

echo "[4/4] Local mode ready!"
echo "--------------------------------------------------"
echo "MCP Configuration:"
cat << MCP
{
  "mcpServers": {
    "gitea-search": {
      "command": "node",
      "args": ["${PROJECT_DIR}/lib/server.mjs"],
      "cwd": "${PROJECT_DIR}",
      "env": {
        "GITEA_MIRROR_ROOT": "${TARGET_DIR}",
        "GITEA_OWNERS": "${OWNER}"
      }
    }
  }
}
MCP
echo "--------------------------------------------------"
