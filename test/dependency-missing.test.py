#!/usr/bin/env python3
"""Tests for H-1: missing jieba fail-closed behavior in search_fts and build_index."""
from pathlib import Path
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import indexer


class DependencyMissingTest(unittest.TestCase):
    def test_has_cjk_cases(self):
        # 正例：简体中文、繁体/日文汉字、扩展 A 区 (U+3400)、兼容表意文字 (U+F900)
        self.assertTrue(indexer._has_cjk('沙盒机制'))
        self.assertTrue(indexer._has_cjk('hello 世界'))
        self.assertTrue(indexer._has_cjk('日本語の漢字'))
        self.assertTrue(indexer._has_cjk('\u3400'))
        self.assertTrue(indexer._has_cjk('\uf900'))
        # 反例：纯 ASCII、空串、None、纯 emoji / 标点
        self.assertFalse(indexer._has_cjk('docker-compose'))
        self.assertFalse(indexer._has_cjk(''))
        self.assertFalse(indexer._has_cjk(None))
        self.assertFalse(indexer._has_cjk('🚀🔥 123 !@#'))

    def test_search_fts_without_jieba_cjk_vs_ascii(self):
        with tempfile.TemporaryDirectory() as raw:
            db_path = indexer.get_db_path(raw)
            conn = sqlite3.connect(db_path)
            indexer.init_db(conn)
            with conn:
                cur = conn.execute(
                    "INSERT INTO docs_fts(repo, ref, path, title, body, kind, mtime, body_raw) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    ('demo', 'main', 'README.md', 'docker guide', 'docker compose setup', 'doc', 1700000000, 'docker compose setup'),
                )
                conn.execute(
                    "INSERT INTO doc_rowid(repo, ref, path, rowid) VALUES (?, ?, ?, ?)",
                    ('demo', 'main', 'README.md', cur.lastrowid),
                )
                conn.execute(
                    "INSERT INTO file_blobs(repo, ref, path, blob_sha, updated_at) VALUES (?, ?, ?, ?, ?)",
                    ('demo', 'main', 'README.md', 'deadbeef', 1700000000),
                )
            conn.close()

            with mock.patch.object(indexer, '_JIEBA_TRIED', False), \
                 mock.patch.object(indexer, '_JIEBA', None), \
                 mock.patch.object(indexer, '_get_jieba', return_value=None):
                cjk_res = indexer.search_fts(raw, '沙盒机制')
                self.assertIn('error', cjk_res)
                self.assertTrue(cjk_res['error'].startswith('DEPENDENCY_MISSING'))
                self.assertNotIn('results', cjk_res)

                ascii_res = indexer.search_fts(raw, 'docker')
                self.assertNotIn('error', ascii_res)
                self.assertIn('results', ascii_res)
                self.assertEqual(ascii_res['total'], 1)
                self.assertEqual(ascii_res['results'][0]['repo'], 'demo')

    def test_build_index_without_jieba_fail_closed_and_env_override(self):
        with tempfile.TemporaryDirectory() as raw:
            owner_dir = Path(raw) / 'team'
            owner_dir.mkdir(parents=True)
            db_path = Path(raw) / 'state' / 'gitea-fts.sqlite'

            env_no_allow = {k: v for k, v in os.environ.items() if k != 'GITQ_ALLOW_NO_JIEBA'}
            with mock.patch.dict(os.environ, env_no_allow, clear=True), \
                 mock.patch.object(indexer, '_JIEBA_TRIED', False), \
                 mock.patch.object(indexer, '_JIEBA', None), \
                 mock.patch.object(indexer, '_get_jieba', return_value=None):
                with self.assertRaises(Exception) as ctx:
                    indexer.build_index(raw, owners=['team'], verbose=False)
                self.assertTrue(str(ctx.exception).startswith('DEPENDENCY_MISSING:'))
                self.assertFalse(db_path.exists())

            with mock.patch.dict(os.environ, {**env_no_allow, 'GITQ_ALLOW_NO_JIEBA': '1'}, clear=True), \
                 mock.patch.object(indexer, '_JIEBA_TRIED', False), \
                 mock.patch.object(indexer, '_JIEBA', None), \
                 mock.patch.object(indexer, '_get_jieba', return_value=None):
                mod, dele, same = indexer.build_index(raw, owners=['team'], verbose=False)
                self.assertEqual((mod, dele, same), (0, 0, 0))
                self.assertTrue(db_path.exists())


if __name__ == '__main__':
    unittest.main()
