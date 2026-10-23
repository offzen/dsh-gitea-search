# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-10-23

### Added
- **SQLite FTS5 + Jieba Full-Text Engine (`search_gitea_docs`)**:
  - Full-text search across Markdown docs, runbooks, and conversation session exports (`kind=doc,session,config,script`).
  - Native Jieba segmentation and BM25 relevance scoring for accurate CJK and multi-token matching.
- **Bare Mirror Grep Fallback (`search_gitea`)**:
  - Automatic fallback to multi-token `--and` intersection matching when exact phrase lookup returns zero results.
- **Context Guardrails**:
  - Configurable line-windowed file reading (`read_gitea_file`) with safe `offset` and `limit` (default 400 lines).
  - Hard result clamp (default 5, maximum 200 items).
- **Diagnostics & Telemetry**:
  - Integrated `IoTracker` tracking CPU consumption, wall time, and `/proc/self/io` read/write metrics with health grading (`OK` / `WARN` / `CRITICAL`).
- **Automated CI & Testbed**:
  - Zero-credential synthetic testbed simulating real-world devops artifacts and session logs.
  - GitHub Actions CI workflow running 26 end-to-end assertions in under 40 seconds.

### Changed
- Refactored buffer handling to use full packet decoding, strictly avoiding mid-character byte splits.
- Reduced default search result limit from legacy unconstrained output to 5 to protect LLM context windows.

### Fixed
- Fixed UTF-8 multibyte boundary truncation issue that caused `U+FFFD` replacement character artifacts during streaming reads.
- Fixed 0-hit false negatives on CJK multi-term queries in historical session logs.

### Security
- Added safe line truncation (2000 bytes/line) and total payload buffer cap (128 KB) to prevent context exhaustion attacks and prompt injection bloat.
