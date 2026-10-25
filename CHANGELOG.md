# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2026-10-26

### Added
- **Zero-Gitea Local Quickstart**: Added `scripts/quickstart-local.sh` enabling developers to spin up a local MCP search engine directly from local Git repositories with zero external Gitea/network dependencies.
- **macOS & BSD Native Compatibility (PR #2)**:
  - Added `scripts/setup-macos.sh` supporting macOS default Bash 3.2 and Homebrew dependency installation.
  - Implemented non-blocking subshell timeout mechanism for platforms without GNU `timeout`.
  - Added compatibility wrappers for BSD `stat` and `awk`.
- **Automatic `config.json` Resolution**: `lib/server.mjs` now automatically resolves and loads `config.json` hierarchically from current or parent directories, removing the need for verbose environment variable exports.
- **Node.js `< 22.15` CLI Fallback**: Added seamless fallback to system `sqlite3` and `zstd` CLI when native `node:sqlite` or `zlib.zstdCompressSync` are unavailable in Node.js `20.11 ~ 22.14`.
- **Extended E2E Test Suite**: Expanded `scripts/run-e2e-test.sh` to 32 assertions across 5 phases, including new assertions for `config.json` resolution and Zero-Gitea local mode.
- **Multi-Environment CI Matrix**: Expanded GitHub Actions and Gitea Actions workflows to validate Node 20 / 22 and Python 3.11-3.13 combinations.
- **Open Source Governance**: Added `CONTRIBUTING.md` documenting contribution workflow, testing requirements, and upstream synchronization disciplines.

### Fixed
- Fixed process hang / deadlock caused by pipe buffering in fallback `timeout()` on macOS and BSD systems.
- Fixed unhandled exception when launching MCP server with empty or unconfigured mirror directories; now issues actionable warnings.
- Unified environment variable precedence: `GITEA_*` standard variables take precedence with full backward compatibility for legacy `GITQ_*` variables.
- Fixed path resolution in `sync.sh` where indexer script path check failed under certain directory layouts.

### Changed
- Refactored `README.md` and `MANUAL.md` to highlight local quickstart, macOS guides, `config.json` schema, and standardized `gitea_search` naming conventions.
- Enforced `engines.node >= 20.11.0` in `package.json`.

---

## [1.1.0] - 2026-10-23

### Added
- **SQLite FTS5 + Jieba Full-Text Search Engine**: Millisecond document and session full-text search with BM25 ranking and content kind segmentation (`doc`, `session`, `config`, `script`).
- **Smart `--and` Intersection Fallback**: Bare mirror `git grep` automatically falls back to same-line token intersection matching when multi-word literal queries yield zero hits.
- **4-Layer Context Guards**:
  - Max results clamp (default 5, hard limit 200).
  - Single-line clamp (2,000 bytes) with UTF-8 byte boundary preservation.
  - Search buffer budget clamp (128 KB total output).
  - Paged file reading window (`read_gitea_file` default 400 lines / 60 KB).
- **BlobReader Optimization**: Batch reading via persistent `git cat-file --batch` processes, eliminating `git show` subshell overhead.
- **DSH Session Health Diagnostic**: Added `scripts/dsh-session-health.mjs` supporting v1~v4 session archive inspection and online WAL truncation (`PRAGMA wal_checkpoint(TRUNCATE)`).
- **GitHub Actions CI Automation**: Initial 28-assertion testbed workflow on Ubuntu runners.

---

## [0.1.1] - 2026-10-15

### Added
- Initial standalone Node.js MCP server prototype with basic `git grep` bridging.
- Basic stdio MCP communication.
