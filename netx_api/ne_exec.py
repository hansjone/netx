"""Execute read-only CLI on netx managed network elements (for oclaw ops tools)."""

from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .cli_resolve import resolve_cli_target
from .config import settings
from .ne_collect_runner import _collect_on_device
from .ne_crypto import credentials_configured

_EXEC_MAX_COMMANDS_CAP = 50
_EXEC_MAX_OUTPUT = 32_000
_EXEC_READ_TIMEOUT_DEFAULT = 60
_EXEC_READ_TIMEOUT_MAX = 120


def _exec_max_commands() -> int:
    raw = int(settings.ne_exec_max_commands or 5)
    return max(1, min(_EXEC_MAX_COMMANDS_CAP, raw))

# Block obvious config-change / destructive patterns (case-insensitive).
_BLOCKED_RE = re.compile(
    r"(?i)("
    r"configure\s+terminal|conf\s+t\b|"
    r"\bwrite\s+(memory|erase)|\bcopy\s+run|\bcopy\s+startup|"
    r"\breload\b|\breboot\b|\berase\b|\bformat\b|\bdelete\b|"
    r"\bcommit\b|\brollback\b|startup-config|"
    r"\bsystem-view\b|\bip\s+address\b|\bvlan\s+\d"
    r")"
)

# Read-only CLI: show/display plus ping/traceroute reachability checks.
_ALLOWED_PREFIX_RE = re.compile(
    r"(?i)^(show\s|display\s|ping\s|ping6\s|traceroute\s|tracert\s|trace\s|trace6\s)"
)

# Unicode / C1 line separators that can smuggle a second CLI after a show prefix.
_FORBIDDEN_LINE_SEPARATORS = ("\u2028", "\u2029", "\x85", "\x0b", "\x0c")

# Pipe segments allowed after show/display (output filtering only).
_ALLOWED_PIPE_SEGMENT_RE = re.compile(
    r"(?i)^(include|exclude|begin|section|count|match|grep|one-line|no-more)(\s|$)"
)
_BLOCKED_PIPE_SEGMENT_RE = re.compile(r"(?i)\b(redirect|append|tee|send)\b")


def _validate_pipe_segments(cmd: str) -> None:
    if "|" not in cmd:
        return
    parts = [p.strip() for p in cmd.split("|")]
    if len(parts) < 2 or not parts[0] or any(not p for p in parts[1:]):
        raise HTTPException(status_code=400, detail="command_pipe_not_allowed")
    for segment in parts[1:]:
        if _BLOCKED_PIPE_SEGMENT_RE.search(segment):
            raise HTTPException(status_code=400, detail="command_pipe_not_allowed")
        if not _ALLOWED_PIPE_SEGMENT_RE.match(segment):
            raise HTTPException(status_code=400, detail="command_pipe_not_allowed")


def _validate_command(command: str) -> None:
    cmd = str(command or "").strip()
    if not cmd:
        raise HTTPException(status_code=400, detail="empty_command")
    if len(cmd) > 500:
        raise HTTPException(status_code=400, detail="command_too_long")
    if any(ch in cmd for ch in (";", "\n", "\r", "`")):
        raise HTTPException(status_code=400, detail="command_chars_not_allowed")
    if any(sep in cmd for sep in _FORBIDDEN_LINE_SEPARATORS):
        raise HTTPException(status_code=400, detail="command_chars_not_allowed")
    if _BLOCKED_RE.search(cmd):
        raise HTTPException(status_code=400, detail="command_blocked")
    if not _ALLOWED_PREFIX_RE.match(cmd):
        raise HTTPException(status_code=400, detail="command_not_allowed_prefix")
    _validate_pipe_segments(cmd)


def _normalize_read_timeout(sec: int | None) -> int:
    raw = int(sec if sec is not None else _EXEC_READ_TIMEOUT_DEFAULT)
    return max(10, min(_EXEC_READ_TIMEOUT_MAX, raw))


def execute_managed_ne_commands(
    db: Session,
    commands: list[str],
    *,
    ne_id: str | None = None,
    ume_ne_id: str | None = None,
    read_timeout_sec: int | None = None,
) -> dict[str, Any]:
    if not credentials_configured():
        raise HTTPException(status_code=503, detail="credential_secret_key_not_configured")
    mid = str(ne_id or "").strip()
    uid = str(ume_ne_id or "").strip()
    if bool(mid) == bool(uid):
        raise HTTPException(status_code=400, detail="exactly_one_of_ne_id_or_ume_ne_id_required")
    cmds = [str(c).strip() for c in commands if str(c).strip()]
    if not cmds:
        raise HTTPException(status_code=400, detail="commands_required")
    max_cmds = _exec_max_commands()
    if len(cmds) > max_cmds:
        raise HTTPException(status_code=400, detail=f"too_many_commands (max {max_cmds})")
    for c in cmds:
        _validate_command(c)

    creds, device = resolve_cli_target(db, managed_ne_id=mid or None, ume_ne_id=uid or None)
    read_timeout = _normalize_read_timeout(read_timeout_sec)

    try:
        output = _collect_on_device(creds, cmds, read_timeout_sec=read_timeout)
    except Exception as exc:
        return {
            "ok": False,
            "device": device,
            "commands": cmds,
            "read_timeout_sec": read_timeout,
            "error": type(exc).__name__,
            "detail": str(exc)[:2000],
        }

    if len(output) > _EXEC_MAX_OUTPUT:
        output = output[:_EXEC_MAX_OUTPUT] + "\n...[truncated]"

    return {
        "ok": True,
        "device": device,
        "commands": cmds,
        "read_timeout_sec": read_timeout,
        "output": output,
    }
