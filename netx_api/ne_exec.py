"""Execute read-only CLI on netx managed network elements (for oclaw ops tools)."""

from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .config import settings
from .models import ManagedNE
from .ne_collect_runner import _collect_on_device
from .ne_crypto import credentials_configured
from .ne_service import get_device_credentials, row_to_out

_EXEC_MAX_COMMANDS = 5
_EXEC_MAX_OUTPUT = 32_000
_EXEC_READ_TIMEOUT_DEFAULT = 60
_EXEC_READ_TIMEOUT_MAX = 120

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

# Only vendor read-only query verbs (Cisco show / Huawei-ZTE display).
_ALLOWED_PREFIX_RE = re.compile(r"(?i)^(show\s|display\s)")

# Unicode / C1 line separators that can smuggle a second CLI after a show prefix.
_FORBIDDEN_LINE_SEPARATORS = ("\u2028", "\u2029", "\x85", "\x0b", "\x0c")


def _validate_command(command: str) -> None:
    cmd = str(command or "").strip()
    if not cmd:
        raise HTTPException(status_code=400, detail="empty_command")
    if len(cmd) > 500:
        raise HTTPException(status_code=400, detail="command_too_long")
    if any(ch in cmd for ch in ("|", ";", "\n", "\r", "`")):
        raise HTTPException(status_code=400, detail="command_chars_not_allowed")
    if any(sep in cmd for sep in _FORBIDDEN_LINE_SEPARATORS):
        raise HTTPException(status_code=400, detail="command_chars_not_allowed")
    if _BLOCKED_RE.search(cmd):
        raise HTTPException(status_code=400, detail="command_blocked")
    if not _ALLOWED_PREFIX_RE.match(cmd):
        raise HTTPException(status_code=400, detail="command_not_allowed_prefix")


def _normalize_read_timeout(sec: int | None) -> int:
    raw = int(sec if sec is not None else _EXEC_READ_TIMEOUT_DEFAULT)
    return max(10, min(_EXEC_READ_TIMEOUT_MAX, raw))


def execute_managed_ne_commands(
    db: Session,
    ne_id: str,
    commands: list[str],
    *,
    read_timeout_sec: int | None = None,
) -> dict[str, Any]:
    if not credentials_configured():
        raise HTTPException(status_code=503, detail="credential_secret_key_not_configured")
    nid = str(ne_id or "").strip()
    if not nid:
        raise HTTPException(status_code=400, detail="ne_id_required")
    cmds = [str(c).strip() for c in commands if str(c).strip()]
    if not cmds:
        raise HTTPException(status_code=400, detail="commands_required")
    if len(cmds) > _EXEC_MAX_COMMANDS:
        raise HTTPException(status_code=400, detail=f"too_many_commands (max {_EXEC_MAX_COMMANDS})")
    for c in cmds:
        _validate_command(c)

    row = db.get(ManagedNE, nid)
    if not row:
        raise HTTPException(status_code=404, detail="managed_ne_not_found")

    read_timeout = _normalize_read_timeout(read_timeout_sec)
    creds = get_device_credentials(row)
    meta = row_to_out(row).model_dump()
    # Shallow copy for response (no secrets).
    device = {
        "id": meta["id"],
        "name": meta["name"],
        "vendor": meta["vendor"],
        "device_type": meta["device_type"],
        "ip_address": meta["ip_address"],
        "port": meta["port"],
        "protocol": meta["protocol"],
        "connect_status": meta["connect_status"],
        "hop_enabled": meta["hop_enabled"],
        "hop_vendor": meta["hop_vendor"],
    }

    prev_collect_timeout = int(settings.ne_collect_read_timeout_sec or 120)
    try:
        settings.ne_collect_read_timeout_sec = read_timeout
        output = _collect_on_device(creds, cmds)
    except Exception as exc:
        return {
            "ok": False,
            "device": device,
            "commands": cmds,
            "read_timeout_sec": read_timeout,
            "error": type(exc).__name__,
            "detail": str(exc)[:2000],
        }
    finally:
        settings.ne_collect_read_timeout_sec = prev_collect_timeout

    if len(output) > _EXEC_MAX_OUTPUT:
        output = output[:_EXEC_MAX_OUTPUT] + "\n...[truncated]"

    return {
        "ok": True,
        "device": device,
        "commands": cmds,
        "read_timeout_sec": read_timeout,
        "output": output,
    }
