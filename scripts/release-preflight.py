#!/usr/bin/env python3
"""Read-only release preflight for the two-commit D/P contract.

The checker never pushes, tags, dispatches, or writes the repository. Operational
receipts must live outside the tracked checkout and all required receipts must
name the final exact provenance commit P.

rc6 hardening (each item maps to the 2026-11-07 audit):
  F1  --remote-evidence GATE=PATH : cross-check the receipt against the forge's own
      answer (run exists, head_sha == P, conclusion == success, every job green).
      A receipt is self-written by the candidate's own CI code, so this is the only
      step that makes "verified" mean more than "the JSON is internally consistent".
  F4  runner_label / runner_environment are validated for linux/macos too, not only windows.
  F5  run identity is deduplicated on workflow_run_id. Gates that legitimately share a
      run (github-linux + github-macos both come from ci.yml) are allowed; a run id
      shared across *different* workflows is rejected.
  F7  toolchain.os platform family is asserted independently of the receipt's own claims.
  F8  test_counts must be present and above floors, so a weakened suite cannot pass.
  F9  --trust-anchor PATH : the in-tree copy of this verifier must be byte-identical to
      an externally pinned copy, so the trust root is not "the thing being verified".
  F10 untracked files in the checkout are rejected (git diff --quiet cannot see them).

rc7 hardening (the 2026-11-07 external audit judged the rc6 chain "not trustworthy":
fail-open default, shallow evidence, self-anchored trust root, unbound receipts):
  A1  fail-closed: `status="verified"` needs ALL of receipts + deep remote evidence +
      external anchor + --expected-ref + artifact binding + exact/clean checkout.
      Anything missing => "configured-unverified" with `blocked_reasons`. The
      --allow-missing-evidence / --allow-missing-anchor switches only let the run finish;
      they force "configured-unverified" and are listed in `relaxed_checks`.
  A2  --trust-anchor is a DIRECTORY holding pinned external copies of ANCHORED_SCRIPTS;
      check-provenance then runs from the anchor copy, not the in-tree one.
  A3  exact (never substring) workflow_ref / ref / repository / event / run_attempt matching.
  A4  evidence deep-validation (schema, forge, required jobs, hosted-runner signals, age).
  A5  --artifact GATE=ZIP binds a receipt to the forge-recorded artifact digest.
  A6  strict integer / arithmetic / floor checks on test counts.
  A7  REQUIRED_NEEDS / REQUIRED_EVIDENCE_JOBS are fixed sets (inclusion, not "if present").
  A8  git runs with GIT_* stripped; the ignored-file surface is checked against a whitelist.

Trust ceiling: every gate here is run by the same party that ships the candidate, so even a
fully "verified" result is `trust_ceiling: "same-party"`. This tool raises the cost of an
accidental or lazy false green; it does not make the chain independent or unforgeable.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import hashlib
import json
import os
import pathlib
import re
import stat
import subprocess
import sys
import zipfile
from typing import Any

# Not resolved on purpose: A2 must be able to see whether *this file* is a symlink.
SELF_PATH = pathlib.Path(os.path.abspath(__file__))
ROOT = pathlib.Path(__file__).resolve().parent.parent
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_GATES = ("gate2-linux", "github-linux", "github-macos", "github-windows")

ALLOWED_WINDOWS_PROVIDERS = {"github-actions-hosted"}
ALLOWED_WINDOWS_RUNNERS = {"windows-latest", "windows-2022", "windows-2025"}
EXPECTED_GITHUB_WORKFLOWS = {
    "github-linux": ".github/workflows/ci.yml",
    "github-macos": ".github/workflows/ci.yml",
    "github-windows": ".github/workflows/windows-ci.yml",
}
# F4: the runner labels each hosted gate may legitimately report.
ALLOWED_RUNNER_LABELS = {
    "github-linux": {"ubuntu-latest", "ubuntu-24.04", "ubuntu-22.04"},
    "github-macos": {"macos-latest", "macos-14", "macos-15"},
    "github-windows": set(ALLOWED_WINDOWS_RUNNERS),
}
# F7: toolchain.os prefixes per gate. macOS runners report "macOS-…"; platform.system()
# would say "Darwin", so both spellings are accepted but nothing else is.
EXPECTED_OS_PREFIXES = {
    "gate2-linux": ("Linux",),
    "github-linux": ("Linux",),
    "github-macos": ("macOS", "Darwin"),
    "github-windows": ("Windows",),
}
# F8: floors. A suite that silently loses tests cannot satisfy these. Node/e2e floors
# allow ordinary cross-platform skips, while the Python suite floor is exact because
# collect-test-counts.py owns a fixed list and every entry must report at least one test.
# rc7 baseline: node total 74 / pass 70 / skipped 4, e2e 31, 10 mandatory Python suites.
TEST_COUNT_FLOORS = {"node_total": 70, "node_pass": 64, "python_suites": 10, "e2e": 30}
# Every key a schema-3 receipt must carry in test_counts; all strict non-negative ints.
TEST_COUNT_INT_KEYS = (
    "node_total", "node_pass", "node_fail", "node_cancelled", "node_skipped", "node_todo",
    "python_suites", "e2e",
)
# The e2e floor only applies to gates that run the e2e suite inside the receipt job.
# On Windows the suite runs in the separate `windows-git-bash` job (the receipt job
# cannot reproduce its environment), so its coverage is attested by that job's
# result in `needs` — which `check_needs` requires to be success. Asserting a count
# we did not measure would be exactly the self-attestation rc6 set out to remove.
E2E_COUNTED_GATES = {"gate2-linux", "github-linux", "github-macos"}
WINDOWS_E2E_NEEDS_JOB = "windows-git-bash"
# Each gate must name the repository where it actually ran. Both the private gate2
# identity and the private GitHub hosted-staging identity are explicit runtime inputs;
# the candidate tree carries no deployment coordinate and cannot silently bless itself.
REPOSITORY_ID = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")

# ---- rc7 constants -------------------------------------------------------------
RECEIPT_SCHEMA = 3          # receipt contract: new mandatory fields (run_attempt, inputs_sha256, ...)
EVIDENCE_SCHEMA = 2         # fetch-remote-evidence.py output
TRUST_CEILING = "same-party"
DEFAULT_MAX_EVIDENCE_AGE_SECONDS = 30 * 24 * 3600
EVIDENCE_FUTURE_SKEW_SECONDS = 300
MAX_ZIP_ENTRY_BYTES = 8 * 1024 * 1024
# A2: scripts whose bytes must equal an externally pinned copy (all live in scripts/).
ANCHORED_SCRIPTS = (
    "release-preflight.py",
    "check-provenance.py",
    "fetch-remote-evidence.py",
    "collect-test-counts.py",
)
GATE2_WORKFLOW = ".gitea/workflows/ci.yml"
EXPECTED_WORKFLOWS = {"gate2-linux": GATE2_WORKFLOW, **EXPECTED_GITHUB_WORKFLOWS}
EXPECTED_FORGE = {"gate2-linux": "gitea", "github-linux": "github", "github-macos": "github", "github-windows": "github"}
ALL_HOSTED_LABELS = set().union(*ALLOWED_RUNNER_LABELS.values())
HOSTED_RUNNER_GROUP = "GitHub Actions"
# A7: the `needs:` edges of each receipt-writing job, read off the real workflow YAML
# (test/workflow-contract.test.py re-derives them and fails if they drift). Verification is
# by INCLUSION: every name below must be present and "success" in the receipt's needs.
REQUIRED_NEEDS = {
    "gate2-linux": frozenset(),  # one job (linux-gate), no `needs:` graph
    "github-linux": frozenset({"test", "package"}),
    "github-macos": frozenset({"test-macos"}),
    "github-windows": frozenset({
        "windows-pe", "windows-ps", "windows-tests", "windows-git-bash", "windows-grep-crlf",
    }),
}
# A4: load-bearing jobs per gate: {job id in the workflow YAML: display names the forge reports}.
# The forge reports the job's `name:` (with matrix values substituted), not its id, and every
# matrix expansion must be present. The set always contains the job that writes the receipt
# and the job(s) that run the tests; windows additionally pins `windows-git-bash`.
REQUIRED_EVIDENCE_JOBS = {
    "gate2-linux": {
        "linux-gate": ("Exact-SHA Linux gate",),
    },
    "github-linux": {
        "test": tuple(
            f"Unit + E2E (Ubuntu, py{py}, node{node})" for py in ("3.11", "3.12") for node in ("20", "22")
        ),
        "package": ("Python wheel + sdist (Ubuntu, py3.12)",),
        "hosted-linux-receipt": ("Hosted Linux exact-SHA receipt",),
    },
    "github-macos": {
        "test-macos": ("Unit + E2E (macOS, py3.12, node22)",),
        "hosted-macos-receipt": ("Hosted macOS exact-SHA receipt",),
    },
    "github-windows": {
        "windows-pe": ("W5 gts.exe 是真实 PE 文件",),
        "windows-ps": ("W6 setup-windows.ps1 PS5.1 + PS7 WhatIf",),
        "windows-tests": tuple(
            f"W7/W8 运行时行为（py{py} / node{node}）" for py in ("3.11", "3.12") for node in ("20", "22")
        ),
        "windows-git-bash": ("W10 Git Bash full E2E",),
        "windows-grep-crlf": ("W9 git grep 的 CRLF 与路径分隔符行为",),
        "hosted-windows-receipt": ("Final exact-SHA hosted Windows receipt",),
    },
}
# A5: receipts record sha256 of the inputs that produced their numbers.  The shared counter and
# the gate's *actual writer* must equal the in-tree bytes (checkout == P and clean, so tree == P).
RECEIPT_INPUT_TREE_FILES = {
    "gate2-linux": {
        "collect-test-counts.py": "scripts/collect-test-counts.py",
        "linux-gate.sh": "scripts/ci/linux-gate.sh",
    },
    "github-linux": {
        "collect-test-counts.py": "scripts/collect-test-counts.py",
        "write-gate-receipt.py": ".github/scripts/write-gate-receipt.py",
    },
    "github-macos": {
        "collect-test-counts.py": "scripts/collect-test-counts.py",
        "write-gate-receipt.py": ".github/scripts/write-gate-receipt.py",
    },
    "github-windows": {
        "collect-test-counts.py": "scripts/collect-test-counts.py",
        "write-gate-receipt.py": ".github/scripts/write-gate-receipt.py",
    },
}
# A8: ignored paths that may legitimately exist in a checkout (matched per path component).
IGNORED_WHITELIST = (
    "node_modules/", "dist/", "build/", "*.egg-info/", "__pycache__/", ".pytest_cache/", ".venv/",
    "*.pyc", ".DS_Store", "coverage/", ".nyc_output/",
)
# These compatibility entrypoints are intentionally ignored because installers create them.
# Allow only the exact symlink shape; an ordinary file, dangling link, or wrong target remains a
# hidden input and is rejected by A8.
COMPAT_IGNORED_SYMLINKS = {
    "bin/gitq": "bin/gts",
    "bin/dsh-search": "bin/gts",
    "bin/mcp-server-gitea-search": "bin/mcp-server",
}
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class Blocked(RuntimeError):
    pass


def clean_git_env() -> dict[str, str]:
    """A8: ambient GIT_DIR / GIT_INDEX_FILE / GIT_WORK_TREE / GIT_CONFIG_* must not steer us."""
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


GIT_PREFIX = ("--no-pager", "-c", "core.fsmonitor=", "-c", "core.quotepath=false")


def git_run(*args: str, binary: bool = False) -> subprocess.CompletedProcess:
    kwargs: dict[str, Any] = {} if binary else {"text": True, "encoding": "utf-8", "errors": "replace"}
    return subprocess.run(
        ["git", *GIT_PREFIX, *args], cwd=ROOT, env=clean_git_env(),
        capture_output=True, check=False, **kwargs,
    )


def git(*args: str) -> str:
    result = git_run(*args)
    if result.returncode:
        raise Blocked(f"git {' '.join(args)} failed (exit {result.returncode})")
    return result.stdout


def git_bytes(*args: str) -> bytes:
    result = git_run(*args, binary=True)
    if result.returncode:
        raise Blocked(f"git {' '.join(args)} failed (exit {result.returncode})")
    return result.stdout


def require(condition: bool, message: str) -> None:
    if not condition:
        raise Blocked(message)


def parse_json_bytes(raw: bytes, path: pathlib.Path, what: str) -> dict[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Blocked(f"invalid {what} {path}: {type(exc).__name__}") from None
    require(isinstance(data, dict), f"{what} must be an object: {path}")
    return data


def read_input(path: pathlib.Path, what: str) -> bytes:
    """Read an input once; the same bytes are hashed and parsed (no read-twice window)."""
    try:
        require(not path.is_symlink(), f"{what} must not be a symlink: {path}")
        return path.read_bytes()
    except OSError as exc:
        raise Blocked(f"invalid {what} {path}: {type(exc).__name__}") from None


def load_json(path: pathlib.Path, what: str) -> dict[str, Any]:
    return parse_json_bytes(read_input(path, what), path, what)


def sha256_file(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def strict_int(value: Any) -> bool:
    """A6: bool is an int subclass in Python; a receipt must not be able to say `true` for 70."""
    return isinstance(value, int) and not isinstance(value, bool)


def check_test_counts(gate: str, counts: Any) -> None:
    """F8 + A6: counts must be present, strict non-negative integers, self-consistent, above floors."""
    require(isinstance(counts, dict) and counts, f"{gate}: test_counts missing")
    for key in TEST_COUNT_INT_KEYS:
        require(strict_int(counts.get(key)), f"{gate}: test_counts.{key} must be an integer (not bool/missing)")
        require(counts[key] >= 0, f"{gate}: test_counts.{key} must be non-negative")
    require(counts["node_fail"] == 0, f"{gate}: test_counts reports {counts['node_fail']} node failures")
    require(counts["node_pass"] <= counts["node_total"], f"{gate}: test_counts node_pass > node_total")
    parts = sum(counts[key] for key in ("node_pass", "node_fail", "node_cancelled", "node_skipped", "node_todo"))
    require(
        parts == counts["node_total"],
        f"{gate}: test_counts arithmetic inconsistent: pass+fail+cancelled+skipped+todo={parts} != node_total={counts['node_total']}",
    )
    for key, floor in TEST_COUNT_FLOORS.items():
        if key == "e2e" and gate not in E2E_COUNTED_GATES:
            # Windows: e2e is measured by its own job; see E2E_COUNTED_GATES.
            continue
        require(
            counts[key] >= floor,
            f"{gate}: test_counts.{key}={counts[key]} below floor {floor} (suite was weakened)",
        )
    if gate == "github-windows":
        require(
            counts["e2e"] == 0,
            f"{gate}: test_counts.e2e={counts['e2e']} but this gate does not run e2e itself",
        )


def check_toolchain(gate: str, toolchain: Any) -> None:
    require(isinstance(toolchain, dict) and toolchain, f"{gate}: toolchain missing")
    for key in ("os", "python", "node", "git"):
        require(bool(toolchain.get(key)), f"{gate}: toolchain.{key} missing")
    os_name = str(toolchain["os"])
    prefixes = EXPECTED_OS_PREFIXES[gate]
    require(
        os_name.startswith(prefixes),
        f"{gate}: toolchain.os {os_name!r} is not one of {prefixes} (F7 platform family)",
    )


def head_branch_for_ref(ref: str) -> str:
    """A3: GitHub reports `head_branch` as the bare branch name for refs/heads/*; for any other
    ref there is no stripping rule, so the full ref is compared."""
    prefix = "refs/heads/"
    return ref[len(prefix):] if ref.startswith(prefix) else ref


def crlf_variant(raw: bytes) -> bytes:
    return raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def check_receipt_inputs(gate: str, data: dict[str, Any]) -> None:
    """A5: inputs_sha256 must be measured values; the two scripts must equal the P bytes."""
    inputs = data.get("inputs_sha256")
    require(isinstance(inputs, dict) and inputs, f"{gate}: inputs_sha256 missing (A5)")
    expected_files = RECEIPT_INPUT_TREE_FILES[gate]
    expected_keys = {"test-counts.json", *expected_files}
    require(set(inputs) == expected_keys,
            f"{gate}: inputs_sha256 keys {sorted(inputs)} != expected {sorted(expected_keys)}")
    for key in expected_keys:
        value = inputs.get(key)
        require(isinstance(value, str) and HEX64.fullmatch(value) is not None,
                f"{gate}: inputs_sha256.{key} must be a lowercase sha256 hex")
    for key, rel in expected_files.items():
        tree_file = ROOT / rel
        require(tree_file.is_file() and not tree_file.is_symlink(), f"{gate}: in-tree {rel} missing")
        raw = tree_file.read_bytes()
        accepted = {sha256_bytes(raw)}
        if gate == "github-windows":
            # actions/checkout on Windows applies core.autocrlf=true, so the runner measured the
            # CRLF form of the same blob. Accept exactly that one alternative, nothing looser.
            accepted.add(sha256_bytes(crlf_variant(raw)))
        require(inputs[key] in accepted,
                f"{gate}: inputs_sha256.{key} does not match the in-tree {rel} at P (A5)")


def check_receipt(
    path: pathlib.Path, expected_gate: str, provenance: str, tree: str,
    expected_ref: str | None = None, expected_gate2_repository: str | None = None,
    expected_hosted_repository: str | None = None,
) -> tuple[dict[str, Any], bytes]:
    require(path.is_absolute(), f"receipt path must be absolute: {path}")
    try:
        path.relative_to(ROOT)
    except ValueError:
        pass
    else:
        raise Blocked(f"receipt must remain outside tracked checkout: {path}")

    raw = read_input(path, "receipt")
    data = parse_json_bytes(raw, path, "receipt")
    require(strict_int(data.get("schema")) and data.get("schema") == RECEIPT_SCHEMA,
            f"{expected_gate}: unsupported receipt schema (want {RECEIPT_SCHEMA})")
    require(data.get("gate") == expected_gate, f"{expected_gate}: gate identity mismatch")
    require(data.get("status") == "success", f"{expected_gate}: status is not success")
    require(data.get("commit") == provenance, f"{expected_gate}: exact P mismatch")
    require(data.get("tree") == tree, f"{expected_gate}: tree mismatch")
    require(bool(data.get("run_id")), f"{expected_gate}: run_id missing")
    require(bool(data.get("started_at")) and bool(data.get("ended_at")), f"{expected_gate}: timestamps missing")
    require(bool(data.get("gate_started_at")), f"{expected_gate}: gate_started_at missing (F3: no now() fallback)")
    duration = data.get("duration_seconds")
    require(strict_int(duration), f"{expected_gate}: duration_seconds must be an integer")
    require(duration >= 1, f"{expected_gate}: duration_seconds < 1s, gate_started_at is not a real start")
    checks = data.get("checks")
    require(isinstance(checks, list) and checks, f"{expected_gate}: checks must be a non-empty list")
    check_toolchain(expected_gate, data.get("toolchain"))
    check_test_counts(expected_gate, data.get("test_counts"))
    check_receipt_inputs(expected_gate, data)

    # A3: required identity fields, then exact (never substring) comparison against values the
    # verifier computes itself.
    for field in ("repository", "workflow", "ref", "workflow_ref", "event"):
        require(bool(data.get(field)), f"{expected_gate}: {field} missing")
    run_attempt = data.get("run_attempt")
    require(strict_int(run_attempt) and run_attempt >= 1,
            f"{expected_gate}: run_attempt must be a positive integer (A3)")
    if expected_ref is not None:
        require(data.get("ref") == expected_ref,
                f"{expected_gate}: receipt ref {data.get('ref')!r} != --expected-ref {expected_ref!r} (A3)")
    expected_workflow = EXPECTED_WORKFLOWS[expected_gate]
    expected_repo = expected_gate2_repository if expected_gate == "gate2-linux" else expected_hosted_repository
    require(bool(expected_repo), (
        "gate2-linux requires --expected-gate2-repository" if expected_gate == "gate2-linux"
        else f"{expected_gate} requires --expected-hosted-repository"
    ))
    got_workflow_ref = str(data.get("workflow_ref"))
    expected_workflow_ref = f"{expected_repo}/{expected_workflow}@{data.get('ref')}"
    ref_head, _, ref_tail = got_workflow_ref.rpartition("@")
    require(
        ref_head == f"{expected_repo}/{expected_workflow}",
        f"{expected_gate}: workflow_ref does not name the expected workflow",
    )
    require(
        ref_tail == str(data.get("ref")),
        f"{expected_gate}: workflow_ref ref does not match receipt ref (F2)",
    )
    require(got_workflow_ref == expected_workflow_ref,
            f"{expected_gate}: workflow_ref {got_workflow_ref!r} != expected {expected_workflow_ref!r} (A3)")

    if expected_gate == "gate2-linux":
        require(data.get("provider") == "gitea-actions", "gate2-linux: provider is not Gitea Actions")
        require(data.get("runner_environment") == "clean-linux-runner", "gate2-linux: runner environment mismatch")
        # F6: same-party gate must say so, and must carry repository/event/ref/workflow.
        require(data.get("trust") == "same-party", "gate2-linux: trust must be declared as same-party")
        require(data.get("repository") == expected_repo,
                f"gate2-linux: repository mismatch (want {expected_repo})")
        require(data.get("workflow") == GATE2_WORKFLOW, "gate2-linux: workflow mismatch")
        require(data.get("workflow_ref_source") in {"native", "gitea-derived"},
                "gate2-linux: workflow_ref_source must be native or gitea-derived")
        require(data.get("event") in {"push", "workflow_dispatch"}, "gate2-linux: unsupported event")

    if expected_gate.startswith("github-"):
        require(data.get("provider") == "github-actions-hosted", f"{expected_gate}: provider is not GitHub hosted")
        require(data.get("repository") == expected_repo,
                f"{expected_gate}: repository mismatch (want {expected_repo})")
        require(data.get("workflow") == EXPECTED_GITHUB_WORKFLOWS[expected_gate], f"{expected_gate}: workflow mismatch")
        require(bool(data.get("workflow_run_id")), f"{expected_gate}: workflow run identity missing")
        require(data.get("event") in {"push", "workflow_dispatch"}, f"{expected_gate}: unsupported event")
        require(data.get("runner_environment") == (
            "github-hosted-clean-windows" if expected_gate == "github-windows" else "github-hosted-clean-runner"
        ), f"{expected_gate}: runner environment mismatch (F4)")
        require(
            data.get("runner_label") in ALLOWED_RUNNER_LABELS[expected_gate],
            f"{expected_gate}: runner_label not allowlisted (F4)",
        )
        require(data.get("runner_hosted_environment") == "github-hosted",
                f"{expected_gate}: measured RUNNER_ENVIRONMENT is not github-hosted (F4)")
        expected_runner_os = {"github-linux": "Linux", "github-macos": "macOS", "github-windows": "Windows"}[expected_gate]
        require(data.get("runner_os") == expected_runner_os,
                f"{expected_gate}: measured RUNNER_OS {data.get('runner_os')!r} != {expected_runner_os!r} (F4)")
        for field in ("runner_name", "runner_arch"):
            require(isinstance(data.get(field), str) and bool(data[field]),
                    f"{expected_gate}: measured {field} missing (F4)")
        # F3: status must be derived from the needs graph, and every need must be success.
        needs = data.get("needs")
        require(isinstance(needs, dict) and needs, f"{expected_gate}: needs results missing (F3)")
        bad = {name: result for name, result in needs.items() if result != "success"}
        require(not bad, f"{expected_gate}: upstream jobs not all successful: {bad}")

    if expected_gate == "github-windows":
        require(data.get("runner_environment") == "github-hosted-clean-windows", "github-windows: not a hosted clean runner")
        require(data.get("provider") in ALLOWED_WINDOWS_PROVIDERS, "github-windows: local/self-hosted provider rejected")
        # F8 on Windows: the e2e suite is measured by the `windows-git-bash` job, so the
        # receipt must name it as a successful need. Dropping that job from the workflow
        # would otherwise remove e2e coverage without any count noticing.
        needs = data.get("needs") or {}
        require(
            needs.get(WINDOWS_E2E_NEEDS_JOB) == "success",
            f"github-windows: needs must include {WINDOWS_E2E_NEEDS_JOB}=success (e2e attestation)",
        )
        forbidden = ("vtwsl", "local", "self-hosted", "dshwindowsgate", "scheduled-task", "broker")
        serialized = json.dumps(data, ensure_ascii=False).lower()
        require(not any(marker in serialized for marker in forbidden), "github-windows: local gate evidence rejected")

    # A7: inclusion against the fixed REQUIRED_NEEDS set ("has it" rather than "check what is there").
    if expected_gate.startswith("github-"):
        needs = data["needs"]
        missing_needs = sorted(REQUIRED_NEEDS[expected_gate] - set(needs))
        require(not missing_needs,
                f"{expected_gate}: needs is missing required jobs {missing_needs} (A7)")
    return data, raw


def check_run_identities(checked: dict[str, dict[str, Any]]) -> None:
    """F5: dedupe on the real run id, allowing gates that legitimately share a run."""
    runs: dict[str, dict[str, set[str]]] = {}
    for gate, data in checked.items():
        run_key = str(data.get("workflow_run_id") or data.get("run_id"))
        entry = runs.setdefault(run_key, {"gates": set(), "workflows": set()})
        entry["gates"].add(gate)
        entry["workflows"].add(str(data.get("workflow") or ""))
    for run_key, entry in runs.items():
        require(
            len(entry["workflows"]) == 1,
            f"run identity {run_key} is shared across different workflows: {sorted(entry['workflows'])} (F5)",
        )


def parse_utc(raw: Any, what: str) -> dt.datetime:
    require(isinstance(raw, str) and bool(raw), f"{what} missing")
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise Blocked(f"{what} is not an ISO-8601 timestamp: {raw!r}") from None
    require(parsed.tzinfo is not None, f"{what} must carry a timezone: {raw!r}")
    return parsed.astimezone(dt.timezone.utc)


def check_remote_evidence(
    path: pathlib.Path, gate: str, receipt: dict[str, Any], provenance: str,
    *, expected_ref: str | None = None, expected_gate2_repository: str | None = None,
    expected_hosted_repository: str | None = None,
    max_age_seconds: int = DEFAULT_MAX_EVIDENCE_AGE_SECONDS,
    now: dt.datetime | None = None,
) -> tuple[dict[str, Any], str]:
    """F1 + A4: cross-check a receipt against the forge's own record of the run (deep)."""
    raw = read_input(path, "remote evidence")
    data = parse_json_bytes(raw, path, "remote evidence")
    digest = sha256_bytes(raw)
    gitea = EXPECTED_FORGE[gate] == "gitea"
    require(strict_int(data.get("schema")) and data.get("schema") == EVIDENCE_SCHEMA,
            f"{gate}: remote evidence schema must be {EVIDENCE_SCHEMA}")
    require(data.get("gate") == gate, f"{gate}: remote evidence is for gate {data.get('gate')}")
    require(data.get("forge") == EXPECTED_FORGE[gate],
            f"{gate}: remote evidence forge is {data.get('forge')!r}, want {EXPECTED_FORGE[gate]!r}")
    expected_repository = expected_gate2_repository if gate == "gate2-linux" else expected_hosted_repository
    require(bool(expected_repository), (
        "gate2-linux requires --expected-gate2-repository" if gate == "gate2-linux"
        else f"{gate} requires --expected-hosted-repository"
    ))
    require(data.get("repository") == expected_repository,
            f"{gate}: remote evidence repository {data.get('repository')!r} != {expected_repository!r}")
    if not gitea:
        require(data.get("repository_private") is True,
                f"{gate}: forge metadata does not prove the hosted staging repository is private")
    expected_workflow = EXPECTED_WORKFLOWS[gate]
    evidence_workflow = data.get("workflow_path")
    if gitea:
        # Gitea reports only the workflow file's base name (its run object has no directory), so
        # equality is the strongest check available for that one field.
        require(evidence_workflow in (expected_workflow, pathlib.PurePosixPath(expected_workflow).name),
                f"{gate}: remote evidence workflow_path {evidence_workflow!r} != {expected_workflow!r}")
    else:
        require(evidence_workflow == expected_workflow,
                f"{gate}: remote evidence workflow_path {evidence_workflow!r} != {expected_workflow!r}")
    require(data.get("event") == receipt.get("event"),
            f"{gate}: remote evidence event {data.get('event')!r} != receipt event {receipt.get('event')!r}")
    require(data.get("head_sha") == provenance, f"{gate}: remote evidence head_sha is not exact P")
    require(data.get("conclusion") == "success", f"{gate}: remote evidence conclusion is {data.get('conclusion')!r}")
    if not gitea or data.get("status") is not None:
        require(data.get("status") == "completed", f"{gate}: remote evidence run status is {data.get('status')!r}")
    if not gitea:
        # Only GitHub exposes these; gate2 is validated on the fields Gitea actually returns.
        require(strict_int(data.get("run_attempt")) and data.get("run_attempt") == receipt.get("run_attempt"),
                f"{gate}: remote evidence run_attempt {data.get('run_attempt')!r} != receipt run_attempt {receipt.get('run_attempt')!r}")
        want_branch = head_branch_for_ref(expected_ref if expected_ref is not None else str(receipt.get("ref")))
        require(data.get("head_branch") == want_branch,
                f"{gate}: remote evidence head_branch {data.get('head_branch')!r} != {want_branch!r}")

    jobs = data.get("jobs")
    require(isinstance(jobs, list) and jobs, f"{gate}: remote evidence has no jobs")
    for job in jobs:
        require(isinstance(job, dict), f"{gate}: malformed remote job entry")
        require(job.get("conclusion") == "success", f"{gate}: remote job {job.get('name')!r} concluded {job.get('conclusion')!r}")
        require(job.get("status") == "completed", f"{gate}: remote job {job.get('name')!r} status is {job.get('status')!r}")
    require(strict_int(data.get("jobs_total_count")) and data.get("jobs_total_count") == len(jobs),
            f"{gate}: remote evidence jobs_total_count {data.get('jobs_total_count')!r} != {len(jobs)} jobs listed (truncated listing)")

    names = [job.get("name") for job in jobs]
    required_names: set[str] = set()
    for display_names in REQUIRED_EVIDENCE_JOBS[gate].values():
        required_names.update(display_names)
    missing = sorted(name for name in required_names if name not in names)
    require(not missing, f"{gate}: remote evidence is missing required jobs {missing} (A4)")

    if not gitea:
        # Hosted-runner hard signals, per job. A run may include jobs of other gates (ci.yml
        # carries both Linux and macOS), so non-required jobs are held to the union of labels.
        for job in jobs:
            labels = job.get("labels")
            name = job.get("name")
            require(isinstance(labels, list) and all(isinstance(label, str) for label in labels) and labels,
                    f"{gate}: remote job {name!r} has no labels")
            require("self-hosted" not in {label.lower() for label in labels},
                    f"{gate}: remote job {name!r} ran on a self-hosted runner")
            allowed = ALLOWED_RUNNER_LABELS[gate] if name in required_names else ALL_HOSTED_LABELS
            require(bool(set(labels) & allowed),
                    f"{gate}: remote job {name!r} labels {labels} do not intersect allowed {sorted(allowed)}")
            require(job.get("runner_group_name") == HOSTED_RUNNER_GROUP,
                    f"{gate}: remote job {name!r} runner_group_name {job.get('runner_group_name')!r} != {HOSTED_RUNNER_GROUP!r}")

    fetched = parse_utc(data.get("fetched_at"), f"{gate}: remote evidence fetched_at")
    current = now or dt.datetime.now(dt.timezone.utc)
    age = (current - fetched).total_seconds()
    require(age >= -EVIDENCE_FUTURE_SKEW_SECONDS, f"{gate}: remote evidence fetched_at is in the future")
    require(age <= max_age_seconds,
            f"{gate}: remote evidence is stale ({int(age)}s old, limit {max_age_seconds}s)")

    receipt_run = str(receipt.get("workflow_run_id") or receipt.get("run_id"))
    remote_run = str(data.get("run_id") or "")
    require(
        remote_run.split(":")[0] == receipt_run.split(":")[0],
        f"{gate}: remote evidence run {remote_run!r} does not match receipt run {receipt_run!r}",
    )

    artifacts = data.get("artifacts")
    require(isinstance(artifacts, list), f"{gate}: remote evidence artifacts must be a list")
    require(isinstance(data.get("artifacts_available"), bool), f"{gate}: remote evidence artifacts_available must be a boolean")
    if data["artifacts_available"]:
        require(strict_int(data.get("artifacts_total_count")) and data["artifacts_total_count"] == len(artifacts),
                f"{gate}: remote evidence artifacts_total_count {data.get('artifacts_total_count')!r} != {len(artifacts)} listed")
    else:
        require(not artifacts, f"{gate}: remote evidence lists artifacts but artifacts_available is false")
    return data, digest


def check_artifact(
    gate: str, zip_path: pathlib.Path, receipt_raw: bytes, receipt: dict[str, Any],
    evidence: dict[str, Any] | None, provenance: str,
) -> dict[str, Any]:
    """A5: tie the receipt to the artifact the forge recorded (digest) and prove the zip carries
    exactly this receipt (byte-for-byte). Returns the artifact_digest entry for the output."""
    require(zip_path.is_absolute(), f"{gate}: --artifact path must be absolute: {zip_path}")
    try:
        zip_path.relative_to(ROOT)
    except ValueError:
        pass
    else:
        raise Blocked(f"{gate}: artifact must remain outside tracked checkout: {zip_path}")
    raw = read_input(zip_path, f"{gate} artifact")
    zip_digest = sha256_bytes(raw)
    receipt_name = f"{gate}.json"
    try:
        with zipfile.ZipFile(zip_path) as archive:
            require(archive.testzip() is None, f"{gate}: artifact zip is corrupt")
            entries: dict[str, zipfile.ZipInfo] = {}
            for info in archive.infolist():
                if info.is_dir():
                    continue
                parts = pathlib.PurePosixPath(info.filename).parts
                require(".." not in parts and not info.filename.startswith("/"),
                        f"{gate}: artifact zip entry has an unsafe path: {info.filename!r}")
                base = parts[-1]
                require(base not in entries, f"{gate}: artifact zip has duplicate entry {base!r}")
                entries[base] = info
            require(receipt_name in entries, f"{gate}: artifact zip does not contain {receipt_name}")
            for base in (receipt_name, "test-counts.json"):
                if base in entries:
                    require(entries[base].file_size <= MAX_ZIP_ENTRY_BYTES, f"{gate}: artifact entry {base} is too large")
            require(archive.read(entries[receipt_name]) == receipt_raw,
                    f"{gate}: artifact zip receipt bytes differ from the --receipt file")
            if "test-counts.json" in entries:
                counts_sha = sha256_bytes(archive.read(entries["test-counts.json"]))
                require(counts_sha == receipt["inputs_sha256"]["test-counts.json"],
                        f"{gate}: artifact zip test-counts.json does not match receipt inputs_sha256 (A5)")
            elif gate.startswith("github-"):
                raise Blocked(f"{gate}: artifact zip does not contain test-counts.json")
    except zipfile.BadZipFile:
        raise Blocked(f"{gate}: artifact is not a valid zip") from None

    name = f"{gate}-{provenance}"
    entry: dict[str, Any] = {"artifact_digest_bound": False, "name": name, "zip_sha256": zip_digest,
                             "receipt_bytes_match": True}
    if evidence is None:
        entry["reason"] = "no remote evidence for this gate; digest cannot be bound"
        return entry
    listed = [a for a in evidence.get("artifacts", []) if isinstance(a, dict) and a.get("name") == name]
    if not evidence.get("artifacts_available") or not listed:
        require(gate == "gate2-linux", f"{gate}: remote evidence has no artifact named {name!r} to bind (A5)")
        entry["reason"] = "gitea exposes no artifact digest; receipt is not forge-bound"
        return entry
    require(len(listed) == 1, f"{gate}: remote evidence lists artifact {name!r} {len(listed)} times")
    artifact = listed[0]
    require(artifact.get("expired") is False, f"{gate}: artifact {name!r} is expired")
    digest = artifact.get("digest")
    require(isinstance(digest, str) and digest.startswith("sha256:"),
            f"{gate}: artifact {name!r} has no sha256 digest in the evidence")
    require(digest.removeprefix("sha256:").lower() == zip_digest,
            f"{gate}: artifact digest mismatch: evidence {digest.removeprefix('sha256:')[:12]}... != zip {zip_digest[:12]}...")
    entry.update({"artifact_digest_bound": True, "digest": digest})
    return entry


def check_trust_anchor(anchor: pathlib.Path) -> dict[str, Any]:
    """F9 + A2: the verifier must not be the only copy of itself. `anchor` is a directory holding
    pinned, byte-identical external copies of every ANCHORED_SCRIPTS file."""
    try:
        anchor_mode = anchor.lstat().st_mode
    except OSError:
        raise Blocked(f"trust anchor not found: {anchor}") from None
    require(not os.path.islink(anchor) and not stat.S_ISLNK(anchor_mode),
            f"trust anchor must not be a symlink: {anchor}")
    require(anchor.is_dir(), f"trust anchor must be an existing directory (rc7: --trust-anchor takes a directory): {anchor}")
    real = pathlib.Path(os.path.realpath(anchor))
    try:
        real.relative_to(ROOT)
    except ValueError:
        pass
    else:
        raise Blocked(f"trust anchor {real} is inside the checkout {ROOT}; it must live outside the tree being verified")
    missing = []
    for name in ANCHORED_SCRIPTS:
        pinned = anchor / name
        try:
            info = pinned.lstat()
        except OSError:
            missing.append(name)
            continue
        require(not stat.S_ISLNK(info.st_mode), f"trust anchor file {name} is a symlink: {pinned}")
        require(stat.S_ISREG(info.st_mode), f"trust anchor file {name} is not a regular file: {pinned}")
    require(not missing, "trust anchor is missing pinned scripts: " + ", ".join(missing))
    digests: dict[str, str] = {}
    mismatched = []
    for name in ANCHORED_SCRIPTS:
        pinned = anchor / name
        tree_file = ROOT / "scripts" / name
        require(tree_file.is_file() and not tree_file.is_symlink(), f"in-tree scripts/{name} missing or a symlink")
        a, t = pinned.stat(), tree_file.stat()
        require((a.st_ino, a.st_dev) != (t.st_ino, t.st_dev),
                f"trust anchor file {name} is the same inode as the in-tree copy (self-compare proves nothing)")
        pinned_digest, tree_digest = sha256_file(pinned), sha256_file(tree_file)
        digests[name] = pinned_digest
        if pinned_digest != tree_digest:
            mismatched.append(f"{name}: in-tree {tree_digest[:12]}... differs from trust anchor {pinned_digest[:12]}...")
    require(not mismatched, "; ".join(mismatched) + " (F9)")
    return {"path": str(real), "sha256": digests, "covers": list(ANCHORED_SCRIPTS)}


def ignored_entry_allowed(path: str, extra: list[str]) -> bool:
    """A8: allow bounded build noise plus three exact compatibility symlinks."""
    is_dir = path.endswith("/")
    norm = path.rstrip("/")
    compat_target = COMPAT_IGNORED_SYMLINKS.get(norm)
    if compat_target is not None:
        entry, target = ROOT / norm, ROOT / compat_target
        try:
            return entry.is_symlink() and target.is_file() and entry.resolve(strict=True) == target.resolve(strict=True)
        except OSError:
            return False
    parts = norm.split("/")
    for pattern in IGNORED_WHITELIST:
        if pattern.endswith("/"):
            components = parts if is_dir else parts[:-1]
            if any(fnmatch.fnmatchcase(component, pattern[:-1]) for component in components):
                return True
        elif not is_dir and fnmatch.fnmatchcase(parts[-1], pattern):
            return True
    return any(norm == e or norm.startswith(e + "/") for e in (item.rstrip("/") for item in extra))


def check_ignored_surface(extra: list[str]) -> list[str]:
    """A8: untracked-but-ignored files are invisible to `git diff` and to ls-files -o
    --exclude-standard; anything outside the whitelist could be hidden build input."""
    records = git_bytes("status", "--porcelain", "-z", "--ignored").decode("utf-8", "replace").split("\0")
    ignored: list[str] = []
    dirty: list[str] = []
    skip_next = False
    for record in records:
        if skip_next:  # rename/copy origin path
            skip_next = False
            continue
        if not record:
            continue
        code, path = record[:2], record[3:]
        if code[0] in "RC":
            skip_next = True
        (ignored if code == "!!" else dirty).append(path)
    require(not dirty, f"checkout is not clean: {dirty[:5]}")
    rejected = [path for path in ignored if not ignored_entry_allowed(path, extra)]
    require(not rejected, f"checkout has non-whitelisted ignored files: {rejected[:5]} (A8; --allow-ignored PATH to extend)")
    return ignored


def parse_gate_paths(items: list[str], option: str, *, unique: bool = True) -> dict[str, pathlib.Path]:
    paths: dict[str, pathlib.Path] = {}
    for item in items:
        gate, sep, raw_path = item.partition("=")
        require(bool(sep) and gate in REQUIRED_GATES and bool(raw_path), f"invalid {option} value: {item}")
        require(not (unique and gate in paths), f"duplicate {option} for {gate}")
        paths[gate] = pathlib.Path(raw_path)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--content", required=True, help="full 40-hex content commit D")
    parser.add_argument("--provenance", required=True, help="full 40-hex provenance commit P")
    parser.add_argument("--receipt", action="append", default=[], metavar="GATE=ABSOLUTE_PATH")
    parser.add_argument("--remote-evidence", action="append", default=[], metavar="GATE=ABSOLUTE_PATH")
    parser.add_argument("--artifact", action="append", default=[], metavar="GATE=ABSOLUTE_ZIP",
                        help="downloaded artifact zip for GATE; its sha256 must equal the digest the forge "
                             "recorded for artifact GATE-P, and its receipt must equal --receipt byte-for-byte (A5)")
    parser.add_argument("--require-remote-evidence", action="store_true",
                        help="DEPRECATED no-op kept for compatibility: evidence is always required for "
                             "`verified` (rc7 A1)")
    parser.add_argument("--trust-anchor", metavar="DIR",
                        help="directory outside the checkout holding pinned copies of "
                             + ", ".join(ANCHORED_SCRIPTS) + " (A2); required for `verified`")
    parser.add_argument("--expected-ref", metavar="refs/heads/BRANCH",
                        help="the ref the receipts must name; required for `verified` (A3)")
    parser.add_argument("--expected-gate2-repository", metavar="OWNER/REPOSITORY",
                        help="private gate2 repository identity; required when gate2 is supplied; no default")
    parser.add_argument("--expected-hosted-repository", metavar="OWNER/REPOSITORY",
                        help="private GitHub staging repository identity; required when hosted gates are supplied; no default")
    parser.add_argument("--max-evidence-age-seconds", type=int, default=DEFAULT_MAX_EVIDENCE_AGE_SECONDS,
                        help="maximum age of remote evidence `fetched_at` (default 30 days)")
    parser.add_argument("--allow-missing-evidence", action="store_true",
                        help="finish without full remote evidence; forces configured-unverified")
    parser.add_argument("--allow-missing-anchor", action="store_true",
                        help="finish without --trust-anchor; forces configured-unverified")
    parser.add_argument("--allow-ignored", action="append", default=[], metavar="PATH",
                        help="extend the ignored-file whitelist with a repo-relative path (repeatable)")
    parser.add_argument("--allow-partial", action="store_true", help="validate supplied receipts without declaring release-ready")
    args = parser.parse_args()

    try:
        require(not SELF_PATH.is_symlink(), f"release-preflight.py is a symlink ({SELF_PATH}); refusing to run (A2)")
        require(FULL_SHA.fullmatch(args.content) is not None, "D must be a full lowercase 40-hex SHA")
        require(FULL_SHA.fullmatch(args.provenance) is not None, "P must be a full lowercase 40-hex SHA")
        require(args.max_evidence_age_seconds > 0, "--max-evidence-age-seconds must be positive")
        if args.expected_ref is not None:
            require(args.expected_ref.startswith("refs/") and len(args.expected_ref) > len("refs/")
                    and not any(ch.isspace() for ch in args.expected_ref),
                    f"--expected-ref must look like refs/heads/<branch>: {args.expected_ref!r}")
        for allowed in args.allow_ignored:
            require(bool(allowed) and not pathlib.PurePosixPath(allowed).is_absolute()
                    and ".." not in pathlib.PurePosixPath(allowed).parts,
                    f"--allow-ignored must be a repo-relative path: {allowed!r}")
        content = git("rev-parse", f"{args.content}^{{commit}}").strip()
        provenance = git("rev-parse", f"{args.provenance}^{{commit}}").strip()
        require(content == args.content, "D does not resolve to the exact commit")
        require(provenance == args.provenance, "P does not resolve to the exact commit")
        parents = git("show", "-s", "--format=%P", provenance).strip().split()
        require(parents == [content], "P must have exactly one parent and P^ must equal D")
        changed = [line for line in git("diff", "--no-ext-diff", "--no-textconv", "--name-only", content, provenance).splitlines() if line]
        require(changed == ["PROVENANCE.json"], "P must change only PROVENANCE.json")

        provenance_raw = git_bytes("show", f"{provenance}:PROVENANCE.json")
        manifest = json.loads(provenance_raw)
        require(manifest.get("schema") == 2, "unsupported provenance schema")
        require(manifest.get("source_commit") == content, "PROVENANCE source_commit must equal D")
        tree = git("rev-parse", f"{provenance}^{{tree}}").strip()

        blocked_reasons: list[str] = []
        relaxed_checks: list[str] = []

        # A2: filesystem-only, so it runs before anything that depends on the checkout state
        # (an anchor placed inside the repo is reported as such, not as "untracked files").
        anchor_info = None
        if args.trust_anchor:
            anchor_info = check_trust_anchor(pathlib.Path(args.trust_anchor).expanduser())
        elif args.allow_missing_anchor:
            relaxed_checks.append("allow-missing-anchor: trust anchor not supplied")
        else:
            blocked_reasons.append("missing --trust-anchor: the verifier is checking itself (A2)")

        # check-provenance validates worktree bytes, so require the checkout to be exact P.
        require(git("rev-parse", "HEAD").strip() == provenance, "checkout HEAD must equal exact P")
        unstaged = git_run("diff", "--no-ext-diff", "--no-textconv", "--quiet").returncode
        staged = git_run("diff", "--no-ext-diff", "--no-textconv", "--cached", "--quiet").returncode
        require(unstaged == 0 and staged == 0, "tracked checkout must be clean")
        # F10: git diff cannot see untracked files, so reject them explicitly.
        untracked = [line for line in git("ls-files", "-o", "--exclude-standard").splitlines() if line.strip()]
        require(not untracked, f"checkout has untracked files: {untracked[:5]} (F10)")
        # A8: ... nor ignored ones, which are only tolerated on a short whitelist.
        check_ignored_surface(args.allow_ignored)

        # A2: when an anchor is present, the anchor copy executes; the in-tree copy never does.
        checker = (pathlib.Path(anchor_info["path"]) / "check-provenance.py") if anchor_info \
            else ROOT / "scripts" / "check-provenance.py"
        check = subprocess.run(
            [sys.executable, str(checker), "--root", str(ROOT)], cwd=ROOT, text=True,
            encoding="utf-8", errors="replace", capture_output=True, check=False,
            env={**clean_git_env(), "PYTHONDONTWRITEBYTECODE": "1"},
        )
        require(check.returncode == 0, "check-provenance failed")
        require(f"baseline={content}" in check.stdout, "check-provenance baseline mismatch")

        receipt_paths = parse_gate_paths(args.receipt, "--receipt")
        if "gate2-linux" in receipt_paths:
            require(bool(args.expected_gate2_repository), "gate2-linux requires --expected-gate2-repository")
            require(bool(REPOSITORY_ID.fullmatch(args.expected_gate2_repository)),
                    "--expected-gate2-repository must be owner/name")
        if any(gate.startswith("github-") for gate in receipt_paths):
            require(bool(args.expected_hosted_repository),
                    "hosted gates require --expected-hosted-repository")
            require(bool(REPOSITORY_ID.fullmatch(args.expected_hosted_repository)),
                    "--expected-hosted-repository must be owner/name")
        missing = [gate for gate in REQUIRED_GATES if gate not in receipt_paths]
        if not args.allow_partial:
            require(not missing, "missing required receipts: " + ", ".join(missing))
        elif missing:
            relaxed_checks.append("allow-partial: no receipt for " + ", ".join(missing))
            blocked_reasons.append("missing required receipts: " + ", ".join(missing))
        evidence_paths = parse_gate_paths(args.remote_evidence, "--remote-evidence")
        artifact_paths = parse_gate_paths(args.artifact, "--artifact")
        for gate in list(evidence_paths) + list(artifact_paths):
            require(gate in receipt_paths, f"{gate}: evidence/artifact supplied without a receipt for the gate")

        checked: dict[str, dict[str, Any]] = {}
        receipt_raw: dict[str, bytes] = {}
        evidence_data: dict[str, dict[str, Any]] = {}
        sha_inputs: dict[str, Any] = {"receipts": {}, "remote_evidence": {}, "artifacts": {}}
        for gate, path in receipt_paths.items():
            data, raw = check_receipt(
                path, gate, provenance, tree, args.expected_ref,
                args.expected_gate2_repository, args.expected_hosted_repository,
            )
            checked[gate] = data
            receipt_raw[gate] = raw
            sha_inputs["receipts"][gate] = sha256_bytes(raw)
            if gate in evidence_paths:
                evidence, evidence_digest = check_remote_evidence(
                    evidence_paths[gate], gate, data, provenance,
                    expected_ref=args.expected_ref,
                    expected_gate2_repository=args.expected_gate2_repository,
                    expected_hosted_repository=args.expected_hosted_repository,
                    max_age_seconds=args.max_evidence_age_seconds,
                )
                evidence_data[gate] = evidence
                sha_inputs["remote_evidence"][gate] = evidence_digest
        check_run_identities(checked)

        missing_evidence = [gate for gate in receipt_paths if gate not in evidence_data]
        remote_complete = not [gate for gate in REQUIRED_GATES if gate not in evidence_data]
        if missing_evidence or not remote_complete:
            lacking = [gate for gate in REQUIRED_GATES if gate not in evidence_data]
            reason = "missing remote evidence for: " + ", ".join(lacking)
            if args.allow_missing_evidence:
                relaxed_checks.append("allow-missing-evidence: " + reason)
            else:
                blocked_reasons.append(reason)
        if args.expected_ref is None:
            blocked_reasons.append("missing --expected-ref: receipts are not pinned to a release ref (A3)")

        artifact_digest: dict[str, dict[str, Any]] = {}
        for gate, path in artifact_paths.items():
            artifact_digest[gate] = check_artifact(
                gate, path.expanduser(), receipt_raw[gate], checked[gate], evidence_data.get(gate), provenance)
            sha_inputs["artifacts"][gate] = artifact_digest[gate]["zip_sha256"]
        for gate in receipt_paths:
            if gate in artifact_digest:
                if not artifact_digest[gate]["artifact_digest_bound"] and gate != "gate2-linux":
                    blocked_reasons.append(f"{gate}: artifact digest not bound ({artifact_digest[gate].get('reason')})")
            elif gate == "gate2-linux":
                artifact_digest[gate] = {"artifact_digest_bound": False, "name": f"{gate}-{provenance}",
                                         "reason": "gitea exposes no artifact digest; receipt is not forge-bound"}
            else:
                blocked_reasons.append(f"{gate}: no --artifact supplied; receipt is not bound to a forge artifact digest (A5)")
                artifact_digest[gate] = {"artifact_digest_bound": False, "name": f"{gate}-{provenance}",
                                         "reason": "no --artifact supplied"}

        if relaxed_checks and not blocked_reasons:
            blocked_reasons.append("relaxed checks in effect; `verified` is unreachable: " + "; ".join(relaxed_checks))
        status = "verified" if not blocked_reasons and not relaxed_checks else "configured-unverified"
        sha_inputs["PROVENANCE.json"] = sha256_bytes(provenance_raw)

        result = {
            "status": status,
            "blocked_reasons": blocked_reasons,
            "relaxed_checks": relaxed_checks,
            "trust_ceiling": TRUST_CEILING,
            "content": content,
            "provenance": provenance,
            "tree": tree,
            "provenance_only": True,
            "receipts": {gate: str(path) for gate, path in receipt_paths.items()},
            "remote_evidence": {gate: str(evidence_paths[gate]) for gate in evidence_data},
            "remote_evidence_complete": remote_complete,
            "inputs_sha256": sha_inputs,
            "anchor": anchor_info,
            "trust_anchor_sha256": anchor_info["sha256"]["release-preflight.py"] if anchor_info else None,
            "expected_ref": args.expected_ref,
            "expected_gate2_repository": args.expected_gate2_repository,
            "expected_hosted_repository": args.expected_hosted_repository,
            "artifact_digest": artifact_digest,
            "artifact_digest_bound": {gate: item["artifact_digest_bound"] for gate, item in artifact_digest.items()},
            "missing_receipts": missing,
            "windows_contract": "github-hosted-clean-runner-only",
            "receipt_schema": RECEIPT_SCHEMA,
            "schema": RECEIPT_SCHEMA,
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        if status != "verified":
            print("RELEASE_PREFLIGHT_UNVERIFIED: " + "; ".join(blocked_reasons), file=sys.stderr)
        return 0
    except (Blocked, json.JSONDecodeError) as exc:
        print(f"RELEASE_PREFLIGHT_BLOCKED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
