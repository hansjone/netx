"""Managed NE shared helpers, constants, and credential extraction."""
from __future__ import annotations

from datetime import datetime
import re
from typing import Any

import pandas as pd
from fastapi import HTTPException
from sqlalchemy.orm import Session

from .device_types import (
    SUPPORTED_DEVICE_TYPES,
    SUPPORTED_VENDORS,
    WEBCRT_DEVICE_TYPES,
    WEBCRT_NE_SOURCE,
)
from .models import ManagedNE
from .ne_crypto import CredentialCryptoError, credentials_configured, decrypt_secret, encrypt_secret
from .ne_schemas import ManagedNeCreate, ManagedNeOut, ManagedNeUpdate
from .ne_session_factory import default_bastion_username_template, default_hop_command_template
from .timeutil import utcnow_naive

IMPORT_COLUMNS = (
    "device_type",
    "ip",
    "username",
    "password",
    "port",
    "protocol",
    "name",
    "vendor",
    "tags",
    "remark",
)

UME_SYNC_SOURCE = "ume_sync"
UME_SYNC_TAG = "UME"
# Re-export for callers (WebCRT quick-connect).
WEBCRT_SOURCE = WEBCRT_NE_SOURCE
_BUILTIN_NE_TYPE_RULES: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"ZXR|ZXCTN|M6000|\bBN\b", re.I), "zte_zxros", "ZTE"),
    (re.compile(r"NE40|CE\b|ATN|MA5800|OptiX", re.I), "huawei", "Huawei"),
    (re.compile(r"ASR|NCS|IOS.?XR|XR\b", re.I), "cisco_xr", "Cisco"),
    (re.compile(r"Catalyst|Nexus|C9[0-9]{3}|ISR", re.I), "cisco_ios", "Cisco"),
]

def _now() -> datetime:
    return utcnow_naive()


def _require_crypto() -> None:
    if not credentials_configured():
        raise HTTPException(status_code=503, detail="credential_secret_key_not_configured")


def _normalize_ip(ip: str) -> str:
    return str(ip or "").strip()


def _normalize_protocol(protocol: str) -> str:
    p = str(protocol or "ssh").strip().lower()
    return p if p in ("ssh", "telnet") else "ssh"


def _normalize_hop_vendor(vendor: str) -> str:
    v = str(vendor or "zte").strip().lower()
    return v if v in ("zte", "linux", "huawei", "cisco", "bastion") else "zte"


def _normalize_hop_target_auth_mode(mode: str) -> str:
    m = str(mode or "bastion_managed").strip().lower()
    return m if m in ("bastion_managed", "manual") else "bastion_managed"


def _normalize_vendor(vendor: str) -> str:
    raw = str(vendor or "").strip()
    if not raw:
        return "Other"
    for item in SUPPORTED_VENDORS:
        if item.lower() == raw.lower():
            return item
    return "Other"


def _merge_tags(tags: str, *extras: str) -> str:
    seen: set[str] = set()
    out: list[str] = []
    for token in str(tags or "").split():
        t = token.strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    for extra in extras:
        t = str(extra or "").strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return " ".join(out)


def _infer_managed_ne_type_vendor(ne_type: str, vendor: str) -> tuple[str, str]:
    raw_vendor = _normalize_vendor(vendor)
    text = str(ne_type or "").strip()
    for pattern, device_type, inferred_vendor in _BUILTIN_NE_TYPE_RULES:
        if pattern.search(text):
            dt = device_type if device_type in SUPPORTED_DEVICE_TYPES else "zte_zxros"
            return dt, _normalize_vendor(inferred_vendor or raw_vendor)
    if raw_vendor == "Huawei":
        return "huawei", "Huawei"
    if raw_vendor == "Cisco":
        return "cisco_ios", "Cisco"
    if raw_vendor == "ZTE":
        return "zte_zxros", "ZTE"
    return "zte_zxros", raw_vendor


def _validate_hop_on_create(body: ManagedNeCreate) -> None:
    if not body.hop_enabled:
        return
    if not str(body.hop_host or "").strip():
        raise HTTPException(status_code=400, detail="hop_host_required")
    if not str(body.hop_username or "").strip():
        raise HTTPException(status_code=400, detail="hop_username_required")
    if not str(body.hop_password or "").strip():
        raise HTTPException(status_code=400, detail="hop_password_required")
    hop_vendor = _normalize_hop_vendor(body.hop_vendor)
    if hop_vendor == "bastion" and _normalize_hop_target_auth_mode(body.hop_target_auth_mode) == "manual":
        if not str(body.password or "").strip():
            raise HTTPException(status_code=400, detail="password_required")


def _parse_import_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "y", "on")


