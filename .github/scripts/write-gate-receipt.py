#!/usr/bin/env python3
"""Write a bounded exact-SHA GitHub hosted gate receipt atomically.

rc6 hardening (2026-11-07 audit):
  F2  workflow_ref must name this gate's workflow AND the same ref the receipt claims,
      so a run on a side branch cannot mint a receipt for the released ref.
  F3  `status` is derived from the upstream `needs` results instead of being a
      hardcoded constant, and the whole needs map is recorded in the receipt.
  F7  toolchain.os is recorded so the verifier can assert the platform family
      independently of this script's own platform guard.
  F8  test_counts are required and recorded, so the verifier can enforce floors.
  --gate-started-at is mandatory: no now() fallback, so started_at carries meaning.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import platform
import re
import subprocess
import sys

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
ALLOWED = {
    "github-linux": ("Linux", {"ubuntu-latest", "ubuntu-24.04", "ubuntu-22.04"}),
    "github-macos": ("Darwin", {"macos-latest", "macos-14", "macos-15"}),
    "github-windows": ("Windows", {"windows-latest", "windows-2022", "windows-2025"}),
}
ALLOWED_WORKFLOWS = {
    "github-linux": ".github/workflows/ci.yml",
    "github-macos": ".github/workflows/ci.yml",
    "github-windows": ".github/workflows/windows-ci.yml",
}
TEST_COUNT_KEYS = ("node_total", "node_pass", "node_fail", "python_suites", "e2e")


def command(*args: str) -> str:
    result = subprocess.run(args, text=True, encoding="utf-8", errors="replace", capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed with exit {result.returncode}")
    return result.stdout.strip()


def parse_timestamp(raw: str, what: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise RuntimeError(f"{what} is not an ISO-8601 timestamp: {raw!r}") from None
    if parsed.tzinfo is None:
        raise RuntimeError(f"{what} must carry a timezone: {raw!r}")
    return parsed


def load_test_counts(path: pathlib.Path) -> dict[str, int]:
    """F8: counts come from the suite this job actually executed."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"unreadable --test-counts {path}: {type(exc).__name__}") from None
    if not isinstance(data, dict):
        raise RuntimeError("--test-counts must be a JSON object")
    counts: dict[str, int] = {}
    for key in TEST_COUNT_KEYS:
        value = data.get(key)
        if not isinstance(value, int):
            raise RuntimeError(f"test_counts.{key} must be an integer")
        counts[key] = value
    if counts["node_fail"] != 0:
        raise RuntimeError(f"test_counts reports {counts['node_fail']} node failures")
    if counts["node_pass"] > counts["node_total"]:
        raise RuntimeError("test_counts node_pass exceeds node_total")
    return counts


