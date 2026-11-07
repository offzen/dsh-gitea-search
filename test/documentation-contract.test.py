#!/usr/bin/env python3
"""Static contracts keeping release documentation aligned with code."""
from __future__ import annotations

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


class DocumentationContractTest(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_release_guide_names_current_contract(self) -> None:
        guide = self.read("docs/DEVELOPMENT-AND-RELEASE.md")
        for value in ("schema 3", "schema 2", "--expected-ref", "--trust-anchor", "--artifact", "same-party"):
            with self.subTest(value=value):
                self.assertIn(value, guide)
        self.assertNotIn("schema-1", guide)
        self.assertNotIn("two independent CI planes", guide)

    def test_formal_state_and_retired_review_boundary(self) -> None:
        state = self.read("PROJECT-STATE.md")
        self.assertIn("private prerelease", state)
        self.assertIn("remote/tag promotion remains **deferred**", state)
        self.assertFalse((ROOT / "docs/RC5-REVIEW.md").exists())

    def test_private_coordinates_are_runtime_only(self) -> None:
        fetcher = self.read("scripts/fetch-remote-evidence.py")
        preflight = self.read("scripts/release-preflight.py")
        state = self.read("PROJECT-STATE.md")
        for value in ("--forge-api-base", "--repository"):
            self.assertIn(value, fetcher)
        self.assertNotIn("GITEA_BASE =", fetcher)
        self.assertNotIn("GITEA_REPO =", fetcher)
        self.assertIn("--expected-gate2-repository", preflight)
        self.assertNotIn("GITEA_REPOSITORY =", preflight)
        self.assertIn("intentionally supplied at runtime", state)

    def test_windows_docs_do_not_overclaim_cmd_quoting(self) -> None:
        manual = self.read("MANUAL.md")
        windows = self.read("WINDOWS-PORT.md")
        self.assertIn("拒绝 NUL、CR/LF", manual)
        self.assertIn("拒绝 NUL、CR 与 LF", windows)
        self.assertNotIn("彻底防御搜索词中恶意注入", manual)
        self.assertNotIn("Python 3.15", windows)

    def test_default_python_chain_documents_dependency_boundary(self) -> None:
        package = self.read("package.json")
        guide = self.read("CODE-INDEX.md")
        counter = self.read("scripts/collect-test-counts.py")
        gate2 = self.read(".gitea/workflows/ci.yml")
        for suite in ("linux-gate-context", "dependency-missing", "profile-yaml", "repo-zero-hit", "documentation-contract"):
            with self.subTest(suite=suite):
                self.assertIn(f"test/{suite}.test.py", package)
                self.assertIn(f"test/{suite}.test.py", counter)
        self.assertIn("依赖 PyYAML，缺失时门禁失败", guide)
        self.assertIn("--upgrade build jieba PyYAML", gate2)
        self.assertIn("Gitea does not provide that GitHub-only variable", self.read("docs/DEVELOPMENT-AND-RELEASE.md"))

    def test_user_docs_match_quickstart_and_kind_contracts(self) -> None:
        readme = self.read("README.md")
        manual = self.read("MANUAL.md")
        usage = self.read("docs/USAGE.md")
        post_install = self.read("docs/POST-INSTALL.md")
        contributing = self.read("CONTRIBUTING.md")
        self.assertIn("不会扫描或导入", readme)
        self.assertIn("不会递归探测或导入已有本地 Git 仓库", manual)
        self.assertNotIn("扫描本地任意 Git 项目目录", readme)
        self.assertNotIn("递归探测目录下的所有 Git 仓库", manual)
        self.assertIn("当前默认分类器把 `*.py` 归入 `code`", usage)
        self.assertNotIn("`*.service`、`*.py`", usage)
        self.assertIn("做一次真实端到端调用", post_install)
        self.assertIn("安装器有意不自动复制", post_install)
        self.assertIn("npm run test:all", contributing)
        self.assertIn("PyYAML", contributing)


if __name__ == "__main__":
    unittest.main()
