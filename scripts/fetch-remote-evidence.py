#!/usr/bin/env python3
"""Fetch forge-side evidence for release receipts (rc6 F1).

Why this exists: every receipt is written by the candidate's own CI code, so a
receipt can only ever be *self*-attesting. `release-preflight.py` deliberately
cannot reach the network, so it accepts the forge's answer as a separate input:

    python3 scripts/fetch-remote-evidence.py --gate github-linux --run-id 42334002845 \
        --output /secure/evidence/github-linux.json

The output is the forge's own record, normalised to the shape the verifier checks.

rc7 (A4) evidence schema 2:

    schema, forge ("github"|"gitea"), gate, provider, repository, workflow_path, workflow_name,
    event, head_branch, run_attempt, head_sha, conclusion, status, run_id,
    jobs[{name, conclusion, status, labels, runner_name, runner_group_name}],
    jobs_total_count, artifacts[{name, digest, size_in_bytes, expired}],
    artifacts_total_count, artifacts_available, fetched_at (UTC ISO-8601)

Jobs and artifacts are paged (`per_page=100`, `page=N`) until `total_count` is reached and the
count is asserted, because the first page alone silently truncates a run with >30/100 jobs.
Gitea cannot supply every field (no run_attempt, no runner group, usually no artifact digest);
fields it cannot answer are left null / `artifacts_available=false` and are NEVER fabricated.

Read-only. Credentials come from the environment (GITHUB_TOKEN / GITEA_TOKEN) and
are never written to the output or to stdout/stderr.

Exit codes: 0 evidence written, 2 could not fetch a usable answer (or --run-id not numeric).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

GATES = ("gate2-linux", "github-linux", "github-macos", "github-windows")
EVIDENCE_SCHEMA = 2
PER_PAGE = 100
MAX_PAGES = 50
RUN_ID = re.compile(r"[0-9]+")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def get(url: str, headers: dict[str, str]) -> tuple[int, object]:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, {"error": exc.read()[:300].decode("utf-8", "replace")}
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": f"{type(exc).__name__}: {exc}"}


def secrets() -> list[str]:
    return [value for value in (os.environ.get(name) for name in ("GITHUB_TOKEN", "GH_TOKEN", "GITEA_TOKEN")) if value]


def scrub(text: object) -> str:
    """Server error bodies are echoed into exit messages; make sure no credential can ride along."""
    out = str(text)
    for secret in secrets():
        out = out.replace(secret, "***")
    return out


def blocked(message: str) -> SystemExit:
    return SystemExit("FETCH_EVIDENCE_BLOCKED: " + scrub(message))


def paged(url: str, key: str, headers: dict[str, str], what: str) -> tuple[list, int]:
    """Fetch every page of `url` (which must not already carry a query string) and assert the
    item count equals the server's own `total_count`."""
    items: list = []
    total = None
    for page in range(1, MAX_PAGES + 1):
        status, payload = get(f"{url}?per_page={PER_PAGE}&page={page}", headers)
        if status != 200 or not isinstance(payload, dict):
            raise blocked(f"{what} page {page} -> HTTP {status}")
        count = payload.get("total_count")
        chunk = payload.get(key)
        if isinstance(count, bool) or not isinstance(count, int) or not isinstance(chunk, list):
            raise blocked(f"{what} page {page} has no total_count/{key}")
        if total is not None and count != total:
            raise blocked(f"{what} total_count changed while paging ({total} -> {count})")
        total = count
        items.extend(chunk)
        if len(items) >= total or not chunk:
            break
    if total is None or len(items) != total:
        raise blocked(f"{what}: fetched {len(items)} items but total_count is {total} (truncated)")
    return items, total


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def workflow_path_of(path: object) -> str | None:
    """Run `path` looks like `.github/workflows/ci.yml@refs/heads/main`; keep only the file."""
    if not isinstance(path, str) or not path:
        return None
    return path.split("@", 1)[0]


