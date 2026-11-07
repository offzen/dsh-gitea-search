# Project state — v1.2.2-rc7 private prerelease review

Snapshot date: 2026-11-08. This file is a concise, publication-safe review surface; detailed session/HANDOFF history and host-local evidence live in the private archive repository, not in this release tree.

## Repository roles

| Repository/ref role | Current role | Rule |
|---|---|---|
| gitorca `zaoz/dsh-gitea-search` | Private formal source and authoritative history | Candidate refs and `release/1.2.x`; its unrelated `main` is not the release line |
| gitpub `zaoz/dsh-gitea-search-RC` | Private prerelease mirror | Receives the exact reviewed local candidate; not a source for reverse synchronization |
| GitHub `offzen/dsh-gitea-search` | Public upstream/release surface | Read-only in this prerelease scope; public `main` remains the v1.2.1 baseline |
| gitorca private archive | Internal matrix, handoffs, audits and operational evidence | Never a release/tag source |

The release baseline is a ref role, not the branch name `main`: gitorca/gitpub `release/1.2.x` currently point to the rc6 private baseline, while GitHub `main` is the public v1.2.1 lineage. The other `main` branches are intentionally not aligned.

## Version and candidate

- Product identity: npm `1.2.2-rc7`; Python `1.2.2rc7`.
- Previous private baseline: rc6 P `7fd7a8b33c3cc625ba91cb764c1e5b388db5c59c`; annotated private tag `v1.2.2-rc6` peels to that commit.
- The original local rc7 pair (`D=7c1376a…`, `P=e20b37c…`) completed its 2026-11-07 local test set, but this tree review found documentation drift and three Python tests outside the default gate. The hygiene tree tested locally with Node 125 (121 pass, 4 Windows-only skips), nine Python suites (49 tests) and E2E 31/31 before the new freeze; exact-P retesting remains required.
- The first private rc7 P (`8183902…`) is preserved with its red gitorca/gitpub runs: both runners omitted the GitHub-only `GITHUB_WORKFLOW_REF`. The dual-forge gate compatibility fix requires another **new D/P pair** and a suffix candidate; old rc7 receipts or local exact-P evidence must not be reused.

## Tracked-tree review

The original rc7 P contained 83 tracked files. Object-level review found no duplicate blobs, no whitespace-normalized whole-file duplicates, and no broken local Markdown links. Local `node_modules`, Python caches and generated compatibility links are ignored worktree noise, not formal repository content.

Disposition:

| Class | Decision |
|---|---|
| Runtime, CLI/MCP, package metadata, workflows and test body | Keep |
| `CODE-INDEX.md`, release guide, manual, Windows guide, WIKI | Update to rc7 contracts |
| Three Python regressions absent from default gates | Add all to `test:preflight`/receipt counter plus a documentation guard; gate2/hosted jobs provision PyYAML, and missing dependencies fail closed |
| `scripts/onboarding.{sh,ps1}` | Keep and formally connect from README/POST-INSTALL as read-only helpers |
| `docs/RC5-REVIEW.md` | Remove from the release tree after preserving it in the private archive; it describes a superseded dirty development snapshot |
| `CHANGELOG.md` / `RELEASE-NOTES.md` | Keep: changelog is cumulative; release notes remain candidate-facing history |

## Release-evidence boundary

A fully `verified` release preflight requires four schema-3 receipts, four schema-2 forge evidence documents, exact expected ref, an external four-script trust anchor, and digest-bound GitHub artifacts. The fixed trust ceiling remains `same-party`.

The private gitorca → gitpub prerelease can verify ref identity, privacy, clean D/P topology, provenance and available forge Actions. It cannot create missing GitHub-hosted Linux/macOS/Windows evidence without a separately authorized GitHub candidate push/dispatch. Until those fresh exact-P gates exist, stable/tag promotion remains **deferred**.

## Freeze and tag recommendation

- **Product code**: feature-frozen for rc7; this hygiene pass changes tests/docs/release bookkeeping, not the search runtime contract.
- **Candidate**: private prerelease may proceed after new exact-P local tests, provenance and clean-tree checks pass; gitorca gate2 is then recorded as a separate required release-evidence gate. If it is pending or red, label the candidate `configured-unverified`, not release-ready.
- **Tag**: do not create `v1.2.2-rc7` or stable `v1.2.2` in this scope. Reconsider a tag only after fresh exact-P GitHub-hosted receipts/evidence/artifacts make preflight `verified`, followed by an explicit release decision.

## Review links

- Formal source: https://git.orca.kdns.fr/zaoz/dsh-gitea-search
- Private prerelease mirror: https://gitpub.orca.kdns.fr/zaoz/dsh-gitea-search-RC
- Public project (read-only for this scope): https://github.com/offzen/dsh-gitea-search
