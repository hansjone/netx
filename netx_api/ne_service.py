from __future__ import annotations

from datetime import datetime
from io import BytesIO
import re
from typing import Any

import pandas as pd
from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .device_types import SUPPORTED_DEVICE_TYPES, SUPPORTED_VENDORS
from .models import ManagedNE, UmeInventoryNE
from .ne_crypto import CredentialCryptoError, credentials_configured, decrypt_secret, encrypt_secret
from .ne_schemas import (
    BatchAccountConfig,
    HopProxyConfig,
    ImportFailure,
    ImportResult,
    ManagedNeCreate,
    ManagedNeOut,
    ManagedNeUpdate,
    UmeManagedDeleteResult,
    UmeManagedSyncResult,
)
from .ne_session_factory import default_bastion_username_template, default_hop_command_template

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
_BUILTIN_NE_TYPE_RULES: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"ZXR|ZXCTN|M6000|\bBN\b", re.I), "zte_zxros", "ZTE"),
    (re.compile(r"NE40|CE\b|ATN|MA5800|OptiX", re.I), "huawei", "Huawei"),
    (re.compile(r"ASR|NCS|IOS.?XR|XR\b", re.I), "cisco_xr", "Cisco"),
    (re.compile(r"Catalyst|Nexus|C9[0-9]{3}|ISR", re.I), "cisco_ios", "Cisco"),
]


def _now() -> datetime:
    return datetime.utcnow()


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


