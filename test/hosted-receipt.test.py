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
GOOD_COUNTS = {"node_total": 74, "node_pass": 70, "node_fail": 0, "python_suites": 2, "e2e": 31}


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
        self.counts = self.temp / "test-counts.json"
        self.counts.write_text(json.dumps(GOOD_COUNTS) + "\n", encoding="utf-8")

    def tearDown(self) -> None:
        remove_test_tree(self.temp)

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.repo, check=True, text=True, capture_output=True).stdout

    @staticmethod
    def platform_gate() -> tuple[str, str, str]:
        on_windows = sys.platform == "win32"
        on_macos = sys.platform == "darwin"
        gate = "github-windows" if on_windows else "github-macos" if on_macos else "github-linux"
        workflow = "windows-ci.yml" if on_windows else "ci.yml"
        runner_label = "windows-latest" if on_windows else "macos-latest" if on_macos else "ubuntu-latest"
        return gate, workflow, runner_label

    def writer_env(self, *, repository: str = "offzen/dsh-gitea-search",
                   workflow: str | None = None, git_ref: str = "refs/heads/release/test",
                   workflow_ref_ref: str | None = None,
                   dispatch_sha: str | None = None) -> dict[str, str]:
        _, default_workflow, _ = self.platform_gate()
        workflow = workflow or default_workflow
        ref_for_workflow = git_ref if workflow_ref_ref is None else workflow_ref_ref
        return {
            **os.environ,
            "GITHUB_ACTIONS": "true",
            "GITHUB_REPOSITORY": repository,
            "GITHUB_RUN_ID": "12345",
            "GITHUB_WORKFLOW_REF": f"offzen/dsh-gitea-search/.github/workflows/{workflow}@{ref_for_workflow}",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": git_ref,
            "GTS_DISPATCH_EXACT_SHA": dispatch_sha if dispatch_sha is not None else self.sha,
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def run_writer(self, *, repository: str = "offzen/dsh-gitea-search", sha: str | None = None,
                   needs: dict[str, str] | None = None, counts: dict | None = None,
                   gate_started_at: str = "2026-11-07T00:00:00Z",
                   env: dict[str, str] | None = None,
                   extra: list[str] | None = None) -> subprocess.CompletedProcess[str]:
        gate, _, runner_label = self.platform_gate()
        needs = needs if needs is not None else {"test": "success"}
        if counts is not None:
            self.counts.write_text(json.dumps(counts) + "\n", encoding="utf-8")
        else:
            self.counts.write_text(json.dumps(GOOD_COUNTS) + "\n", encoding="utf-8")
        args = [
            sys.executable, ".github/scripts/write-gate-receipt.py",
            "--gate", gate, "--commit", sha or self.sha,
            "--runner-label", runner_label, "--output", str(self.temp / "receipt.json"),
            "--test-counts", str(self.counts),
            "--needs-results", json.dumps(needs),
            "--gate-started-at", gate_started_at,
        ]
        args += extra or []
        return subprocess.run(args, cwd=self.repo, env=env or self.writer_env(repository=repository),
                              text=True, capture_output=True)

    def test_writes_bounded_exact_sha_receipt(self) -> None:
        result = self.run_writer()
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads((self.temp / "receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(data["commit"], self.sha)
        self.assertEqual(data["provider"], "github-actions-hosted")
        expected_gate, _, _ = self.platform_gate()
        self.assertEqual(data["gate"], expected_gate)
        self.assertEqual(data["repository"], "offzen/dsh-gitea-search")
        # rc6: schema 2, derived status, real timestamps, recorded counts.
        self.assertEqual(data["schema"], 2)
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["needs"], {"test": "success"})
        self.assertEqual(data["test_counts"]["node_total"], GOOD_COUNTS["node_total"])
        self.assertGreaterEqual(data["duration_seconds"], 1)
        self.assertEqual(data["gate_started_at"], "2026-11-07T00:00:00+00:00")
        self.assertNotEqual(data["started_at"], data["ended_at"])

    def test_rejects_wrong_repository(self) -> None:
        result = self.run_writer(repository="offzen/orca-mcp")
        self.assertEqual(result.returncode, 2)
        self.assertIn("repository identity mismatch", result.stderr)

    def test_rejects_wrong_sha(self) -> None:
        result = self.run_writer(sha="0" * 40)
        self.assertEqual(result.returncode, 2)
        self.assertIn("exact SHA", result.stderr)

    def test_rejects_dispatch_sha_mismatch(self) -> None:
        result = self.run_writer(env=self.writer_env(dispatch_sha="0" * 40))
        self.assertEqual(result.returncode, 2)
        self.assertIn("exact_sha does not match", result.stderr)

    def test_rejects_wrong_workflow_identity(self) -> None:
        # The receipt writer checks the platform before the workflow identity, so the
        # gate under test must match the platform this suite runs on — otherwise the
        # run fails with "gate/platform mismatch" and never reaches the assertion.
        on_windows = sys.platform == "win32"
        wrong_workflow = "ci.yml" if on_windows else "windows-ci.yml"
        result = self.run_writer(env=self.writer_env(workflow=wrong_workflow))
        self.assertEqual(result.returncode, 2)
        self.assertIn("workflow identity mismatch", result.stderr)

    # ---- rc6 hardening -----------------------------------------------------------

    def test_rejects_workflow_ref_pointing_at_another_ref(self) -> None:
        """F2: the workflow that minted the receipt must be running at the claimed ref."""
        result = self.run_writer(env=self.writer_env(workflow_ref_ref="refs/heads/evil-side-branch"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("workflow_ref ref does not match the running ref", result.stderr)

    def test_rejects_failed_upstream_job(self) -> None:
        """F3: status is derived from the needs graph, so a red need blocks the receipt."""
        result = self.run_writer(needs={"test": "success", "package": "failure"})
        self.assertEqual(result.returncode, 2)
        self.assertIn("upstream jobs did not all succeed", result.stderr)

    def test_rejects_missing_needs_results(self) -> None:
        """F3: an empty needs map would make status unverifiable."""
        result = self.run_writer(needs={})
        self.assertEqual(result.returncode, 2)
        self.assertIn("non-empty object", result.stderr)

    def test_rejects_failing_test_counts(self) -> None:
        """F8: a suite with failures must not produce a receipt."""
        result = self.run_writer(counts={**GOOD_COUNTS, "node_fail": 3})
        self.assertEqual(result.returncode, 2)
        self.assertIn("node failures", result.stderr)

    def test_rejects_incomplete_test_counts(self) -> None:
        """F8: every floor key must be present."""
        result = self.run_writer(counts={"node_total": 74, "node_pass": 70})
        self.assertEqual(result.returncode, 2)
        self.assertIn("must be an integer", result.stderr)

    def test_rejects_missing_gate_start(self) -> None:
        """F3: gate start is mandatory — there is no now() fallback any more."""
        result = self.run_writer(extra=[], gate_started_at="")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--gate-started-at", result.stderr)

    def test_rejects_future_gate_start(self) -> None:
        """F3: a start time in the future cannot yield a meaningful duration."""
        result = self.run_writer(gate_started_at="2099-01-01T00:00:00Z")
        self.assertEqual(result.returncode, 2)
        self.assertIn("not in the past", result.stderr)


if __name__ == "__main__":
    unittest.main()
