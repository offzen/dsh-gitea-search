#!/usr/bin/env python3
"""Read-only release preflight for the two-commit D/P contract.

The checker never pushes, tags, dispatches, or writes the repository. Operational
receipts must live outside the tracked checkout and all required receipts must
name the final exact provenance commit P.

rc6 hardening (each item maps to the 2026-11-07 audit):
  F1  --remote-evidence GATE=PATH : cross-check the receipt against the forge's own
      answer (run exists, head_sha == P, conclusion == success, every job green).
      A receipt is self-written by the candidate's own CI code, so this is the only
      step that makes "verified" mean more than "the JSON is internally consistent".
  F4  runner_label / runner_environment are validated for linux/macos too, not only windows.
  F5  run identity is deduplicated on workflow_run_id. Gates that legitimately share a
      run (github-linux + github-macos both come from ci.yml) are allowed; a run id
      shared across *different* workflows is rejected.
  F7  toolchain.os platform family is asserted independently of the receipt's own claims.
  F8  test_counts must be present and above floors, so a weakened suite cannot pass.
  F9  --trust-anchor PATH : the in-tree copy of this verifier must be byte-identical to
      an externally pinned copy, so the trust root is not "the thing being verified".
  F10 untracked files in the checkout are rejected (git diff --quiet cannot see them).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_GATES = ("gate2-linux", "github-linux", "github-macos", "github-windows")

ALLOWED_WINDOWS_PROVIDERS = {"github-actions-hosted"}
ALLOWED_WINDOWS_RUNNERS = {"windows-latest", "windows-2022", "windows-2025"}
EXPECTED_GITHUB_WORKFLOWS = {
    "github-linux": ".github/workflows/ci.yml",
    "github-macos": ".github/workflows/ci.yml",
    "github-windows": ".github/workflows/windows-ci.yml",
}
# F4: the runner labels each hosted gate may legitimately report.
ALLOWED_RUNNER_LABELS = {
    "github-linux": {"ubuntu-latest", "ubuntu-24.04", "ubuntu-22.04"},
    "github-macos": {"macos-latest", "macos-14", "macos-15"},
    "github-windows": set(ALLOWED_WINDOWS_RUNNERS),
}
# F7: toolchain.os prefixes per gate. macOS runners report "macOS-…"; platform.system()
# would say "Darwin", so both spellings are accepted but nothing else is.
EXPECTED_OS_PREFIXES = {
    "gate2-linux": ("Linux",),
    "github-linux": ("Linux",),
    "github-macos": ("macOS", "Darwin"),
    "github-windows": ("Windows",),
}
# F8: floors. A suite that silently loses tests cannot satisfy these. They are set
# below the current counts (node 70 executed / e2e 31 / 2 python suites) so ordinary
# cross-platform variance (skips differ per platform) does not produce a false red,
# while a gutted suite (a handful of tests) is still caught.
TEST_COUNT_FLOORS = {"node_total": 60, "python_suites": 2, "e2e": 25}
# The e2e floor only applies to gates that run the e2e suite inside the receipt job.
# On Windows the suite runs in the separate `windows-git-bash` job (the receipt job
# cannot reproduce its environment), so its coverage is attested by that job's
# result in `needs` — which `check_needs` requires to be success. Asserting a count
# we did not measure would be exactly the self-attestation rc6 set out to remove.
E2E_COUNTED_GATES = {"gate2-linux", "github-linux", "github-macos"}
WINDOWS_E2E_NEEDS_JOB = "windows-git-bash"
# The two forges are different repositories on purpose: gitorca is the private
# canonical primary, GitHub is the external mirror. Each gate runs where it runs, so
# each receipt must name the repository it actually ran in. Demanding one name for
# both made a truthful gate2 receipt fail verification (measured 2026-11-07).
GITEA_REPOSITORY = "zaoz/dsh-gitea-search"
GITHUB_REPOSITORY = "offzen/dsh-gitea-search"


class Blocked(RuntimeError):
    pass


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, encoding="utf-8", errors="replace",
        capture_output=True, check=False,
    )
    if result.returncode:
        raise Blocked(f"git {' '.join(args)} failed (exit {result.returncode})")
    return result.stdout


def require(condition: bool, message: str) -> None:
    if not condition:
        raise Blocked(message)


def load_json(path: pathlib.Path, what: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Blocked(f"invalid {what} {path}: {type(exc).__name__}") from None
    require(isinstance(data, dict), f"{what} must be an object: {path}")
    return data


def sha256_file(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_test_counts(gate: str, counts: Any) -> None:
    """F8: counts must be present, well-formed, and above the floors."""
    require(isinstance(counts, dict) and counts, f"{gate}: test_counts missing")
    for key in ("node_total", "node_pass", "node_fail", "python_suites", "e2e"):
        require(isinstance(counts.get(key), int), f"{gate}: test_counts.{key} must be an integer")
    require(counts["node_fail"] == 0, f"{gate}: test_counts reports {counts['node_fail']} node failures")
    require(counts["node_pass"] <= counts["node_total"], f"{gate}: test_counts node_pass > node_total")
    for key, floor in TEST_COUNT_FLOORS.items():
        if key == "e2e" and gate not in E2E_COUNTED_GATES:
            # Windows: e2e is measured by its own job; see E2E_COUNTED_GATES.
            continue
        require(
            counts[key] >= floor,
            f"{gate}: test_counts.{key}={counts[key]} below floor {floor} (suite was weakened)",
        )
    if gate == "github-windows":
        require(
            counts["e2e"] == 0,
            f"{gate}: test_counts.e2e={counts['e2e']} but this gate does not run e2e itself",
        )


def check_toolchain(gate: str, toolchain: Any) -> None:
    require(isinstance(toolchain, dict) and toolchain, f"{gate}: toolchain missing")
    for key in ("os", "python", "node", "git"):
        require(bool(toolchain.get(key)), f"{gate}: toolchain.{key} missing")
    os_name = str(toolchain["os"])
    prefixes = EXPECTED_OS_PREFIXES[gate]
    require(
        os_name.startswith(prefixes),
        f"{gate}: toolchain.os {os_name!r} is not one of {prefixes} (F7 platform family)",
    )


def check_receipt(path: pathlib.Path, expected_gate: str, provenance: str, tree: str) -> dict[str, Any]:
    require(path.is_absolute(), f"receipt path must be absolute: {path}")
    try:
        path.relative_to(ROOT)
    except ValueError:
        pass
    else:
        raise Blocked(f"receipt must remain outside tracked checkout: {path}")

    data = load_json(path, "receipt")
    require(data.get("schema") == 2, f"{expected_gate}: unsupported receipt schema (want 2)")
    require(data.get("gate") == expected_gate, f"{expected_gate}: gate identity mismatch")
    require(data.get("status") == "success", f"{expected_gate}: status is not success")
    require(data.get("commit") == provenance, f"{expected_gate}: exact P mismatch")
    require(data.get("tree") == tree, f"{expected_gate}: tree mismatch")
    require(bool(data.get("run_id")), f"{expected_gate}: run_id missing")
    require(bool(data.get("started_at")) and bool(data.get("ended_at")), f"{expected_gate}: timestamps missing")
    require(bool(data.get("gate_started_at")), f"{expected_gate}: gate_started_at missing (F3: no now() fallback)")
    duration = data.get("duration_seconds")
    require(isinstance(duration, int), f"{expected_gate}: duration_seconds must be an integer")
    require(duration >= 1, f"{expected_gate}: duration_seconds < 1s, gate_started_at is not a real start")
    checks = data.get("checks")
    require(isinstance(checks, list) and checks, f"{expected_gate}: checks must be a non-empty list")
    check_toolchain(expected_gate, data.get("toolchain"))
    check_test_counts(expected_gate, data.get("test_counts"))

    if expected_gate == "gate2-linux":
        require(data.get("provider") == "gitea-actions", "gate2-linux: provider is not Gitea Actions")
        require(data.get("runner_environment") == "clean-linux-runner", "gate2-linux: runner environment mismatch")
        # F6: same-party gate must say so, and must carry repository/event/ref/workflow.
        require(data.get("trust") == "same-party", "gate2-linux: trust must be declared as same-party")
        require(data.get("repository") == GITEA_REPOSITORY,
                f"gate2-linux: repository mismatch (want {GITEA_REPOSITORY})")
        require(bool(data.get("workflow")), "gate2-linux: workflow missing")
        require(data.get("event") in {"push", "workflow_dispatch"}, "gate2-linux: unsupported event")
        require(bool(data.get("ref")), "gate2-linux: ref missing")

    if expected_gate.startswith("github-"):
        require(data.get("provider") == "github-actions-hosted", f"{expected_gate}: provider is not GitHub hosted")
        require(data.get("repository") == GITHUB_REPOSITORY,
                f"{expected_gate}: repository mismatch (want {GITHUB_REPOSITORY})")
        require(data.get("workflow") == EXPECTED_GITHUB_WORKFLOWS[expected_gate], f"{expected_gate}: workflow mismatch")
        require(bool(data.get("workflow_run_id")), f"{expected_gate}: workflow run identity missing")
        require(data.get("event") in {"push", "workflow_dispatch"}, f"{expected_gate}: unsupported event")
        require(bool(data.get("ref")), f"{expected_gate}: ref missing")
        require(data.get("runner_environment") == (
            "github-hosted-clean-windows" if expected_gate == "github-windows" else "github-hosted-clean-runner"
        ), f"{expected_gate}: runner environment mismatch (F4)")
        require(
            data.get("runner_label") in ALLOWED_RUNNER_LABELS[expected_gate],
            f"{expected_gate}: runner_label not allowlisted (F4)",
        )
        # F2: the workflow_ref must point at this gate's workflow AND at the same ref
        # the receipt claims, so a run from a side branch cannot mint a receipt.
        workflow_ref = str(data.get("workflow_ref") or "")
        require(
            f"/{EXPECTED_GITHUB_WORKFLOWS[expected_gate]}@" in workflow_ref,
            f"{expected_gate}: workflow_ref does not name the expected workflow",
        )
        require(
            workflow_ref.endswith("@" + str(data.get("ref"))),
            f"{expected_gate}: workflow_ref ref does not match receipt ref (F2)",
        )
        # F3: status must be derived from the needs graph, and every need must be success.
        needs = data.get("needs")
        require(isinstance(needs, dict) and needs, f"{expected_gate}: needs results missing (F3)")
        bad = {name: result for name, result in needs.items() if result != "success"}
        require(not bad, f"{expected_gate}: upstream jobs not all successful: {bad}")

    if expected_gate == "github-windows":
        require(data.get("runner_environment") == "github-hosted-clean-windows", "github-windows: not a hosted clean runner")
        require(data.get("provider") in ALLOWED_WINDOWS_PROVIDERS, "github-windows: local/self-hosted provider rejected")
        # F8 on Windows: the e2e suite is measured by the `windows-git-bash` job, so the
        # receipt must name it as a successful need. Dropping that job from the workflow
        # would otherwise remove e2e coverage without any count noticing.
        needs = data.get("needs") or {}
        require(
            needs.get(WINDOWS_E2E_NEEDS_JOB) == "success",
            f"github-windows: needs must include {WINDOWS_E2E_NEEDS_JOB}=success (e2e attestation)",
        )
        forbidden = ("vtwsl", "local", "self-hosted", "dshwindowsgate", "scheduled-task", "broker")
        serialized = json.dumps(data, ensure_ascii=False).lower()
        require(not any(marker in serialized for marker in forbidden), "github-windows: local gate evidence rejected")
    return data


def check_run_identities(checked: dict[str, dict[str, Any]]) -> None:
    """F5: dedupe on the real run id, allowing gates that legitimately share a run."""
    runs: dict[str, dict[str, set[str]]] = {}
    for gate, data in checked.items():
        run_key = str(data.get("workflow_run_id") or data.get("run_id"))
        entry = runs.setdefault(run_key, {"gates": set(), "workflows": set()})
        entry["gates"].add(gate)
        entry["workflows"].add(str(data.get("workflow") or ""))
    for run_key, entry in runs.items():
        require(
            len(entry["workflows"]) == 1,
            f"run identity {run_key} is shared across different workflows: {sorted(entry['workflows'])} (F5)",
        )


def check_remote_evidence(
    path: pathlib.Path, gate: str, receipt: dict[str, Any], provenance: str,
) -> dict[str, Any]:
    """F1: cross-check a receipt against the forge's own record of the run."""
    data = load_json(path, "remote evidence")
    require(data.get("gate") in (gate, None), f"{gate}: remote evidence is for gate {data.get('gate')}")
    require(data.get("head_sha") == provenance, f"{gate}: remote evidence head_sha is not exact P")
    require(data.get("conclusion") == "success", f"{gate}: remote evidence conclusion is {data.get('conclusion')!r}")
    jobs = data.get("jobs")
    require(isinstance(jobs, list) and jobs, f"{gate}: remote evidence has no jobs")
    for job in jobs:
        require(isinstance(job, dict), f"{gate}: malformed remote job entry")
        require(job.get("conclusion") == "success", f"{gate}: remote job {job.get('name')!r} concluded {job.get('conclusion')!r}")
    receipt_run = str(receipt.get("workflow_run_id") or receipt.get("run_id"))
    remote_run = str(data.get("run_id") or "")
    require(
        remote_run.split(":")[0] == receipt_run.split(":")[0],
        f"{gate}: remote evidence run {remote_run!r} does not match receipt run {receipt_run!r}",
    )
    return data


