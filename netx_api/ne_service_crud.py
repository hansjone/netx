"""Managed NE CRUD, batch hop/account, and stats."""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .device_types import SUPPORTED_DEVICE_TYPES
from .models import ManagedNE
from .ne_crypto import encrypt_secret
from .ne_schemas import (
    BatchAccountConfig,
    HopProxyConfig,
    ManagedNeCreate,
    ManagedNeOut,
    ManagedNeUpdate,
)
from .ne_service_common import (
    _apply_hop_create,
    _apply_hop_update,
    _normalize_ip,
    _normalize_protocol,
    _normalize_vendor,
    _now,
    _require_crypto,
    _validate_hop_on_create,
    row_to_out,
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
    from .topology_inventory_lifecycle import detach_fabric_from_managed

    row = db.get(ManagedNE, ne_id)
    if not row:
        raise HTTPException(status_code=404, detail="managed_ne_not_found")
    detach_fabric_from_managed(db, [str(row.id)])
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
    from .topology_inventory_lifecycle import detach_fabric_from_managed

    ne_ids = [str(x).strip() for x in ids if str(x).strip()]
    if not ne_ids:
        raise HTTPException(status_code=400, detail="ids_required")
    rows = db.query(ManagedNE).filter(ManagedNE.id.in_(ne_ids)).all()
    found_ids = {str(r.id) for r in rows}
    missing = [x for x in ne_ids if x not in found_ids]
    if missing:
        raise HTTPException(status_code=404, detail=f"managed_ne_not_found: {','.join(missing[:5])}")
    detach_fabric_from_managed(db, [str(r.id) for r in rows])
    for row in rows:
        db.delete(row)
    db.commit()
    return {"ok": True, "deleted": len(rows)}


