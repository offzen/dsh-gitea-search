#!/usr/bin/env python3
"""Idempotently update one DSH MCP entry in a cordis.patch.yml file."""

import argparse
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

ENTRY_ID = "mcp-gitea-search"


def yaml_scalar(value):
    value = str(value)
    if any(
        ord(char) < 0x20 or ord(char) == 0x7F or 0x80 <= ord(char) <= 0x9F
        or ord(char) in (0x2028, 0x2029, 0xFEFF)
        for char in value
    ):
        raise ValueError("profile values must not contain control characters")
    return "'" + value.replace("'", "''") + "'"


def yaml_path(value):
    value = str(value).replace("\\", "/") if os.name == "nt" else str(value)
    return yaml_scalar(value)


def render_entry(node, server, gts, mirror, owners, venv=None):
    lines = [
        "- insert:",
        f"    - id: {ENTRY_ID}",
        "      name: '@deepseek-ai/dsh-mcp-client'",
        "      config:",
        "        serverName: gitea-search",
        "        transport: stdio",
        f"        command: {yaml_path(node)}",
        f"        args: [{yaml_path(server)}]",
        f"        cwd: {yaml_path(Path(server).parent.parent)}",
        "        env:",
        f"          GTS_BIN: {yaml_path(gts)}",
        f"          GITEA_MIRROR_ROOT: {yaml_path(mirror)}",
        f"          GITEA_OWNERS: {yaml_scalar(owners)}",
    ]
    if venv:
        lines.append(f"          GITQ_VENV_PATH: {yaml_path(venv)}")
    return "\n".join(lines) + "\n"


def remove_existing(text):
    lines = text.splitlines(keepends=True)
    out = []
    i = 0
    target = re.compile(r"^( +)- id:\s*['\"]?" + re.escape(ENTRY_ID) + r"['\"]?\s*$")
    while i < len(lines):
        if lines[i].startswith("- insert:"):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("- "):
                j += 1
            block = lines[i:j]
            if not any(target.match(line) for line in block):
                out.extend(block)
                i = j
                continue
            kept = [block[0]]
            k = 1
            while k < len(block):
                match = target.match(block[k])
                if not match:
                    kept.append(block[k]); k += 1; continue
                indent = match.group(1)
                sibling = re.compile(r"^ {1," + str(len(indent)) + r"}- ")
                k += 1
                while k < len(block) and not sibling.match(block[k]):
                    k += 1
            if any(re.match(r"^ +- ", line) for line in kept[1:]):
                out.extend(kept)
            i = j
            continue
        out.append(lines[i])
        i += 1
    return "".join(out)


def update_text(text, entry):
    base = remove_existing(text)
    if base.strip() == "[]":
        return entry
    return base.rstrip() + "\n" + entry


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--server", required=True)
    parser.add_argument("--gts", required=True)
    parser.add_argument("--mirror", required=True)
    parser.add_argument("--owners", required=True)
    parser.add_argument("--venv")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    target = Path(args.file)
    if not target.is_file():
        parser.error(f"profile patch not found: {target}")
    original = target.read_text(encoding="utf-8-sig")
    entry = render_entry(args.node, args.server, args.gts, args.mirror, args.owners, args.venv)
    updated = update_text(original, entry)
    if updated.count(f"id: {ENTRY_ID}") != 1:
        parser.error("profile update did not produce exactly one MCP entry")
    if args.check:
        print(updated, end="")
        return

    stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    backup = target.with_name(target.name + ".bak-" + stamp)
    shutil.copy2(target, backup)
    fd, temp_name = tempfile.mkstemp(prefix=target.name + ".", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(updated)
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        shutil.copy2(backup, target)
        raise
    print(f"updated={target}")
    print(f"backup={backup}")


if __name__ == "__main__":
    main()
