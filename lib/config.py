#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared effective-configuration resolver for Python entry points."""

import json
import os
import sys
from pathlib import Path

PLACEHOLDER_OWNERS = {"myorg", "example-org", "your-org", "your-owner"}
_SUPPORTED_TOP_LEVEL_KEYS = {"gitea", "storage", "mirror_root", "owners"}
_SUPPORTED_SECTION_KEYS = {"gitea": {"owners"}, "storage": {"mirror_root"}}
_WARNED_LEGACY_ENV = set()
_WARNED_MIGRATIONS = set()


def _write_stderr_utf8(text):
    stream = sys.stderr
    if stream is None:
        return
    enc = (getattr(stream, "encoding", None) or "").lower().replace("-", "").replace("_", "")
    if enc not in ("utf8", "utf8mb4"):
        rc = getattr(stream, "reconfigure", None)
        if rc is not None:
            try:
                rc(encoding="utf-8", errors="backslashreplace")
            except (ValueError, OSError):
                pass
    stream.write(text)


def _warn_once(store, key, message):
    if key in store:
        return
    store.add(key)
    _write_stderr_utf8(f"[gitea-search-mcp] ⚠️ {message}\n")


def _safe_config_key(path):
    return path if path and all(ch.isalnum() or ch in "_.-" for ch in path) else "<invalid-key-name>"


def _config_warnings(data):
    """Report ignored config key paths without ever echoing their values."""
    if not isinstance(data, dict):
        return []
    paths = []
    for key, value in data.items():
        if key not in _SUPPORTED_TOP_LEVEL_KEYS:
            paths.append(str(key))
            continue
        if key in _SUPPORTED_SECTION_KEYS and isinstance(value, dict):
            for child in value:
                if child not in _SUPPORTED_SECTION_KEYS[key]:
                    paths.append(f"{key}.{child}")
    return [
        f'config: key "{_safe_config_key(path)}" is not supported and is ignored'
        for path in sorted(paths)
    ]


def _platform_data_base():
    if os.name == "nt":
        return os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    if sys_platform() == "darwin":
        return str(Path.home() / "Library" / "Application Support")
    return os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")


def _platform_default_root():
    base = Path(_platform_data_base())
    preferred = str(base / "gitea-search-mcp" / "mirror")
    legacy = str(base / "dsh-gitea-search" / "mirror")
    if not os.path.exists(preferred) and os.path.exists(legacy):
        notice = f"config: using legacy default mirror directory {legacy}; migrate to {preferred}"
        _warn_once(_WARNED_MIGRATIONS, f"mirror:{legacy}", notice)
        return legacy, notice
    return preferred, None


def sys_platform():
    return sys.platform


def _config_candidates(explicit_config=None, project_dir=None):
    out = []
    if explicit_config:
        out.append((Path(explicit_config).expanduser(), "explicit-config", False))
    env_path = os.environ.get("GITEA_SEARCH_CONFIG")
    if env_path and not explicit_config:
        out.append((Path(env_path).expanduser(), "env-config", False))
    out.append((Path.home() / ".config" / "gitea-search-mcp" / "config.json", "user-config", False))
    out.append((Path.home() / ".config" / "dsh-gitea-search" / "config.json", "user-config", True))
    if project_dir:
        out.append((Path(project_dir) / "config.json", "project-config", False))
    return out


