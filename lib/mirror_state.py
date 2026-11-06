"""Read-only sync metadata; an attempt timestamp never proves success."""
from datetime import datetime
from pathlib import Path
import re


_TIMESTAMP_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})?$"
)


def _normalize_timestamp(raw):
    # Match Python 3.10 and Node parsing: normalize fractional seconds to six digits,
    # Z to +00:00, and a space separator to T before calendar validation.
    if not isinstance(raw, str):
        return None
    match = _TIMESTAMP_RE.fullmatch(raw)
    if not match:
        return None
    date_part, time_part, frac_part, tz_part = match.groups()
    frac = f".{frac_part[:6].ljust(6, '0')}" if frac_part is not None else ""
    tz = "+00:00" if tz_part == "Z" else (tz_part or "")
    normalized = f"{date_part}T{time_part}{frac}{tz}"
    try:
        datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return normalized


def _timestamp(root, name):
    try:
        raw = (Path(root) / name).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeError):
        return None, name
    normalized = _normalize_timestamp(raw)
    if not normalized:
        return None, name
    return raw, None


def read_sync_state(root):
    attempt, attempt_error = _timestamp(root, "last_sync")
    success, success_error = _timestamp(root, "state/last_ok")
    errors = [e for e in (attempt_error, success_error) if e]
    status = "unknown"
    try:
        raw = (Path(root) / "state/last_status").read_text(encoding="utf-8")
        fail = re.search(r"(?:^|\s)fail=(\d+)(?:\s|$)", raw)
        if fail:
            status = "failed" if int(fail[1]) else "success"
        else:
            errors.append("state/last_status")
    except FileNotFoundError:
        pass
    except (OSError, UnicodeError):
        errors.append("state/last_status")
    if errors:
        status = "invalid"
    elif not attempt and not success and status == "unknown":
        status = "unrecorded"
    return {"status": status, "last_attempt": attempt, "last_success": success, "invalid_records": errors}


def sync_summary(state):
    label = {
        "unrecorded": "无同步记录（镜像可能手工建立）",
        "invalid": "同步记录损坏或不可读",
        "failed": "最近同步失败",
        "success": "最近同步成功",
        "unknown": "同步结果未记录",
    }[state["status"]]
    return f"{label}；最近尝试: {state['last_attempt'] or '无记录'}；最近成功: {state['last_success'] or '无记录'}"