def github_evidence(gate: str, run_id: str, repository: str) -> dict:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    base = f"https://api.github.com/repos/{repository}/actions/runs/{run_id}"
    status, run = get(base, headers)
    if status != 200 or not isinstance(run, dict):
        err = run.get("error", "") if isinstance(run, dict) else ""
        raise blocked(f"run {run_id} -> HTTP {status} {str(err)[:160]}")
    jobs, jobs_total = paged(f"{base}/jobs", "jobs", headers, f"jobs for {run_id}")
    artifacts, artifacts_total = paged(f"{base}/artifacts", "artifacts", headers, f"artifacts for {run_id}")
    repository_data = run.get("repository") if isinstance(run.get("repository"), dict) else {}
    return {
        "schema": EVIDENCE_SCHEMA,
        "forge": "github",
        "gate": gate,
        "provider": "github-actions-hosted",
        "repository": repository_data.get("full_name"),
        "repository_private": repository_data.get("private"),
        "workflow_path": workflow_path_of(run.get("path")),
        "workflow_name": run.get("name"),
        "event": run.get("event"),
        "head_branch": run.get("head_branch"),
        "run_attempt": run.get("run_attempt"),
        "head_sha": run.get("head_sha"),
        "conclusion": run.get("conclusion"),
        "status": run.get("status"),
        "run_id": str(run.get("id") or run_id),
        "jobs": [
            {"name": job.get("name"), "conclusion": job.get("conclusion"), "status": job.get("status"),
             "labels": job.get("labels") or [], "runner_name": job.get("runner_name"),
             "runner_group_name": job.get("runner_group_name")}
            for job in jobs if isinstance(job, dict)
        ],
        "jobs_total_count": jobs_total,
        "artifacts": [
            {"name": a.get("name"), "digest": a.get("digest"), "size_in_bytes": a.get("size_in_bytes"),
             "expired": a.get("expired")}
            for a in artifacts if isinstance(a, dict)
        ],
        "artifacts_total_count": artifacts_total,
        "artifacts_available": True,
        "fetched_at": utc_now(),
    }


def gitea_task(run_id: str, headers: dict[str, str], api_base: str, repository: str) -> dict:
    """Best effort: Gitea's tasks listing carries event / head_branch / workflow_id per run."""
    for page in range(1, 6):
        status, payload = get(f"{api_base}/repos/{repository}/actions/tasks?limit=50&page={page}", headers)
        rows = payload.get("workflow_runs") if status == 200 and isinstance(payload, dict) else None
        if not isinstance(rows, list) or not rows:
            return {}
        for row in rows:
            if isinstance(row, dict) and str(row.get("run_number")) == run_id:
                return row
    return {}


