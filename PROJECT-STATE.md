# Project state — v1.2.3 (tracked snapshot)

This tracked snapshot describes v1.2.3 version content, not live publication status.
The npm, MCP and Python version identities are `1.2.3`. Publication status is
established by the release record; qualification is established by matching
exact-SHA provenance, receipts and strict joint preflight. Historical evidence
does not validate later tracked edits.

The public stable v1.2.2 was released on 2026-11-11 at commit
`fa1ebfa8d68643a101ab26ab05f60eb4bdcf1f58`. The review below is a
**Historical snapshot** of the earlier v1.2.2 integration work, not a live
v1.2.3 status page or the final v1.2.2 release result.

## Historical v1.2.2 candidate snapshot (2026-11-09)

> **Historical archive boundary:** Everything below records the earlier v1.2.2
> integration context and its follow-up notes. The `1.2.2` identity, `0/3` receipts,
> deferred promotion and stage-local non-goals are historical statements; none
> establishes v1.2.3 status. References to "current", "new P" or "this scope"
> below refer to that recorded context, not this document review.

Snapshot date: 2026-11-09. This file is a publication-safe review surface for the local `release-integration/1.2.2` line derived from immutable baseline `52d05b6…`. Private forge coordinates, host-local evidence, detailed handoffs and operational ledgers remain outside the public release tree.

This tracked state is a snapshot taken when the candidate is frozen, not a live status page. Final formal/release status for the same exact P is recorded outside the tracked tree by canonical receipts, remote evidence, strict preflight JSON and the release ledger; closing those gates does not require rewriting this snapshot.

## Repository roles

| Repository/ref role | Current role | Rule |
|---|---|---|
| Private canonical source | Authoritative development history | Exact coordinates and internal ref topology are supplied only by private runtime configuration |
| Private prerelease mirror | Stages the exact reviewed candidate | Never acts as a reverse-synchronization source |
| Private GitHub hosted staging | Runs exact-P Linux/macOS/Windows clean-hosted gates | Its identity is an explicit verifier input; it is never a public release surface |
| Public project | Public source and release surface | Public refs remain unchanged until strict preflight is verified and a separate release decision completes |
| Private audit archive | Stores matrices, handoffs and operational evidence | Never acts as a release or tag source |

The release baseline is a role, not a branch named `main`; private branch names and coordinates are intentionally omitted from this public tree.

## Version and candidate

- Product identity: npm/MCP `1.2.2`; Python `1.2.2`.
- Development follows the public `v1.2.1` / `main` first-parent line. The historical rc2 tree entered through a two-parent bridge and is an imported content ancestor, not the current development baseline.
- The stable content line starts from the reviewed rc7 successor, adds the missing Windows full-suite PyYAML dependency, and updates stable-version metadata. Its direct-child provenance commit records the exact content commit in `PROVENANCE.json`; private commit coordinates remain in the private release ledger.
- Evidence from earlier rc candidates is historical only and must not be reused for the stable exact P.
- Follow-up first-run fixes pass effective settings into sync, reject partial multi-owner
  sync, provision Windows FTS/MCP dependencies, and preserve JSON diagnostics and spaced
  CLI paths during onboarding.
- Cross-owner repositories with identical names are no longer an open runtime limitation:
  the owner-qualified CLI/MCP/FTS path resolves repositories by `owner/name`, and it is
  covered by dedicated regressions plus hosted product jobs.
- The Windows legacy-index upgrade releases its SQLite handle before re-raising
  `INDEX_REBUILD_REQUIRED`, so an in-process `--full` rebuild works on NTFS; a
  cross-platform regression guards this behaviour.
- Evidence is layered and the layers must not be conflated: product-level hosted
  compatibility is historical evidence for the pre-convergence candidate only. It
  does not transfer to this release-integration line or its new exact P. Formal hosted
  schema-3 receipts for the new P remain 0/3 and joint release preflight is not
  `verified`. Candidate-mirror product results alone do not authorize promotion.
- Private canonical and prerelease forges retain an immutable legacy annotated
  `v1.2.2` tag at commit `9db7010…` (tree `e3964dd…`). This line is different content.
  Cross-forge consumers must identify full commit or tree hashes rather than assume
  tag-name equivalence. This is a disclosure of an unresolved naming risk, not an
  acceptance of that risk or authorization to move, delete or recreate the legacy tag.

## Tracked-tree review

The original rc7 P contained 83 tracked files. Object-level review found no duplicate blobs, no whitespace-normalized whole-file duplicates, and no broken local Markdown links. Local `node_modules`, Python caches and generated compatibility links are ignored worktree noise, not formal repository content.

Disposition:

| Class | Decision |
|---|---|
| Runtime, CLI/MCP, package metadata, workflows and test body | Keep |
| `CODE-INDEX.md`, release guide, manual, Windows guide, WIKI | Keep aligned with the 1.2.2 contracts and historical RC record |
| Three Python regressions absent from default gates | Add all to `test:preflight`/receipt counter plus a documentation guard; gate2/hosted jobs provision PyYAML, and missing dependencies fail closed |
| `scripts/onboarding.{sh,ps1}` | Keep and formally connect from README/POST-INSTALL as read-only helpers |
| Superseded private review notes | Keep only in the private audit archive |
| `CHANGELOG.md` / `RELEASE-NOTES.md` | Keep: changelog is cumulative; release notes remain candidate-facing history |

## Release-evidence boundary

A fully `verified` release preflight requires four schema-3 receipts, four schema-2 forge evidence documents, an exact expected ref, explicit expected identities for both private gate2 and private GitHub hosted staging, an external four-script trust anchor and digest-bound hosted artifacts. The fixed trust ceiling remains `same-party`.

The private prerelease and hosted-staging path records the repository where each receipt actually ran and requires forge API metadata to prove it is private; the external verifier, not candidate-controlled CI, decides whether it matches the approved private staging identity. Public refs are not a pre-release test bed. Until fresh stable exact-P hosted platform evidence exists and strict joint preflight is verified, remote/tag promotion remains **deferred**.

## Freeze and tag recommendation

- **Product code**: feature-frozen for 1.2.2; stable-version work changes workflow test provisioning, version identity and candidate documentation, not the search runtime contract.
- **Local candidate**: a valid stable candidate is an exact D/P pair whose focused tests, full suite, package smoke, provenance and clean-tree checks pass. Any tracked edit requires a new pair.
- **Remote/tag boundary**: no remote candidate, tag or promotion is created in this scope. Those actions require fresh exact-P platform evidence and a separate explicit release decision.

## Review links

- Private source and prerelease coordinates: intentionally supplied at runtime and omitted from this public tree.
- Public project: `https://github.com/offzen/dsh-gitea-search`.
