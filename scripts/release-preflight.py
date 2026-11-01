#!/usr/bin/env python3
"""Read-only release preflight for the two-commit D/P contract.

The checker never pushes, tags, dispatches, or writes the repository. Operational
receipts must live outside the tracked checkout and all required receipts must
name the final exact provenance commit P.
"""
from __future__ import annotations

import argparse
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


def load_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Blocked(f"invalid receipt {path}: {type(exc).__name__}") from None
    require(isinstance(data, dict), f"receipt must be an object: {path}")
    return data


def check_receipt(path: pathlib.Path, expected_gate: str, provenance: str, tree: str) -> dict[str, Any]:
    require(path.is_absolute(), f"receipt path must be absolute: {path}")
    try:
        path.relative_to(ROOT)
    except ValueError:
        pass
    else:
        raise Blocked(f"receipt must remain outside tracked checkout: {path}")

    data = load_json(path)
    require(data.get("schema") == 1, f"{expected_gate}: unsupported receipt schema")
    require(data.get("gate") == expected_gate, f"{expected_gate}: gate identity mismatch")
    require(data.get("status") == "success", f"{expected_gate}: status is not success")
    require(data.get("commit") == provenance, f"{expected_gate}: exact P mismatch")
    require(data.get("tree") == tree, f"{expected_gate}: tree mismatch")
    require(bool(data.get("run_id")), f"{expected_gate}: run_id missing")
    require(bool(data.get("started_at")) and bool(data.get("ended_at")), f"{expected_gate}: timestamps missing")
    require(isinstance(data.get("toolchain"), dict) and data["toolchain"], f"{expected_gate}: toolchain missing")

    if expected_gate == "gate2-linux":
        require(data.get("provider") == "gitea-actions", "gate2-linux: provider is not Gitea Actions")
        require(data.get("runner_environment") == "clean-linux-runner", "gate2-linux: runner environment mismatch")
    if expected_gate.startswith("github-"):
        require(data.get("provider") == "github-actions-hosted", f"{expected_gate}: provider is not GitHub hosted")
        require(data.get("repository") == "offzen/dsh-gitea-search", f"{expected_gate}: repository mismatch")
        require(data.get("workflow") == EXPECTED_GITHUB_WORKFLOWS[expected_gate], f"{expected_gate}: workflow mismatch")
        require(bool(data.get("workflow_run_id")), f"{expected_gate}: workflow run identity missing")
        require(data.get("event") in {"push", "workflow_dispatch"}, f"{expected_gate}: unsupported event")
    if expected_gate == "github-windows":
        require(data.get("runner_environment") == "github-hosted-clean-windows", "github-windows: not a hosted clean runner")
        require(data.get("runner_label") in ALLOWED_WINDOWS_RUNNERS, "github-windows: runner label is not allowlisted")
        require(data.get("provider") in ALLOWED_WINDOWS_PROVIDERS, "github-windows: local/self-hosted provider rejected")
        forbidden = ("vtwsl", "local", "self-hosted", "dshwindowsgate", "scheduled-task", "broker")
        serialized = json.dumps(data, ensure_ascii=False).lower()
        require(not any(marker in serialized for marker in forbidden), "github-windows: local gate evidence rejected")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--content", required=True, help="full 40-hex content commit D")
    parser.add_argument("--provenance", required=True, help="full 40-hex provenance commit P")
    parser.add_argument("--receipt", action="append", default=[], metavar="GATE=ABSOLUTE_PATH")
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

        checked = {}
        run_ids = set()
        for gate, path in receipt_paths.items():
            data = check_receipt(path, gate, provenance, tree)
            run_key = (data.get("provider"), str(data.get("run_id")))
            require(run_key not in run_ids, f"duplicate run identity: {run_key[1]}")
            run_ids.add(run_key)
            checked[gate] = str(path)

        result = {
            "status": "verified" if not missing else "configured-unverified",
            "content": content,
            "provenance": provenance,
            "tree": tree,
            "provenance_only": True,
            "receipts": checked,
            "missing_receipts": missing,
            "windows_contract": "github-hosted-clean-runner-only",
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (Blocked, json.JSONDecodeError) as exc:
        print(f"RELEASE_PREFLIGHT_BLOCKED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
