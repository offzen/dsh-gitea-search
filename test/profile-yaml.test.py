#!/usr/bin/env python3
"""YAML round-trip, control-character and indentation regression tests."""
import importlib.util
from pathlib import Path
import unittest
import yaml

spec = importlib.util.spec_from_file_location("patcher", Path(__file__).resolve().parents[1] / "scripts/profile-patch.py")
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)


class ProfileYamlTest(unittest.TestCase):
    def test_windows_paths_round_trip(self):
        entry = patcher.render_entry(r"C:\Program Files\node.exe", r"C:\项目\O'Brien\lib\server.mjs", r"C:\Scoop\apps\gts.exe", r"C:\镜像 data", "team", r"C:\venv")
        cfg = yaml.safe_load(entry)[0]["insert"][0]["config"]
        self.assertEqual(cfg["command"], "C:/Program Files/node.exe")
        self.assertEqual(cfg["args"], ["C:/项目/O'Brien/lib/server.mjs"])
        self.assertEqual(cfg["cwd"], "C:/项目/O'Brien")
        self.assertEqual(cfg["env"]["GTS_BIN"], "C:/Scoop/apps/gts.exe")
        self.assertEqual(cfg["env"]["GITEA_MIRROR_ROOT"], "C:/镜像 data")

    def test_controls_rejected(self):
        for value in ["C:/a\x07pp/gts.exe", "a\nb", "a\x00b", "a\u0085b", "a\u2028b", "a\ufeffb"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                patcher.render_entry("node", "/app/lib/server.mjs", value, "/mirror", "team")

    def test_removes_only_entry_leaves_empty_insert(self):
        for indent in ("  ", "    ", "      "):
            old = f"- insert:\n{indent}- id: mcp-gitea-search\n{indent}  name: old\n"
            updated = patcher.update_text(old, patcher.render_entry("node", "/app/lib/server.mjs", "gts", "/mirror", "team"))
            parsed = yaml.safe_load(updated)
            ids = [item["id"] for block in parsed if "insert" in block for item in block["insert"]]
            self.assertEqual(ids, ["mcp-gitea-search"])
            self.assertEqual(sum("insert" in block for block in parsed), 1)

    def test_removes_existing_entry_across_indentation_and_preserves_siblings(self):
        entry = patcher.render_entry("node", "/app/lib/server.mjs", "gts", "/mirror", "team")
        for indent in ("  ", "    ", "      "):
            old = f"- insert:\n{indent}- id: keep-before\n{indent}  name: before\n{indent}- id: mcp-gitea-search\n{indent}  name: old\n{indent}- id: keep-after\n{indent}  name: after\n"
            updated = patcher.update_text(old, entry)
            self.assertEqual(patcher.update_text(updated, entry), updated)
            parsed = yaml.safe_load(updated)
            ids = [item["id"] for block in parsed if "insert" in block for item in block["insert"]]
            self.assertEqual(ids, ["keep-before", "keep-after", "mcp-gitea-search"])


if __name__ == "__main__":
    unittest.main()
