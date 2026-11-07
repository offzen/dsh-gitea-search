# rc5 review and release-stage evidence

Date: 2026-11-06

This review note is a development-branch record; it is deliberately not part of D or P. The pre-correction candidate is frozen historically; the working tree currently holds a tested correction pending a replacement D/P.

## Scope and sequence

Reviewed the requested code index and complete local diff in the local rc5 worktree. Made one minimal correction: removed a stray empty YAML insert block when replacing the sole `mcp-gitea-search` entry; added a regression assertion for the existing-entry-only case. The first broad test run exposed this issue, and the corrected regression suite passes.

Historical pre-correction D = `ca71c7f31f3a238d2e7eb694051ca8eeda7363cd` (`feat(rc5): add install diagnostics and mirror state`).
Historical pre-correction P = `4c6b5cba4dec4672f679d0a8172e9737b0b5500a` (`chore(rc5): bind provenance to content D`).
These SHAs are no longer the candidate after the correction described below.

**Candidate correction found during continued validation:** the required local PyYAML test exposed that POSIX-hosted profile patch tests did not normalize simulated Windows drive paths, and `pathlib.Path` on POSIX computed the wrong Windows `cwd`. The fix is currently uncommitted on the development branch, after P, and invalidates P as a candidate for the corrected source. Latest available verification is the corrected Node suite (70 total, 66 passed, 4 platform skips), full local Python regression suites, and E2E 31/31; all ran against the working tree, not a frozen candidate SHA. Freeze a new D from the corrected tree and regenerate a new provenance-only P; rerun all gates against that new P. The SHA above is retained only as historical pre-correction evidence.

## Verified on prior corrected worktree (not a frozen candidate SHA)

- `npm test`: 70 total, 66 passed, 4 Windows-only skips, 0 failed.
- Python suites: release preflight 6/6; missing dependency 3/3; repo zero-hit 1/1; hosted receipt 5/5; PyYAML profile suite 4/4.
- `bash scripts/run-e2e-test.sh <isolated-mirror>`: 31 passed, 0 failed.
- Wheel/sdist package gate remains blocked on a suitable isolated Python packaging toolchain.

## Current review-fix delta (2026-11-09; dirty development tree, not candidate evidence)

User authorized code changes for F2 receipt lifecycle and F3 MCP probe diagnostics. The current tree adds:

- Linux gate now rejects pre-existing receipt paths (including symlinks) before test/build execution, instead of permitting stale success JSON to remain or be overwritten. Added isolated-fixture regressions for existing-file and symlink targets.
- `probe-stdio.mjs` now records the active MCP request stage, bounded child stderr tail, spawn errors, and child exit code/signal; on early close it rejects pending calls immediately rather than waiting for the 90-second timeout. It terminates the child with a bounded SIGTERM/SIGKILL cleanup path.
- E2E Phase 4 no longer discards probe output. Added regressions for early child exit and Node spawn ENOENT handling.
- Focused checks: 8/8 passed (`linux-gate-static`, receipt-path, probe-stdio); `node --check`, `bash -n`, and `git diff --check` passed.
- Full `npm run test:all`, package build gate, true independent-clone E2E, and hosted receipts were not run. F1 dependency-input digest suggestion remains unchanged/not addressed.
- Gate2 workflow receipt path now includes run ID and exact commit SHA; its readback steps use the same path, avoiding a fixed-path collision under the new F2 fail-closed contract. The release guide corrects the old artifact-upload claim to match the existing job-log receipt mechanism.
- These tracked changes are after P=`1acc0a3dce71c3041acdf32dad91635af320092d`; P is invalid for the modified source. No D/P commit, push, tag, dispatch, or remote write was performed.

## Candidate invariants

- `P^ == D` and P is a direct single-parent child.
- `git diff --name-only D P` contains only `PROVENANCE.json`.
- Manifest `source_commit` is D; `check-provenance.py` passes.
- Exact P is not release-ready until gate2 and GitHub Linux/macOS/Windows receipts bind to P and P's tree.

## Next actions

1. Review current dirty code diff; rerun full local tests and independent-clone smoke only with a confirmed independent dependency path.
2. Resolve the isolated Python packaging toolchain blocker without modifying system Python.
3. Freeze a new content D and provenance-only P after all tracked corrections, then rerun exact-P gates.
4. Hosted workflow dispatch and remote ref writes remain out of scope without separate explicit authorization and target/action-matched precheck.
