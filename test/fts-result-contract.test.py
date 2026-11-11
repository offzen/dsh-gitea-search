#!/usr/bin/env python3
"""Stage 6 FTS result correctness contracts."""
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import indexer


class FtsResultContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.conn = sqlite3.connect(indexer.get_db_path(self.root))
        indexer.init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def insert_doc(self, repo, path, body, blob_sha, *, mtime=1700000000):
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO docs_fts(repo, ref, path, title, body, kind, mtime, body_raw) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (repo, "main", path, path, body, "doc", mtime, body),
            )
            self.conn.execute(
                "INSERT INTO doc_rowid(repo, ref, path, rowid) VALUES (?, ?, ?, ?)",
                (repo, "main", path, cur.lastrowid),
            )
            self.conn.execute(
                "INSERT INTO file_blobs(repo, ref, path, blob_sha, updated_at) VALUES (?, ?, ?, ?, ?)",
                (repo, "main", path, blob_sha, mtime),
            )

    def test_c6_identical_blob_remains_visible_in_each_repository(self):
        body = "CrossRepoDuplicateSentinel shared runbook"
        self.insert_doc("team/a", "RUNBOOK.md", body, "same-blob-sha")
        self.insert_doc("team/b", "RUNBOOK.md", body, "same-blob-sha")
        self.conn.close()

        result = indexer.search_fts(self.root, "CrossRepoDuplicateSentinel", limit=10)

        self.assertNotIn("error", result)
        self.assertEqual(
            [row["repo"] for row in result["results"]],
            ["team/a", "team/b"],
            "content dedupe must not collapse repository identity",
        )
        self.assertEqual(result["total"], 2)

    def test_c6_dedupe_continues_past_a_full_candidate_window(self):
        body = "CandidateWindowSentinel repeated content"
        for index in range(22):
            self.insert_doc(
                "team/a", f"backup/{index:02d}.md", body, "repeated-blob",
                mtime=2000000100 - index,
            )
        self.insert_doc("team/b", "UNIQUE.md", body, "unique-blob", mtime=1)
        self.conn.close()

        result = indexer.search_fts(self.root, "CandidateWindowSentinel", limit=2)

        self.assertNotIn("error", result)
        self.assertEqual(len(result["results"]), 2, "dedupe must keep scanning available candidates")
        self.assertEqual({row["repo"] for row in result["results"]}, {"team/a", "team/b"})

    def test_r3_pagination_metadata_is_truthful_without_redefining_total(self):
        body = "PaginationTruthSentinel"
        for index in range(3):
            self.insert_doc("team/page", f"UNIQUE-{index}.md", body, f"unique-{index}")
        self.conn.close()

        limited = indexer.search_fts(self.root, "PaginationTruthSentinel", limit=1)
        complete = indexer.search_fts(self.root, "PaginationTruthSentinel", limit=10)

        self.assertEqual(limited["total"], 1, "legacy total remains returned page length")
        self.assertEqual(limited["returned"], 1)
        self.assertIs(limited["has_more"], True)
        self.assertEqual(limited["total_relation"], "gt")
        self.assertEqual(complete["total"], 3)
        self.assertEqual(complete["returned"], 3)
        self.assertIs(complete["has_more"], False)
        self.assertEqual(complete["total_relation"], "eq")

    def test_r3_saturated_dedupe_window_reports_unknown_more(self):
        body = "PaginationUncertainSentinel"
        for index in range(25):
            self.insert_doc(
                "team/page", f"backup/{index:02d}.md", body, "same-blob",
                mtime=2000000100 - index,
            )
        self.conn.close()

        result = indexer.search_fts(self.root, "PaginationUncertainSentinel", limit=1)

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["returned"], 1)
        self.assertIsNone(result["has_more"])
        self.assertEqual(result["total_relation"], "gte")

    def test_c7_repo_filter_treats_underscore_and_percent_as_literals(self):
        body = "LiteralRepoFilterSentinel"
        for index, repo in enumerate((
            "team/foo_bar", "team/fooXbar", "team/foo%bar", "team/fooZZbar",
        )):
            self.insert_doc(repo, "README.md", body, f"blob-{index}")
        self.conn.close()

        underscore = indexer.search_fts(
            self.root, "LiteralRepoFilterSentinel", repo="team/foo_bar", limit=10,
        )
        percent = indexer.search_fts(
            self.root, "LiteralRepoFilterSentinel", repo="team/foo%bar", limit=10,
        )
        explicit_glob = indexer.search_fts(
            self.root, "LiteralRepoFilterSentinel", repo="team/foo*bar", limit=10,
        )

        self.assertEqual([row["repo"] for row in underscore["results"]], ["team/foo_bar"])
        self.assertEqual([row["repo"] for row in percent["results"]], ["team/foo%bar"])
        self.assertEqual(
            {row["repo"] for row in explicit_glob["results"]},
            {"team/foo_bar", "team/fooXbar", "team/foo%bar", "team/fooZZbar"},
            "documented '*' wildcard behavior must remain available",
        )


if __name__ == "__main__":
    unittest.main()
