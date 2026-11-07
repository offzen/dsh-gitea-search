#!/usr/bin/env python3
"""Offline HTTP-fixture contracts for fetch-remote-evidence.py (rc7 A4)."""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("fetch_remote_evidence", ROOT / "scripts/fetch-remote-evidence.py")
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

PRIVATE_API = "https://forge.example.invalid/api/v1"
PRIVATE_REPO = "example-org/private-source"


class FetchEvidenceTest(unittest.TestCase):
    def test_paged_fetches_every_item_and_rejects_truncation(self) -> None:
        pages = [
            (200, {"total_count": 3, "jobs": [{"id": 1}, {"id": 2}]}),
            (200, {"total_count": 3, "jobs": [{"id": 3}]}),
        ]
        with mock.patch.object(mod, "get", side_effect=pages) as get:
            rows, total = mod.paged("https://example/jobs", "jobs", {}, "jobs")
        self.assertEqual(total, 3); self.assertEqual(len(rows), 3); self.assertEqual(get.call_count, 2)
        with mock.patch.object(mod, "get", return_value=(200, {"total_count": 2, "jobs": [{"id": 1}]})):
            mod.MAX_PAGES = 1
            with self.assertRaises(SystemExit) as caught:
                mod.paged("https://example/jobs", "jobs", {}, "jobs")
            self.assertIn("truncated", str(caught.exception))
        mod.MAX_PAGES = 50

    def test_github_fixture_emits_deep_schema_and_artifact_digest(self) -> None:
        run = {
            "id": 42, "repository": {"full_name": mod.GITHUB_REPO},
            "path": ".github/workflows/ci.yml@refs/heads/release/test", "name": "CI",
            "event": "push", "head_branch": "release/test", "run_attempt": 2,
            "head_sha": "a" * 40, "conclusion": "success", "status": "completed",
        }
        job = {"name": "Hosted Linux exact-SHA receipt", "conclusion": "success", "status": "completed",
               "labels": ["ubuntu-latest"], "runner_name": "GitHub Actions 1", "runner_group_name": "GitHub Actions"}
        artifact = {"name": "github-linux-" + "a" * 40, "digest": "sha256:" + "b" * 64,
                    "size_in_bytes": 10, "expired": False}
        def fake_get(url, _headers):
            if url.endswith("/42"): return 200, run
            if "/jobs?" in url: return 200, {"total_count": 1, "jobs": [job]}
            if "/artifacts?" in url: return 200, {"total_count": 1, "artifacts": [artifact]}
            raise AssertionError(url)
        with mock.patch.object(mod, "get", side_effect=fake_get):
            data = mod.github_evidence("github-linux", "42")
        self.assertEqual(data["schema"], 2)
        self.assertEqual(data["repository"], mod.GITHUB_REPO)
        self.assertEqual(data["workflow_path"], ".github/workflows/ci.yml")
        self.assertEqual(data["run_attempt"], 2)
        self.assertEqual(data["jobs_total_count"], 1)
        self.assertEqual(data["artifacts"][0]["digest"], "sha256:" + "b" * 64)
        self.assertTrue(data["fetched_at"].endswith("Z"))

    def test_gate2_requires_runtime_coordinates_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            output = pathlib.Path(td) / "out.json"
            with mock.patch.object(mod, "get") as get:
                result = mod.main_from_args([
                    "--gate", "gate2-linux", "--run-id", "42", "--output", str(output),
                ])
            self.assertEqual(result, 2)
            get.assert_not_called()
            self.assertFalse(output.exists())

    def test_gate2_uses_supplied_coordinates(self) -> None:
        run = {"id": 42, "head_sha": "a" * 40, "event": "push",
               "path": ".gitea/workflows/ci.yml", "name": "CI",
               "conclusion": "success", "status": "completed"}
        job = {"name": "Exact-SHA Linux gate", "head_sha": "a" * 40,
               "conclusion": "success", "status": "completed", "id": 7}
        def fake_get(url, _headers):
            self.assertTrue(url.startswith(PRIVATE_API + "/repos/" + PRIVATE_REPO))
            if url.endswith("/42"): return 200, run
            if url.endswith("/42/jobs"): return 200, [job]
            if url.endswith("/42/artifacts"): return 404, {}
            raise AssertionError(url)
        with mock.patch.object(mod, "get", side_effect=fake_get):
            data = mod.gitea_evidence("gate2-linux", "42", PRIVATE_API, PRIVATE_REPO)
        self.assertEqual(data["repository"], PRIVATE_REPO)
        self.assertEqual(data["head_sha"], "a" * 40)

    def test_non_numeric_run_id_is_rejected_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = subprocess.run([
                sys.executable, str(ROOT / "scripts/fetch-remote-evidence.py"),
                "--gate", "github-linux", "--run-id", "42;rm", "--output", str(pathlib.Path(td) / "out.json"),
            ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("plain decimal", result.stderr)

    def test_error_text_scrubs_all_tokens(self) -> None:
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "TOP-SECRET-TOKEN", "GITEA_TOKEN": "OTHER-SECRET"}, clear=False):
            text = str(mod.blocked("server echoed TOP-SECRET-TOKEN and OTHER-SECRET"))
        self.assertNotIn("TOP-SECRET-TOKEN", text)
        self.assertNotIn("OTHER-SECRET", text)
        self.assertIn("***", text)


if __name__ == "__main__":
    unittest.main()
