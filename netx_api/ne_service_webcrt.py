"""WebCRT managed-NE host upsert helpers."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .device_types import WEBCRT_DEVICE_TYPES, WEBCRT_NE_SOURCE
from .models import ManagedNE
from .ne_crypto import encrypt_secret
from .ne_schemas import ManagedNeCreate, ManagedNeOut
from .ne_service_common import (
    WEBCRT_SOURCE,
    _apply_hop_create,
    _normalize_hop_target_auth_mode,
    _normalize_hop_vendor,
    _normalize_ip,
    _normalize_protocol,
    _normalize_vendor,
    _now,
    _require_crypto,
    _validate_hop_on_create,
    row_to_out,
)

def _normalize_webcrt_device_type(device_type: str) -> str:
    dt = str(device_type or "").strip()
    low = dt.lower()
    if low in ("linux", "linux_ssh", "linux_telnet"):
        return "linux"
    if low in ("generic", "generic_ssh", "generic_telnet", "terminal_server", "generic_termserver"):
        return "generic"
    return dt


def upsert_webcrt_managed_ne(db: Session, body: ManagedNeCreate) -> tuple[ManagedNeOut, str]:
    """Create/update a WebCRT-origin NE, or reuse an existing inventory NE by IP.

    Returns ``(ne_out, action)`` where action is ``created`` | ``updated`` | ``reused``.
    """
    _require_crypto()
    _validate_hop_on_create(body)
    ip = _normalize_ip(body.ip_address)
    if not ip:
        raise HTTPException(status_code=400, detail="ip_address_required")
    if not str(body.username or "").strip():
        raise HTTPException(status_code=400, detail="cli_username_required")
    device_type = _normalize_webcrt_device_type(body.device_type)
    if device_type not in WEBCRT_DEVICE_TYPES:
        raise HTTPException(status_code=400, detail="unsupported_device_type")

    existing = db.query(ManagedNE).filter(ManagedNE.ip_address == ip).first()
    now = _now()

    if existing is not None:
        src = str(existing.source or "").strip()
        if src != WEBCRT_NE_SOURCE:
            # Do not overwrite inventory / UME-synced assets; just open them.
            return row_to_out(existing), "reused"

        existing.name = str(body.name or "").strip() or existing.name or ip
        existing.vendor = _normalize_vendor(body.vendor) if str(body.vendor or "").strip() else (
            "Other" if device_type == "linux" else existing.vendor
        )
        existing.device_type = device_type
        existing.port = int(body.port or existing.port or 22)
        existing.protocol = _normalize_protocol(body.protocol)
        existing.username = str(body.username or "").strip()
        if str(body.password or "").strip():
            existing.password_enc = encrypt_secret(body.password)
        elif not str(existing.password_enc or "").strip() and not (
            body.hop_enabled
            and _normalize_hop_vendor(body.hop_vendor) == "bastion"
            and _normalize_hop_target_auth_mode(body.hop_target_auth_mode) == "bastion_managed"
        ):
            raise HTTPException(status_code=400, detail="password_required")
        existing.source = WEBCRT_NE_SOURCE
        _apply_hop_create(existing, body)
        existing.updated_at = now
        db.commit()
        db.refresh(existing)
        return row_to_out(existing), "updated"

    if not str(body.password or "").strip() and not (
        body.hop_enabled
        and _normalize_hop_vendor(body.hop_vendor) == "bastion"
        and _normalize_hop_target_auth_mode(body.hop_target_auth_mode) == "bastion_managed"
    ):
        raise HTTPException(status_code=400, detail="password_required")

    vendor = _normalize_vendor(body.vendor)
    if device_type == "linux" and not str(body.vendor or "").strip():
        vendor = "Other"

    row = ManagedNE(
        name=str(body.name or "").strip() or ip,
        vendor=vendor,
        device_type=device_type,
        ip_address=ip,
        port=int(body.port or 22),
        protocol=_normalize_protocol(body.protocol),
        username=str(body.username or "").strip(),
        password_enc=encrypt_secret(body.password) if str(body.password or "").strip() else "",
        enable_secret_enc="",
        connect_status="unknown",
        tags=str(body.tags or "").strip(),
        remark=str(body.remark or "").strip(),
        source=WEBCRT_NE_SOURCE,
        source_ref="",
        created_at=now,
        updated_at=now,
    )
    _apply_hop_create(row, body)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row_to_out(row), "created"


def _next_webcrt_session_name(db: Session, base: str) -> str:
    """Return base, or ``base (1)``, ``base (2)``, … among WebCRT session names."""
    root = str(base or "").strip() or "session"
    rows = (
        db.query(ManagedNE.name)
        .filter(ManagedNE.source == WEBCRT_NE_SOURCE)
        .all()
    )
    taken = {str(r[0] or "").strip() for r in rows if str(r[0] or "").strip()}
    if root not in taken:
        return root
    n = 1
    while f"{root} ({n})" in taken:
        n += 1
    return f"{root} ({n})"


def upsert_webcrt_session_host(
    db: Session,
    *,
    name: str = "",
    ip_address: str,
    port: int = 22,
    protocol: str = "ssh",
    username: str = "",
    password: str = "",
    save_password: bool = False,
) -> tuple[ManagedNeOut, str]:
    """Create a WebCRT session host (linux, no hop). Always inserts a new row.

    Same IP is allowed; session name auto-suffixes ``(1)``, ``(2)``, … on collision.
    Telnet never persists a password. SSH persists password only when ``save_password``.
    Returns ``(ne_out, \"created\")``.
    """
    _require_crypto()
    ip = _normalize_ip(ip_address)
    if not ip:
        raise HTTPException(status_code=400, detail="ip_address_required")
    proto = _normalize_protocol(protocol)
    user = str(username or "").strip()
    pwd = str(password or "")
    if proto == "ssh" and not user:
        raise HTTPException(status_code=400, detail="cli_username_required")
    if proto == "ssh" and save_password and not pwd.strip():
        raise HTTPException(status_code=400, detail="password_required")

    now = _now()
    display_name = _next_webcrt_session_name(db, str(name or "").strip() or ip)

    password_enc = ""
    if proto == "ssh" and save_password and pwd.strip():
        password_enc = encrypt_secret(pwd)

    row = ManagedNE(
        name=display_name,
        vendor="Other",
        # generic → Netmiko terminal_server: SSH auth then raw PTY (no linux session prep).
        device_type="generic",
        ip_address=ip,
        port=int(port or (23 if proto == "telnet" else 22)),
        protocol=proto,
        username=user,
        password_enc=password_enc,
        enable_secret_enc="",
        connect_status="unknown",
        tags="",
        remark="",
        source=WEBCRT_NE_SOURCE,
        source_ref="",
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        # Stale unique index on ip_address → restart API after migration, or drop constraint manually.
        from sqlalchemy.exc import IntegrityError

        if isinstance(exc, IntegrityError):
            raise HTTPException(
                status_code=409,
                detail="ip_address_conflict_restart_required",
            ) from exc
        raise
    db.refresh(row)
    return row_to_out(row), "created"