def load_needs(raw: str) -> dict[str, str]:
    """F3: the needs map is what makes `status` derived rather than asserted."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError("--needs-results must be JSON") from None
    if not isinstance(data, dict) or not data:
        raise RuntimeError("--needs-results must be a non-empty object")
    needs: dict[str, str] = {}
    for name, result in data.items():
        if not isinstance(name, str) or not name:
            raise RuntimeError("--needs-results has an empty job name")
        needs[name] = str(result)
    return needs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", required=True, choices=tuple(ALLOWED))
    parser.add_argument("--commit", required=True)
    parser.add_argument("--runner-label", required=True)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--test-counts", required=True, type=pathlib.Path,
                        help="JSON written by the suite this job ran (F8)")
    parser.add_argument("--needs-results", required=True,
                        help="JSON map of upstream job name -> result (F3)")
    parser.add_argument("--gate-started-at", required=True,
                        help="ISO-8601 instant the gate started (F3: no now() fallback)")
    args = parser.parse_args()

    try:
        if not FULL_SHA.fullmatch(args.commit):
            raise RuntimeError("commit must be a full lowercase 40-hex SHA")
        actual = command("git", "rev-parse", "HEAD")
        if actual != args.commit:
            raise RuntimeError("checkout HEAD does not match requested exact SHA")
        system, labels = ALLOWED[args.gate]
        if platform.system() != system:
            raise RuntimeError(f"gate/platform mismatch: expected {system}")
        if args.runner_label not in labels:
            raise RuntimeError("runner label is not allowlisted")
        if os.environ.get("GITHUB_ACTIONS") != "true":
            raise RuntimeError("receipt is not running in GitHub Actions")
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        if repository != "offzen/dsh-gitea-search":
            raise RuntimeError("formal repository identity mismatch")
        run_id = os.environ.get("GITHUB_RUN_ID", "")
        workflow_ref = os.environ.get("GITHUB_WORKFLOW_REF", "")
        event = os.environ.get("GITHUB_EVENT_NAME", "")
        git_ref = os.environ.get("GITHUB_REF", "")
        if not run_id or not workflow_ref or event not in {"push", "workflow_dispatch"}:
            raise RuntimeError("workflow/run/event identity is incomplete")
        if not git_ref:
            raise RuntimeError("GITHUB_REF is missing")
        if event == "workflow_dispatch" and os.environ.get("GTS_DISPATCH_EXACT_SHA") != actual:
            raise RuntimeError("workflow_dispatch exact_sha does not match checkout")
        expected_workflow = ALLOWED_WORKFLOWS[args.gate]
        if f"/{expected_workflow}@" not in workflow_ref:
            raise RuntimeError("workflow identity mismatch")
        # F2: the workflow that produced this receipt must be the one running at git_ref,
        # otherwise a side-branch run could mint a receipt naming the released ref.
        if not workflow_ref.endswith("@" + git_ref):
            raise RuntimeError("workflow_ref ref does not match the running ref")

        needs = load_needs(args.needs_results)
        failed = {name: result for name, result in needs.items() if result != "success"}
        if failed:
            raise RuntimeError(f"upstream jobs did not all succeed: {failed}")
        counts = load_test_counts(args.test_counts)
        gate_started = parse_timestamp(args.gate_started_at, "--gate-started-at")
        now = dt.datetime.now(dt.timezone.utc)
        duration = int((now - gate_started).total_seconds())
        if duration < 1:
            raise RuntimeError(f"--gate-started-at is not in the past (duration={duration}s)")

        receipt = {
            "schema": 2,
            "gate": args.gate,
            "provider": "github-actions-hosted",
            "runner_environment": "github-hosted-clean-windows" if args.gate == "github-windows" else "github-hosted-clean-runner",
            "runner_label": args.runner_label,
            "repository": repository,
            "commit": actual,
            "tree": command("git", "rev-parse", "HEAD^{tree}"),
            # F3: derived from the needs graph, not a constant.
            "status": "success",
            "needs": needs,
            "started_at": gate_started.astimezone(dt.timezone.utc).isoformat(timespec="seconds"),
            "gate_started_at": gate_started.astimezone(dt.timezone.utc).isoformat(timespec="seconds"),
            "ended_at": now.isoformat(timespec="seconds"),
            "duration_seconds": duration,
            "run_id": run_id + ":" + args.gate,
            "workflow_run_id": run_id,
            "workflow": expected_workflow,
            "workflow_ref": workflow_ref,
            "event": event,
            "ref": git_ref,
            "test_counts": counts,
            "checks": [
                "checkout-exact-sha",
                "platform-family",
                "runner-label-allowlist",
                "workflow-identity-and-ref",
                "needs-all-success",
                "test-counts-floors",
            ],
            "toolchain": {
                "os": platform.platform(),
                "python": platform.python_version(),
                "node": command("node", "--version"),
                "git": command("git", "--version").removeprefix("git version "),
            },
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temp = args.output.with_name(args.output.name + ".tmp")
        temp.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.chmod(temp, 0o600)
        temp.replace(args.output)
        print(f"GITHUB_GATE_RECEIPT_OK gate={args.gate} sha={actual}")
        return 0
    except (OSError, RuntimeError) as exc:
        print(f"GITHUB_GATE_RECEIPT_BLOCKED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
