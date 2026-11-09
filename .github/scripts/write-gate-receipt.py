#!/usr/bin/env python3
"""Write a bounded exact-SHA GitHub-hosted gate receipt atomically.

The receipt is same-party evidence.  rc7 binds it to measured runner/workflow
identity, complete test counts, and the exact input bytes consumed by the writer.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import platform
import re
import subprocess
import sys

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
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
COUNT_KEYS = (
    "node_total", "node_pass", "node_fail", "node_cancelled", "node_skipped", "node_todo",
    "python_suites",
)


def clean_git_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def command(*args: str) -> str:
    result = subprocess.run(
        args, text=True, encoding="utf-8", errors="replace", capture_output=True,
        check=False, env=clean_git_env(),
    )
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed with exit {result.returncode}")
    return result.stdout.strip()


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def strict_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def parse_timestamp(raw: str, what: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise RuntimeError(f"{what} is not an ISO-8601 timestamp: {raw!r}") from None
    if parsed.tzinfo is None:
        raise RuntimeError(f"{what} must carry a timezone: {raw!r}")
    return parsed


def load_test_counts(path: pathlib.Path) -> dict[str, int]:
    """Load schema-2 output from collect-test-counts.py and flatten it for the receipt."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"unreadable --test-counts {path}: {type(exc).__name__}") from None
    if not isinstance(data, dict) or data.get("schema") != 2:
        raise RuntimeError("--test-counts must be a schema-2 JSON object")
    counts: dict[str, int] = {}
    for key in COUNT_KEYS:
        value = data.get(key)
        if not strict_count(value):
            raise RuntimeError(f"test_counts.{key} must be a non-negative integer (not bool)")
        counts[key] = value
    e2e = data.get("e2e")
    if not isinstance(e2e, dict) or not strict_count(e2e.get("pass")) or not strict_count(e2e.get("fail")):
        raise RuntimeError("test_counts.e2e must be an object with non-negative integer pass/fail")
    counts["e2e"] = e2e["pass"]
    if counts["node_fail"] != 0:
        raise RuntimeError(f"test_counts reports {counts['node_fail']} node failures")
    parts = sum(counts[key] for key in ("node_pass", "node_fail", "node_cancelled", "node_skipped", "node_todo"))
    if parts != counts["node_total"]:
        raise RuntimeError("test_counts node arithmetic is inconsistent")
    if e2e["fail"] != 0:
        raise RuntimeError(f"test_counts reports {e2e['fail']} e2e failures")
    return counts


def load_needs(raw: str) -> dict[str, str]:
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
    parser.add_argument("--test-counts", required=True, type=pathlib.Path)
    parser.add_argument("--needs-results", required=True)
    parser.add_argument("--gate-started-at", required=True)
    args = parser.parse_args()

    try:
        if not FULL_SHA.fullmatch(args.commit):
            raise RuntimeError("commit must be a full lowercase 40-hex SHA")
        actual = command("git", "--no-pager", "-c", "core.fsmonitor=", "rev-parse", "HEAD")
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
        if not REPOSITORY.fullmatch(repository):
            raise RuntimeError("GitHub repository identity must be owner/name")
        repository_visibility = os.environ.get("GITHUB_REPOSITORY_VISIBILITY", "")
        if repository_visibility != "private":
            raise RuntimeError("hosted staging repository visibility must be private")
        run_id = os.environ.get("GITHUB_RUN_ID", "")
        run_attempt_raw = os.environ.get("GITHUB_RUN_ATTEMPT", "")
        workflow_ref = os.environ.get("GITHUB_WORKFLOW_REF", "")
        event = os.environ.get("GITHUB_EVENT_NAME", "")
        git_ref = os.environ.get("GITHUB_REF", "")
        if not run_id.isdecimal() or not run_attempt_raw.isdecimal() or int(run_attempt_raw) < 1:
            raise RuntimeError("workflow run id/attempt must be positive decimal integers")
        if event not in {"push", "workflow_dispatch"} or not git_ref:
            raise RuntimeError("workflow/run/event identity is incomplete")
        if event == "workflow_dispatch" and os.environ.get("GTS_DISPATCH_EXACT_SHA") != actual:
            raise RuntimeError("workflow_dispatch exact_sha does not match checkout")
        expected_workflow = ALLOWED_WORKFLOWS[args.gate]
        expected_workflow_ref = f"{repository}/{expected_workflow}@{git_ref}"
        if workflow_ref != expected_workflow_ref:
            raise RuntimeError(f"workflow identity/ref mismatch: {workflow_ref!r} != {expected_workflow_ref!r}")

        runner_hosted = os.environ.get("RUNNER_ENVIRONMENT", "")
        runner_name = os.environ.get("RUNNER_NAME", "")
        runner_os = os.environ.get("RUNNER_OS", "")
        runner_arch = os.environ.get("RUNNER_ARCH", "")
        if runner_hosted != "github-hosted" or not all((runner_name, runner_os, runner_arch)):
            raise RuntimeError("measured hosted-runner identity is missing or not github-hosted")

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

        root = pathlib.Path(__file__).resolve().parent.parent.parent
        inputs_sha256 = {
            "test-counts.json": sha256(args.test_counts),
            "collect-test-counts.py": sha256(root / "scripts" / "collect-test-counts.py"),
            "write-gate-receipt.py": sha256(root / ".github" / "scripts" / "write-gate-receipt.py"),
        }
        receipt = {
            "schema": 3,
            "gate": args.gate,
            "provider": "github-actions-hosted",
            "runner_environment": "github-hosted-clean-windows" if args.gate == "github-windows" else "github-hosted-clean-runner",
            "runner_hosted_environment": runner_hosted,
            "runner_name": runner_name,
            "runner_os": runner_os,
            "runner_arch": runner_arch,
            "runner_label": args.runner_label,
            "repository": repository,
            "repository_visibility": repository_visibility,
            "commit": actual,
            "tree": command("git", "--no-pager", "-c", "core.fsmonitor=", "rev-parse", "HEAD^{tree}"),
            "status": "success",
            "needs": needs,
            "started_at": gate_started.astimezone(dt.timezone.utc).isoformat(timespec="seconds"),
            "gate_started_at": gate_started.astimezone(dt.timezone.utc).isoformat(timespec="seconds"),
            "ended_at": now.isoformat(timespec="seconds"),
            "duration_seconds": duration,
            "run_id": run_id + ":" + args.gate,
            "workflow_run_id": run_id,
            "run_attempt": int(run_attempt_raw),
            "workflow": expected_workflow,
            "workflow_ref": workflow_ref,
            "event": event,
            "ref": git_ref,
            "inputs_sha256": inputs_sha256,
            "test_counts": counts,
            "checks": [
                "checkout-exact-sha", "platform-family", "measured-hosted-runner",
                "workflow-identity-and-ref", "needs-all-success", "test-counts-floors",
                "input-sha256",
            ],
            "toolchain": {
                "os": platform.platform(), "python": platform.python_version(),
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
