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

    def test_rejected_legacy_build_closes_sqlite_connection(self):
        """A rejected incremental build must release the DB handle before raising.

        NTFS refuses to unlink a file while an sqlite3.Connection still holds it, so
        the required ``--full`` upgrade can only follow the INDEX_REBUILD_REQUIRED
        rejection if the exception path closes the connection. POSIX hides the leak,
        hence this handle-level assertion instead of relying on the delete.
        """
        legacy_root = self.root / "legacy-handle"
        db_path = Path(indexer.get_db_path(str(legacy_root)))
        seed = sqlite3.connect(db_path)
        indexer.init_db(seed, identity_version=None)
        with seed:
            cur = seed.execute(
                "INSERT INTO docs_fts(repo, ref, path, title, body, kind, mtime, body_raw) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("demo", "main", "README.md", "legacy", "legacy", "doc", 1, "legacy"),
            )
            seed.execute("INSERT INTO doc_rowid VALUES (?, ?, ?, ?)",
                         ("demo", "main", "README.md", cur.lastrowid))
        seed.close()

        opened: list[sqlite3.Connection] = []
        real_connect = sqlite3.connect

        def tracking_connect(*args, **kwargs):
            connection = real_connect(*args, **kwargs)
            opened.append(connection)
            return connection

        with mock.patch.object(indexer.sqlite3, "connect", side_effect=tracking_connect), \
             mock.patch.object(indexer, "_get_jieba", return_value=None), \
             mock.patch.dict(os.environ, {**self.env, "GITQ_ALLOW_NO_JIEBA": "1"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "INDEX_REBUILD_REQUIRED"):
                indexer.build_index(str(legacy_root), owners=["team"], verbose=False)

        self.assertTrue(opened, "build_index should open the index database")
        for connection in opened:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")


class R2StaleRepositoryPruneTests(unittest.TestCase):
    """R2: only a trusted complete-scope build may prune vanished repositories."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = {
            **os.environ,
            "GITEA_MIRROR_ROOT": str(self.root),
            "GITEA_OWNERS": "team",
            "GITQ_ALLOW_NO_JIEBA": "1",
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _make(self, owner: str, repo: str, marker: str) -> None:
        make_bare(self.root, owner, repo, marker)

    def _build(self, owners=("team",), *, repo_filter=None, full=False,
               allow_global_prune=None):
        kwargs = {}
        if allow_global_prune is not None:
            kwargs["allow_global_prune"] = allow_global_prune
        with mock.patch.object(indexer, "_get_jieba", return_value=None), \
             mock.patch.dict(os.environ, self.env, clear=True):
            return indexer.build_index(
                str(self.root), owners=list(owners), repo_filter=repo_filter,
                full=full, verbose=False, **kwargs,
            )

    def _snapshot(self):
        conn = sqlite3.connect(indexer.get_db_path(str(self.root)))
        try:
            queries = {
                "docs_fts": (
                    "SELECT rowid, repo, ref, path, title, body, kind, mtime, body_raw "
                    "FROM docs_fts ORDER BY rowid"
                ),
                "doc_rowid": (
                    "SELECT repo, ref, path, rowid FROM doc_rowid "
                    "ORDER BY repo, ref, path"
                ),
                "file_blobs": (
                    "SELECT repo, ref, path, blob_sha, updated_at FROM file_blobs "
                    "ORDER BY repo, ref, path"
                ),
                "repo_heads": (
                    "SELECT repo, ref, head_commit, updated_at FROM repo_heads "
                    "ORDER BY repo, ref"
                ),
            }
            return {name: conn.execute(sql).fetchall() for name, sql in queries.items()}
        finally:
            conn.close()

    @staticmethod
    def _repo_rows(snapshot, repo_id):
        return {
            name: [row for row in rows if row[1 if name == "docs_fts" else 0] == repo_id]
            for name, rows in snapshot.items()
        }

    def _move_bare_away(self, owner: str, repo: str) -> Path:
        source = self.root / owner / f"{repo}.git"
        target = self.root / f"away-{owner}-{repo}.git"
        self.assertTrue(source.is_dir())
        source.rename(target)
        return target

    def _assert_repo_absent(self, repo_id: str) -> None:
        rows = self._repo_rows(self._snapshot(), repo_id)
        self.assertEqual(rows, {name: [] for name in rows})

    def test_r2_trusted_build_prunes_vanished_repo_and_keeps_live_repo(self):
        self._make("team", "stale", "UniqueStaleSentinel")
        self._make("team", "live", "LiveSentinel")
        self._build(full=True)
        live_before = self._repo_rows(self._snapshot(), "team/live")
        self._move_bare_away("team", "stale")

        self._build()

        self._assert_repo_absent("team/stale")
        self.assertEqual(self._repo_rows(self._snapshot(), "team/live"), live_before)
        self.assertEqual(indexer.search_fts(str(self.root), "UniqueStaleSentinel")["total"], 0)
        self.assertEqual(indexer.search_fts(str(self.root), "LiveSentinel")["total"], 1)
        after = self._snapshot()
        self._build()
        self.assertEqual(self._snapshot(), after)

    def test_r2_trusted_empty_owner_prunes_last_vanished_repo(self):
        self._make("team", "only", "LastRepoSentinel")
        self._build(full=True)
        self._move_bare_away("team", "only")

        self._build()

        self._assert_repo_absent("team/only")
        self.assertEqual(indexer.search_fts(str(self.root), "LastRepoSentinel")["total"], 0)

    def test_r2_repo_filter_builds_never_prune_globally(self):
        self._make("team", "stale", "FilteredStaleSentinel")
        self._make("team", "live", "FilteredLiveSentinel")
        self._build(full=True)
        stale_before = self._repo_rows(self._snapshot(), "team/stale")
        self._move_bare_away("team", "stale")

        self._build(repo_filter="live")
        self.assertEqual(self._repo_rows(self._snapshot(), "team/stale"), stale_before)
        self._build(repo_filter="no-such-repo")
        self.assertEqual(self._repo_rows(self._snapshot(), "team/stale"), stale_before)
        self._build(repo_filter="live", full=True)
        self.assertEqual(self._repo_rows(self._snapshot(), "team/stale"), stale_before)

        self._build()
        self._assert_repo_absent("team/stale")

    def test_r2_missing_owner_directory_disables_all_global_pruning(self):
        self._make("team", "stale", "MissingOwnerStaleSentinel")
        self._make("team", "live", "MissingOwnerLiveSentinel")
        self._make("guard", "kept", "MissingOwnerGuardSentinel")
        self._build(owners=("team", "guard"), full=True)
        self._move_bare_away("team", "stale")
        guard_dir = self.root / "guard"
        guard_away = self.root / "guard-away"
        guard_dir.rename(guard_away)
        before = self._snapshot()

        self._build(owners=("team", "guard"))
        self.assertEqual(self._snapshot(), before)

        guard_away.rename(guard_dir)
        self._build(owners=("team", "guard"))
        self._assert_repo_absent("team/stale")
        self.assertTrue(self._repo_rows(self._snapshot(), "guard/kept")["repo_heads"])

    def test_r2_listdir_failure_preserves_all_rows_and_propagates(self):
        self._make("team", "stale", "ListdirStaleSentinel")
        self._make("team", "live", "ListdirLiveSentinel")
        self._build(full=True)
        self._move_bare_away("team", "stale")
        before = self._snapshot()
        owner_dir = os.path.abspath(self.root / "team")
        real_listdir = indexer.os.listdir

        def fail_owner_listdir(path):
            if os.path.abspath(path) == owner_dir:
                raise PermissionError("synthetic R2 enumeration failure")
            return real_listdir(path)

        with mock.patch.object(indexer.os, "listdir", side_effect=fail_owner_listdir):
            with self.assertRaisesRegex(PermissionError, "synthetic R2"):
                self._build()
        self.assertEqual(self._snapshot(), before)

        self._build()
        self._assert_repo_absent("team/stale")

    def test_r2_index_failure_preserves_global_stale_repo_until_clean_retry(self):
        self._make("team", "stale", "ReadFailureStaleSentinel")
        self._make("team", "live", "ReadFailureLiveSentinel")
        self._build(full=True)
        self._move_bare_away("team", "stale")
        live_work = self.root / "work-team-live"
        (live_work / "README.md").write_text("ReadFailureLiveChanged\n", encoding="utf-8")
        run("git", "add", "README.md", cwd=live_work)
        run("git", "commit", "-q", "-m", "force tree read", cwd=live_work)
        live_bare = self.root / "team" / "live.git"
        run("git", "push", "-q", str(live_bare), "HEAD:main", cwd=live_work)
        before = self._snapshot()
        real_get_tree_entries = indexer.get_tree_entries

        def fail_live_tree(gitdir, ref, valid_exts):
            if os.path.abspath(gitdir) == os.path.abspath(live_bare):
                raise indexer.IndexReadError("INDEX_READ_FAILED: synthetic R2 partial build")
            return real_get_tree_entries(gitdir, ref, valid_exts)

        with mock.patch.object(indexer, "get_tree_entries", side_effect=fail_live_tree):
            with self.assertRaisesRegex(indexer.IndexReadError, "INDEX_READ_FAILED"):
                self._build()
        self.assertEqual(self._snapshot(), before)

        self._build()
        self._assert_repo_absent("team/stale")
        self.assertEqual(indexer.search_fts(str(self.root), "ReadFailureLiveChanged")["total"], 1)

    def test_r2_single_owner_build_never_prunes_other_owner(self):
        self._make("team", "stale", "ScopedStaleSentinel")
        self._make("team", "live", "ScopedLiveSentinel")
        self._make("guard", "kept", "ScopedGuardSentinel")
        self._build(owners=("team", "guard"), full=True)
        live_before = self._repo_rows(self._snapshot(), "team/live")
        guard_before = self._repo_rows(self._snapshot(), "guard/kept")
        self._move_bare_away("team", "stale")

        self._build(owners=("team",))
        self._assert_repo_absent("team/stale")
        self.assertEqual(self._repo_rows(self._snapshot(), "team/live"), live_before)
        self.assertEqual(self._repo_rows(self._snapshot(), "guard/kept"), guard_before)

        guard_dir = self.root / "guard"
        guard_away = self.root / "guard-away"
        guard_dir.rename(guard_away)
        self._build(owners=("team",))
        self.assertEqual(self._repo_rows(self._snapshot(), "guard/kept"), guard_before)

    def test_r2_unlisted_existing_entry_is_not_proof_of_removal(self):
        self._make("team", "stale", "LexistsStaleSentinel")
        self._make("team", "live", "LexistsLiveSentinel")
        self._build(full=True)
        stale_before = self._repo_rows(self._snapshot(), "team/stale")
        away = self._move_bare_away("team", "stale")
        placeholder = self.root / "team" / "stale.git"
        placeholder.write_text("incomplete mirror placeholder\n", encoding="utf-8")

        self._build()
        self.assertEqual(self._repo_rows(self._snapshot(), "team/stale"), stale_before)

        placeholder.unlink()
        self._build()
        self._assert_repo_absent("team/stale")
        self.assertTrue(away.is_dir())

    def test_r2_cli_no_global_prune_reaches_build_contract(self):
        effective = {
            "mirror_root": str(self.root),
            "owners": ["team"],
            "valid": True,
            "errors": [],
        }
        argv = ["indexer.py", "--build", "--no-global-prune"]
        with mock.patch.object(indexer, "resolve_config", return_value=effective), \
             mock.patch.object(indexer, "build_index") as build, \
             mock.patch.object(sys, "argv", argv):
            indexer.main()
        self.assertEqual(build.call_count, 1)
        self.assertIs(build.call_args.kwargs["allow_global_prune"], False)

    def test_r2_explicit_no_prune_signal_preserves_rows_until_trusted_retry(self):
        self._make("team", "stale", "NoPruneStaleSentinel")
        self._make("team", "live", "NoPruneLiveSentinel")
        self._build(full=True)
        stale_before = self._repo_rows(self._snapshot(), "team/stale")
        self._move_bare_away("team", "stale")

        self._build(allow_global_prune=False)
        self.assertEqual(self._repo_rows(self._snapshot(), "team/stale"), stale_before)
        self._build(full=True, allow_global_prune=False)
        self.assertEqual(self._repo_rows(self._snapshot(), "team/stale"), stale_before)

        self._build()
        self._assert_repo_absent("team/stale")


class C3FailClosedReadTests(unittest.TestCase):
    """C3 read failures belong here to avoid widening the release suite inventory."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.owner = "team"
        self.repo = "c3-read-gate"
        make_bare(self.root, self.owner, self.repo, "c3_original")
        self.work = self.root / f"work-{self.owner}-{self.repo}"
        self.bare = self.root / self.owner / f"{self.repo}.git"
        self.repo_id = f"{self.owner}/{self.repo}"
        self.env = {
            **os.environ,
            "GITEA_MIRROR_ROOT": str(self.root),
            "GITEA_OWNERS": self.owner,
            "GITQ_ALLOW_NO_JIEBA": "1",
        }
        self._build(full=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _build(self, *, full: bool = False):
        with mock.patch.object(indexer, "_get_jieba", return_value=None), \
             mock.patch.dict(os.environ, self.env, clear=True):
            return indexer.build_index(
                str(self.root), owners=[self.owner], full=full, verbose=False
            )

    def _commit_and_push(self, message: str) -> str:
        run("git", "add", "-A", cwd=self.work)
        run("git", "commit", "-q", "-m", message, cwd=self.work)
        run("git", "push", "-q", str(self.bare), "HEAD:main", cwd=self.work)
        return run("git", "rev-parse", "HEAD", cwd=self.work).stdout.strip()

    def _snapshot(self):
        conn = sqlite3.connect(indexer.get_db_path(str(self.root)))
        try:
            queries = {
                "docs_fts": (
                    "SELECT rowid, repo, ref, path, title, body, kind, mtime, body_raw "
                    "FROM docs_fts ORDER BY rowid"
                ),
                "doc_rowid": (
                    "SELECT repo, ref, path, rowid FROM doc_rowid "
                    "ORDER BY repo, ref, path"
                ),
                "file_blobs": (
                    "SELECT repo, ref, path, blob_sha, updated_at FROM file_blobs "
                    "ORDER BY repo, ref, path"
                ),
                "repo_heads": (
                    "SELECT repo, ref, head_commit, updated_at FROM repo_heads "
                    "ORDER BY repo, ref"
                ),
            }
            return {name: conn.execute(sql).fetchall() for name, sql in queries.items()}
        finally:
            conn.close()

    def test_c3_ls_tree_failure_preserves_rows_and_head_gate(self):
        before = self._snapshot()
        (self.work / "README.md").write_text("c3_after_tree_failure\n", encoding="utf-8")
        self._commit_and_push("new head before injected tree failure")

        real_run = indexer.subprocess.run

        def fail_ls_tree(args, *pargs, **kwargs):
            if isinstance(args, (list, tuple)) and "ls-tree" in args:
                return subprocess.CompletedProcess(
                    args, 128, stdout="", stderr="synthetic C3 ls-tree failure"
                )
            return real_run(args, *pargs, **kwargs)

        caught = None
        with mock.patch.object(indexer.subprocess, "run", side_effect=fail_ls_tree):
            try:
                self._build()
            except RuntimeError as exc:
                caught = exc

        self.assertEqual(self._snapshot(), before)
        self.assertIsNotNone(caught, "a failed tree read must abort the build")
        self.assertIn("INDEX_READ_FAILED", str(caught))

    def test_c3_blob_failure_after_flushed_batch_rolls_back_entire_repo(self):
        docs = self.work / "docs"
        docs.mkdir()
        for i in range(205):
            (docs / f"{i:03d}.md").write_text(f"old-{i}\n", encoding="utf-8")
        self._commit_and_push("seed more than one index batch")
        self._build()

        for i in range(205):
            (docs / f"{i:03d}.md").write_text(f"new-{i}\n", encoding="utf-8")
        self._commit_and_push("change more than one index batch")
        failing_blob = run(
            "git", f"--git-dir={self.bare}", "rev-parse", "main:docs/204.md"
        ).stdout.strip()
        before = self._snapshot()
        real_read = indexer.BlobReader.read
        calls = []

        def fail_last_blob(reader, blob_sha):
            calls.append(blob_sha)
            if blob_sha == failing_blob:
                return None
            return real_read(reader, blob_sha)

        caught = None
        with mock.patch.object(indexer.BlobReader, "read", new=fail_last_blob):
            try:
                self._build()
            except RuntimeError as exc:
                caught = exc

        self.assertIn(failing_blob, calls)
        self.assertGreaterEqual(
            calls.index(failing_blob), 200,
            "fixture must fail only after an earlier batch was written",
        )
        self.assertEqual(self._snapshot(), before)
        self.assertIsNotNone(caught, "a failed blob read must abort the build")
        self.assertIn("INDEX_READ_FAILED", str(caught))

        self.assertEqual(self._build(), (205, 0, 1))
        recovered = self._snapshot()
        recovered_head = recovered["repo_heads"][0][2]
        self.assertNotEqual(recovered, before)
        self.assertEqual(recovered_head, run("git", f"--git-dir={self.bare}", "rev-parse", "main").stdout.strip())
        self.assertEqual(len(recovered["file_blobs"]), 206)

    def test_c3_successful_empty_tree_still_deletes_rows_and_advances_head(self):
        (self.work / "README.md").unlink()
        new_head = self._commit_and_push("legitimate empty tree")

        self.assertEqual(self._build(), (0, 1, 0))
        after = self._snapshot()
        self.assertEqual(after["docs_fts"], [])
        self.assertEqual(after["doc_rowid"], [])
        self.assertEqual(after["file_blobs"], [])
        self.assertEqual(
            after["repo_heads"],
            [(self.repo_id, "main", new_head, after["repo_heads"][0][3])],
        )

    def test_c3_tree_parser_preserves_valid_whitespace_paths(self):
        for name in (" lead.md", "\u3000wide.md", "a\u2028b.md", "\u0085x.md"):
            (self.work / name).write_text(f"path={name!r}\n", encoding="utf-8")
        (self.work / "\u00a0").write_text("not an indexed extension\n", encoding="utf-8")
        self._commit_and_push("valid unusual git paths")

        self._build()
        indexed = [row[2] for row in self._snapshot()["file_blobs"]]
        for name in (" lead.md", "\u3000wide.md", "a\u2028b.md", "\u0085x.md"):
            self.assertIn(name, indexed)
        self.assertIn("README.md", indexed)
        self.assertNotIn("\u00a0", indexed)

    def test_c3_malformed_tree_record_fails_closed(self):
        before = self._snapshot()
        malformed = subprocess.CompletedProcess(
            ["git", "ls-tree"], 0, stdout=b"malformed-without-tab\0", stderr=b""
        )
        real_run = indexer.subprocess.run

        def inject_malformed(args, *pargs, **kwargs):
            if isinstance(args, (list, tuple)) and "ls-tree" in args:
                return malformed
            return real_run(args, *pargs, **kwargs)

        with mock.patch.object(indexer.subprocess, "run", side_effect=inject_malformed):
            with self.assertRaisesRegex(indexer.IndexReadError, "malformed tree"):
                indexer.get_tree_entries(str(self.bare), "main", indexer.DEFAULT_EXTS)
        self.assertEqual(self._snapshot(), before)


if __name__ == "__main__":
    unittest.main()