def gitea_evidence(gate: str, run_id: str, api_base: str, repository: str) -> dict:
    token = os.environ.get("GITEA_TOKEN") or ""
    headers = {"Authorization": f"token {token}"} if token else {}
    status, run = get(f"{api_base}/repos/{repository}/actions/runs/{run_id}", headers)
    # On the 2026-11 Gitea build, GET /actions/runs/{index} answers 403 ("Only signed
    # in user is allowed to call APIs") for a read token while /jobs works, so the run
    # metadata is reconstructed from the jobs response and the tasks listing instead.
    run_ok = status == 200 and isinstance(run, dict) and run.get("head_sha")
    status, jobs = get(f"{api_base}/repos/{repository}/actions/runs/{run_id}/jobs", headers)
    items = jobs if isinstance(jobs, list) else (jobs.get("jobs", []) if isinstance(jobs, dict) else [])
    if status != 200 or not items:
        raise blocked(f"run {run_id} -> HTTP {status} (run and jobs both unusable)" if not run_ok
                      else f"jobs for {run_id} -> HTTP {status}")
    items = [job for job in items if isinstance(job, dict)]
    jobs_total = jobs.get("total_count") if isinstance(jobs, dict) else None
    if isinstance(jobs_total, bool) or not isinstance(jobs_total, int):
        jobs_total = len(items)
    if jobs_total != len(items):
        raise blocked(f"jobs for {run_id}: fetched {len(items)} but total_count is {jobs_total}")
    if not run_ok:
        run = {"id": run_id, "head_sha": items[0].get("head_sha"), "event": None,
               "name": items[0].get("name"), "conclusion": None}
    task = gitea_task(run_id, headers, api_base, repository) if not (run.get("event") and run.get("path")) else {}
    conclusions = [j.get("conclusion") for j in items]
    artifacts: list = []
    artifacts_available = False
    a_status, a_payload = get(f"{api_base}/repos/{repository}/actions/runs/{run_id}/artifacts", headers)
    if a_status == 200 and isinstance(a_payload, dict) and isinstance(a_payload.get("artifacts"), list):
        artifacts = [
            {"name": a.get("name"), "digest": a.get("digest"), "size_in_bytes": a.get("size_in_bytes"),
             "expired": a.get("expired")}
            for a in a_payload["artifacts"] if isinstance(a, dict)
        ]
        artifacts_available = True
    return {
        "schema": EVIDENCE_SCHEMA,
        "forge": "gitea",
        "gate": gate,
        "provider": "gitea-actions",
        "repository": repository,
        "workflow_path": workflow_path_of(run.get("path")) or task.get("workflow_id") or None,
        "workflow_name": run.get("name") or run.get("title"),
        "event": run.get("event") or task.get("event"),
        "head_branch": run.get("head_branch") or task.get("head_branch"),
        "run_attempt": run.get("run_attempt"),
        "head_sha": run.get("head_sha") or items[0].get("head_sha"),
        "conclusion": run.get("conclusion") or ("success" if conclusions and all(c == "success" for c in conclusions) else "failure"),
        "status": run.get("status") or items[0].get("status"),
        "run_id": str(run.get("id") or run_id),
        "jobs": [
            {"name": job.get("name"), "conclusion": job.get("conclusion"), "status": job.get("status"),
             "labels": job.get("labels") or [], "runner_name": job.get("runner_name"),
             "runner_group_name": None, "id": job.get("id")}
            for job in items
        ],
        "jobs_total_count": jobs_total,
        "artifacts": artifacts,
        "artifacts_total_count": len(artifacts),
        "artifacts_available": artifacts_available,
        "fetched_at": utc_now(),
    }


def main_from_args(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", required=True, choices=GATES)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--forge-api-base", help="gate2 only: private forge API base (required; no default)")
    parser.add_argument("--repository", help="owner/repository identity (required for every gate; no default)")
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args(argv)
    if not RUN_ID.fullmatch(args.run_id):
        print("FETCH_EVIDENCE_BLOCKED: --run-id must be a plain decimal number", file=sys.stderr)
        return 2

    if not args.repository or not REPOSITORY.fullmatch(args.repository):
        print("FETCH_EVIDENCE_BLOCKED: --repository must be owner/name", file=sys.stderr)
        return 2

    if args.gate == "gate2-linux":
        if not args.forge_api_base:
            print("FETCH_EVIDENCE_BLOCKED: gate2-linux requires --forge-api-base", file=sys.stderr)
            return 2
        parsed = urllib.parse.urlsplit(args.forge_api_base.rstrip("/"))
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            print("FETCH_EVIDENCE_BLOCKED: --forge-api-base must be an HTTPS URL without credentials, query, or fragment", file=sys.stderr)
            return 2
        evidence = gitea_evidence(args.gate, args.run_id, args.forge_api_base.rstrip("/"), args.repository)
    else:
        evidence = github_evidence(args.gate, args.run_id, args.repository)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(
        f"FETCH_EVIDENCE_OK gate={args.gate} run={evidence['run_id']} "
        f"head_sha={evidence['head_sha']} conclusion={evidence['conclusion']} "
        f"jobs={len(evidence['jobs'])}/{evidence['jobs_total_count']} artifacts={len(evidence['artifacts'])}"
    )
    return 0


def main() -> int:
    return main_from_args()


if __name__ == "__main__":
    raise SystemExit(main())
