#!/usr/bin/env python3
"""Negative/positive contracts for gate2's dual-forge workflow identity resolver."""
from __future__ import annotations

import functools
import json
import os
import pathlib
import shutil
import stat
import subprocess
import tempfile
import unittest

SOURCE = pathlib.Path(__file__).resolve().parent.parent
GATE = "scripts/ci/linux-gate.sh"


@functools.lru_cache(maxsize=1)
def resolve_bash() -> str:
    if os.name != "nt":
        path = shutil.which("bash")
        if not path:
            raise AssertionError("GNU bash not found on PATH")
        return path

    # Anchor native Python to the Git for Windows installation, never the WSL launcher.
    git_env = {key: value for key, value in os.environ.items() if key.upper() != "GIT_EXEC_PATH"}
    exec_path = pathlib.Path(subprocess.run(
        ["git", "--exec-path"], check=True, text=True, capture_output=True, env=git_env,
    ).stdout.strip())
    roots = list(exec_path.parents[:4])
    if git_bin := shutil.which("git"):
        roots.extend(pathlib.Path(git_bin).resolve().parents[:3])
    roots.extend(
        pathlib.Path(base) / "Git"
        for key in ("ProgramW6432", "ProgramFiles")
        if (base := os.environ.get(key))
    )
    checked = []
    for root in dict.fromkeys(roots):
        if root == root.parent:
            continue
        for relative in (("bin", "bash.exe"), ("usr", "bin", "bash.exe")):
            candidate = root.joinpath(*relative)
            checked.append(str(candidate))
            if not candidate.is_file():
                continue
            probe = subprocess.run(
                [str(candidate), "--noprofile", "--norc", "--version"],
                encoding="utf-8", errors="replace", capture_output=True,
            )
            if probe.returncode == 0 and "GNU bash" in probe.stdout:
                return str(candidate)
    raise AssertionError(
        f"Git for Windows GNU bash.exe not found (git --exec-path={exec_path}); checked: {checked}"
    )


class LinuxGateContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = pathlib.Path(tempfile.mkdtemp(prefix="linux-gate-context-test-"))
        self.repo = self.temp / "repo"
        shutil.copytree(
            SOURCE,
            self.repo,
            ignore=shutil.ignore_patterns(
                ".git", "node_modules", "__pycache__", "build", "dist", "*.egg-info",
                "gitq", "dsh-search", "mcp-server-gitea-search",
            ),
        )
        self.git("init", "-q")
        self.git("config", "user.name", "Gate Context Test")
        self.git("config", "user.email", "gate-context@example.invalid")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        self.sha = self.git("rev-parse", "HEAD").strip()
        self.event = self.temp / "event.json"
        self.write_event()

    def tearDown(self) -> None:
        def make_writable(function, path, _exc_info):  # noqa: ANN001
            os.chmod(path, stat.S_IRWXU)
            function(path)

        shutil.rmtree(self.temp, onerror=make_writable)

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True, text=True, capture_output=True,
        ).stdout

    def write_event(self, *, repository: str = "zaoz/dsh-gitea-search", ref: str = "refs/heads/rc/test",
                    after: str | None = None) -> None:
        self.event.write_text(
            json.dumps({"repository": {"full_name": repository}, "ref": ref, "after": after or self.sha}) + "\n",
            encoding="utf-8",
        )

    def environment(self, **overrides: str) -> dict[str, str]:
        repository = "zaoz/dsh-gitea-search"
        ref = "refs/heads/rc/test"
        env = {
            **os.environ,
            "GITEA_ACTIONS": "true",
            "GITHUB_ACTIONS": "true",
            "GITHUB_REPOSITORY": repository,
            "GITHUB_REF": ref,
            "GITHUB_SHA": self.sha,
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_EVENT_PATH": str(self.event),
            "GITHUB_WORKFLOW": "gate2 Linux CI",
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_WORKFLOW_REF": "",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        env.update(overrides)
        return env

    def run_gate(self, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        # Define the npm sentinel inside Bash. Passing BASH_FUNC_npm%% through the native
        # Windows CreateProcess environment is not portable; Bash-to-Bash export is.
        clean_env = {key: value for key, value in env.items()
                     if not key.startswith("BASH_FUNC_npm")}
        return subprocess.run(
            [
                resolve_bash(), "--noprofile", "--norc", "-c",
                'npm() { return 42; }; export -f npm; exec "$BASH" "$@"',
                "gate-context", GATE, self.sha, str(self.temp / "receipt.json"),
            ],
            cwd=self.repo,
            env=clean_env,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )

    def assert_context_rejected(self, env: dict[str, str], text: str) -> None:
        result = self.run_gate(env)
        diagnostics = f"stdout={result.stdout}\nstderr={result.stderr}"
        self.assertEqual(result.returncode, 69, diagnostics)
        self.assertIn(text, result.stderr)
        self.assertFalse((self.temp / "receipt.json").exists())

    def test_gitea_derivation_passes_context_then_reaches_build(self) -> None:
        # With a valid Gitea context, identity resolution must pass. A stub npm then stops the
        # gate immediately after identity resolution, proving it reached the build phase.
        env = self.environment()
        result = self.run_gate(env)
        diagnostics = f"stdout={result.stdout}\nstderr={result.stderr}"
        self.assertEqual(result.returncode, 42, diagnostics)
        self.assertNotIn("event payload does not match", result.stderr)

    def test_native_workflow_ref_remains_exact(self) -> None:
        expected = "zaoz/dsh-gitea-search/.gitea/workflows/ci.yml@refs/heads/rc/test"
        env = self.environment(GITHUB_WORKFLOW_REF=expected)
        result = self.run_gate(env)
        diagnostics = f"stdout={result.stdout}\nstderr={result.stderr}"
        self.assertEqual(result.returncode, 42, diagnostics)

    def test_rejects_missing_gitea_marker(self) -> None:
        self.assert_context_rejected(self.environment(GITEA_ACTIONS=""), "Gitea Actions forge markers")

    def test_rejects_missing_github_actions_marker(self) -> None:
        self.assert_context_rejected(self.environment(GITHUB_ACTIONS=""), "Gitea Actions forge markers")

    def test_rejects_conflicting_native_workflow_ref(self) -> None:
        self.assert_context_rejected(
            self.environment(GITHUB_WORKFLOW_REF="zaoz/dsh-gitea-search/.gitea/workflows/other.yml@refs/heads/rc/test"),
            "GITHUB_WORKFLOW_REF",
        )

    def test_rejects_repository_ref_and_sha_event_mismatch(self) -> None:
        cases = [
            {"repository": "zaoz/other", "ref": "refs/heads/rc/test", "after": self.sha},
            {"repository": "zaoz/dsh-gitea-search", "ref": "refs/heads/evil", "after": self.sha},
            {"repository": "zaoz/dsh-gitea-search", "ref": "refs/heads/rc/test", "after": "0" * 40},
        ]
        for case in cases:
            with self.subTest(case=case):
                self.write_event(**case)
                self.assert_context_rejected(self.environment(), "event payload does not match")

    def test_rejects_environment_sha_mismatch(self) -> None:
        self.assert_context_rejected(self.environment(GITHUB_SHA="0" * 40), "GITHUB_SHA")

    def test_rejects_wrong_workflow_name(self) -> None:
        self.assert_context_rejected(self.environment(GITHUB_WORKFLOW="other"), "identity is incomplete")


if __name__ == "__main__":
    unittest.main()
