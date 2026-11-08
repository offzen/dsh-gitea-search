#!/usr/bin/env python3
"""Run the test suites and record their counts as JSON (rc6 F8).

Why this exists: before rc6 the receipt's `status` was a hardcoded constant and the
receipt carried no evidence that any test ran. A receipt job that only *needs* the
test job cannot prove the suite still contains the same number of tests either — a
suite quietly reduced to three tests would still be green.

This script is run **inside the receipt job itself**, so the counts describe tests
executed on that runner, for that exact SHA, in the same job that signs the receipt.
The verifier (`scripts/release-preflight.py`) then enforces floors on the numbers.

rc7 (A6) hardening: output is schema 2 and records the full node breakdown
(`node{total,pass,fail,cancelled,skipped,todo}`), per-suite python results
(`python[{suite,ran,ok,tests}]`), `e2e{pass,fail}` and the `reporter` that produced the node
numbers. Every number must be a strict non-negative integer (no bool) and the node figures must
add up (`pass+fail+cancelled+skipped+todo == total`). `node --test` is run with the
machine-readable TAP reporter first and only falls back to the default reporter when the TAP
summary cannot be parsed. A total of 0 is a failure (exit 1), never a silent pass.

For compatibility with receipt writers the same numbers are also emitted flat
(`node_total`, `node_pass`, `node_fail`, `node_cancelled`, `node_skipped`, `node_todo`,
`python_suites`); the nested `e2e` object is the e2e result (`e2e.pass` is the count).

Usage:
    python3 scripts/collect-test-counts.py --output "$RUNNER_TEMP/test-counts.json"

Exit codes: 0 all suites green, 1 a suite failed or the counts are unusable, 2 the output
could not be written.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

SCHEMA = 2
NODE_FIELDS = ("total", "pass", "fail", "cancelled", "skipped", "todo")
NODE_LABELS = {"total": "tests", "pass": "pass", "fail": "fail", "cancelled": "cancelled",
               "skipped": "skipped", "todo": "todo"}
# TAP summary: "# tests 74". The default `node --test` reporter is not TAP: it prints
# "ℹ tests 74". Accept any leading non-word decoration so both reporters work.
TAP_SUMMARY = {field: re.compile(rf"^# {label} (\d+)\s*$", re.MULTILINE) for field, label in NODE_LABELS.items()}
# U+2139 INFORMATION SOURCE is treated as a Unicode word character by Python's `\w`, so
# `\W*` does not match Node's default `ℹ tests 74` reporter.  Only the English summary label
# is significant; allow any non-ASCII-identifier decoration before it.
SPEC_SUMMARY = {
    field: re.compile(rf"^[^A-Za-z0-9_]*{label} (\d+)\s*$", re.MULTILINE)
    for field, label in NODE_LABELS.items()
}
PYTHON_RAN = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)
PYTHON_OK = re.compile(r"^OK\b", re.MULTILINE)
PYTHON_SKIPPED = re.compile(r"^OK \(skipped=(\d+)\)$", re.MULTILINE)
# The e2e runner reports in Chinese: "验收汇总: 31 绿 (PASS) / 0 红 (FAIL)".
E2E_SUMMARY = re.compile(r"验收汇总:\s*(\d+)\s*绿")
E2E_FAIL_SUMMARY = re.compile(r"验收汇总:\s*\d+\s*绿[^/]*/\s*(\d+)\s*红")
E2E_PASS_LINE = re.compile(r"^\s*✓ \[PASS\]", re.MULTILINE)
E2E_FAIL_LINE = re.compile(r"^\s*\S+ \[FAIL\]", re.MULTILINE)

# The Python suites that must each run at least one test. Each entry is a file the
# release contract depends on; `python_suites` counts how many of them actually ran.
PYTHON_SUITES = (
    "test/release-preflight.test.py",
    "test/hosted-receipt.test.py",
    "test/collect-test-counts.test.py",
    "test/fetch-remote-evidence.test.py",
    "test/workflow-contract.test.py",
    "test/linux-gate-context.test.py",
    "test/dependency-missing.test.py",
    "test/owner-qualified-repos.test.py",
    "test/profile-yaml.test.py",
    "test/repo-zero-hit.test.py",
    "test/documentation-contract.test.py",
)


def run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, encoding="utf-8", errors="replace", capture_output=True, check=False)


def strict_count(value: object) -> bool:
    """A6: bool is an int subclass; `True` must not be accepted as the count 1."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def parse_node_summary(output: str, patterns: dict[str, re.Pattern[str]]) -> dict[str, int] | None:
    """Return the node summary, or None when the total line is absent (reporter mismatch).
    A missing optional line (cancelled/skipped/todo) means 0; a missing total means unparseable."""
    # The summary is printed last, so the LAST match wins (a test whose title looks like
    # "pass 3" earlier in the log must not be mistaken for the summary).
    totals = patterns["total"].findall(output)
    if not totals:
        return None
    counts = {"total": int(totals[-1])}
    for field in NODE_FIELDS[1:]:
        found = patterns[field].findall(output)
        counts[field] = int(found[-1]) if found else 0
    return counts


