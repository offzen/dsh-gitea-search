#!/usr/bin/env python3
"""Validate built Python artifacts from outside the source checkout."""

import argparse
import json
import os
import pathlib
import subprocess
import tarfile
import tempfile
import venv
import zipfile

ALIASES = ("gts", "gitq", "dsh-search")
SDIST_SUFFIXES = ("/gts_cli.py", "/bin/gts", "/lib/indexer.py", "/lib/config.py", "/lib/mirror_state.py", "/scripts/sync.sh")
WHEEL_SUFFIXES = (
    "gts_cli.py",
    ".data/data/share/dsh-gitea-search/bin/gts",
    ".data/data/share/dsh-gitea-search/lib/indexer.py",
    ".data/data/share/dsh-gitea-search/lib/config.py",
    ".data/data/share/dsh-gitea-search/lib/mirror_state.py",
    ".data/data/share/dsh-gitea-search/scripts/sync.sh",
)


def run(args, *, cwd=None, env=None):
    result = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if result.returncode:
        raise RuntimeError(
            f"command failed ({result.returncode}): {args!r}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def assert_members(artifact):
    if artifact.name.endswith(".tar.gz"):
        with tarfile.open(artifact) as archive:
            names = archive.getnames()
        required = SDIST_SUFFIXES
    else:
        with zipfile.ZipFile(artifact) as archive:
            names = archive.namelist()
        required = WHEEL_SUFFIXES
    missing = [suffix for suffix in required if not any(name.endswith(suffix) for name in names)]
    if missing:
        raise AssertionError(f"{artifact.name} is missing packaged paths: {missing}")


def launcher_path(venv_dir, alias):
    scripts = venv_dir / ("Scripts" if os.name == "nt" else "bin")
    return scripts / (f"{alias}.exe" if os.name == "nt" else alias)


def same_path(left, right):
    left_path = pathlib.Path(left)
    right_path = pathlib.Path(right)
    if os.name != "nt":
        return left_path.resolve() == right_path.resolve()
    try:
        return os.path.samefile(left_path, right_path)
    except (FileNotFoundError, OSError):
        return os.path.normcase(os.path.abspath(str(left_path))) == os.path.normcase(
            os.path.abspath(str(right_path))
        )


def validate_artifact(artifact, scratch):
    kind = "sdist" if artifact.name.endswith(".tar.gz") else "wheel"
    venv_dir = scratch / f"{kind}-venv"
    venv.EnvBuilder(with_pip=True).create(venv_dir)
    python = launcher_path(venv_dir, "python")
    install = [str(python), "-m", "pip", "install", "--no-deps", str(artifact)]
    run(install)

    neutral = scratch / "neutral"
    neutral.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    mirror = scratch / f"{kind}-mirror"

    forbidden = ("未找到共享配置模块", "找不到 bin/gts", "RecursionError")
    for alias in ALIASES:
        command = launcher_path(venv_dir, alias)
        help_result = run([str(command), "--help"], cwd=neutral, env=env)
        if "usage:" not in help_result.stdout:
            raise AssertionError(f"{alias} --help did not emit usage")
        config_result = run(
            [
                str(command),
                "--mirror-root",
                str(mirror),
                "--owners",
                "acme",
                "--show-effective-config",
            ],
            cwd=neutral,
            env=env,
        )
        config = json.loads(config_result.stdout)
        if not config["valid"] or config["owners"] != ["acme"]:
            raise AssertionError(f"unexpected config from {alias}: {config}")
        pathlib.Path(config["mirror_root"]).mkdir(parents=True, exist_ok=True)
        if not same_path(config["mirror_root"], mirror.resolve()):
            raise AssertionError(f"unexpected mirror root from {alias}: {config['mirror_root']}")
        combined = help_result.stderr + config_result.stderr
        if any(marker in combined for marker in forbidden):
            raise AssertionError(f"{alias} emitted a packaging failure: {combined}")
        if os.name == "nt" and command.read_bytes()[:2] != b"MZ":
            raise AssertionError(f"{command} is not a PE launcher")

    stats = run(
        [
            str(launcher_path(venv_dir, "gts")),
            "--mirror-root",
            str(mirror),
            "--owners",
            "acme",
            "--fts-stats",
        ],
        cwd=neutral,
        env=env,
    )
    if "索引库不存在" not in stats.stdout:
        raise AssertionError(f"packaged indexer was not reached: {stats.stdout}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dist", type=pathlib.Path, help="directory containing one wheel and one sdist")
    parser.add_argument("--inspect-only", action="store_true", help="only validate archive members")
    args = parser.parse_args()
    dist = args.dist.resolve()
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        parser.error(f"expected exactly one wheel and one sdist in {dist}")

    artifacts = (wheels[0], sdists[0])
    for artifact in artifacts:
        assert_members(artifact)
    if args.inspect_only:
        print("PACKAGE_CONTENTS_OK wheel+sdist")
        return
    with tempfile.TemporaryDirectory(prefix="gts-package-gate-") as temp:
        scratch = pathlib.Path(temp)
        for artifact in artifacts:
            validate_artifact(artifact, scratch)
    print("PACKAGE_GATE_OK wheel+sdist aliases=3 config=ok indexer=ok")


if __name__ == "__main__":
    main()
