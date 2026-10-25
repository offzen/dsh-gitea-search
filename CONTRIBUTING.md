# Contributing to dsh-gitea-search

Thank you for your interest in contributing to `dsh-gitea-search`!

## Open Source Governance & Pipeline

This repository is maintained openly under the Apache-2.0 license.
We actively welcome community contributions, bug reports, and pull requests!

- **Public Repository & PR Mainline**: [https://github.com/offzen/dsh-gitea-search](https://github.com/offzen/dsh-gitea-search)
- **CI Automation**: Every Pull Request triggers automated GitHub Actions workflows running the full 32-assertion E2E regression test suite across multiple Node.js (20.x, 22.x) and Python (3.11-3.13) environments.
- **Upstream Synchronization & Release Discipline**: Community PRs merged on GitHub are automatically verified and synchronized with upstream infrastructure following fast-forward release disciplines (force-pushing or orphan overwrites are strictly prohibited).

## How to Contribute

1. **Fork & Clone**: Fork the repository on GitHub and clone your fork locally.
2. **Local Environment**:
   - Node.js >= 20.11 (tested on Node 20.x, 22.x)
   - Python 3.10+ with sqlite3 and jieba (`pip install jieba`)
   - Git 2.25+
3. **Run Tests Locally**:
   Before submitting your changes, run the built-in end-to-end test suite:
   ```bash
   bash scripts/run-e2e-test.sh
   ```
   All phases must pass (PASS 100%).
4. **Submit a Pull Request**:
   - Provide a clear summary of what was fixed or added.
   - Reference any related issues or discussions.
