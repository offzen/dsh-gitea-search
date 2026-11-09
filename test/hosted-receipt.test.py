#!/usr/bin/env python3
"""Offline contract tests for the GitHub-hosted schema-3 receipt writer."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

SOURCE = pathlib.Path(__file__).resolve().parent.parent
GOOD_COUNTS = {
    "schema": 2,
    "reporter": "tap",
    "node": {"total": 74, "pass": 70, "fail": 0, "cancelled": 0, "skipped": 4, "todo": 0},
    "python": [{"suite": f"suite-{i}", "ran": True, "ok": True, "tests": 1} for i in range(10)],
    "e2e": {"pass": 31, "fail": 0},
    "node_total": 74, "node_pass": 70, "node_fail": 0,
    "node_cancelled": 0, "node_skipped": 4, "node_todo": 0,
    "python_suites": 10, "node_ok": True, "python_ok": True, "e2e_ok": True,
}


def remove_tree(path: pathlib.Path) -> None:
    def retry(function, target, _exc_info):
        os.chmod(target, 0o700)
        function(target)
    shutil.rmtree(path, onerror=retry)


class HostedReceiptTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = pathlib.Path(tempfile.mkdtemp(prefix="hosted-receipt-test-"))
        self.repo = self.temp / "repo"
        shutil.copytree(
            SOURCE, self.repo,
            ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__", "build", "dist", "*.egg-info",
                                          "gitq", "dsh-search", "mcp-server-gitea-search"),
        )
        self.git("init", "-q")
        self.git("config", "user.name", "Receipt Test")
        self.git("config", "user.email", "receipt@example.invalid")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        self.sha = self.git("rev-parse", "HEAD").strip()
        self.counts = self.temp / "test-counts.json"
        self.write_counts(GOOD_COUNTS)

    def tearDown(self) -> None:
        remove_tree(self.temp)

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.repo, check=True, text=True, capture_output=True).stdout

    def write_counts(self, counts: dict) -> None:
        self.counts.write_text(json.dumps(counts, sort_keys=True) + "\n", encoding="utf-8")

    @staticmethod
    def platform_gate() -> tuple[str, str, str]:
        if sys.platform == "win32":
            return "github-windows", "windows-ci.yml", "windows-latest"
        if sys.platform == "darwin":
            return "github-macos", "ci.yml", "macos-latest"
        return "github-linux", "ci.yml", "ubuntu-latest"

    def writer_env(self, *, repository: str = "example-org/private-hosted", workflow: str | None = None,
                   git_ref: str = "refs/heads/release/test", workflow_ref_ref: str | None = None,
                   dispatch_sha: str | None = None, run_attempt: str = "1",
                   runner_environment: str = "github-hosted", repository_visibility: str = "private") -> dict[str, str]:
        _, default_workflow, _ = self.platform_gate()
        workflow = workflow or default_workflow
        ref_for_workflow = git_ref if workflow_ref_ref is None else workflow_ref_ref
        return {
            **os.environ,
            "GITHUB_ACTIONS": "true",
            "GITHUB_REPOSITORY": repository,
            "GITHUB_REPOSITORY_VISIBILITY": repository_visibility,
            "GITHUB_RUN_ID": "12345",
            "GITHUB_RUN_ATTEMPT": run_attempt,
            "GITHUB_WORKFLOW_REF": f"{repository}/.github/workflows/{workflow}@{ref_for_workflow}",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": git_ref,
            "GTS_DISPATCH_EXACT_SHA": dispatch_sha if dispatch_sha is not None else self.sha,
            "RUNNER_ENVIRONMENT": runner_environment,
            "RUNNER_NAME": "GitHub Actions 1000000001",
            "RUNNER_OS": "Windows" if sys.platform == "win32" else "macOS" if sys.platform == "darwin" else "Linux",
            "RUNNER_ARCH": "X64",
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def run_writer(self, *, repository: str = "example-org/private-hosted", sha: str | None = None,
                   needs: dict[str, str] | None = None, counts: dict | None = None,
                   gate_started_at: str = "2026-11-07T00:00:00Z", env: dict[str, str] | None = None,
                   extra: list[str] | None = None) -> subprocess.CompletedProcess[str]:
        gate, _, runner_label = self.platform_gate()
        self.write_counts(GOOD_COUNTS if counts is None else counts)
        args = [
            sys.executable, ".github/scripts/write-gate-receipt.py",
            "--gate", gate, "--commit", sha or self.sha,
            "--runner-label", runner_label, "--output", str(self.temp / "receipt.json"),
            "--test-counts", str(self.counts),
            "--needs-results", json.dumps(needs if needs is not None else {"test": "success"}),
            "--gate-started-at", gate_started_at,
        ] + (extra or [])
        return subprocess.run(args, cwd=self.repo, env=env or self.writer_env(repository=repository),
                              text=True, capture_output=True)

    def test_writes_schema3_measured_receipt(self) -> None:
        result = self.run_writer()
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads((self.temp / "receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(data["schema"], 3)
        self.assertEqual(data["repository"], "example-org/private-hosted")
        self.assertEqual(data["repository_visibility"], "private")
        self.assertEqual(data["commit"], self.sha)
        self.assertEqual(data["run_attempt"], 1)
        self.assertEqual(data["runner_hosted_environment"], "github-hosted")
        self.assertEqual(data["test_counts"]["node_skipped"], 4)
        self.assertEqual(data["inputs_sha256"]["test-counts.json"], hashlib.sha256(self.counts.read_bytes()).hexdigest())
        self.assertEqual(data["inputs_sha256"]["collect-test-counts.py"],
                         hashlib.sha256((self.repo / "scripts/collect-test-counts.py").read_bytes()).hexdigest())
        self.assertEqual(data["inputs_sha256"]["write-gate-receipt.py"],
                         hashlib.sha256((self.repo / ".github/scripts/write-gate-receipt.py").read_bytes()).hexdigest())
        self.assertNotIn("linux-gate.sh", data["inputs_sha256"])

    def test_records_private_staging_repository_and_rejects_malformed_identity(self) -> None:
        accepted = self.run_writer(repository="example-org/another-private-stage")
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        receipt = json.loads((self.temp / "receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["repository"], "example-org/another-private-stage")
        rejected = self.run_writer(repository="not-owner-slash-repository")
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("owner/name", rejected.stderr)

    def test_rejects_non_private_staging_repository(self) -> None:
        for visibility in ("", "public", "internal"):
            with self.subTest(visibility=visibility):
                result = self.run_writer(env=self.writer_env(repository_visibility=visibility))
                self.assertEqual(result.returncode, 2)
                self.assertIn("visibility must be private", result.stderr)

    def test_rejects_wrong_sha(self) -> None:
        result = self.run_writer(sha="0" * 40)
        self.assertEqual(result.returncode, 2)
        self.assertIn("exact SHA", result.stderr)

    def test_rejects_dispatch_sha_mismatch(self) -> None:
        result = self.run_writer(env=self.writer_env(dispatch_sha="0" * 40))
        self.assertEqual(result.returncode, 2)
        self.assertIn("exact_sha does not match", result.stderr)

    def test_rejects_workflow_identity_or_ref_mismatch(self) -> None:
        result = self.run_writer(env=self.writer_env(workflow_ref_ref="refs/heads/evil"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("workflow identity/ref mismatch", result.stderr)

    def test_rejects_non_hosted_runner(self) -> None:
        result = self.run_writer(env=self.writer_env(runner_environment="self-hosted"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("not github-hosted", result.stderr)

    def test_rejects_bad_run_attempt(self) -> None:
        for value in ("", "0", "true", "1.5"):
            with self.subTest(value=value):
                result = self.run_writer(env=self.writer_env(run_attempt=value))
                self.assertEqual(result.returncode, 2)
                self.assertIn("run id/attempt", result.stderr)

    def test_rejects_failed_upstream_job(self) -> None:
        result = self.run_writer(needs={"test": "success", "package": "failure"})
        self.assertEqual(result.returncode, 2)
        self.assertIn("did not all succeed", result.stderr)

    def test_rejects_missing_needs_results(self) -> None:
        result = self.run_writer(needs={})
        self.assertEqual(result.returncode, 2)
        self.assertIn("non-empty object", result.stderr)

    def test_rejects_schema1_counts(self) -> None:
        result = self.run_writer(counts={"node_total": 74})
        self.assertEqual(result.returncode, 2)
        self.assertIn("schema-2", result.stderr)

    def test_rejects_bool_negative_and_arithmetic_counts(self) -> None:
        cases = [
            {**GOOD_COUNTS, "node_pass": True},
            {**GOOD_COUNTS, "node_skipped": -1},
            {**GOOD_COUNTS, "node_total": 73},
        ]
        for counts in cases:
            with self.subTest(counts=counts):
                result = self.run_writer(counts=counts)
                self.assertEqual(result.returncode, 2)

    def test_rejects_node_or_e2e_failures(self) -> None:
        node_bad = {**GOOD_COUNTS, "node": {**GOOD_COUNTS["node"], "fail": 1}, "node_fail": 1,
                    "node_pass": 69}
        e2e_bad = {**GOOD_COUNTS, "e2e": {"pass": 30, "fail": 1}}
        for counts in (node_bad, e2e_bad):
            with self.subTest(counts=counts):
                result = self.run_writer(counts=counts)
                self.assertEqual(result.returncode, 2)

    def test_rejects_missing_or_future_gate_start(self) -> None:
        for started in ("", "2099-01-01T00:00:00Z"):
            with self.subTest(started=started):
                result = self.run_writer(gate_started_at=started)
                self.assertEqual(result.returncode, 2)
                self.assertIn("--gate-started-at", result.stderr)


if __name__ == "__main__":
    unittest.main()
