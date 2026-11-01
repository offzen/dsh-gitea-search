#!/usr/bin/env python3
"""Check the tracked-file provenance manifest."""

import argparse
import hashlib
import json
import pathlib
import subprocess


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", nargs="?", default="PROVENANCE.json")
    args = parser.parse_args()
    root = pathlib.Path(__file__).resolve().parent.parent
    manifest_path = root / args.manifest
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if data.get("schema") != 2:
        parser.error("unsupported provenance schema")
    files = data.get("files") or {}
    if data.get("file_count") != len(files):
        parser.error("file_count does not match files mapping")
    if "PROVENANCE.json" in files:
        parser.error("manifest must not hash itself")

    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode("utf-8").split("\0")
    expected = {path for path in tracked if path and path != "PROVENANCE.json"}
    actual = set(files)
    if expected != actual:
        parser.error(f"tracked-file set mismatch: missing={sorted(expected-actual)} extra={sorted(actual-expected)}")
    mismatches = [path for path, digest in files.items() if sha256(root / path) != digest]
    if mismatches:
        parser.error(f"hash mismatch: {mismatches}")
    print(f"PROVENANCE_OK files={len(files)} baseline={data['source_commit']}")


if __name__ == "__main__":
    main()
