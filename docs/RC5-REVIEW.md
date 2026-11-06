# rc5 review and release-stage evidence

Date: 2026-11-06

This review note is a post-candidate development-branch record; it is deliberately not part of D or P, and does not change the frozen candidate tree.

## Scope and sequence

Reviewed the requested code index and complete local diff in `/root/dev-10/dsh-gitea-search-rc5`. Made one minimal correction: removed a stray empty YAML insert block when replacing the sole `mcp-gitea-search` entry; added a regression assertion for the existing-entry-only case. The first broad test run exposed this issue, and the corrected regression suite passes.

D (content) = `ca71c7f31f3a238d2e7eb694051ca8eeda7363cd` (`feat(rc5): add install diagnostics and mirror state`).
P (provenance-only child) = `4c6b5cba4dec4672f679d0a8172e9737b0b5500a` (`chore(rc5): bind provenance to content D`).

Verified locally:

- `npm test`: 70 total, 66 passed, 4 platform-specific Windows-only skipped, 0 failed.
- Python suites: release preflight 6/6; missing dependency 3/3; repo zero-hit 1/1; hosted receipt 5/5.
- `python3 scripts/check-provenance.py`: 74 tracked content files verified against D.
- Python/Node/shell syntax checks and `git diff --check` passed.
- `test/profile-yaml.test.py` is configured in hosted CI with PyYAML but could not run locally because the available Python interpreter has no PyYAML and no pip. Required status: blocked (local environment dependency missing), not equivalent to hosted verification.
- Native Windows/macOS behavior and exact-P hosted receipts remain pending.
- No push, tag, dispatch, remote-ref write, or production mutation was performed.

## Candidate invariants

- `P^ == D` and P is a direct single-parent child.
- `git diff --name-only D P` contains only `PROVENANCE.json`.
- Manifest `source_commit` is D; `check-provenance.py` passes.
- Exact P is not release-ready until gate2 and GitHub Linux/macOS/Windows receipts bind to P and P's tree.

## Next actions

1. Fresh-clone P into an isolated temporary directory; run the local release preflight in allow-partial mode with zero receipts to verify exact-P/provenance validation. Do not treat this as full release readiness.
2. Obtain exact-P gate2/GitHub hosted receipts through their authorized workflows. GitHub workflow dispatch/ref writes require separate explicit authorization and target/action-matched precheck; do not perform them implicitly.
3. Run full `scripts/release-preflight.py` against all four exact-P receipts. If any tracked file changes after P, produce a new D/P pair.
4. Only after the acceptance gate, separately evaluate the documented remote-ref candidate-only procedure. This note authorizes no push or remote write.
