#!/usr/bin/env python3
"""Offline contract tests for the GitHub hosted receipt writer."""
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


class HostedReceiptTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = pathlib.Path(tempfile.mkdtemp(prefix="hosted-receipt-test-"))
        self.repo = self.temp / "repo"
        shutil.copytree(SOURCE, self.repo, ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__", "build", "dist", "*.egg-info"))
        self.git("init", "-q")
        self.git("config", "user.name", "Receipt Test")
        self.git("config", "user.email", "receipt@example.invalid")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        self.sha = self.git("rev-parse", "HEAD").strip()

    def tearDown(self) -> None:
        remove_test_tree(self.temp)

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.repo, check=True, text=True, capture_output=True).stdout

    def run_writer(self, *, repository: str = "offzen/dsh-gitea-search", sha: str | None = None) -> subprocess.CompletedProcess[str]:
        on_windows = sys.platform == "win32"
        on_macos = sys.platform == "darwin"
        gate = "github-windows" if on_windows else "github-macos" if on_macos else "github-linux"
        workflow = "windows-ci.yml" if on_windows else "ci.yml"
        runner_label = "windows-latest" if on_windows else "macos-latest" if on_macos else "ubuntu-latest"
        env = {
            **os.environ,
            "GITHUB_ACTIONS": "true",
            "GITHUB_REPOSITORY": repository,
            "GITHUB_RUN_ID": "12345",
            "GITHUB_WORKFLOW_REF": f"offzen/dsh-gitea-search/.github/workflows/{workflow}@refs/heads/release/test",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": "refs/heads/release/test",
            "GTS_DISPATCH_EXACT_SHA": self.sha,
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        return subprocess.run(
            [
                sys.executable, ".github/scripts/write-gate-receipt.py",
                "--gate", gate, "--commit", sha or self.sha,
                "--runner-label", runner_label, "--output", str(self.temp / "receipt.json"),
            ],
            cwd=self.repo, env=env, text=True, capture_output=True,
        )

    def test_writes_bounded_exact_sha_receipt(self) -> None:
        result = self.run_writer()
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads((self.temp / "receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(data["commit"], self.sha)
        self.assertEqual(data["provider"], "github-actions-hosted")
        expected_gate = "github-windows" if sys.platform == "win32" else "github-macos" if sys.platform == "darwin" else "github-linux"
        self.assertEqual(data["gate"], expected_gate)
        self.assertEqual(data["repository"], "offzen/dsh-gitea-search")

    def test_rejects_wrong_repository(self) -> None:
        result = self.run_writer(repository="offzen/orca-mcp")
        self.assertEqual(result.returncode, 2)
        self.assertIn("repository identity mismatch", result.stderr)

    def test_rejects_wrong_sha(self) -> None:
        result = self.run_writer(sha="0" * 40)
        self.assertEqual(result.returncode, 2)
        self.assertIn("exact SHA", result.stderr)

    def test_rejects_dispatch_sha_mismatch(self) -> None:
        on_windows = sys.platform == "win32"
        on_macos = sys.platform == "darwin"
        gate = "github-windows" if on_windows else "github-macos" if on_macos else "github-linux"
        workflow = "windows-ci.yml" if on_windows else "ci.yml"
        runner_label = "windows-latest" if on_windows else "macos-latest" if on_macos else "ubuntu-latest"
        env = {
            **os.environ,
            "GITHUB_ACTIONS": "true",
            "GITHUB_REPOSITORY": "offzen/dsh-gitea-search",
            "GITHUB_RUN_ID": "12345",
            "GITHUB_WORKFLOW_REF": f"offzen/dsh-gitea-search/.github/workflows/{workflow}@refs/heads/release/test",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": "refs/heads/release/test",
            "GTS_DISPATCH_EXACT_SHA": "0" * 40,
        }
        result = subprocess.run(
            [sys.executable, ".github/scripts/write-gate-receipt.py", "--gate", gate,
             "--commit", self.sha, "--runner-label", runner_label,
             "--output", str(self.temp / "receipt.json")],
            cwd=self.repo, env=env, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("exact_sha does not match", result.stderr)

    def test_rejects_wrong_workflow_identity(self) -> None:
        on_windows = sys.platform == "win32"
        gate = "github-windows" if on_windows else "github-linux"
        wrong_workflow = "ci.yml" if on_windows else "windows-ci.yml"
        runner_label = "windows-latest" if on_windows else "ubuntu-latest"
        env = {
            **os.environ,
            "GITHUB_ACTIONS": "true",
            "GITHUB_REPOSITORY": "offzen/dsh-gitea-search",
            "GITHUB_RUN_ID": "12345",
            "GITHUB_WORKFLOW_REF": f"offzen/dsh-gitea-search/.github/workflows/{wrong_workflow}@refs/heads/release/test",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": "refs/heads/release/test",
            "GTS_DISPATCH_EXACT_SHA": self.sha,
        }
        result = subprocess.run(
            [sys.executable, ".github/scripts/write-gate-receipt.py", "--gate", gate,
             "--commit", self.sha, "--runner-label", runner_label,
             "--output", str(self.temp / "receipt.json")],
            cwd=self.repo, env=env, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("workflow identity mismatch", result.stderr)


if __name__ == "__main__":
    unittest.main()