def list_managed_ne(
    db: Session,
    *,
    keyword: str | None = None,
    vendor: str | None = None,
    connect_status: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    stmt = db.query(ManagedNE)
    kw = str(keyword or "").strip()
    v = str(vendor or "").strip()
    cs = str(connect_status or "").strip()
    if not (kw or v or cs):
        raise HTTPException(status_code=400, detail="managed_ne_filter_required")
    if kw and len(kw) < 2:
        raise HTTPException(status_code=400, detail="managed_ne_keyword_too_short")
    if kw:
        like = f"%{kw}%"
        stmt = stmt.filter(
            or_(
                ManagedNE.name.ilike(like),
                ManagedNE.ip_address.ilike(like),
                ManagedNE.username.ilike(like),
                ManagedNE.tags.ilike(like),
                ManagedNE.vendor.ilike(like),
                ManagedNE.device_type.ilike(like),
            )
        )
    if v:
        stmt = stmt.filter(ManagedNE.vendor == v)
    if cs:
        stmt = stmt.filter(ManagedNE.connect_status == cs)
    total = int(stmt.count())
    rows = (
        stmt.order_by(ManagedNE.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [row_to_out(x).model_dump() for x in rows],
    }


def get_managed_ne(db: Session, ne_id: str) -> ManagedNeOut:
    row = db.get(ManagedNE, ne_id)
    if not row:
        raise HTTPException(status_code=404, detail="managed_ne_not_found")
    return row_to_out(row)


def create_managed_ne(db: Session, body: ManagedNeCreate) -> ManagedNeOut:
    _require_crypto()
    _validate_hop_on_create(body)
    ip = _normalize_ip(body.ip_address)
    if not ip:
        raise HTTPException(status_code=400, detail="ip_address_required")
    if body.device_type not in SUPPORTED_DEVICE_TYPES:
        raise HTTPException(status_code=400, detail="unsupported_device_type")
    existing = db.query(ManagedNE).filter(ManagedNE.ip_address == ip).first()
    if existing:
        raise HTTPException(status_code=400, detail="ip_address_exists")
    now = _now()
    row = ManagedNE(
        name=str(body.name or "").strip() or ip,
        vendor=body.vendor,
        device_type=body.device_type,
        ip_address=ip,
        port=int(body.port or 22),
        protocol=_normalize_protocol(body.protocol),
        username=str(body.username or "").strip(),
        password_enc=encrypt_secret(body.password) if str(body.password or "").strip() else "",
        enable_secret_enc="",
        connect_status="unknown",
        tags=str(body.tags or "").strip(),
        remark=str(body.remark or "").strip(),
        source="",
        source_ref="",
        created_at=now,
        updated_at=now,
    )
    _apply_hop_create(row, body)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row_to_out(row)


def update_managed_ne(db: Session, ne_id: str, body: ManagedNeUpdate) -> ManagedNeOut:
    row = db.get(ManagedNE, ne_id)
    if not row:
        raise HTTPException(status_code=404, detail="managed_ne_not_found")
    data = body.model_dump(exclude_unset=True)
    if "ip_address" in data:
        ip = _normalize_ip(data["ip_address"])
        if not ip:
            raise HTTPException(status_code=400, detail="ip_address_required")
        other = db.query(ManagedNE).filter(ManagedNE.ip_address == ip, ManagedNE.id != ne_id).first()
        if other:
            raise HTTPException(status_code=400, detail="ip_address_exists")
        row.ip_address = ip
    if "device_type" in data:
        if data["device_type"] not in SUPPORTED_DEVICE_TYPES:
            raise HTTPException(status_code=400, detail="unsupported_device_type")
        row.device_type = data["device_type"]
    if "vendor" in data:
        v = str(data["vendor"] or "").strip()
        row.vendor = v if v in SUPPORTED_VENDORS else "Other"
    if "name" in data:
        row.name = str(data["name"] or "").strip()
    if "port" in data and data["port"] is not None:
        row.port = int(data["port"])
    if "protocol" in data and data["protocol"] is not None:
        row.protocol = _normalize_protocol(data["protocol"])
    if "username" in data and data["username"] is not None:
        row.username = str(data["username"]).strip()
    if "tags" in data and data["tags"] is not None:
        row.tags = str(data["tags"]).strip()
    if "remark" in data and data["remark"] is not None:
        row.remark = str(data["remark"]).strip()
    if "password" in data and data["password"]:
        _require_crypto()
        row.password_enc = encrypt_secret(str(data["password"]))
    hop_keys = (
        "hop_enabled",
        "hop_vendor",
        "hop_host",
        "hop_port",
        "hop_protocol",
        "hop_username",
        "hop_password",
        "hop_command_template",
        "hop_vrf",
        "hop_target_auth_mode",
    )
    if any(k in data for k in hop_keys):
        _apply_hop_update(row, data)
    row.updated_at = _now()
    db.commit()
    db.refresh(row)
    return row_to_out(row)


def batch_apply_hop_proxy(db: Session, ids: list[str], hop: HopProxyConfig) -> dict[str, Any]:
    """Apply the same jump-host (proxy) settings to multiple managed NEs."""
    hop_host = str(hop.hop_host or "").strip()
    hop_user = str(hop.hop_username or "").strip()
    hop_pass = str(hop.hop_password or "").strip()
    if hop_pass:
        _require_crypto()
    if not hop_host:
        raise HTTPException(status_code=400, detail="hop_host_required")
    if not hop_user:
        raise HTTPException(status_code=400, detail="hop_username_required")
    hop_auth_mode = _normalize_hop_target_auth_mode(hop.hop_target_auth_mode)
    if not hop_pass and hop_auth_mode != "bastion_managed":
        raise HTTPException(status_code=400, detail="hop_password_required")

    hop_vendor = _normalize_hop_vendor(hop.hop_vendor)
    template = str(hop.hop_command_template or "").strip()
    if hop_vendor == "bastion" and not template:
        template = default_bastion_username_template()
    elif hop_vendor not in ("linux", "bastion") and not template:
        template = default_hop_command_template(hop_vendor, hop.hop_protocol, hop.hop_vrf)

    ne_ids = [str(x).strip() for x in ids if str(x).strip()]
    if not ne_ids:
        raise HTTPException(status_code=400, detail="ids_required")

    rows = db.query(ManagedNE).filter(ManagedNE.id.in_(ne_ids)).all()
    found_ids = {str(r.id) for r in rows}
    missing = [x for x in ne_ids if x not in found_ids]
    if missing:
        raise HTTPException(status_code=404, detail=f"managed_ne_not_found: {','.join(missing[:5])}")

    now = _now()
    for row in rows:
        row.hop_enabled = True
        row.hop_vendor = hop_vendor
        row.hop_host = hop_host
        row.hop_port = int(hop.hop_port or 22)
        row.hop_protocol = _normalize_protocol(hop.hop_protocol)
        row.hop_username = hop_user
        if hop_pass:
            row.hop_password_enc = encrypt_secret(hop_pass)
        row.hop_command_template = template
        row.hop_vrf = str(hop.hop_vrf or "").strip()
        row.hop_target_auth_mode = hop_auth_mode
        row.updated_at = now
    db.commit()
    return {"ok": True, "updated": len(rows)}


def batch_apply_account(db: Session, ids: list[str], account: BatchAccountConfig) -> dict[str, Any]:
    user = str(account.username or "").strip()
    pwd = str(account.password or "")
    if not user and not pwd:
        raise HTTPException(status_code=400, detail="username_or_password_required")
    if pwd:
        _require_crypto()
        pwd_enc = encrypt_secret(pwd)
    else:
        pwd_enc = ""
    ne_ids = [str(x).strip() for x in ids if str(x).strip()]
    if not ne_ids:
        raise HTTPException(status_code=400, detail="ids_required")
    rows = db.query(ManagedNE).filter(ManagedNE.id.in_(ne_ids)).all()
    found_ids = {str(r.id) for r in rows}
    missing = [x for x in ne_ids if x not in found_ids]
    if missing:
        raise HTTPException(status_code=404, detail=f"managed_ne_not_found: {','.join(missing[:5])}")
    now = _now()
    for row in rows:
        if user:
            row.username = user
        if pwd:
            row.password_enc = pwd_enc
        row.updated_at = now
    db.commit()
    return {"ok": True, "updated": len(rows)}


def delete_managed_ne(db: Session, ne_id: str) -> dict[str, bool]:
    row = db.get(ManagedNE, ne_id)
    if not row:
        raise HTTPException(status_code=404, detail="managed_ne_not_found")
    db.delete(row)
    db.commit()
    return {"ok": True}


def get_managed_ne_stats(db: Session) -> dict[str, Any]:
    """Return total counts by connect_status, and tag statistics."""
    from sqlalchemy import func

    rows = db.query(ManagedNE.connect_status, func.count(ManagedNE.id)).group_by(ManagedNE.connect_status).all()
    by_status: dict[str, int] = {}
    total = 0
    for status, cnt in rows:
        by_status[str(status or "unknown")] = int(cnt)
        total += int(cnt)

    # Tag statistics & per-tag connect_status aggregation (space-separated)
    def _bump(bucket: dict[str, int], status: str) -> None:
        s = str(status or "unknown")
        bucket[s] = int(bucket.get(s, 0)) + 1

    tag_counts: dict[str, int] = {}
    no_tag_count = 0
    per_tag_by_status: dict[str, dict[str, int]] = {}
    per_tag_total: dict[str, int] = {}

    for connect_status, tags_str in db.query(ManagedNE.connect_status, ManagedNE.tags).all():
        status = str(connect_status or "unknown")
        tags_val = str(tags_str or "").strip()
        if not tags_val:
            no_tag_count += 1
            per_tag_total["__no_tag__"] = int(per_tag_total.get("__no_tag__", 0)) + 1
            per_tag_by_status.setdefault("__no_tag__", {})
            _bump(per_tag_by_status["__no_tag__"], status)
            continue
        for t in tags_val.split():
            if not t:
                continue
            tag_counts[t] = int(tag_counts.get(t, 0)) + 1
            per_tag_total[t] = int(per_tag_total.get(t, 0)) + 1
            per_tag_by_status.setdefault(t, {})
            _bump(per_tag_by_status[t], status)

    return {
        "total": total,
        "by_status": by_status,
        "no_tag_count": int(no_tag_count),
        "tag_counts": {k: int(tag_counts[k]) for k in sorted(tag_counts.keys())},
        "tags": sorted(tag_counts.keys()),
        "per_tag": {
            k: {"total": int(per_tag_total.get(k, 0)), "by_status": per_tag_by_status.get(k, {})}
            for k in sorted(per_tag_total.keys(), key=lambda x: ("0" if x == "__no_tag__" else "1") + x)
        },
    }


def get_ids_by_tag(db: Session, tag: str | None) -> list[str]:
    """
    Return NE ids by tag.

    - tag is None: all ids
    - tag == "__no_tag__": ids where tags is empty/blank
    - otherwise: ids where tag exists in space-separated tags list
    """
    result: list[str] = []
    norm = str(tag).strip() if tag is not None else None
    for ne_id, tags_str in db.query(ManagedNE.id, ManagedNE.tags).all():
        tags_val = str(tags_str or "").strip()
        if norm is None:
            result.append(str(ne_id))
        elif norm == "__no_tag__":
            if not tags_val:
                result.append(str(ne_id))
        else:
            if norm in tags_val.split():
                result.append(str(ne_id))
    return result


def batch_delete_managed_ne(db: Session, ids: list[str]) -> dict[str, Any]:
    ne_ids = [str(x).strip() for x in ids if str(x).strip()]
    if not ne_ids:
        raise HTTPException(status_code=400, detail="ids_required")
    rows = db.query(ManagedNE).filter(ManagedNE.id.in_(ne_ids)).all()
    found_ids = {str(r.id) for r in rows}
    missing = [x for x in ne_ids if x not in found_ids]
    if missing:
        raise HTTPException(status_code=404, detail=f"managed_ne_not_found: {','.join(missing[:5])}")
    for row in rows:
        db.delete(row)
    db.commit()
    return {"ok": True, "deleted": len(rows)}


def sync_ume_inventory_to_managed_ne(db: Session) -> UmeManagedSyncResult:
    rows = db.query(UmeInventoryNE).all()
    by_source_ref = {
        str(x.source_ref or ""): x
        for x in db.query(ManagedNE).filter(ManagedNE.source == UME_SYNC_SOURCE).all()
    }
    inventory_ids = {str(x.ne_id or "").strip() for x in rows if str(x.ne_id or "").strip()}
    inserted = 0
    updated = 0
    now = _now()
    for inv in rows:
        source_ref = str(inv.ne_id or "").strip()
        ip = _normalize_ip(str(inv.ip_address or ""))
        if not source_ref or not ip:
            continue
        existing = by_source_ref.get(source_ref)
        if existing is None:
            existing = db.query(ManagedNE).filter(ManagedNE.ip_address == ip).first()
        device_type, vendor = _infer_managed_ne_type_vendor(str(inv.ne_type or ""), str(inv.vendor or ""))
        display_name = str(inv.ne_name or "").strip() or ip
        existing_tags = str(existing.tags or "").strip() if existing is not None else ""
        if existing is None:
            existing = ManagedNE(
                ip_address=ip,
                created_at=now,
                source=UME_SYNC_SOURCE,
                source_ref=source_ref,
            )
            db.add(existing)
            inserted += 1
        else:
            updated += 1
        existing.name = display_name
        existing.vendor = vendor
        existing.device_type = device_type
        existing.port = int(existing.port or 22 or 22)
        existing.protocol = _normalize_protocol(str(existing.protocol or "ssh"))
        existing.tags = _merge_tags(existing_tags, UME_SYNC_TAG)
        existing.source = UME_SYNC_SOURCE
        existing.source_ref = source_ref
        existing.updated_at = now
    deleted = 0
    for row in db.query(ManagedNE).filter(ManagedNE.source == UME_SYNC_SOURCE).all():
        ref = str(row.source_ref or "").strip()
        if not ref or ref not in inventory_ids:
            db.delete(row)
            deleted += 1
    db.commit()
    return UmeManagedSyncResult(
        inserted=inserted,
        updated=updated,
        deleted=deleted,
        total_inventory=len(inventory_ids),
    )


def delete_ume_synced_managed_ne(db: Session) -> UmeManagedDeleteResult:
    rows = db.query(ManagedNE).filter(ManagedNE.source == UME_SYNC_SOURCE).all()
    deleted = len(rows)
    for row in rows:
        db.delete(row)
    db.commit()
    return UmeManagedDeleteResult(deleted=deleted)


def build_managed_ne_import_template(fmt: str = "xlsx") -> tuple[str, bytes, str]:
    """Return (filename, content, media_type) for bulk-import template."""
    rows = [
        {
            "device_type": "cisco_ios",
            "ip": "192.168.0.1",
            "username": "admin",
            "password": "your_password",
            "port": 22,
            "protocol": "ssh",
            "name": "Core-SW1",
            "vendor": "Cisco",
            "tags": "core",
            "remark": "",
        },
        {
            "device_type": "zte_zxros",
            "ip": "2.2.2.2",
            "username": "target-user",
            "password": "",
            "port": 22,
            "protocol": "ssh",
            "name": "PE-01",
            "vendor": "ZTE",
            "tags": "edge bastion",
            "remark": "no direct password, use batch proxy",
        },
    ]
    df = pd.DataFrame(rows, columns=list(IMPORT_COLUMNS))
    buf = BytesIO()
    kind = str(fmt or "xlsx").strip().lower()
    if kind == "csv":
        df.to_csv(buf, index=False, encoding="utf-8-sig")
        return (
            "managed_ne_import_template.csv",
            buf.getvalue(),
            "text/csv; charset=utf-8",
        )
    device_types_df = pd.DataFrame({"device_type": list(SUPPORTED_DEVICE_TYPES)})
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="import", index=False)
        device_types_df.to_excel(writer, sheet_name="device_type", index=False)
    return (
        "managed_ne_import_template.xlsx",
        buf.getvalue(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def import_managed_ne(db: Session, content: bytes, filename: str) -> ImportResult:
    _require_crypto()
    name = str(filename or "").lower()
    try:
        if name.endswith(".csv"):
            df = pd.read_csv(BytesIO(content))
        else:
            df = pd.read_excel(BytesIO(content))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"import_parse_failed: {exc}") from exc
    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = [c for c in IMPORT_COLUMNS if c not in df.columns]
    if missing:
        raise HTTPException(status_code=400, detail=f"import_missing_columns: {','.join(missing)}")
    inserted = 0
    updated = 0
    failed: list[ImportFailure] = []
    for idx, row in df.iterrows():
        row_no = int(idx) + 2
        try:
            ip = _normalize_ip(_import_cell_str(row.get("ip", "")))
            if not ip:
                failed.append(ImportFailure(row=row_no, reason="ip_required"))
                continue
            device_type = _import_cell_str(row.get("device_type", ""))
            if device_type not in SUPPORTED_DEVICE_TYPES:
                failed.append(ImportFailure(row=row_no, reason="unsupported_device_type"))
                continue
            username = _import_cell_str(row.get("username", ""))
            password = _import_cell_str(row.get("password", ""))
            if not username:
                failed.append(ImportFailure(row=row_no, reason="username_required"))
                continue
            port_raw = row.get("port", 22)
            try:
                port = int(port_raw)
            except (TypeError, ValueError):
                port = 22
            protocol = _normalize_protocol(str(row.get("protocol", "ssh")))
            display_name = _import_cell_str(row.get("name", "")) or ip
            vendor_raw = _import_cell_str(row.get("vendor", "")) or "Other"
            vendor = "Other"
            for v in SUPPORTED_VENDORS:
                if v.lower() == vendor_raw.lower():
                    vendor = v
                    break
            existing = db.query(ManagedNE).filter(ManagedNE.ip_address == ip).first()
            now = _now()
            if existing is None:
                existing = ManagedNE(ip_address=ip, created_at=now)
                db.add(existing)
                inserted += 1
            else:
                updated += 1
            existing.name = display_name
            existing.vendor = vendor
            existing.device_type = device_type
            existing.port = port
            existing.protocol = protocol
            existing.username = username
            existing.password_enc = encrypt_secret(password) if password else ""
            tags_val = _import_cell_str(row.get("tags", ""))
            remark_val = _import_cell_str(row.get("remark", ""))
            if tags_val:
                existing.tags = tags_val
            if remark_val:
                existing.remark = remark_val
            existing.updated_at = now
        except CredentialCryptoError as exc:
            failed.append(ImportFailure(row=row_no, reason=str(exc)))
        except Exception as exc:
            failed.append(ImportFailure(row=row_no, reason=str(exc)[:200]))
    db.commit()
    return ImportResult(inserted=inserted, updated=updated, failed=failed)


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
