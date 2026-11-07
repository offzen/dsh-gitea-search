#!/usr/bin/env python3
"""Static workflow/preflight contract alignment checks."""
from __future__ import annotations

import importlib.util
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("release_preflight", ROOT / "scripts/release-preflight.py")
assert SPEC and SPEC.loader
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


def job_block(path: pathlib.Path, job: str) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"^  {re.escape(job)}:\s*$", text, re.MULTILINE)
    if not match:
        raise AssertionError(f"missing job {job} in {path}")
    following = re.search(r"^  [A-Za-z0-9_-]+:\s*$", text[match.end():], re.MULTILINE)
    end = match.end() + following.start() if following else len(text)
    return text[match.start():end]


def inline_needs(block: str) -> set[str]:
    match = re.search(r"^    needs:\s*\[([^]]*)\]", block, re.MULTILINE)
    return {item.strip() for item in match.group(1).split(",") if item.strip()} if match else set()


class WorkflowContractTest(unittest.TestCase):
    def test_gate_maps_are_complete(self) -> None:
        self.assertEqual(set(preflight.REQUIRED_GATES), set(preflight.EXPECTED_WORKFLOWS))
        self.assertEqual(set(preflight.REQUIRED_GATES), set(preflight.REQUIRED_NEEDS))
        self.assertEqual(set(preflight.REQUIRED_GATES), set(preflight.REQUIRED_EVIDENCE_JOBS))

    def test_github_receipt_jobs_needs_and_artifact_names_match(self) -> None:
        cases = [
            ("github-linux", ROOT / ".github/workflows/ci.yml", "hosted-linux-receipt"),
            ("github-macos", ROOT / ".github/workflows/ci.yml", "hosted-macos-receipt"),
            ("github-windows", ROOT / ".github/workflows/windows-ci.yml", "hosted-windows-receipt"),
        ]
        for gate, path, receipt_job in cases:
            with self.subTest(gate=gate):
                block = job_block(path, receipt_job)
                self.assertTrue(set(preflight.REQUIRED_NEEDS[gate]).issubset(inline_needs(block)))
                self.assertIn(f"name: {gate}-${{{{ github.sha }}}}", block)
                self.assertIn("write-gate-receipt.py", block)
                for job_id in preflight.REQUIRED_EVIDENCE_JOBS[gate]:
                    job_block(path, job_id)

    def test_windows_receipt_depends_on_git_bash(self) -> None:
        block = job_block(ROOT / ".github/workflows/windows-ci.yml", "hosted-windows-receipt")
        self.assertIn("windows-git-bash", inline_needs(block))

    def test_windows_full_suite_provisions_pyyaml(self) -> None:
        block = job_block(ROOT / ".github/workflows/windows-ci.yml", "windows-git-bash")
        self.assertRegex(block, r'python -m pip install [^\n]*PyYAML')
        self.assertIn("npm run test:all", block)

    def test_gitea_gate_exists_and_invokes_linux_gate(self) -> None:
        path = ROOT / ".gitea/workflows/ci.yml"
        block = job_block(path, "linux-gate")
        self.assertIn("scripts/ci/linux-gate.sh", block)
        self.assertEqual(preflight.REQUIRED_NEEDS["gate2-linux"], frozenset())


if __name__ == "__main__":
    unittest.main()
