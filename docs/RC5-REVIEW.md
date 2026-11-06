# rc5 review and release-stage evidence

Date: 2026-11-06

This review note is a development-branch record; it is deliberately not part of D or P. The pre-correction candidate is frozen historically; the working tree currently holds a tested correction pending a replacement D/P.

## Scope and sequence

Reviewed the requested code index and complete local diff in `/root/dev-10/dsh-gitea-search-rc5`. Made one minimal correction: removed a stray empty YAML insert block when replacing the sole `mcp-gitea-search` entry; added a regression assertion for the existing-entry-only case. The first broad test run exposed this issue, and the corrected regression suite passes.

Historical pre-correction D = `ca71c7f31f3a238d2e7eb694051ca8eeda7363cd` (`feat(rc5): add install diagnostics and mirror state`).
Historical pre-correction P = `4c6b5cba4dec4672f679d0a8172e9737b0b5500a` (`chore(rc5): bind provenance to content D`).
These SHAs are no longer the candidate after the correction described below.

**Candidate correction found during continued validation:** the required local PyYAML test exposed that POSIX-hosted profile patch tests did not normalize simulated Windows drive paths, and `pathlib.Path` on POSIX computed the wrong Windows `cwd`. The fix is currently uncommitted on the development branch, after P, and invalidates P as a candidate for the corrected source. Latest available verification is the corrected Node suite (70 total, 66 passed, 4 platform skips), full local Python regression suites, and E2E 31/31; all ran against the working tree, not a frozen candidate SHA. Freeze a new D from the corrected tree and regenerate a new provenance-only P; rerun all gates against that new P. The SHA above is retained only as historical pre-correction evidence.

Verified locally on the corrected worktree (not a frozen candidate SHA):

- `npm test`: 70 total, 66 passed, 4 Windows-only skips, 0 failed.
- Python suites: release preflight 6/6; missing dependency 3/3; repo zero-hit 1/1; hosted receipt 5/5; PyYAML profile suite 4/4 (PyYAML loaded from an isolated temporary extraction).
- `bash scripts/run-e2e-test.sh <isolated-mirror>`: 31 passed, 0 failed. This run also verified the new empty-mirror fixture setup.
- `git diff --check` passed. `scripts/check-provenance.py` passed after regenerating a pending-freeze manifest for 75 tracked content files; final D/P are not yet created.
- Wheel/sdist package gate remains blocked: system Python has neither `build` nor `pip`, `venv` creation fails because `ensurepip` is unavailable, and no `uv`/`pipx` is installed. An isolated install was authorized but could not be carried out without changing system packages; no system mutation was attempted.
- Native Windows/macOS behavior and exact-P hosted receipts remain pending. No CI workflow was dispatched or ref pushed.
- No push, tag, dispatch, remote-ref write, or production mutation was performed.

## Candidate invariants

- `P^ == D` and P is a direct single-parent child.
- `git diff --name-only D P` contains only `PROVENANCE.json`.
- Manifest `source_commit` is D; `check-provenance.py` passes.
- Exact P is not release-ready until gate2 and GitHub Linux/macOS/Windows receipts bind to P and P's tree.

## Next actions

1. Fresh-clone P into an isolated temporary directory; run the local release preflight in allow-partial mode with zero receipts to verify exact-P/provenance validation. Do not treat this as full release readiness.
2. Obtain exact-P gate2/GitHub hosted receipts through their authorized workflows. GitHub workflow dispatch/ref writes require separate explicit authorization and target/action-matched precheck; do not perform them implicitly.
3. Commit the correction as a new D, regenerate a provenance-only P, and repeat exact-P gate/fresh-clone checks against that new P. GitHub workflow dispatch and remote ref writes remain out of scope unless separately reauthorized.
4. Only after the gates, ask for explicit authorization before any remote candidate-ref write; this note authorizes no push.
