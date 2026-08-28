from __future__ import annotations

import json
import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .device_types import SUPPORTED_DEVICE_TYPES
from .models import CliConnectProfile, ManagedNE, UmeCliOverride, UmeInventoryNE
from .ne_crypto import decrypt_secret
from .ne_service import get_device_credentials, row_to_out

_BUILTIN_NE_TYPE_RULES: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"ZXR|ZXCTN|M6000|\bBN\b", re.I), "zte_zxros", "ZTE"),
    (re.compile(r"NE40|CE\b|ATN|MA5800|OptiX", re.I), "huawei", "Huawei"),
    (re.compile(r"ASR|NCS|IOS.?XR|XR\b", re.I), "cisco_xr", "Cisco"),
    (re.compile(r"Catalyst|Nexus|C9[0-9]{3}|ISR", re.I), "cisco_ios", "Cisco"),
]


def _parse_ne_type_rules(raw: str) -> list[tuple[re.Pattern[str], str, str]]:
    out: list[tuple[re.Pattern[str], str, str]] = []
    text = str(raw or "").strip()
    if not text:
        return out
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return out
    if not isinstance(data, list):
        return out
    for item in data:
        if not isinstance(item, dict):
            continue
        pattern = str(item.get("pattern") or item.get("match") or "").strip()
        device_type = str(item.get("device_type") or "").strip()
        vendor = str(item.get("vendor") or "").strip()
        if not pattern or not device_type:
            continue
        try:
            out.append((re.compile(pattern, re.I), device_type, vendor or "Other"))
        except re.error:
            continue
    return out


def infer_device_type_vendor(ne_type: str, profile: CliConnectProfile) -> tuple[str, str]:
    text = str(ne_type or "").strip()
    for rules in (_parse_ne_type_rules(profile.ne_type_rules), _BUILTIN_NE_TYPE_RULES):
        for pattern, device_type, vendor in rules:
            if pattern.search(text):
                dt = device_type if device_type in SUPPORTED_DEVICE_TYPES else str(profile.device_type_default or "zte_zxros")
                return dt, vendor or str(profile.vendor_default or "ZTE")
    dt = str(profile.device_type_default or "zte_zxros").strip()
    if dt not in SUPPORTED_DEVICE_TYPES:
        dt = "zte_zxros"
    return dt, str(profile.vendor_default or "ZTE")


def get_default_profile(db: Session) -> CliConnectProfile | None:
    row = (
        db.query(CliConnectProfile)
        .filter(CliConnectProfile.is_default.is_(True))
        .order_by(CliConnectProfile.updated_at.desc())
        .first()
    )
    if row is not None:
        return row
    return db.query(CliConnectProfile).order_by(CliConnectProfile.created_at.asc()).first()


def profile_to_creds(
    profile: CliConnectProfile,
    *,
    ip_address: str,
    username: str,
    device_type: str,
    vendor: str,
    port: int | None = None,
    protocol: str | None = None,
    target_password: str = "",
) -> dict[str, Any]:
    hop_password = ""
    if profile.hop_enabled and str(profile.hop_password_enc or "").strip():
        hop_password = decrypt_secret(profile.hop_password_enc)
    target_pass = target_password
    if not target_pass and str(profile.password_enc or "").strip():
        target_pass = decrypt_secret(profile.password_enc)
    return {
        "ip_address": str(ip_address),
        "port": int(port or profile.port or 22),
        "protocol": str(protocol or profile.protocol or "ssh"),
        "username": str(username),
        "password": str(target_pass),
        "device_type": str(device_type),
        "vendor": str(vendor),
        "enable_secret": "",
        "hop_enabled": bool(profile.hop_enabled),
        "hop_vendor": str(profile.hop_vendor or "zte"),
        "hop_host": str(profile.hop_host or ""),
        "hop_port": int(profile.hop_port or 22),
        "hop_protocol": str(profile.hop_protocol or "ssh"),
        "hop_username": str(profile.hop_username or ""),
        "hop_password": hop_password,
        "hop_command_template": str(profile.hop_command_template or ""),
        "hop_vrf": str(profile.hop_vrf or ""),
        "hop_target_auth_mode": str(profile.hop_target_auth_mode or "bastion_managed"),
        "hop_enter_system_view": bool(getattr(profile, "hop_enter_system_view", False)),
    }