def _json_type(value):
    """JSON type name shared (verbatim) with lib/config.mjs so both sides emit identical errors."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _read_config(explicit_config=None, project_dir=None):
    """Return (data, path, source, errors, migration_warning); never raises for a bad/missing config file.

    Contract (single source of truth, mirrored in lib/config.mjs):
      * an explicitly requested file (--config / GITEA_SEARCH_CONFIG) that is missing or not a
        regular file is an error and NEVER falls back to the user/project config;
      * fallback to user/project config only happens when nothing was requested explicitly;
      * unreadable / non-JSON / wrongly-typed content is reported in ``errors``.
    """
    preferred_user_config = os.path.abspath(str(Path.home() / ".config" / "gitea-search-mcp" / "config.json"))
    for path, source, is_legacy_user in _config_candidates(explicit_config, project_dir):
        full = os.path.abspath(str(path))
        explicit = source in ("explicit-config", "env-config")
        if not os.path.isfile(full):
            if explicit:
                origin = "--config" if source == "explicit-config" else "GITEA_SEARCH_CONFIG"
                kind = "is not a regular file" if os.path.exists(full) else "does not exist"
                return {}, None, source, [
                    f"{origin} points to a config file that {kind}: {full} "
                    "(an explicitly specified config never falls back to user/project config)"
                ], None
            continue
        try:
            data = json.loads(Path(full).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:  # JSONDecodeError and UnicodeDecodeError are ValueErrors
            return {}, full, source, [f"invalid config file {full}: {exc}"], None
        errors = _validate_config(data, full)
        if errors:
            return {}, full, source, errors, None
        migration_warning = None
        if is_legacy_user:
            migration_warning = f"config: using legacy user config path {full}; migrate to {preferred_user_config}"
            _warn_once(_WARNED_MIGRATIONS, f"user-config:{full}", migration_warning)
        return data, full, source, [], migration_warning
    return {}, None, None, [], None


def _validate_config(data, full):
    """Structural validation of a parsed config file; returns a list of error strings."""
    prefix = f"invalid config file {full}: "
    if not isinstance(data, dict):
        return [prefix + f"top-level value must be a JSON object, got {_json_type(data)}"]
    errors = []
    for section in ("storage", "gitea"):
        value = data.get(section)
        if value is not None and not isinstance(value, dict):
            errors.append(prefix + f'"{section}" must be an object, got {_json_type(value)}')
    storage = data.get("storage") if isinstance(data.get("storage"), dict) else {}
    gitea = data.get("gitea") if isinstance(data.get("gitea"), dict) else {}
    for label, value in (("storage.mirror_root", storage.get("mirror_root")), ("mirror_root", data.get("mirror_root"))):
        if value is not None and not isinstance(value, str):
            errors.append(prefix + f'"{label}" must be a string, got {_json_type(value)}')
    for label, value in (("gitea.owners", gitea.get("owners")), ("owners", data.get("owners"))):
        if value is None or isinstance(value, str):
            continue
        if not isinstance(value, list):
            errors.append(prefix + f'"{label}" must be a string or an array of strings, got {_json_type(value)}')
            continue
        for index, item in enumerate(value):
            if not isinstance(item, str):
                errors.append(prefix + f'"{label}" items must be strings, item {index} is {_json_type(item)}')
                break
    return errors


def _owners(raw):
    if isinstance(raw, list):
        values = raw
    else:
        values = str(raw or "").split(",")
    return [str(v).strip() for v in values if str(v).strip()]


def _pick_owners(layers):
    """First layer whose *normalized* list is non-empty wins.

    Single cross-language contract: an empty array / empty string / ``" , "`` is an empty set,
    equivalent to "not set", so the next layer is consulted (lib/config.mjs does the same).
    """
    for raw, source in layers:
        names = _owners(raw)
        if names:
            return names, source
    return [], "missing"


def resolve_config(cli_root=None, cli_owners=None, config_path=None, project_dir=None):
    cfg, loaded_path, file_source, file_errors, migration_warning = _read_config(config_path, project_dir)
    storage = cfg.get("storage") or {}
    gitea = cfg.get("gitea") or {}
    file_root = storage.get("mirror_root") or cfg.get("mirror_root")

    primary_env_root = os.environ.get("GITEA_MIRROR_ROOT") or os.environ.get("GITEA_MIRROR_PATH")
    legacy_env_root = None if primary_env_root else os.environ.get("GITQ_MIRROR_ROOT")
    if legacy_env_root:
        _warn_once(_WARNED_LEGACY_ENV, "GITQ_MIRROR_ROOT", "环境变量 GITQ_MIRROR_ROOT 已废弃，请改用 GITEA_MIRROR_ROOT。")
    env_root = primary_env_root or legacy_env_root

    primary_env_owners = os.environ.get("GITEA_OWNERS") or os.environ.get("GITEA_OWNER")
    legacy_env_owners = None if primary_env_owners else os.environ.get("GITQ_OWNER")
    if legacy_env_owners:
        _warn_once(_WARNED_LEGACY_ENV, "GITQ_OWNER", "环境变量 GITQ_OWNER 已废弃，请改用 GITEA_OWNERS。")
    env_owners = primary_env_owners or legacy_env_owners

    warnings = _config_warnings(cfg)
    if migration_warning:
        warnings.append(migration_warning)

    if cli_root:
        root, root_source = cli_root, "cli"
    elif env_root:
        root, root_source = env_root, "env"
    elif file_root:
        root, root_source = file_root, file_source
    else:
        root, mirror_warning = _platform_default_root()
        root_source = "platform-default"
        if mirror_warning:
            warnings.append(mirror_warning)

    owners, owners_source = _pick_owners([
        (cli_owners, "cli"),
        (env_owners, "env"),
        (gitea.get("owners"), file_source),
        (cfg.get("owners"), file_source),
    ])

    errors = list(file_errors)
    if not owners:
        if not errors:
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
        "warnings": warnings,
    }


def require_config(**kwargs):
    resolved = resolve_config(**kwargs)
    if not resolved["valid"]:
        raise ValueError("CONFIG_ERROR: " + "; ".join(resolved["errors"]))
    return resolved
