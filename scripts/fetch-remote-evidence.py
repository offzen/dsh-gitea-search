#!/usr/bin/env python3
"""Fetch forge-side evidence for release receipts (rc6 F1).

Why this exists: every receipt is written by the candidate's own CI code, so a
receipt can only ever be *self*-attesting. `release-preflight.py` deliberately
cannot reach the network, so it accepts the forge's answer as a separate input:

    python3 scripts/fetch-remote-evidence.py --gate github-linux --run-id 42334002845 \
        --output /secure/evidence/github-linux.json

The output is the forge's own record — run head_sha, conclusion, and every job's
conclusion — normalised to the small shape the verifier checks:

    {"gate", "run_id", "head_sha", "conclusion", "jobs": [{"name", "conclusion"}]}

Read-only. Credentials come from the environment (GITHUB_TOKEN / GITEA_TOKEN) and
are never written to the output or to stdout.

Exit codes: 0 evidence written, 2 could not fetch a usable answer.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

GITHUB_REPO = "offzen/dsh-gitea-search"
GITEA_REPO = "zaoz/dsh-gitea-search"
GITEA_BASE = "https://git.orca.kdns.fr/api/v1"
GATES = ("gate2-linux", "github-linux", "github-macos", "github-windows")


def get(url: str, headers: dict[str, str]) -> tuple[int, object]:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, {"error": exc.read()[:300].decode("utf-8", "replace")}
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": f"{type(exc).__name__}: {exc}"}


def github_evidence(gate: str, run_id: str) -> dict:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    status, run = get(f"https://api.github.com/repos/{GITHUB_REPO}/actions/runs/{run_id}", headers)
    if status != 200 or not isinstance(run, dict):
        raise SystemExit(f"FETCH_EVIDENCE_BLOCKED: run {run_id} -> HTTP {status} {run.get('error', '')[:160]}")
    status, jobs = get(f"https://api.github.com/repos/{GITHUB_REPO}/actions/runs/{run_id}/jobs?per_page=100", headers)
    if status != 200 or not isinstance(jobs, dict):
        raise SystemExit(f"FETCH_EVIDENCE_BLOCKED: jobs for {run_id} -> HTTP {status}")
    return {
        "gate": gate,
        "provider": "github-actions-hosted",
        "run_id": str(run.get("id") or run_id),
        "head_sha": run.get("head_sha"),
        "conclusion": run.get("conclusion"),
        "event": run.get("event"),
        "head_branch": run.get("head_branch"),
        "workflow_name": run.get("name"),
        "jobs": [
            {"name": job.get("name"), "conclusion": job.get("conclusion"),
             "labels": job.get("labels") or [], "runner_name": job.get("runner_name")}
            for job in jobs.get("jobs", [])
        ],
    }


def gitea_evidence(gate: str, run_id: str) -> dict:
    token = os.environ.get("GITEA_TOKEN") or ""
    headers = {"Authorization": f"token {token}"} if token else {}
    status, run = get(f"{GITEA_BASE}/repos/{GITEA_REPO}/actions/runs/{run_id}", headers)
    # On the 2026-11 Gitea build, GET /actions/runs/{index} answers 403 ("Only signed
    # in user is allowed to call APIs") for a read token while /jobs works, so the run
    # metadata is reconstructed from the jobs response and the tasks listing instead.
    run_ok = status == 200 and isinstance(run, dict) and run.get("head_sha")
    if not run_ok:
        status, jobs = get(f"{GITEA_BASE}/repos/{GITEA_REPO}/actions/runs/{run_id}/jobs", headers)
        items = jobs if isinstance(jobs, list) else (jobs.get("jobs", []) if isinstance(jobs, dict) else [])
        if status != 200 or not items:
            raise SystemExit(f"FETCH_EVIDENCE_BLOCKED: run {run_id} -> HTTP {status} (run and jobs both unusable)")
        run = {"id": run_id, "head_sha": items[0].get("head_sha"), "event": None,
               "name": items[0].get("name"), "conclusion": None}
    else:
        status, jobs = get(f"{GITEA_BASE}/repos/{GITEA_REPO}/actions/runs/{run_id}/jobs", headers)
        items = jobs if isinstance(jobs, list) else (jobs.get("jobs", []) if isinstance(jobs, dict) else [])
    conclusions = [j.get("conclusion") for j in items if isinstance(j, dict)]
    return {
        "gate": gate,
        "provider": "gitea-actions",
        "run_id": str(run.get("id") or run_id),
        "head_sha": run.get("head_sha") or (items[0].get("head_sha") if items else None),
        "conclusion": run.get("conclusion") or ("success" if conclusions and all(c == "success" for c in conclusions) else "failure"),
        "event": run.get("event"),
        "workflow_name": run.get("name") or run.get("title"),
        "jobs": [
            {"name": job.get("name"), "conclusion": job.get("conclusion"), "id": job.get("id")}
            for job in items if isinstance(job, dict)
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", required=True, choices=GATES)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()

    evidence = (gitea_evidence if args.gate == "gate2-linux" else github_evidence)(args.gate, args.run_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(
        f"FETCH_EVIDENCE_OK gate={args.gate} run={evidence['run_id']} "
        f"head_sha={evidence['head_sha']} conclusion={evidence['conclusion']} jobs={len(evidence['jobs'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
