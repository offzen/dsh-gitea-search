#!/usr/bin/env python3
"""Tests for H-4: explicit -r not in mirror returns structured zero-hit instead of failing."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]


class RepoZeroHitTest(unittest.TestCase):
    def test_doc_search_with_nonexistent_repo_returns_zero_hit_json(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            app = root / 'app'
            (app / 'bin').mkdir(parents=True)
            shutil.copytree(SOURCE / 'lib', app / 'lib')
            shutil.copy2(SOURCE / 'bin/gts', app / 'bin/gts')
            mirror = root / 'mirror'
            (mirror / 'one').mkdir(parents=True)
            (mirror / 'one' / 'existing-repo.git').mkdir(parents=True)

            command = [
                os.sys.executable,
                str(app / 'bin/gts'),
                '--mirror-root', str(mirror),
                '--owners', 'one',
                '-D', '--json',
                '-r', 'nonexistent-owner/nope',
                'docker',
            ]
            res = subprocess.run(command, env={**os.environ}, text=True, capture_output=True, encoding='utf-8')
            self.assertEqual(res.returncode, 0, res.stderr)
            payload = json.loads(res.stdout)
            self.assertEqual(payload['total'], 0)
            self.assertEqual(payload['results'], [])
            self.assertEqual(payload['repos_hit'], 0)
            self.assertEqual(payload['repos_scanned'], 0)
            self.assertTrue(payload.get('repo_unmatched'))
            self.assertIn('nonexistent-owner/nope', payload['repo_unmatched'])
            self.assertTrue(payload.get('note', '').startswith('REPO_NOT_IN_MIRROR'))


if __name__ == '__main__':
    unittest.main()
