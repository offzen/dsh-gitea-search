# Project state — v1.2.2-rc7 prerelease review

Snapshot date: 2026-11-09. This file is a publication-safe review surface. Private forge coordinates, host-local evidence, detailed handoffs and operational ledgers remain outside the public release tree.

## Repository roles

| Repository/ref role | Current role | Rule |
|---|---|---|
| Private canonical source | Authoritative development history | Exact coordinates and internal ref topology are supplied only by private runtime configuration |
| Private prerelease mirror | Stages the exact reviewed candidate | Never acts as a reverse-synchronization source |
| Public project | Public source and release surface | Public refs remain unchanged until a separately completed release decision |
| Private audit archive | Stores matrices, handoffs and operational evidence | Never acts as a release or tag source |

The release baseline is a role, not a branch named `main`; private branch names and coordinates are intentionally omitted from this public tree.

## Version and candidate

- Product identity: npm `1.2.2-rc7`; Python `1.2.2rc7`.
- A previous private baseline exists, but its exact commit, tag and repository coordinates belong in the private release ledger.
- An earlier local rc7 pair completed its local test set, but the tree review found documentation drift and three Python tests outside the default gate. The hygiene tree then passed Node 125 (121 pass, 4 Windows-only skips), nine Python suites (49 tests) and E2E 31/31 before the new freeze; exact-P retesting remains required.
- A prior private candidate failed its workflow-identity check. The compatibility fix requires a new D/P pair and new evidence; old receipts and local exact-P evidence must not be reused.

## Tracked-tree review

The original rc7 P contained 83 tracked files. Object-level review found no duplicate blobs, no whitespace-normalized whole-file duplicates, and no broken local Markdown links. Local `node_modules`, Python caches and generated compatibility links are ignored worktree noise, not formal repository content.

Disposition:

| Class | Decision |
|---|---|
| Runtime, CLI/MCP, package metadata, workflows and test body | Keep |
| `CODE-INDEX.md`, release guide, manual, Windows guide, WIKI | Update to rc7 contracts |
| Three Python regressions absent from default gates | Add all to `test:preflight`/receipt counter plus a documentation guard; gate2/hosted jobs provision PyYAML, and missing dependencies fail closed |
| `scripts/onboarding.{sh,ps1}` | Keep and formally connect from README/POST-INSTALL as read-only helpers |
| Superseded private review notes | Keep only in the private audit archive |
| `CHANGELOG.md` / `RELEASE-NOTES.md` | Keep: changelog is cumulative; release notes remain candidate-facing history |

## Release-evidence boundary

A fully `verified` release preflight requires four schema-3 receipts, four schema-2 forge evidence documents, an exact expected ref, an external four-script trust anchor and digest-bound hosted artifacts. The fixed trust ceiling remains `same-party`.

The private prerelease path can verify ref identity, privacy, clean D/P topology, provenance and available forge Actions. Until fresh exact-P hosted platform evidence exists, stable/tag promotion remains **deferred**.

## Freeze and tag recommendation

- **Product code**: feature-frozen for rc7; this hygiene pass changes tests/docs/release bookkeeping, not the search runtime contract.
- **Candidate**: private prerelease may proceed only after new exact-P local tests, provenance and clean-tree checks pass; the private Linux gate is then recorded as a separate required release-evidence gate. If pending or red, label the candidate `configured-unverified`, not release-ready.
- **Tag**: do not create a new candidate or stable tag in this scope. Reconsider only after fresh exact-P hosted receipts/evidence/artifacts make preflight `verified`, followed by an explicit release decision.

## Review links

- Private source and prerelease coordinates: intentionally supplied at runtime and omitted from this public tree.
- Public project: `https://github.com/offzen/dsh-gitea-search`.