def resolve_cli_target(
    db: Session,
    *,
    managed_ne_id: str | None = None,
    ume_ne_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    mid = str(managed_ne_id or "").strip()
    uid = str(ume_ne_id or "").strip()
    if bool(mid) == bool(uid):
        raise HTTPException(status_code=400, detail="exactly_one_of_ne_id_or_ume_ne_id_required")

    if mid:
        row = db.get(ManagedNE, mid)
        if not row:
            raise HTTPException(status_code=404, detail="managed_ne_not_found")
        creds = get_device_credentials(row)
        meta = row_to_out(row).model_dump()
        device = {
            "source": "managed",
            "id": meta["id"],
            "ume_ne_id": None,
            "name": meta["name"],
            "ip_address": meta["ip_address"],
            "ne_type": "",
            "host_name": meta["name"],
            "vendor": meta["vendor"],
            "device_type": meta["device_type"],
            "port": meta["port"],
            "protocol": meta["protocol"],
            "connect_status": meta["connect_status"],
            "hop_enabled": meta["hop_enabled"],
            "hop_vendor": meta["hop_vendor"],
        }
        return creds, device

    inv = db.get(UmeInventoryNE, uid)
    if not inv:
        raise HTTPException(status_code=404, detail="ume_ne_not_found")
    ip = str(inv.ip_address or "").strip()
    if not ip:
        raise HTTPException(status_code=400, detail="ume_ne_ip_missing")

    override = db.get(UmeCliOverride, uid)
    profile: CliConnectProfile | None = None
    if override and override.profile_id:
        profile = db.get(CliConnectProfile, str(override.profile_id))
    if profile is None:
        profile = get_default_profile(db)
    if profile is None:
        raise HTTPException(status_code=503, detail="cli_connect_profile_not_configured")

    username = str(override.username_override or "").strip() if override else ""
    if not username:
        username = str(profile.username or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="cli_username_required")

    if override and str(override.device_type_override or "").strip():
        device_type = str(override.device_type_override).strip()
        vendor = str(override.vendor_override or profile.vendor_default or "ZTE").strip()
    else:
        device_type, vendor = infer_device_type_vendor(str(inv.ne_type or ""), profile)
        if override and str(override.vendor_override or "").strip():
            vendor = str(override.vendor_override).strip()

    creds = profile_to_creds(
        profile,
        ip_address=ip,
        username=username,
        device_type=device_type,
        vendor=vendor,
    )
    name = str(inv.user_label or inv.ne_name or inv.host_name or ip).strip()
    connect_status = str(override.connect_status or "unknown") if override else "unknown"
    device = {
        "source": "ume",
        "id": uid,
        "ume_ne_id": uid,
        "name": name,
        "ip_address": ip,
        "ne_type": str(inv.ne_type or ""),
        "host_name": str(inv.host_name or ""),
        "vendor": vendor,
        "device_type": device_type,
        "port": int(profile.port or 22),
        "protocol": str(profile.protocol or "ssh"),
        "connect_status": connect_status,
        "hop_enabled": bool(profile.hop_enabled),
        "hop_vendor": str(profile.hop_vendor or ""),
        "cli_profile_id": str(profile.id),
        "cli_profile_name": str(profile.name or ""),
    }
    return creds, device


def cli_profile_ready(db: Session) -> bool:
    profile = get_default_profile(db)
    if profile is None:
        return False
    if not str(profile.username or "").strip():
        return False
    if profile.hop_enabled:
        if not str(profile.hop_host or "").strip() or not str(profile.hop_username or "").strip():
            return False
        if not str(profile.hop_password_enc or "").strip():
            return False
    return True