def validate_node(counts: dict[str, int]) -> str | None:
    """Return a reason the node counts are unusable, or None."""
    for field in NODE_FIELDS:
        if not strict_count(counts.get(field)):
            return f"node.{field}={counts.get(field)!r} is not a non-negative integer"
    if counts["total"] == 0:
        return "node reported 0 tests (unparseable output or an empty suite)"
    parts = sum(counts[field] for field in NODE_FIELDS[1:])
    if parts != counts["total"]:
        return (f"node counts do not add up: pass+fail+cancelled+skipped+todo={parts} "
                f"!= total={counts['total']}")
    return None


def node_counts() -> tuple[dict[str, int], str, bool]:
    """Run node's tests. TAP first (machine-readable); only if its summary cannot be parsed fall
    back to the default reporter. A *failing* TAP run is not re-run: that would hide flakiness."""
    result = run(["node", "--test", "--test-reporter=tap"])
    output = (result.stdout or "") + (result.stderr or "")
    reporter = "tap"
    counts = parse_node_summary(output, TAP_SUMMARY)
    if counts is None:
        result = run(["node", "--test"])
        output = (result.stdout or "") + (result.stderr or "")
        reporter = "default"
        counts = parse_node_summary(output, SPEC_SUMMARY)
    if counts is None:
        # Nothing parseable at all: report zeros so validate_node() fails the run loudly.
        counts = {field: 0 for field in NODE_FIELDS}
    return counts, reporter, result.returncode == 0 and counts["fail"] == 0


def python_suites() -> tuple[list[dict[str, object]], bool]:
    records: list[dict[str, object]] = []
    all_ok = True
    for suite in PYTHON_SUITES:
        result = run([sys.executable, suite])
        output = (result.stdout or "") + (result.stderr or "")
        ran_match = PYTHON_RAN.search(output)
        tests = int(ran_match.group(1)) if ran_match else 0
        skipped_match = PYTHON_SKIPPED.search(output)
        skipped = int(skipped_match.group(1)) if skipped_match else 0
        ok = result.returncode == 0 and PYTHON_OK.search(output) is not None and skipped == 0
        records.append({"suite": suite, "ran": tests >= 1, "ok": ok, "tests": tests})
        if not ok:
            print(f"PYTHON_SUITE_FAILED suite={suite} rc={result.returncode} skipped={skipped}", file=sys.stderr)
            for line in output.splitlines()[-12:]:
                print("  " + line[:300], file=sys.stderr)
            all_ok = False
    return records, all_ok


def e2e_count() -> tuple[dict[str, int], bool]:
    result = run(["bash", "scripts/run-e2e-test.sh"])
    output = (result.stdout or "") + (result.stderr or "")
    passed = int(m.group(1)) if (m := E2E_SUMMARY.search(output)) else 0
    if not passed:
        passed = len(E2E_PASS_LINE.findall(output))
    failed = int(m.group(1)) if (m := E2E_FAIL_SUMMARY.search(output)) else len(E2E_FAIL_LINE.findall(output))
    if result.returncode != 0:
        # Without this the job log only says "e2e=0" and the real reason is lost;
        # rc6 run 42336982455 failed exactly that way.
        print("E2E_FAILED_OUTPUT_TAIL:", file=sys.stderr)
        for line in output.splitlines()[-25:]:
            print("  " + line[:300], file=sys.stderr)
    return {"pass": passed, "fail": failed}, result.returncode == 0 and failed == 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--skip-e2e", action="store_true",
                        help="e2e is exercised by its own job; record 0 and let floors catch it")
    args = parser.parse_args()

    node, reporter, node_ok = node_counts()
    python, python_ok = python_suites()
    e2e, e2e_ok = ({"pass": 0, "fail": 0}, True) if args.skip_e2e else e2e_count()
    suites = sum(1 for record in python if record["ran"])

    problem = validate_node(node)
    if problem is None:
        for field in ("pass", "fail"):
            if not strict_count(e2e[field]):
                problem = f"e2e.{field}={e2e[field]!r} is not a non-negative integer"
    if problem:
        print(f"TEST_COUNTS_BLOCKED: {problem}", file=sys.stderr)
        return 1

    counts = {
        "schema": SCHEMA,
        "reporter": reporter,
        "node": node,
        "python": python,
        "e2e": e2e,
        # flat form kept for receipt writers / floors
        "node_total": node["total"],
        "node_pass": node["pass"],
        "node_fail": node["fail"],
        "node_cancelled": node["cancelled"],
        "node_skipped": node["skipped"],
        "node_todo": node["todo"],
        "python_suites": suites,
        "node_ok": node_ok,
        "python_ok": python_ok,
        "e2e_ok": e2e_ok,
    }
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(counts, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"TEST_COUNTS_BLOCKED: cannot write {args.output}: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(
        f"TEST_COUNTS node={node['pass']}/{node['total']} fail={node['fail']} skipped={node['skipped']} "
        f"reporter={reporter} python_suites={suites} e2e={e2e['pass']}"
    )
    print(f"TEST_COUNTS_WRITTEN {args.output}")

    if not (node_ok and python_ok and e2e_ok):
        print("TEST_COUNTS_BLOCKED: at least one suite failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
