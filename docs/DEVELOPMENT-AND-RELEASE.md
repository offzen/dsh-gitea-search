# Development and release gates

This repository uses two independent CI planes:

1. **gate2 / Gitea Actions** is the fast private Linux development gate. Its complete authority is `.gitea/workflows/ci.yml`, which calls `scripts/ci/linux-gate.sh` with the event's full commit SHA.
2. **GitHub Actions hosted runners** are the final clean-platform gate. Linux and macOS run from `.github/workflows/ci.yml`; Windows runs from `.github/workflows/windows-ci.yml`.

The old vtWSL Task/S4U/Broker/local-canary designs are not release execution paths. A Windows release receipt is valid only when it identifies the final exact candidate commit and a GitHub-hosted clean Windows runner.

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

## Routine development

1. Make and review changes on a private development branch.
2. Run `bash scripts/ci/linux-gate.sh "$(git rev-parse HEAD)" /absolute/outside/repo/linux.json`.
3. Push the reviewed branch to private gitorca and require the gate2 Linux job to pass for that exact SHA.
4. Freeze D, regenerate `PROVENANCE.json` with `--source-commit D`, and commit only the manifest as P.
5. Re-run gate2 for exact P.

Receipts must remain outside the tracked checkout (CI artifact storage or a private audit ledger). The gate2 workflow records the receipt and its digest in the job log, using a run- and SHA-specific path under runner.temp; Gitea's runner does not support the upload-artifact action used by GitHub. Never commit a receipt into the candidate tree.

## Final hosted gate

Before any GitHub credential rotation, write, branch push, or workflow dispatch:

- complete a target/action-matched PRECHECK;
- resolve the recorded legacy GitHub credential exposure by authorized rotation;
- obtain separate user authorization for the exact GitHub write/dispatch action;
- use safe credential injection and never place a secret or derived authorization header in argv or logs.

The final gate must produce four schema-1 receipts for exact P:

- `gate2-linux`;
- `github-linux`;
- `github-macos`;
- `github-windows`.

The Windows receipt must contain `provider=github-actions-hosted`, `runner_environment=github-hosted-clean-windows`, an allowlisted hosted Windows label, the formal repository identity, workflow/run identity, timestamps, toolchain facts, P, and P's tree. Historical rc3, vtWSL, self-hosted, Task, Broker, or local-canary evidence is rejected.

Validate without mutation:

```bash
python3 scripts/release-preflight.py \
  --content "$D" \
  --provenance "$P" \
  --receipt gate2-linux=/absolute/receipts/gate2-linux.json \
  --receipt github-linux=/absolute/receipts/github-linux.json \
  --receipt github-macos=/absolute/receipts/github-macos.json \
  --receipt github-windows=/absolute/receipts/github-windows.json
```

`release-preflight.py` is read-only. It never pushes, tags, dispatches, or promotes a ref. Those actions remain separate, explicitly authorized stages with fresh CAS/readback checks.
