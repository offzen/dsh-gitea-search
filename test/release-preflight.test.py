#!/usr/bin/env python3
"""Offline negative/positive fixtures for scripts/release-preflight.py."""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

SOURCE = pathlib.Path(__file__).resolve().parent.parent


def remove_test_tree(path: pathlib.Path) -> None:
    def clear_readonly_and_retry(function, target, _exc_info):
        os.chmod(target, 0o700)
        function(target)

    shutil.rmtree(path, onerror=clear_readonly_and_retry)


class ReleasePreflightTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = pathlib.Path(tempfile.mkdtemp(prefix="release-preflight-test-"))
        self.repo = self.temp / "repo"
        shutil.copytree(SOURCE, self.repo, ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__", "build", "dist", "*.egg-info"))
        self.git("init", "-q")
        self.git("config", "user.name", "Preflight Test")
        self.git("config", "user.email", "preflight@example.invalid")
        self.git("add", ".")
        self.git("commit", "-qm", "content D")
        self.content = self.git("rev-parse", "HEAD").strip()
        subprocess.run(
            [sys.executable, "scripts/generate-provenance.py", "--source-commit", self.content],
            cwd=self.repo, check=True, text=True, capture_output=True,
        )
        self.git("add", "PROVENANCE.json")
        self.git("commit", "-qm", "provenance P")
        self.provenance = self.git("rev-parse", "HEAD").strip()
        self.tree = self.git("rev-parse", "HEAD^{tree}").strip()
        self.receipts = self.temp / "receipts"
        self.receipts.mkdir()
        self.write_all_receipts()

    def tearDown(self) -> None:
        remove_test_tree(self.temp)

    def git(self, *args: str) -> str:
        result = subprocess.run(["git", *args], cwd=self.repo, check=True, text=True, capture_output=True)
        return result.stdout

    def receipt(self, gate: str) -> dict:
        hosted = gate.startswith("github-")
        data = {
            "schema": 1,
            "gate": gate,
            "provider": "github-actions-hosted" if hosted else "gitea-actions",
            "runner_environment": (
                "github-hosted-clean-windows" if gate == "github-windows"
                else "clean-linux-runner" if gate == "gate2-linux"
                else "github-hosted-clean-runner"
            ),
            "commit": self.provenance,
            "tree": self.tree,
            "status": "success",
            "started_at": "2026-11-01T00:00:00Z",
            "ended_at": "2026-11-01T00:01:00Z",
            "run_id": "run-" + gate,
            "toolchain": {"os": gate, "python": "3.12", "node": "22"},
        }
        if hosted:
            data.update({
                "repository": "offzen/dsh-gitea-search",
                "workflow": ".github/workflows/windows-ci.yml" if gate == "github-windows" else ".github/workflows/ci.yml",
                "workflow_run_id": "workflow-" + gate,
                "event": "workflow_dispatch",
            })
        if gate == "github-windows":
            data["runner_label"] = "windows-latest"
        return data

    def write_all_receipts(self) -> None:
        for gate in ("gate2-linux", "github-linux", "github-macos", "github-windows"):
            (self.receipts / f"{gate}.json").write_text(json.dumps(self.receipt(gate)) + "\n", encoding="utf-8")

    def run_preflight(self, *extra: str) -> subprocess.CompletedProcess[str]:
        args = [
            sys.executable, "scripts/release-preflight.py",
            "--content", self.content,
            "--provenance", self.provenance,
        ]
        for gate in ("gate2-linux", "github-linux", "github-macos", "github-windows"):
            args += ["--receipt", f"{gate}={self.receipts / f'{gate}.json'}"]
        args += list(extra)
        return subprocess.run(args, cwd=self.repo, text=True, capture_output=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})

    def test_accepts_exact_dp_and_hosted_receipts(self) -> None:
        result = self.run_preflight()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "verified")

    def test_rejects_local_gate2_receipt(self) -> None:
        path = self.receipts / "gate2-linux.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["provider"] = "local-offline"
        path.write_text(json.dumps(data) + "\n", encoding="utf-8")
        result = self.run_preflight()
        self.assertEqual(result.returncode, 2)
        self.assertIn("provider is not Gitea Actions", result.stderr)

    def test_rejects_wrong_exact_sha(self) -> None:
        path = self.receipts / "github-windows.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["commit"] = "0" * 40
        path.write_text(json.dumps(data) + "\n", encoding="utf-8")
        result = self.run_preflight()
        self.assertEqual(result.returncode, 2)
        self.assertIn("exact P mismatch", result.stderr)

    def test_rejects_local_windows_receipt(self) -> None:
        path = self.receipts / "github-windows.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["provider"] = "vtwsl-local-gate"
        data["runner_environment"] = "local-windows"
        path.write_text(json.dumps(data) + "\n", encoding="utf-8")
        result = self.run_preflight()
        self.assertEqual(result.returncode, 2)
        self.assertIn("provider is not GitHub hosted", result.stderr)

    def test_rejects_missing_receipt(self) -> None:
        (self.receipts / "github-macos.json").unlink()
        result = self.run_preflight()
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid receipt", result.stderr)

    def test_rejects_non_provenance_only_tip(self) -> None:
        (self.repo / "README.md").write_text("not provenance-only\n", encoding="utf-8")
        self.git("add", "README.md")
        self.git("commit", "-qm", "bad P")
        bad_p = self.git("rev-parse", "HEAD").strip()
        args = [sys.executable, "scripts/release-preflight.py", "--content", self.provenance, "--provenance", bad_p, "--allow-partial"]
        result = subprocess.run(args, cwd=self.repo, text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("only PROVENANCE.json", result.stderr)


if __name__ == "__main__":
    unittest.main()
