#!/usr/bin/env python3
"""Write a bounded exact-SHA GitHub hosted gate receipt atomically."""
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


def command(*args: str) -> str:
    result = subprocess.run(args, text=True, encoding="utf-8", errors="replace", capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed with exit {result.returncode}")
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", required=True, choices=tuple(ALLOWED))
    parser.add_argument("--commit", required=True)
    parser.add_argument("--runner-label", required=True)
    parser.add_argument("--output", required=True, type=pathlib.Path)
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
        if not run_id or not workflow_ref or event not in {"push", "workflow_dispatch"}:
            raise RuntimeError("workflow/run/event identity is incomplete")
        if event == "workflow_dispatch" and os.environ.get("GTS_DISPATCH_EXACT_SHA") != actual:
            raise RuntimeError("workflow_dispatch exact_sha does not match checkout")
        expected_workflow = ALLOWED_WORKFLOWS[args.gate]
        if f"/{expected_workflow}@" not in workflow_ref:
            raise RuntimeError("workflow identity mismatch")

        receipt = {
            "schema": 1,
            "gate": args.gate,
            "provider": "github-actions-hosted",
            "runner_environment": "github-hosted-clean-windows" if args.gate == "github-windows" else "github-hosted-clean-runner",
            "runner_label": args.runner_label,
            "repository": repository,
            "commit": actual,
            "tree": command("git", "rev-parse", "HEAD^{tree}"),
            "status": "success",
            "started_at": os.environ.get("GTS_GATE_STARTED_AT", dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")),
            "ended_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "run_id": run_id + ":" + args.gate,
            "workflow_run_id": run_id,
            "workflow": expected_workflow,
            "workflow_ref": workflow_ref,
            "event": event,
            "ref": os.environ.get("GITHUB_REF", ""),
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
