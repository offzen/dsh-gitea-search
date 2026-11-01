#!/usr/bin/env python3
"""Generate the tracked-file provenance manifest without self-reference."""

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import subprocess

EXCLUDED = {"PROVENANCE.json"}


def tracked_files(root):
    staged = subprocess.check_output(
        ["git", "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR"], cwd=root
    ).decode("utf-8").split("\0")
    output = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=root
    ).decode("utf-8").split("\0")
    return sorted({path for path in [*output, *staged] if path and path not in EXCLUDED})


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="PROVENANCE.json")
    parser.add_argument("--source-commit", default="pending-candidate-freeze", help="exact candidate commit; may be filled after the content commit exists")
    args = parser.parse_args()

    root = pathlib.Path(__file__).resolve().parent.parent
    files = tracked_files(root)
    missing = [path for path in files if not (root / path).is_file()]
    if missing:
        parser.error(f"tracked non-files are unsupported: {missing}")
    hashes = {path: sha256(root / path) for path in sorted(files)}
    manifest = {
        "schema": 2,
        "package": "dsh-gitea-search",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source_commit": args.source_commit,
        "source_commit_role": "exact-candidate-commit-or-pending-freeze",
        "source": "https://github.com/offzen/dsh-gitea-search",
        "note": "Tracked-file SHA-256 manifest. PROVENANCE.json is deliberately excluded to avoid self-reference.",
        "file_count": len(hashes),
        "files": hashes,
    }
    output = root / args.output
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote={output} files={len(hashes)} baseline={args.source_commit}")


if __name__ == "__main__":
    main()
