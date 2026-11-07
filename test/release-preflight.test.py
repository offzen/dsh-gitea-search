#!/usr/bin/env python3
"""Offline positive and negative contracts for the rc7 release preflight."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

SOURCE = pathlib.Path(__file__).resolve().parent.parent
GATES = ("gate2-linux", "github-linux", "github-macos", "github-windows")
REF = "refs/heads/release/test"
WORKFLOWS = {
    "gate2-linux": ".gitea/workflows/ci.yml",
    "github-linux": ".github/workflows/ci.yml",
    "github-macos": ".github/workflows/ci.yml",
    "github-windows": ".github/workflows/windows-ci.yml",
}
REQUIRED_NEEDS = {
    "github-linux": {"test": "success", "package": "success"},
    "github-macos": {"test-macos": "success"},
    "github-windows": {
        "windows-pe": "success", "windows-ps": "success", "windows-tests": "success",
        "windows-git-bash": "success", "windows-grep-crlf": "success",
    },
}
JOB_NAMES = {
    "gate2-linux": ["Exact-SHA Linux gate"],
    "github-linux": [
        *(f"Unit + E2E (Ubuntu, py{py}, node{node})" for py in ("3.11", "3.12") for node in ("20", "22")),
        "Python wheel + sdist (Ubuntu, py3.12)", "Hosted Linux exact-SHA receipt",
    ],
    "github-macos": ["Unit + E2E (macOS, py3.12, node22)", "Hosted macOS exact-SHA receipt"],
    "github-windows": [
        "W5 gts.exe 是真实 PE 文件", "W6 setup-windows.ps1 PS5.1 + PS7 WhatIf",
        *(f"W7/W8 运行时行为（py{py} / node{node}）" for py in ("3.11", "3.12") for node in ("20", "22")),
        "W10 Git Bash full E2E", "W9 git grep 的 CRLF 与路径分隔符行为",
        "Final exact-SHA hosted Windows receipt",
    ],
}


def remove_tree(path: pathlib.Path) -> None:
    def retry(function, target, _exc_info):
        os.chmod(target, 0o700)
        function(target)
    shutil.rmtree(path, onerror=retry)


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ReleasePreflightTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = pathlib.Path(tempfile.mkdtemp(prefix="release-preflight-test-"))
        self.repo = self.temp / "repo"
        shutil.copytree(
            SOURCE, self.repo,
            ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__", "build", "dist", "*.egg-info",
                                          "gitq", "dsh-search", "mcp-server-gitea-search"),
        )
        self.git("init", "-q")
        self.git("config", "user.name", "Preflight Test")
        self.git("config", "user.email", "preflight@example.invalid")
        self.git("add", ".")
        self.git("commit", "-qm", "content D")
        self.content = self.git("rev-parse", "HEAD").strip()
        subprocess.run([sys.executable, "scripts/generate-provenance.py", "--source-commit", self.content],
                       cwd=self.repo, check=True, capture_output=True, text=True)
        self.git("add", "PROVENANCE.json")
        self.git("commit", "-qm", "provenance P")
        self.provenance = self.git("rev-parse", "HEAD").strip()
        self.tree = self.git("rev-parse", "HEAD^{tree}").strip()
        self.data = self.temp / "data"
        self.data.mkdir()
        self.anchor = self.temp / "anchor"
        self.anchor.mkdir()
        for name in ("release-preflight.py", "check-provenance.py", "fetch-remote-evidence.py", "collect-test-counts.py"):
            shutil.copy2(self.repo / "scripts" / name, self.anchor / name)
        self.write_contract_inputs()

    def tearDown(self) -> None:
        remove_tree(self.temp)

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True, text=True).stdout

    def counts(self, gate: str) -> dict:
        return {
            "node_total": 74, "node_pass": 70, "node_fail": 0, "node_cancelled": 0,
            "node_skipped": 4, "node_todo": 0, "python_suites": 9,
            "e2e": 0 if gate == "github-windows" else 31,
        }

    def receipt(self, gate: str, counts_raw: bytes) -> dict:
        hosted = gate.startswith("github-")
        repo = "offzen/dsh-gitea-search" if hosted else "zaoz/dsh-gitea-search"
        workflow = WORKFLOWS[gate]
        result = {
            "schema": 3, "gate": gate,
            "provider": "github-actions-hosted" if hosted else "gitea-actions",
            "runner_environment": ("github-hosted-clean-windows" if gate == "github-windows"
                                   else "github-hosted-clean-runner" if hosted else "clean-linux-runner"),
            "repository": repo, "commit": self.provenance, "tree": self.tree, "status": "success",
            "started_at": "2026-11-07T00:00:00Z", "gate_started_at": "2026-11-07T00:00:00Z",
            "ended_at": "2026-11-07T00:01:00Z", "duration_seconds": 60,
            "run_id": str(900 if not hosted else 1000 + GATES.index(gate)) + (":" + gate if hosted else ""),
            "run_attempt": 1, "workflow": workflow,
            "workflow_ref": f"{repo}/{workflow}@{REF}", "event": "push", "ref": REF,
            "inputs_sha256": {
                "test-counts.json": hashlib.sha256(counts_raw).hexdigest(),
                "collect-test-counts.py": sha(self.repo / "scripts/collect-test-counts.py"),
                **({"write-gate-receipt.py": sha(self.repo / ".github/scripts/write-gate-receipt.py")} if hosted
                   else {"linux-gate.sh": sha(self.repo / "scripts/ci/linux-gate.sh")}),
            },
            "test_counts": self.counts(gate),
            "checks": ["schema-3-contract"],
            "toolchain": {
                "os": "Windows-2025" if gate == "github-windows" else "macOS-15" if gate == "github-macos" else "Linux-6",
                "python": "3.12", "node": "v22", "git": "2.43",
            },
        }
        if hosted:
            result.update({
                "workflow_run_id": str(1000 + GATES.index(gate)),
                "runner_label": "windows-latest" if gate == "github-windows" else "macos-latest" if gate == "github-macos" else "ubuntu-latest",
                "needs": REQUIRED_NEEDS[gate],
                "runner_hosted_environment": "github-hosted",
                "runner_name": "GitHub Actions 1000000001",
                "runner_os": "Windows" if gate == "github-windows" else "macOS" if gate == "github-macos" else "Linux",
                "runner_arch": "X64",
            })
        else:
            result["trust"] = "same-party"
        return result

    def evidence(self, gate: str, artifact_digest: str | None) -> dict:
        hosted = gate.startswith("github-")
        label = "windows-latest" if gate == "github-windows" else "macos-latest" if gate == "github-macos" else "ubuntu-latest"
        jobs = [{
            "name": name, "conclusion": "success", "status": "completed",
            "labels": [label] if hosted else [], "runner_name": "GitHub Actions 1000000001" if hosted else "gitea-runner",
            "runner_group_name": "GitHub Actions" if hosted else None,
        } for name in JOB_NAMES[gate]]
        artifacts = [] if artifact_digest is None else [{
            "name": f"{gate}-{self.provenance}", "digest": "sha256:" + artifact_digest,
            "size_in_bytes": (self.data / f"{gate}.zip").stat().st_size, "expired": False,
        }]
        return {
            "schema": 2, "forge": "github" if hosted else "gitea", "gate": gate,
            "provider": "github-actions-hosted" if hosted else "gitea-actions",
            "repository": "offzen/dsh-gitea-search" if hosted else "zaoz/dsh-gitea-search",
            "workflow_path": WORKFLOWS[gate], "workflow_name": "CI", "event": "push",
            "head_branch": "release/test", "run_attempt": 1 if hosted else None,
            "head_sha": self.provenance, "conclusion": "success", "status": "completed",
            "run_id": str(1000 + GATES.index(gate)) if hosted else "900",
            "jobs": jobs, "jobs_total_count": len(jobs), "artifacts": artifacts,
            "artifacts_total_count": len(artifacts), "artifacts_available": hosted,
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        }

    def write_contract_inputs(self) -> None:
        for gate in GATES:
            counts_raw = (json.dumps(self.counts(gate), sort_keys=True) + "\n").encode()
            (self.data / f"counts-{gate}.json").write_bytes(counts_raw)
            receipt = self.receipt(gate, counts_raw)
            receipt_raw = (json.dumps(receipt, sort_keys=True) + "\n").encode()
            (self.data / f"{gate}.json").write_bytes(receipt_raw)
            digest = None
            if gate.startswith("github-"):
                with zipfile.ZipFile(self.data / f"{gate}.zip", "w") as archive:
                    archive.writestr(f"{gate}.json", receipt_raw)
                    archive.writestr("test-counts.json", counts_raw)
                digest = sha(self.data / f"{gate}.zip")
            (self.data / f"evidence-{gate}.json").write_text(
                json.dumps(self.evidence(gate, digest), sort_keys=True) + "\n", encoding="utf-8")

    def patch_json(self, name: str, mutate) -> None:
        path = self.data / name
        value = json.loads(path.read_text(encoding="utf-8"))
        mutate(value)
        path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")

    def run_preflight(self, *, full: bool = False, extra: list[str] | None = None,
                      env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        args = [sys.executable, "scripts/release-preflight.py", "--content", self.content, "--provenance", self.provenance]
        for gate in GATES:
            args += ["--receipt", f"{gate}={self.data / f'{gate}.json'}"]
        if full:
            args += ["--expected-ref", REF, "--trust-anchor", str(self.anchor)]
            for gate in GATES:
                args += ["--remote-evidence", f"{gate}={self.data / f'evidence-{gate}.json'}"]
                if gate.startswith("github-"):
                    args += ["--artifact", f"{gate}={self.data / f'{gate}.zip'}"]
        args += extra or []
        return subprocess.run(args, cwd=self.repo, capture_output=True, text=True,
                              env=env or {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})

    def test_full_contract_verifies_and_default_fails_closed(self) -> None:
        result = self.run_preflight(full=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "verified")
        self.assertEqual(payload["trust_ceiling"], "same-party")
        self.assertTrue(all(payload["artifact_digest_bound"].get(g, False) for g in GATES[1:]))
        partial = self.run_preflight()
        self.assertEqual(partial.returncode, 0, partial.stderr)
        payload = json.loads(partial.stdout)
        self.assertEqual(payload["status"], "configured-unverified")
        self.assertTrue(payload["blocked_reasons"])

    def test_rejects_receipt_identity_and_expected_ref_mismatches(self) -> None:
        cases = [
            ("github-linux.json", lambda d: d.update(commit="0" * 40), "exact P mismatch"),
            ("github-macos.json", lambda d: d.update(workflow_ref="offzen/dsh-gitea-search/.github/workflows/ci.yml@refs/heads/evil"), "workflow_ref ref"),
            ("gate2-linux.json", lambda d: d.update(repository="offzen/dsh-gitea-search"), "repository mismatch"),
        ]
        for name, mutation, message in cases:
            with self.subTest(name=name):
                self.write_contract_inputs(); self.patch_json(name, mutation)
                result = self.run_preflight(full=True)
                self.assertEqual(result.returncode, 2); self.assertIn(message, result.stderr)

    def test_rejects_bad_counts(self) -> None:
        cases = [
            (lambda c: c.update(node_pass=True), "not bool"),
            (lambda c: c.update(node_skipped=-1), "non-negative"),
            (lambda c: c.update(node_total=73), "arithmetic inconsistent"),
            (lambda c: c.update(node_pass=63, node_skipped=11), "below floor"),
        ]
        for mutation, message in cases:
            with self.subTest(message=message):
                self.write_contract_inputs()
                self.patch_json("gate2-linux.json", lambda d: mutation(d["test_counts"]))
                result = self.run_preflight(full=True)
                self.assertEqual(result.returncode, 2); self.assertIn(message, result.stderr)

    def test_rejects_deep_evidence_mismatches(self) -> None:
        cases = [
            (lambda d: d.update(repository="other/repo"), "repository"),
            (lambda d: d.update(workflow_path=".github/workflows/other.yml"), "workflow_path"),
            (lambda d: d.update(event="pull_request"), "event"),
            (lambda d: d.update(run_attempt=2), "run_attempt"),
            (lambda d: d.update(jobs_total_count=len(d["jobs"]) + 1), "jobs_total_count"),
            (lambda d: d["jobs"][0]["labels"].append("self-hosted"), "self-hosted"),
        ]
        for mutation, message in cases:
            with self.subTest(message=message):
                self.write_contract_inputs(); self.patch_json("evidence-github-linux.json", mutation)
                result = self.run_preflight(full=True)
                self.assertEqual(result.returncode, 2); self.assertIn(message, result.stderr)

    def test_rejects_artifact_digest_and_receipt_byte_mismatches(self) -> None:
        self.patch_json("evidence-github-linux.json", lambda d: d["artifacts"][0].update(digest="sha256:" + "0" * 64))
        result = self.run_preflight(full=True)
        self.assertEqual(result.returncode, 2); self.assertIn("artifact digest mismatch", result.stderr)
        self.write_contract_inputs()
        self.patch_json("github-linux.json", lambda d: d["checks"].append("post-zip-change"))
        result = self.run_preflight(full=True)
        self.assertEqual(result.returncode, 2); self.assertIn("receipt bytes differ", result.stderr)

    def test_anchor_rejects_symlink_inside_and_tamper(self) -> None:
        link = self.temp / "anchor-link"; link.symlink_to(self.anchor, target_is_directory=True)
        result = self.run_preflight(extra=["--trust-anchor", str(link), "--allow-missing-evidence"])
        self.assertEqual(result.returncode, 2); self.assertIn("must not be a symlink", result.stderr)
        inside = self.repo / "anchor"; shutil.copytree(self.anchor, inside)
        result = self.run_preflight(extra=["--trust-anchor", str(inside), "--allow-missing-evidence"])
        self.assertEqual(result.returncode, 2); self.assertIn("inside the checkout", result.stderr)
        shutil.rmtree(inside)
        (self.anchor / "release-preflight.py").write_text("tampered\n", encoding="utf-8")
        result = self.run_preflight(extra=["--trust-anchor", str(self.anchor), "--allow-missing-evidence"])
        self.assertEqual(result.returncode, 2); self.assertIn("differs from trust anchor", result.stderr)

    def test_ignored_compat_symlinks_are_exactly_bounded(self) -> None:
        targets = {"gitq": "gts", "dsh-search": "gts", "mcp-server-gitea-search": "mcp-server"}
        for name, target in targets.items():
            (self.repo / "bin" / name).symlink_to(target)
        ok = self.run_preflight()
        self.assertEqual(ok.returncode, 0, ok.stderr)
        (self.repo / "bin" / "gitq").unlink(); (self.repo / "bin" / "gitq").write_text("hidden\n")
        bad = self.run_preflight()
        self.assertEqual(bad.returncode, 2); self.assertIn("non-whitelisted ignored", bad.stderr)

    def test_rejects_untracked_and_cleans_ambient_git_env(self) -> None:
        env = {**os.environ, "GIT_DIR": "/definitely/not/a/repository", "PYTHONDONTWRITEBYTECODE": "1"}
        clean = self.run_preflight(env=env)
        self.assertEqual(clean.returncode, 0, clean.stderr)
        (self.repo / "leftover.txt").write_text("untracked\n")
        dirty = self.run_preflight()
        self.assertEqual(dirty.returncode, 2); self.assertIn("untracked files", dirty.stderr)

    def test_rejects_failed_needs_wrong_platform_and_zero_duration(self) -> None:
        cases = [
            ("github-linux.json", lambda d: d["needs"].update(package="failure"), "upstream jobs"),
            ("github-macos.json", lambda d: d["toolchain"].update(os="Linux-6"), "platform family"),
            ("github-linux.json", lambda d: d.update(duration_seconds=0), "duration_seconds < 1s"),
            ("github-windows.json", lambda d: d["needs"].pop("windows-git-bash"), "windows-git-bash"),
        ]
        for name, mutation, message in cases:
            with self.subTest(name=name):
                self.write_contract_inputs(); self.patch_json(name, mutation)
                result = self.run_preflight(full=True)
                self.assertEqual(result.returncode, 2); self.assertIn(message, result.stderr)


if __name__ == "__main__":
    unittest.main()
