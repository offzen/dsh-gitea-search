#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared effective-configuration resolver for Python entry points."""

import json
import os
from pathlib import Path

PLACEHOLDER_OWNERS = {"myorg", "example-org", "your-org", "your-owner"}


def _platform_default_root():
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return str(Path(base) / "dsh-gitea-search" / "mirror")
    if sys_platform() == "darwin":
        return str(Path.home() / "Library" / "Application Support" / "dsh-gitea-search" / "mirror")
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return str(Path(base) / "dsh-gitea-search" / "mirror")


def sys_platform():
    import sys
    return sys.platform


def _config_candidates(explicit_config=None, project_dir=None):
    out = []
    if explicit_config:
        out.append((Path(explicit_config).expanduser(), "explicit-config"))
    env_path = os.environ.get("GITEA_SEARCH_CONFIG")
    if env_path and not explicit_config:
        out.append((Path(env_path).expanduser(), "env-config"))
    out.append((Path.home() / ".config" / "dsh-gitea-search" / "config.json", "user-config"))
    if project_dir:
        out.append((Path(project_dir) / "config.json", "project-config"))
    return out


def _read_config(explicit_config=None, project_dir=None):
    for path, source in _config_candidates(explicit_config, project_dir):
        if not path.is_file():
            continue
        try:
            return json.loads(path.read_text(encoding="utf-8-sig")), str(path), source
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid config file {path}: {exc}") from exc
    return {}, None, None


def _owners(raw):
    if isinstance(raw, list):
        values = raw
    else:
        values = str(raw or "").split(",")
    return [str(v).strip() for v in values if str(v).strip()]


def resolve_config(cli_root=None, cli_owners=None, config_path=None, project_dir=None):
    cfg, loaded_path, file_source = _read_config(config_path, project_dir)
    file_root = (cfg.get("storage") or {}).get("mirror_root") or cfg.get("mirror_root")
    file_owners = (cfg.get("gitea") or {}).get("owners") or cfg.get("owners")

    env_root = (
        os.environ.get("GITEA_MIRROR_ROOT")
        or os.environ.get("GITEA_MIRROR_PATH")
        or os.environ.get("GITQ_MIRROR_ROOT")
    )
    env_owners = (
        os.environ.get("GITEA_OWNERS")
        or os.environ.get("GITEA_OWNER")
        or os.environ.get("GITQ_OWNER")
    )

    if cli_root:
        root, root_source = cli_root, "cli"
    elif env_root:
        root, root_source = env_root, "env"
    elif file_root:
        root, root_source = file_root, file_source
    else:
        root, root_source = _platform_default_root(), "platform-default"

    if cli_owners:
        owners, owners_source = _owners(cli_owners), "cli"
    elif env_owners:
        owners, owners_source = _owners(env_owners), "env"
    elif file_owners:
        owners, owners_source = _owners(file_owners), file_source
    else:
        owners, owners_source = [], "missing"

    errors = []
    if not owners:
        errors.append("owners are not configured")
    elif any(owner.lower() in PLACEHOLDER_OWNERS for owner in owners):
        errors.append("owners contain a placeholder value")

    return {
        "mirror_root": os.path.abspath(os.path.expanduser(str(root))),
        "owners": owners,
        "sources": {"mirror_root": root_source, "owners": owners_source},
        "config_path": loaded_path,
        "valid": not errors,
        "errors": errors,
    }


def require_config(**kwargs):
    resolved = resolve_config(**kwargs)
    if not resolved["valid"]:
        raise ValueError("CONFIG_ERROR: " + "; ".join(resolved["errors"]))
    return resolved
