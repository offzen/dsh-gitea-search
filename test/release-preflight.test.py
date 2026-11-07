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
            "schema": 2,
            "gate": gate,
            "provider": "github-actions-hosted" if hosted else "gitea-actions",
            "runner_environment": (
                "github-hosted-clean-windows" if gate == "github-windows"
                else "clean-linux-runner" if gate == "gate2-linux"
                else "github-hosted-clean-runner"
            ),
            "repository": ("offzen/dsh-gitea-search" if hosted else "zaoz/dsh-gitea-search"),
            "commit": self.provenance,
            "tree": self.tree,
            "status": "success",
            "started_at": "2026-11-01T00:00:00Z",
            "gate_started_at": "2026-11-01T00:00:00Z",
            "ended_at": "2026-11-01T00:01:00Z",
            "duration_seconds": 60,
            "run_id": "run-" + gate,
            "test_counts": {
                "node_total": 74, "node_pass": 70, "node_fail": 0,
                "python_suites": 2,
                # Windows measures e2e in the separate windows-git-bash job, so the
                # receipt job records 0 and the verifier requires that job via `needs`.
                "e2e": 0 if gate == "github-windows" else 31,
            },
            "checks": ["checkout-exact-sha", "test-counts-floors"],
            "toolchain": {
                "os": ("Windows-2025Server" if gate == "github-windows"
                       else "macOS-26.7.2" if gate == "github-macos"
                       else "Linux-7.0.0"),
                "python": "3.12", "node": "v22.23.4", "git": "2.43.0",
            },
        }
        if hosted:
            data.update({
                "workflow": ".github/workflows/windows-ci.yml" if gate == "github-windows" else ".github/workflows/ci.yml",
                "workflow_run_id": "workflow-" + gate,
                "event": "workflow_dispatch",
                "ref": "refs/heads/release/test",
                "runner_label": (
                    "windows-latest" if gate == "github-windows"
                    else "macos-latest" if gate == "github-macos"
                    else "ubuntu-latest"
                ),
                "needs": (
                    {"windows-git-bash": "success", "windows-pe": "success", "windows-ps": "success",
                     "windows-tests": "success", "windows-grep-crlf": "success"}
                    if gate == "github-windows" else {"test": "success"}
                ),
            })
            data["workflow_ref"] = (
                f"offzen/dsh-gitea-search/{data['workflow']}@{data['ref']}"
            )
        else:
            data.update({
                "trust": "same-party",
                "workflow": ".gitea/workflows/ci.yml",
                "event": "workflow_dispatch",
                "ref": "refs/heads/release/test",
            })
        return data

    def write_all_receipts(self) -> None:
        for gate in ("gate2-linux", "github-linux", "github-macos", "github-windows"):
            (self.receipts / f"{gate}.json").write_text(json.dumps(self.receipt(gate)) + "\n", encoding="utf-8")

    def remote_evidence(self, gate: str) -> dict:
        receipt = self.receipt(gate)
        return {
            "gate": gate,
            "run_id": receipt.get("workflow_run_id") or receipt["run_id"],
            "head_sha": self.provenance,
            "conclusion": "success",
            "jobs": [{"name": "unit", "conclusion": "success"}],
        }

    def write_remote_evidence(self) -> None:
        for gate in ("gate2-linux", "github-linux", "github-macos", "github-windows"):
            (self.receipts / f"remote-{gate}.json").write_text(
                json.dumps(self.remote_evidence(gate)) + "\n", encoding="utf-8")

    def patch_receipt(self, gate: str, **changes: object) -> None:
        path = self.receipts / f"{gate}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(changes)
        path.write_text(json.dumps(data) + "\n", encoding="utf-8")

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

    def run_preflight_with_evidence(self, *extra: str) -> subprocess.CompletedProcess[str]:
        self.write_remote_evidence()
        args = ["--require-remote-evidence"]
        for gate in ("gate2-linux", "github-linux", "github-macos", "github-windows"):
            args += ["--remote-evidence", f"{gate}={self.receipts / f'remote-{gate}.json'}"]
        return self.run_preflight(*(args + list(extra)))

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

    # ---- rc6 hardening: one test per audit finding -------------------------------

    def test_accepts_with_complete_remote_evidence(self) -> None:
        """F1: forge-side evidence must be accepted when it agrees with the receipts."""
        result = self.run_preflight_with_evidence()
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "verified")
        self.assertTrue(payload["remote_evidence_complete"])

    def test_rejects_remote_evidence_with_wrong_sha(self) -> None:
        """F1: evidence about a different commit must not verify this candidate."""
        self.write_remote_evidence()
        path = self.receipts / "remote-github-macos.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["head_sha"] = "0" * 40
        path.write_text(json.dumps(data) + "\n", encoding="utf-8")
        args = ["--require-remote-evidence"]
        for gate in ("gate2-linux", "github-linux", "github-macos", "github-windows"):
            args += ["--remote-evidence", f"{gate}={self.receipts / f'remote-{gate}.json'}"]
        result = self.run_preflight(*args)
        self.assertEqual(result.returncode, 2)
        self.assertIn("remote evidence head_sha is not exact P", result.stderr)

    def test_rejects_remote_evidence_with_failed_job(self) -> None:
        """F1: one red job in the forge record must block, even if the receipt says success."""
        self.write_remote_evidence()
        path = self.receipts / "remote-github-linux.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["jobs"].append({"name": "e2e", "conclusion": "failure"})
        path.write_text(json.dumps(data) + "\n", encoding="utf-8")
        args = ["--require-remote-evidence"]
        for gate in ("gate2-linux", "github-linux", "github-macos", "github-windows"):
            args += ["--remote-evidence", f"{gate}={self.receipts / f'remote-{gate}.json'}"]
        result = self.run_preflight(*args)
        self.assertEqual(result.returncode, 2)
        self.assertIn("concluded 'failure'", result.stderr)

    def test_requires_remote_evidence_for_every_gate(self) -> None:
        """F1: --require-remote-evidence must not pass with a partial evidence set."""
        self.write_remote_evidence()
        result = self.run_preflight("--require-remote-evidence",
                                    "--remote-evidence", f"github-linux={self.receipts / 'remote-github-linux.json'}")
        self.assertEqual(result.returncode, 2)
        self.assertIn("missing remote evidence", result.stderr)

    def test_rejects_unallowlisted_runner_label(self) -> None:
        """F4: linux/macos runner labels are now checked, not only windows."""
        self.patch_receipt("github-macos", runner_label="ubuntu-latest")
        result = self.run_preflight()
        self.assertEqual(result.returncode, 2)
        self.assertIn("runner_label not allowlisted", result.stderr)

    def test_rejects_wrong_platform_family(self) -> None:
        """F7: a macOS receipt claiming a Linux toolchain must be rejected."""
        self.patch_receipt("github-macos", toolchain={
            "os": "Linux-7.0.0", "python": "3.12", "node": "v22.23.4", "git": "2.43.0"})
        result = self.run_preflight()
        self.assertEqual(result.returncode, 2)
        self.assertIn("platform family", result.stderr)

    def test_rejects_weakened_suite(self) -> None:
        """F8: a suite that shrank below the floors must not verify."""
        self.patch_receipt("gate2-linux", test_counts={
            "node_total": 3, "node_pass": 3, "node_fail": 0, "python_suites": 2, "e2e": 31})
        result = self.run_preflight()
        self.assertEqual(result.returncode, 2)
        self.assertIn("below floor", result.stderr)

    def test_rejects_missing_test_counts(self) -> None:
        """F8: a receipt without counts carries no evidence that anything ran."""
        path = self.receipts / "github-windows.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop("test_counts")
        path.write_text(json.dumps(data) + "\n", encoding="utf-8")
        result = self.run_preflight()
        self.assertEqual(result.returncode, 2)
        self.assertIn("test_counts missing", result.stderr)

    def test_rejects_failed_need(self) -> None:
        """F3: status must follow the needs graph, so a failed upstream job blocks."""
        self.patch_receipt("github-linux", needs={"test": "success", "package": "failure"})
        result = self.run_preflight()
        self.assertEqual(result.returncode, 2)
        self.assertIn("upstream jobs not all successful", result.stderr)

    def test_rejects_workflow_ref_on_other_ref(self) -> None:
        """F2: a run from a side branch must not mint a receipt for the released ref."""
        self.patch_receipt(
            "github-macos",
            workflow_ref="offzen/dsh-gitea-search/.github/workflows/ci.yml@refs/heads/evil-side-branch",
        )
        result = self.run_preflight()
        self.assertEqual(result.returncode, 2)
        self.assertIn("workflow_ref ref does not match receipt ref", result.stderr)

    def test_rejects_run_shared_across_workflows(self) -> None:
        """F5: two different workflows must not report the same run identity."""
        self.patch_receipt("github-windows", workflow_run_id="workflow-github-linux")
        result = self.run_preflight()
        self.assertEqual(result.returncode, 2)
        self.assertIn("shared across different workflows", result.stderr)

    def test_allows_run_shared_within_one_workflow(self) -> None:
        """F5: linux and macos legitimately come from the same ci.yml run."""
        self.patch_receipt("github-macos", workflow_run_id="workflow-github-linux")
        result = self.run_preflight()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_zero_duration_gate(self) -> None:
        """F3: a gate_started_at that equals ended_at is the old now() fallback."""
        self.patch_receipt("github-linux", duration_seconds=0)
        result = self.run_preflight()
        self.assertEqual(result.returncode, 2)
        self.assertIn("duration_seconds < 1s", result.stderr)

    def test_rejects_untracked_files(self) -> None:
        """F10: git diff cannot see untracked files, so they are rejected explicitly."""
        (self.repo / "leftover.txt").write_text("untracked\n", encoding="utf-8")
        result = self.run_preflight()
        self.assertEqual(result.returncode, 2)
        self.assertIn("untracked files", result.stderr)

    def test_trust_anchor_must_match(self) -> None:
        """F9: the in-tree verifier must equal an externally pinned copy."""
        anchor = self.temp / "pinned-verifier.py"
        shutil.copy2(self.repo / "scripts" / "release-preflight.py", anchor)
        ok = self.run_preflight("--trust-anchor", str(anchor))
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout)["trust_anchor_sha256"],
                         __import__("hashlib").sha256(anchor.read_bytes()).hexdigest())

        anchor.write_text(anchor.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
        bad = self.run_preflight("--trust-anchor", str(anchor))
        self.assertEqual(bad.returncode, 2)
        self.assertIn("differs from trust anchor", bad.stderr)

    def test_rejects_undeclared_same_party_gate(self) -> None:
        """F6: gate2 must declare that it is same-party regression coverage."""
        self.patch_receipt("gate2-linux", trust="independent")
        result = self.run_preflight()
        self.assertEqual(result.returncode, 2)
        self.assertIn("trust must be declared as same-party", result.stderr)

    def test_rejects_windows_receipt_without_e2e_attestation(self) -> None:
        """F8 on Windows: e2e coverage comes from the windows-git-bash job, so the
        receipt must name it as a successful need."""
        self.patch_receipt("github-windows", needs={"windows-pe": "success", "windows-ps": "success"})
        result = self.run_preflight()
        self.assertEqual(result.returncode, 2)
        self.assertIn("needs must include windows-git-bash=success", result.stderr)

    def test_rejects_windows_receipt_claiming_e2e_counts(self) -> None:
        """F8 on Windows: the receipt job does not run e2e, so claiming a count is
        an unmeasured assertion."""
        self.patch_receipt("github-windows", test_counts={
            "node_total": 74, "node_pass": 70, "node_fail": 0, "python_suites": 2, "e2e": 31})
        result = self.run_preflight()
        self.assertEqual(result.returncode, 2)
        self.assertIn("does not run e2e itself", result.stderr)

    def test_gate2_names_the_gitea_repository(self) -> None:
        """gate2 runs on gitorca (private primary), so its receipt must name that
        repository — not the GitHub mirror. Requiring one name for both made a
        truthful gate2 receipt fail (2026-11-07)."""
        self.patch_receipt("gate2-linux", repository="offzen/dsh-gitea-search")
        result = self.run_preflight()
        self.assertEqual(result.returncode, 2)
        self.assertIn("repository mismatch (want zaoz/dsh-gitea-search)", result.stderr)

    def test_github_gate_rejects_the_gitea_repository(self) -> None:
        """The mirror identity must not be interchangeable either."""
        self.patch_receipt("github-linux", repository="zaoz/dsh-gitea-search")
        result = self.run_preflight()
        self.assertEqual(result.returncode, 2)
        self.assertIn("repository mismatch (want offzen/dsh-gitea-search)", result.stderr)


if __name__ == "__main__":
    unittest.main()
