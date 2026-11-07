#!/usr/bin/env python3
"""Unit contracts for scripts/collect-test-counts.py (rc7 A6)."""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("collect_test_counts", ROOT / "scripts/collect-test-counts.py")
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def completed(stdout: str = "", stderr: str = "", rc: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], rc, stdout, stderr)


class CollectCountsTest(unittest.TestCase):
    TAP = "\n".join(["# tests 74", "# pass 70", "# fail 0", "# cancelled 0", "# skipped 4", "# todo 0"])
    DEFAULT = "\n".join(["ℹ tests 74", "ℹ pass 70", "ℹ fail 0", "ℹ cancelled 0", "ℹ skipped 4", "ℹ todo 0"])

    def test_parses_tap_and_default_summaries(self) -> None:
        for output, patterns in ((self.TAP, mod.TAP_SUMMARY), (self.DEFAULT, mod.SPEC_SUMMARY)):
            with self.subTest(output=output[:5]):
                counts = mod.parse_node_summary(output, patterns)
                self.assertEqual(counts, {"total": 74, "pass": 70, "fail": 0, "cancelled": 0, "skipped": 4, "todo": 0})
                self.assertIsNone(mod.validate_node(counts))

    def test_falls_back_only_when_tap_is_unparseable(self) -> None:
        with mock.patch.object(mod, "run", side_effect=[completed("not a summary"), completed(self.DEFAULT)]):
            counts, reporter, ok = mod.node_counts()
        self.assertEqual(reporter, "default")
        self.assertTrue(ok)
        self.assertEqual(counts["skipped"], 4)
        with mock.patch.object(mod, "run", return_value=completed(self.TAP, rc=1)) as runner:
            _, reporter, ok = mod.node_counts()
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(reporter, "tap")
        self.assertFalse(ok)

    def test_rejects_zero_bool_negative_and_inconsistent_counts(self) -> None:
        good = {"total": 74, "pass": 70, "fail": 0, "cancelled": 0, "skipped": 4, "todo": 0}
        cases = [
            ({**good, "total": 0, "pass": 0, "skipped": 0}, "0 tests"),
            ({**good, "pass": True}, "not a non-negative integer"),
            ({**good, "skipped": -1}, "not a non-negative integer"),
            ({**good, "total": 73}, "do not add up"),
        ]
        for value, text in cases:
            with self.subTest(text=text):
                self.assertIn(text, mod.validate_node(value) or "")

    def test_release_suite_inventory_includes_product_regressions(self) -> None:
        self.assertEqual(len(mod.PYTHON_SUITES), 10)
        for suite in (
            "test/dependency-missing.test.py",
            "test/profile-yaml.test.py",
            "test/repo-zero-hit.test.py",
            "test/workflow-contract.test.py",
            "test/linux-gate-context.test.py",
            "test/documentation-contract.test.py",
        ):
            self.assertIn(suite, mod.PYTHON_SUITES)

    def test_python_suites_reject_missing_dependency_or_skipped_tests(self) -> None:
        skipped = completed(stderr="Ran 4 tests in 0.001s\n\nOK (skipped=4)\n")
        missing = completed(stderr="ModuleNotFoundError: No module named 'yaml'\n", rc=1)
        with mock.patch.object(mod, "PYTHON_SUITES", ("test/profile-yaml.test.py",)), \
             mock.patch.object(mod, "run", side_effect=[skipped]):
            records, ok = mod.python_suites()
        self.assertFalse(ok)
        self.assertEqual(records[0]["tests"], 4)
        self.assertFalse(records[0]["ok"])
        with mock.patch.object(mod, "PYTHON_SUITES", ("test/profile-yaml.test.py",)), \
             mock.patch.object(mod, "run", side_effect=[missing]):
            records, ok = mod.python_suites()
        self.assertFalse(ok)
        self.assertFalse(records[0]["ran"])

    def test_main_writes_schema2_full_shape(self) -> None:
        node = {"total": 74, "pass": 70, "fail": 0, "cancelled": 0, "skipped": 4, "todo": 0}
        python = [{"suite": f"suite-{i}", "ran": True, "ok": True, "tests": i + 1} for i in range(10)]
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(mod, "node_counts", return_value=(node, "tap", True)), \
             mock.patch.object(mod, "python_suites", return_value=(python, True)), \
             mock.patch.object(mod, "e2e_count", return_value=({"pass": 31, "fail": 0}, True)), \
             mock.patch.object(sys, "argv", ["collect-test-counts.py", "--output", str(pathlib.Path(td) / "counts.json")]):
            self.assertEqual(mod.main(), 0)
            data = json.loads((pathlib.Path(td) / "counts.json").read_text())
        self.assertEqual(data["schema"], 2)
        self.assertEqual(data["node_skipped"], 4)
        self.assertEqual(data["python_suites"], 10)
        self.assertEqual(data["e2e"], {"pass": 31, "fail": 0})

    def test_main_refuses_unparseable_zero_suite(self) -> None:
        zero = {field: 0 for field in mod.NODE_FIELDS}
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(mod, "node_counts", return_value=(zero, "default", True)), \
             mock.patch.object(mod, "python_suites", return_value=([], True)), \
             mock.patch.object(mod, "e2e_count", return_value=({"pass": 31, "fail": 0}, True)), \
             mock.patch.object(sys, "argv", ["collect-test-counts.py", "--output", str(pathlib.Path(td) / "counts.json")]):
            self.assertEqual(mod.main(), 1)
            self.assertFalse((pathlib.Path(td) / "counts.json").exists())


if __name__ == "__main__":
    unittest.main()
