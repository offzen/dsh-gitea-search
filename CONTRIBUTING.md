# Contributing to dsh-gitea-search

Thank you for your interest in contributing to `dsh-gitea-search`!

## Open Source Governance & Pipeline

This repository is maintained openly under the Apache-2.0 license.
We actively welcome community contributions, bug reports, and pull requests!

- **Public Repository & PR Mainline**: [https://github.com/offzen/dsh-gitea-search](https://github.com/offzen/dsh-gitea-search)
- **CI Automation**: Every Pull Request triggers automated GitHub Actions workflows running the 31-assertion E2E regression suite plus unit and packaging gates across supported Node.js and Python versions.
- **Release Discipline**: Pull requests are verified by the workflows in this repository. Merging a PR does **not** imply automatic synchronization or release to another forge; maintainers handle any downstream candidate or release step separately, without force-moving existing tags.

## How to Contribute

1. **Fork & Clone**: Fork the repository on GitHub and clone your fork locally.
2. **Local Environment**:
   - Node.js >= 20.11 (tested on Node 20.x, 22.x)
   - Python 3.10+ with sqlite3, jieba and PyYAML (`python3 -m pip install jieba PyYAML`)
   - Git 2.25+
3. **Run Tests Locally**:
   Before submitting changes, install Node dependencies and run the same default chain used by the release counter:
   ```bash
   npm install
   PYTHONDONTWRITEBYTECODE=1 npm run test:all
   ```
   This runs Node tests, all explicit Python contract/regression suites, and the 31-assertion E2E test. Missing PyYAML is a failure, not a skip. For release-candidate freezing and the exact-SHA receipt contract, see [docs/DEVELOPMENT-AND-RELEASE.md](docs/DEVELOPMENT-AND-RELEASE.md).
4. **Submit a Pull Request**:
   - Provide a clear summary of what was fixed or added.
   - Reference any related issues or discussions.
