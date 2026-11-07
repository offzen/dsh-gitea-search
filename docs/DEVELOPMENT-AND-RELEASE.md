# Development and release gates

This repository uses **two same-party CI planes**, not two independent attestations:

1. **gate2 / Gitea Actions** is the private Linux development gate. Its authority is `.gitea/workflows/ci.yml`, which calls `scripts/ci/linux-gate.sh` with the event's full commit SHA.
2. **GitHub Actions hosted runners** are the clean-platform gate. Linux and macOS run from `.github/workflows/ci.yml`; Windows runs from `.github/workflows/windows-ci.yml`.

The old vtWSL Task/S4U/Broker/local-canary designs are not release execution paths. A Windows release receipt is valid only when it identifies the final exact candidate commit and a GitHub-hosted clean Windows runner. Historical rc3, vtWSL, self-hosted, Task, Broker, or local-canary evidence is rejected. Even when every check passes, the fixed trust ceiling is `same-party`: these gates make accidental or lazy false greens harder, but are not independent signatures or unforgeable attestations.

## Candidate state machine

A releasable candidate is always two commits:

- **D (content)**: product, test, workflow, or documentation changes.
- **P (provenance)**: the direct child of D and changes only `PROVENANCE.json`.

Required invariants:

- `P^ = D` and P has exactly one parent;
- `git diff --name-only D P` is exactly `PROVENANCE.json`;
- `PROVENANCE.json.source_commit = D`;
- `scripts/check-provenance.py` passes at P;
- every operational receipt names exact P and P's tree.

Any tracked edit after P invalidates the candidate. Create a new D, then a new provenance-only P. Existing release tags are immutable.

## Routine development and freeze

1. Make and review changes on a private development branch.
2. Install test dependencies (`jieba`, `PyYAML` and the package/Node toolchain) in an isolated environment, then run `PYTHONDONTWRITEBYTECODE=1 npm run test:all`. The profile-YAML suite is in the default chain and must not be skipped.
3. Commit the complete content change as D. No receipt is valid for D once P exists.
4. Regenerate the manifest with `python3 scripts/generate-provenance.py --source-commit "$D"` and commit **only** `PROVENANCE.json` as P.
5. Confirm the D/P invariants and run `scripts/check-provenance.py` at exact P.
6. Push the reviewed candidate branch to private gitorca. Require gate2 for exact P, then copy its receipt from the run log into the private audit ledger.
7. If GitHub clean-platform evidence is authorized, push/dispatch the exact same P on the expected ref and collect the three hosted receipts, remote evidence and artifact zips.

Receipts, evidence, artifacts and trust anchors must remain outside the tracked checkout. Gitea's gate2 runner records the receipt and digest in the job log rather than claiming a forge artifact digest it cannot provide. Never commit operational evidence into the candidate tree.

## Exact-P evidence contract

A fully checked candidate needs four **receipt schema 3** documents:

- `gate2-linux`;
- `github-linux`;
- `github-macos`;
- `github-windows`.

Each receipt binds exact P/tree, repository, workflow, event/ref/run attempt, measured runner/toolchain fields, `needs` outcomes, schema-2 test counts and SHA-256 of the counter plus the actual receipt writer.

It also needs four **remote evidence schema 2** documents produced by `scripts/fetch-remote-evidence.py`. Evidence deep-checks the forge's complete job listing, required job names and conclusions, run/ref/workflow identity, freshness and hosted-runner signals. For GitHub gates, the downloaded artifact zip must match the forge-recorded digest and contain the byte-identical receipt and test-count data. Gate2 is explicitly recorded as having no forge artifact digest.

The trust anchor is an **out-of-tree directory** containing byte-identical pinned copies of:

- `release-preflight.py`;
- `check-provenance.py`;
- `fetch-remote-evidence.py`;
- `collect-test-counts.py`.

The verifier rejects an in-tree anchor, symlinks, same-inode self-comparison, missing files and byte drift. `--expected-ref` is mandatory for a `verified` result. Any missing receipt, evidence, artifact, anchor or expected ref yields `configured-unverified`; relaxation switches cannot produce `verified`.

## Read-only validation

```bash
python3 /absolute/external-anchor/release-preflight.py \
  --content "$D" \
  --provenance "$P" \
  --expected-ref refs/heads/rc/v1.2.2-rc7 \
  --trust-anchor /absolute/external-anchor \
  --receipt gate2-linux=/absolute/receipts/gate2-linux.json \
  --receipt github-linux=/absolute/receipts/github-linux.json \
  --receipt github-macos=/absolute/receipts/github-macos.json \
  --receipt github-windows=/absolute/receipts/github-windows.json \
  --remote-evidence gate2-linux=/absolute/evidence/gate2-linux.json \
  --remote-evidence github-linux=/absolute/evidence/github-linux.json \
  --remote-evidence github-macos=/absolute/evidence/github-macos.json \
  --remote-evidence github-windows=/absolute/evidence/github-windows.json \
  --artifact github-linux=/absolute/artifacts/github-linux.zip \
  --artifact github-macos=/absolute/artifacts/github-macos.zip \
  --artifact github-windows=/absolute/artifacts/github-windows.zip
```

The final JSON must say `status: verified`, `remote_evidence_complete: true`, `provenance_only: true`, and still `trust_ceiling: same-party`. Treat a shell exit code without parsing `status` as insufficient: `configured-unverified` deliberately exits successfully so it can provide a complete diagnostic report.

## Promotion and private prerelease

Every remote mutation requires its own target/action-matched PRECHECK and compare-and-set readback. The normal private prerelease path is:

1. local exact P → gitorca candidate ref;
2. the same local exact P → private gitpub RC ref after a fail-closed secret/internal-identifier review;
3. read back both refs and confirm their SHA equals local P;
4. leave all `main`, `release/*`, existing tags and GitHub refs unchanged unless separately authorized.

A candidate branch is append-only in practice: if a published candidate fails, retain it as evidence and publish a corrected suffix/new candidate rather than force-moving the ref. Do not create a tag merely because private prerelease branches exist. Stable/tag consideration requires fresh exact-P clean-platform evidence and an explicit release decision.

`release-preflight.py` is read-only. It never pushes, tags, dispatches, promotes a ref or rotates credentials.