def check_trust_anchor(anchor: pathlib.Path) -> str:
    """F9: the verifier must not be the only copy of itself."""
    require(anchor.is_file(), f"trust anchor not found: {anchor}")
    here = pathlib.Path(__file__).resolve()
    anchor_digest = sha256_file(anchor)
    here_digest = sha256_file(here)
    require(
        anchor_digest == here_digest,
        f"in-tree verifier {here_digest[:12]}... differs from trust anchor {anchor_digest[:12]}... (F9)",
    )
    return here_digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--content", required=True, help="full 40-hex content commit D")
    parser.add_argument("--provenance", required=True, help="full 40-hex provenance commit P")
    parser.add_argument("--receipt", action="append", default=[], metavar="GATE=ABSOLUTE_PATH")
    parser.add_argument("--remote-evidence", action="append", default=[], metavar="GATE=ABSOLUTE_PATH")
    parser.add_argument("--require-remote-evidence", action="store_true",
                        help="every gate must also supply forge-side evidence (F1)")
    parser.add_argument("--trust-anchor", metavar="PATH",
                        help="externally pinned copy of this verifier that must match byte-for-byte (F9)")
    parser.add_argument("--allow-partial", action="store_true", help="validate supplied receipts without declaring release-ready")
    args = parser.parse_args()

    try:
        require(FULL_SHA.fullmatch(args.content) is not None, "D must be a full lowercase 40-hex SHA")
        require(FULL_SHA.fullmatch(args.provenance) is not None, "P must be a full lowercase 40-hex SHA")
        content = git("rev-parse", f"{args.content}^{{commit}}").strip()
        provenance = git("rev-parse", f"{args.provenance}^{{commit}}").strip()
        require(content == args.content, "D does not resolve to the exact commit")
        require(provenance == args.provenance, "P does not resolve to the exact commit")
        parents = git("show", "-s", "--format=%P", provenance).strip().split()
        require(parents == [content], "P must have exactly one parent and P^ must equal D")
        changed = [line for line in git("diff", "--name-only", content, provenance).splitlines() if line]
        require(changed == ["PROVENANCE.json"], "P must change only PROVENANCE.json")

        manifest = json.loads(git("show", f"{provenance}:PROVENANCE.json"))
        require(manifest.get("schema") == 2, "unsupported provenance schema")
        require(manifest.get("source_commit") == content, "PROVENANCE source_commit must equal D")
        tree = git("rev-parse", f"{provenance}^{{tree}}").strip()

        # check-provenance validates worktree bytes, so require the checkout to be exact P.
        require(git("rev-parse", "HEAD").strip() == provenance, "checkout HEAD must equal exact P")
        unstaged = subprocess.run(["git", "diff", "--quiet"], cwd=ROOT, check=False).returncode
        staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT, check=False).returncode
        require(unstaged == 0 and staged == 0, "tracked checkout must be clean")
        # F10: git diff cannot see untracked files, so reject them explicitly.
        untracked = [line for line in git("ls-files", "-o", "--exclude-standard").splitlines() if line.strip()]
        require(not untracked, f"checkout has untracked files: {untracked[:5]} (F10)")

        trust_digest = None
        if args.trust_anchor:
            trust_digest = check_trust_anchor(pathlib.Path(args.trust_anchor).expanduser().resolve())

        check = subprocess.run(
            [sys.executable, "scripts/check-provenance.py"], cwd=ROOT, text=True,
            encoding="utf-8", errors="replace", capture_output=True, check=False,
        )
        require(check.returncode == 0, "check-provenance failed")
        require(f"baseline={content}" in check.stdout, "check-provenance baseline mismatch")

        receipt_paths: dict[str, pathlib.Path] = {}
        for item in args.receipt:
            gate, sep, raw_path = item.partition("=")
            require(bool(sep) and gate in REQUIRED_GATES and raw_path, f"invalid --receipt value: {item}")
            require(gate not in receipt_paths, f"duplicate receipt for {gate}")
            receipt_paths[gate] = pathlib.Path(raw_path)
        missing = [gate for gate in REQUIRED_GATES if gate not in receipt_paths]
        if not args.allow_partial:
            require(not missing, "missing required receipts: " + ", ".join(missing))

        evidence_paths: dict[str, pathlib.Path] = {}
        for item in args.remote_evidence:
            gate, sep, raw_path = item.partition("=")
            require(bool(sep) and gate in REQUIRED_GATES and raw_path, f"invalid --remote-evidence value: {item}")
            evidence_paths[gate] = pathlib.Path(raw_path)
        if args.require_remote_evidence:
            missing_evidence = [gate for gate in REQUIRED_GATES if gate not in evidence_paths]
            require(not missing_evidence, "missing remote evidence for: " + ", ".join(missing_evidence))

        checked: dict[str, dict[str, Any]] = {}
        verified_remote: dict[str, str] = {}
        for gate, path in receipt_paths.items():
            data = check_receipt(path, gate, provenance, tree)
            checked[gate] = data
            if gate in evidence_paths:
                check_remote_evidence(evidence_paths[gate], gate, data, provenance)
                verified_remote[gate] = str(evidence_paths[gate])
        check_run_identities(checked)

        remote_complete = not [gate for gate in REQUIRED_GATES if gate not in verified_remote]
        status = "verified" if not missing else "configured-unverified"
        if status == "verified" and args.require_remote_evidence and not remote_complete:
            status = "configured-unverified"

        result = {
            "status": status,
            "content": content,
            "provenance": provenance,
            "tree": tree,
            "provenance_only": True,
            "receipts": {gate: str(path) for gate, path in receipt_paths.items()},
            "remote_evidence": verified_remote,
            "remote_evidence_complete": remote_complete,
            "trust_anchor_sha256": trust_digest,
            "missing_receipts": missing,
            "windows_contract": "github-hosted-clean-runner-only",
            "receipt_schema": 2,
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (Blocked, json.JSONDecodeError) as exc:
        print(f"RELEASE_PREFLIGHT_BLOCKED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