def _import_cell_str(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def _apply_hop_create(row: ManagedNE, body: ManagedNeCreate) -> None:
    row.hop_enabled = bool(body.hop_enabled)
    row.hop_vendor = _normalize_hop_vendor(body.hop_vendor)
    row.hop_host = str(body.hop_host or "").strip()
    row.hop_port = int(body.hop_port or 22)
    row.hop_protocol = _normalize_protocol(body.hop_protocol)
    row.hop_username = str(body.hop_username or "").strip()
    row.hop_password_enc = encrypt_secret(body.hop_password) if body.hop_enabled else ""
    row.hop_command_template = str(body.hop_command_template or "").strip()
    row.hop_vrf = str(body.hop_vrf or "").strip()
    row.hop_target_auth_mode = _normalize_hop_target_auth_mode(body.hop_target_auth_mode)


def _apply_hop_update(row: ManagedNE, data: dict[str, Any]) -> None:
    if "hop_enabled" in data and data["hop_enabled"] is not None:
        row.hop_enabled = bool(data["hop_enabled"])
    if "hop_vendor" in data and data["hop_vendor"] is not None:
        row.hop_vendor = _normalize_hop_vendor(data["hop_vendor"])
    if "hop_host" in data and data["hop_host"] is not None:
        row.hop_host = str(data["hop_host"]).strip()
    if "hop_port" in data and data["hop_port"] is not None:
        row.hop_port = int(data["hop_port"])
    if "hop_protocol" in data and data["hop_protocol"] is not None:
        row.hop_protocol = _normalize_protocol(data["hop_protocol"])
    if "hop_username" in data and data["hop_username"] is not None:
        row.hop_username = str(data["hop_username"]).strip()
    if "hop_password" in data and data["hop_password"]:
        _require_crypto()
        row.hop_password_enc = encrypt_secret(str(data["hop_password"]))
    if "hop_command_template" in data and data["hop_command_template"] is not None:
        row.hop_command_template = str(data["hop_command_template"]).strip()
    if "hop_vrf" in data and data["hop_vrf"] is not None:
        row.hop_vrf = str(data["hop_vrf"]).strip()
    if "hop_target_auth_mode" in data and data["hop_target_auth_mode"] is not None:
        row.hop_target_auth_mode = _normalize_hop_target_auth_mode(data["hop_target_auth_mode"])
    if row.hop_enabled:
        if not str(row.hop_host or "").strip():
            raise HTTPException(status_code=400, detail="hop_host_required")
        if not str(row.hop_username or "").strip():
            raise HTTPException(status_code=400, detail="hop_username_required")
        if (
            not str(row.hop_password_enc or "").strip()
            and _normalize_hop_target_auth_mode(row.hop_target_auth_mode) != "bastion_managed"
        ):
            raise HTTPException(status_code=400, detail="hop_password_required")


def row_to_out(row: ManagedNE) -> ManagedNeOut:
    status = str(row.connect_status or "unknown")
    if status not in ("unknown", "testing", "pass", "fail"):
        status = "unknown"
    return ManagedNeOut(
        id=str(row.id),
        name=str(row.name or ""),
        vendor=str(row.vendor or "Other"),
        device_type=str(row.device_type or ""),
        ip_address=str(row.ip_address or ""),
        port=int(row.port or 22),
        protocol=str(row.protocol or "ssh"),
        username=str(row.username or ""),
        connect_status=status,  # type: ignore[arg-type]
        connect_message=str(row.connect_message or "")[:500],
        connect_detail=str(row.connect_detail or "")[:8000],
        connect_tested_at=row.connect_tested_at,
        tags=str(row.tags or ""),
        remark=str(row.remark or ""),
        source=str(row.source or ""),
        source_ref=str(row.source_ref or ""),
        hop_enabled=bool(row.hop_enabled),
        hop_vendor=str(row.hop_vendor or "zte"),
        hop_host=str(row.hop_host or ""),
        hop_port=int(row.hop_port or 22),
        hop_protocol=str(row.hop_protocol or "ssh"),
        hop_username=str(row.hop_username or ""),
        hop_command_template=str(row.hop_command_template or ""),
        hop_vrf=str(row.hop_vrf or ""),
        hop_target_auth_mode=str(row.hop_target_auth_mode or "bastion_managed"),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )



def get_device_credentials(row: ManagedNE) -> dict[str, Any]:
    hop_enabled = bool(row.hop_enabled)
    hop_password = ""
    if hop_enabled and str(row.hop_password_enc or "").strip():
        hop_password = decrypt_secret(row.hop_password_enc)
    return {
        "id": str(row.id),
        "vendor": str(row.vendor or ""),
        "device_type": str(row.device_type or ""),
        "ip_address": str(row.ip_address or ""),
        "port": int(row.port or 22),
        "protocol": str(row.protocol or "ssh"),
        "username": str(row.username or ""),
        "password": decrypt_secret(row.password_enc),
        "enable_secret": decrypt_secret(row.enable_secret_enc),
        "name": str(row.name or ""),
        "hop_enabled": hop_enabled,
        "hop_vendor": str(row.hop_vendor or "zte"),
        "hop_host": str(row.hop_host or ""),
        "hop_port": int(row.hop_port or 22),
        "hop_protocol": str(row.hop_protocol or "ssh"),
        "hop_username": str(row.hop_username or ""),
        "hop_password": hop_password,
        "hop_command_template": str(row.hop_command_template or ""),
        "hop_vrf": str(row.hop_vrf or ""),
        "hop_target_auth_mode": str(row.hop_target_auth_mode or "bastion_managed"),
    }
