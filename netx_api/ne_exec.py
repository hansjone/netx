"""Execute read-only CLI on netx managed network elements (for oclaw ops tools)."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .cli_resolve import resolve_cli_target
from .config import settings
from .ne_collect_runner import _collect_on_device
from .ne_crypto import credentials_configured
from .ne_exec_guard import _validate_command, validate_ne_exec_command

_EXEC_MAX_COMMANDS_CAP = 50
_EXEC_MAX_OUTPUT = 32_000
_EXEC_READ_TIMEOUT_DEFAULT = 60
_EXEC_READ_TIMEOUT_MAX = 120

__all__ = [
    "_validate_command",
    "execute_managed_ne_commands",
    "validate_ne_exec_command",
]


def _exec_max_commands() -> int:
    raw = int(settings.ne_exec_max_commands or 5)
    return max(1, min(_EXEC_MAX_COMMANDS_CAP, raw))


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
        validate_ne_exec_command(c)

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
