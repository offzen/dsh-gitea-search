#!/usr/bin/env python3
"""Run the test suites and record their counts as JSON (rc6 F8).

Why this exists: before rc6 the receipt's `status` was a hardcoded constant and the
receipt carried no evidence that any test ran. A receipt job that only *needs* the
test job cannot prove the suite still contains the same number of tests either — a
suite quietly reduced to three tests would still be green.

This script is run **inside the receipt job itself**, so the counts describe tests
executed on that runner, for that exact SHA, in the same job that signs the receipt.
The verifier (`scripts/release-preflight.py`) then enforces floors on the numbers.

Usage:
    python3 scripts/collect-test-counts.py --output "$RUNNER_TEMP/test-counts.json"

Exit codes: 0 all suites green, 1 a suite failed, 2 the output could not be written.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

NODE_PASS = re.compile(r"^# pass (\d+)$", re.MULTILINE)
NODE_FAIL = re.compile(r"^# fail (\d+)$", re.MULTILINE)
NODE_TOTAL = re.compile(r"^# tests (\d+)$", re.MULTILINE)
# The default `node --test` reporter is not TAP: it prints "ℹ tests 74". Accept any
# leading non-word decoration so both reporters work.
NODE_PASS_SPEC = re.compile(r"^\W*pass (\d+)\s*$", re.MULTILINE)
NODE_FAIL_SPEC = re.compile(r"^\W*fail (\d+)\s*$", re.MULTILINE)
NODE_TOTAL_SPEC = re.compile(r"^\W*tests (\d+)\s*$", re.MULTILINE)
PYTHON_RAN = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)
PYTHON_OK = re.compile(r"^OK\b", re.MULTILINE)
# The e2e runner reports in Chinese: "验收汇总: 31 绿 (PASS) / 0 红 (FAIL)".
E2E_SUMMARY = re.compile(r"验收汇总:\s*(\d+)\s*绿")
E2E_PASS_LINE = re.compile(r"^\s*✓ \[PASS\]", re.MULTILINE)

# The Python suites that must each run at least one test. Each entry is a file the
# release contract depends on; `python_suites` counts how many of them actually ran.
PYTHON_SUITES = ("test/release-preflight.test.py", "test/hosted-receipt.test.py")


def run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, encoding="utf-8", errors="replace", capture_output=True, check=False)


def node_counts() -> tuple[int, int, int, bool]:
    result = run(["node", "--test"])
    output = (result.stdout or "") + (result.stderr or "")
    total = passed = failed = 0
    for total_re, pass_re, fail_re in (
        (NODE_TOTAL, NODE_PASS, NODE_FAIL),
        (NODE_TOTAL_SPEC, NODE_PASS_SPEC, NODE_FAIL_SPEC),
    ):
        if total_re.search(output):
            total = int(total_re.search(output).group(1))
            passed = int(pass_re.search(output).group(1)) if pass_re.search(output) else 0
            failed = int(fail_re.search(output).group(1)) if fail_re.search(output) else 0
            break
    if not total:
        # Last resort: count per-test result lines.
        passed = len(re.findall(r"^\s*[✔✓] ", output, re.MULTILINE))
        failed = len(re.findall(r"^\s*[✖✗] ", output, re.MULTILINE))
        total = passed + failed
    return total, passed, failed, result.returncode == 0 and failed == 0


def python_suites() -> tuple[int, bool]:
    ran = 0
    all_ok = True
    for suite in PYTHON_SUITES:
        result = run([sys.executable, suite])
        output = (result.stdout or "") + (result.stderr or "")
        if PYTHON_RAN.search(output):
            ran += 1
        if result.returncode != 0 or not PYTHON_OK.search(output):
            all_ok = False
    return ran, all_ok


def e2e_count() -> tuple[int, bool]:
    result = run(["bash", "scripts/run-e2e-test.sh"])
    output = (result.stdout or "") + (result.stderr or "")
    total = int(m.group(1)) if (m := E2E_SUMMARY.search(output)) else 0
    if not total:
        total = len(E2E_PASS_LINE.findall(output))
    return total, result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--skip-e2e", action="store_true",
                        help="e2e is exercised by its own job; record 0 and let floors catch it")
    args = parser.parse_args()

    total, passed, failed, node_ok = node_counts()
    suites, python_ok = python_suites()
    e2e, e2e_ok = (0, True) if args.skip_e2e else e2e_count()

    counts = {
        "node_total": total,
        "node_pass": passed,
        "node_fail": failed,
        "python_suites": suites,
        "e2e": e2e,
        "node_ok": node_ok,
        "python_ok": python_ok,
        "e2e_ok": e2e_ok,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(counts, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(f"TEST_COUNTS node={passed}/{total} fail={failed} python_suites={suites} e2e={e2e}")
    print(f"TEST_COUNTS_WRITTEN {args.output}")

    if not (node_ok and python_ok and e2e_ok):
        print("TEST_COUNTS_BLOCKED: at least one suite failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
