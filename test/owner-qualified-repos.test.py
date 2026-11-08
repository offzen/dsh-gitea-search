#!/usr/bin/env python3
"""A1 regression: owner/repo is the canonical repository identity."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import indexer


def run(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None,
        check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, env=env, check=check, text=True,
                          capture_output=True, encoding="utf-8")


def make_bare(root: Path, owner: str, repo: str, marker: str) -> None:
    work = root / f"work-{owner}-{repo}"
    work.mkdir()
    run("git", "init", "-q", "-b", "main", cwd=work)
    run("git", "config", "user.email", "fixture@example.invalid", cwd=work)
    run("git", "config", "user.name", "fixture", cwd=work)
    (work / "README.md").write_text(marker + "\n", encoding="utf-8")
    run("git", "add", "README.md", cwd=work)
    run("git", "commit", "-q", "-m", "fixture", cwd=work)
    bare = root / owner / f"{repo}.git"
    bare.parent.mkdir(parents=True, exist_ok=True)
    run("git", "clone", "-q", "--bare", str(work), str(bare))


class OwnerQualifiedReposTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        make_bare(self.root, "team", "demo", "alpha_unique")
        make_bare(self.root, "other", "demo", "beta_unique")
        self.env = {
            **os.environ,
            "GITEA_MIRROR_ROOT": str(self.root),
            "GITEA_OWNERS": "team,other",
            "GITQ_ALLOW_NO_JIEBA": "1",
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def gts(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return run(sys.executable, str(ROOT / "bin" / "gts"), *args,
                   env=self.env, check=check)

    def test_cli_lists_both_and_exact_read_is_owner_qualified(self):
        repos = json.loads(self.gts("--repos", "--json").stdout)["repos"]
        self.assertEqual(repos, ["other/demo", "team/demo"])
        exact = self.gts("-r", "other/demo", "--read", "README.md")
        self.assertEqual(exact.stdout.strip(), "beta_unique")
        ambiguous = self.gts("-r", "demo", "--read", "README.md", check=False)
        self.assertNotEqual(ambiguous.returncode, 0)
        self.assertIn("AMBIGUOUS_REPO", ambiguous.stderr)
        self.assertIn("other/demo", ambiguous.stderr)
        self.assertIn("team/demo", ambiguous.stderr)

    def test_git_search_preserves_both_owner_qualified_results(self):
        alpha = json.loads(self.gts("--json", "alpha_unique").stdout)
        beta = json.loads(self.gts("--json", "beta_unique").stdout)
        self.assertEqual([r["repo"] for r in alpha["results"]], ["team/demo"])
        self.assertEqual([r["repo"] for r in beta["results"]], ["other/demo"])

    def test_fts_keeps_both_repositories_and_filters_full_identity(self):
        with mock.patch.object(indexer, "_get_jieba", return_value=None), \
             mock.patch.dict(os.environ, {**self.env, "GITQ_ALLOW_NO_JIEBA": "1"}, clear=True):
            changed = indexer.build_index(str(self.root), owners=["team", "other"],
                                          full=True, verbose=False)
        self.assertEqual(changed[0], 2)
        db = sqlite3.connect(indexer.get_db_path(str(self.root)))
        try:
            for table in ("docs_fts", "doc_rowid", "file_blobs", "repo_heads"):
                rows = [r[0] for r in db.execute(f"SELECT DISTINCT repo FROM {table} ORDER BY repo")]
                self.assertEqual(rows, ["other/demo", "team/demo"], table)
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], indexer.INDEX_IDENTITY_VERSION)
        finally:
            db.close()
        alpha = indexer.search_fts(str(self.root), "alpha_unique", repo="team/demo")
        beta = indexer.search_fts(str(self.root), "beta_unique", repo="other/demo")
        self.assertEqual([r["repo"] for r in alpha["results"]], ["team/demo"])
        self.assertEqual([r["repo"] for r in beta["results"]], ["other/demo"])
        self.assertEqual(indexer.search_fts(str(self.root), "alpha_unique", repo="other/demo")["total"], 0)

    def test_nonempty_legacy_index_requires_full_rebuild(self):
        legacy_root = self.root / "legacy"
        db_path = Path(indexer.get_db_path(str(legacy_root)))
        conn = sqlite3.connect(db_path)
        indexer.init_db(conn, identity_version=None)
        with conn:
            cur = conn.execute(
                "INSERT INTO docs_fts(repo, ref, path, title, body, kind, mtime, body_raw) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("demo", "main", "README.md", "legacy", "legacy", "doc", 1, "legacy"),
            )
            conn.execute("INSERT INTO doc_rowid VALUES (?, ?, ?, ?)",
                         ("demo", "main", "README.md", cur.lastrowid))
        conn.close()
        result = indexer.search_fts(str(legacy_root), "legacy")
        self.assertIn("INDEX_REBUILD_REQUIRED", result.get("error", ""))
        with mock.patch.object(indexer, "_get_jieba", return_value=None), \
             mock.patch.dict(os.environ, {**self.env, "GITQ_ALLOW_NO_JIEBA": "1"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "INDEX_REBUILD_REQUIRED"):
                indexer.build_index(str(legacy_root), owners=["team"], verbose=False)
            # Full rebuild is the only supported upgrade because v1 rows lost owner identity.
            owner_dir = legacy_root / "team"
            owner_dir.mkdir(parents=True, exist_ok=True)
            source_bare = self.root / "team" / "demo.git"
            run("git", "clone", "-q", "--bare", str(source_bare), str(owner_dir / "demo.git"))
            indexer.build_index(str(legacy_root), owners=["team"], full=True, verbose=False)
        rebuilt = indexer.search_fts(str(legacy_root), "alpha_unique")
        self.assertNotIn("error", rebuilt)
        self.assertEqual([r["repo"] for r in rebuilt["results"]], ["team/demo"])


if __name__ == "__main__":
    unittest.main()
