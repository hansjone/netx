from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .cli_schemas import (
    CliConnectProfileCreate,
    CliConnectProfileOut,
    CliConnectProfileUpdate,
    CliTargetOut,
    UmeCliOverrideOut,
    UmeCliOverrideUpdate,
)
from .cli_resolve import cli_profile_ready, get_default_profile
from .device_types import SUPPORTED_DEVICE_TYPES
from .models import CliConnectProfile, ManagedNE, UmeCliOverride, UmeInventoryNE
from .ne_crypto import credentials_configured, encrypt_secret
from .ne_service import (
    _normalize_hop_target_auth_mode,
    _normalize_hop_vendor,
    _normalize_protocol,
    _require_crypto,
)


def _now() -> datetime:
    return datetime.utcnow()


def _validate_profile_hop(body: CliConnectProfileCreate | CliConnectProfileUpdate, *, hop_enabled: bool) -> None:
    if not hop_enabled:
        return
    host = str(getattr(body, "hop_host", None) or "").strip()
    user = str(getattr(body, "hop_username", None) or "").strip()
    if not host:
        raise HTTPException(status_code=400, detail="hop_host_required")
    if not user:
        raise HTTPException(status_code=400, detail="hop_username_required")
    pwd = getattr(body, "hop_password", None)
    if isinstance(body, CliConnectProfileCreate) and not str(pwd or "").strip():
        raise HTTPException(status_code=400, detail="hop_password_required")


def _profile_out(row: CliConnectProfile) -> CliConnectProfileOut:
    return CliConnectProfileOut(
        id=str(row.id),
        name=str(row.name or ""),
        is_default=bool(row.is_default),
        username=str(row.username or ""),
        port=int(row.port or 22),
        protocol=str(row.protocol or "ssh"),
        device_type_default=str(row.device_type_default or ""),
        vendor_default=str(row.vendor_default or ""),
        ne_type_rules=str(row.ne_type_rules or ""),
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


def _override_out(row: UmeCliOverride) -> UmeCliOverrideOut:
    return UmeCliOverrideOut(
        ume_ne_id=str(row.ume_ne_id),
        profile_id=str(row.profile_id) if row.profile_id else None,
        username_override=str(row.username_override or ""),
        device_type_override=str(row.device_type_override or ""),
        vendor_override=str(row.vendor_override or ""),
        connect_status=str(row.connect_status or "unknown"),
        connect_message=str(row.connect_message or ""),
        connect_detail=str(row.connect_detail or "")[:8000],
        connect_tested_at=row.connect_tested_at,
        updated_at=row.updated_at,
    )


def list_cli_profiles(db: Session) -> list[CliConnectProfileOut]:
    rows = db.query(CliConnectProfile).order_by(CliConnectProfile.is_default.desc(), CliConnectProfile.name.asc()).all()
    return [_profile_out(r) for r in rows]


def get_cli_profile(db: Session, profile_id: str) -> CliConnectProfileOut:
    row = db.get(CliConnectProfile, str(profile_id or "").strip())
    if not row:
        raise HTTPException(status_code=404, detail="cli_profile_not_found")
    return _profile_out(row)


def create_cli_profile(db: Session, body: CliConnectProfileCreate) -> CliConnectProfileOut:
    _require_crypto()
    if body.device_type_default not in SUPPORTED_DEVICE_TYPES:
        raise HTTPException(status_code=400, detail="unsupported_device_type")
    _validate_profile_hop(body, hop_enabled=bool(body.hop_enabled))
    if not str(body.username or "").strip():
        raise HTTPException(status_code=400, detail="username_required")
    row = CliConnectProfile(
        name=str(body.name or "").strip() or "default",
        username=str(body.username).strip(),
        password_enc=encrypt_secret(body.password) if str(body.password or "").strip() else "",
        port=int(body.port or 22),
        protocol=_normalize_protocol(body.protocol),
        device_type_default=str(body.device_type_default),
        vendor_default=str(body.vendor_default),
        ne_type_rules=str(body.ne_type_rules or ""),
        hop_enabled=bool(body.hop_enabled),
        hop_vendor=_normalize_hop_vendor(body.hop_vendor),
        hop_host=str(body.hop_host or "").strip(),
        hop_port=int(body.hop_port or 22),
        hop_protocol=_normalize_protocol(body.hop_protocol),
        hop_username=str(body.hop_username or "").strip(),
        hop_password_enc=encrypt_secret(body.hop_password) if body.hop_enabled and body.hop_password else "",
        hop_command_template=str(body.hop_command_template or "").strip(),
        hop_vrf=str(body.hop_vrf or "").strip(),
        hop_target_auth_mode=_normalize_hop_target_auth_mode(body.hop_target_auth_mode),
    )
    if body.is_default or db.query(CliConnectProfile).count() == 0:
        db.query(CliConnectProfile).update({CliConnectProfile.is_default: False})
        row.is_default = True
    db.add(row)
    db.commit()
    db.refresh(row)
    return _profile_out(row)


def update_cli_profile(db: Session, profile_id: str, body: CliConnectProfileUpdate) -> CliConnectProfileOut:
    row = db.get(CliConnectProfile, str(profile_id or "").strip())
    if not row:
        raise HTTPException(status_code=404, detail="cli_profile_not_found")
    data = body.model_dump(exclude_unset=True)
    hop_enabled = bool(data["hop_enabled"]) if "hop_enabled" in data else bool(row.hop_enabled)
    if hop_enabled:
        _validate_profile_hop(body, hop_enabled=True)
    if "name" in data and data["name"] is not None:
        row.name = str(data["name"]).strip()
    if "username" in data and data["username"] is not None:
        row.username = str(data["username"]).strip()
    if "password" in data and data["password"]:
        _require_crypto()
        row.password_enc = encrypt_secret(str(data["password"]))
    if "port" in data and data["port"] is not None:
        row.port = int(data["port"])
    if "protocol" in data and data["protocol"] is not None:
        row.protocol = _normalize_protocol(data["protocol"])
    if "device_type_default" in data and data["device_type_default"] is not None:
        if data["device_type_default"] not in SUPPORTED_DEVICE_TYPES:
            raise HTTPException(status_code=400, detail="unsupported_device_type")
        row.device_type_default = str(data["device_type_default"])
    if "vendor_default" in data and data["vendor_default"] is not None:
        row.vendor_default = str(data["vendor_default"])
    if "ne_type_rules" in data and data["ne_type_rules"] is not None:
        row.ne_type_rules = str(data["ne_type_rules"])
    hop_keys = (
        "hop_enabled",
        "hop_vendor",
        "hop_host",
        "hop_port",
        "hop_protocol",
        "hop_username",
        "hop_command_template",
        "hop_vrf",
        "hop_target_auth_mode",
    )
    for key in hop_keys:
        if key in data and data[key] is not None:
            setattr(row, key, data[key])
    if "hop_vendor" in data and data["hop_vendor"] is not None:
        row.hop_vendor = _normalize_hop_vendor(data["hop_vendor"])
    if "hop_protocol" in data and data["hop_protocol"] is not None:
        row.hop_protocol = _normalize_protocol(data["hop_protocol"])
    if "hop_target_auth_mode" in data and data["hop_target_auth_mode"] is not None:
        row.hop_target_auth_mode = _normalize_hop_target_auth_mode(data["hop_target_auth_mode"])
    if "hop_password" in data and data["hop_password"]:
        _require_crypto()
        row.hop_password_enc = encrypt_secret(str(data["hop_password"]))
    if body.is_default is True:
        db.query(CliConnectProfile).filter(CliConnectProfile.id != row.id).update({CliConnectProfile.is_default: False})
        row.is_default = True
    row.updated_at = _now()
    db.commit()
    db.refresh(row)
    return _profile_out(row)


def set_default_cli_profile(db: Session, profile_id: str) -> CliConnectProfileOut:
    row = db.get(CliConnectProfile, str(profile_id or "").strip())
    if not row:
        raise HTTPException(status_code=404, detail="cli_profile_not_found")
    db.query(CliConnectProfile).update({CliConnectProfile.is_default: False})
    row.is_default = True
    row.updated_at = _now()
    db.commit()
    db.refresh(row)
    return _profile_out(row)


def delete_cli_profile(db: Session, profile_id: str) -> dict[str, Any]:
    row = db.get(CliConnectProfile, str(profile_id or "").strip())
    if not row:
        raise HTTPException(status_code=404, detail="cli_profile_not_found")
    was_default = bool(row.is_default)
    db.delete(row)
    db.commit()
    if was_default:
        first = db.query(CliConnectProfile).order_by(CliConnectProfile.created_at.asc()).first()
        if first is not None:
            first.is_default = True
            first.updated_at = _now()
            db.commit()
    return {"ok": True}


def get_ume_cli_override(db: Session, ume_ne_id: str) -> UmeCliOverrideOut | None:
    row = db.get(UmeCliOverride, str(ume_ne_id or "").strip())
    return _override_out(row) if row else None


def upsert_ume_cli_override(db: Session, ume_ne_id: str, body: UmeCliOverrideUpdate) -> UmeCliOverrideOut:
    uid = str(ume_ne_id or "").strip()
    if not db.get(UmeInventoryNE, uid):
        raise HTTPException(status_code=404, detail="ume_ne_not_found")
    row = db.get(UmeCliOverride, uid)
    if row is None:
        row = UmeCliOverride(ume_ne_id=uid)
        db.add(row)
    data = body.model_dump(exclude_unset=True)
    if "profile_id" in data:
        pid = str(data["profile_id"] or "").strip() if data["profile_id"] else ""
        if pid and not db.get(CliConnectProfile, pid):
            raise HTTPException(status_code=404, detail="cli_profile_not_found")
        row.profile_id = pid or None
    if "username_override" in data and data["username_override"] is not None:
        row.username_override = str(data["username_override"]).strip()
    if "device_type_override" in data and data["device_type_override"] is not None:
        row.device_type_override = str(data["device_type_override"]).strip()
    if "vendor_override" in data and data["vendor_override"] is not None:
        row.vendor_override = str(data["vendor_override"]).strip()
    row.updated_at = _now()
    db.commit()
    db.refresh(row)
    return _override_out(row)


def list_cli_targets(
    db: Session,
    *,
    source: str = "all",
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    src = str(source or "all").strip().lower()
    if src not in ("managed", "ume", "all"):
        raise HTTPException(status_code=400, detail="invalid_source")
    ready = cli_profile_ready(db)
    page = max(1, int(page or 1))
    page_size = max(1, min(500, int(page_size or 50)))
    offset = (page - 1) * page_size
    kw = str(keyword or "").strip()

    def _managed_item(row: Any) -> dict[str, Any]:
        return CliTargetOut(
            source="managed",
            id=str(row.id),
            ume_ne_id=None,
            name=str(row.name or row.ip_address),
            ip_address=str(row.ip_address),
            vendor=str(row.vendor),
            device_type=str(row.device_type),
            connect_status=str(row.connect_status),
            cli_profile_ready=True,
        ).model_dump()

    def _ume_item(inv: UmeInventoryNE, ov: UmeCliOverride | None) -> dict[str, Any]:
        return CliTargetOut(
            source="ume",
            id=str(inv.ne_id),
            ume_ne_id=str(inv.ne_id),
            name=str(inv.user_label or inv.ne_name or inv.host_name or inv.ip_address or inv.ne_id),
            ip_address=str(inv.ip_address or ""),
            ne_type=str(inv.ne_type or ""),
            vendor=str(inv.vendor or ""),
            connect_status=str(ov.connect_status if ov else "unknown"),
            cli_profile_ready=ready,
        ).model_dump()

    def _managed_query():
        stmt = db.query(ManagedNE)
        if kw:
            like = f"%{kw}%"
            stmt = stmt.filter(
                ManagedNE.name.ilike(like)
                | ManagedNE.ip_address.ilike(like)
                | ManagedNE.username.ilike(like)
                | ManagedNE.tags.ilike(like)
                | ManagedNE.vendor.ilike(like)
                | ManagedNE.device_type.ilike(like)
            )
        return stmt.order_by(ManagedNE.updated_at.desc())

    def _ume_query():
        stmt = db.query(UmeInventoryNE, UmeCliOverride).outerjoin(
            UmeCliOverride, UmeInventoryNE.ne_id == UmeCliOverride.ume_ne_id
        )
        if kw:
            like = f"%{kw}%"
            stmt = stmt.filter(
                UmeInventoryNE.ne_id.ilike(like)
                | UmeInventoryNE.ne_name.ilike(like)
                | UmeInventoryNE.user_label.ilike(like)
                | UmeInventoryNE.ip_address.ilike(like)
                | UmeInventoryNE.host_name.ilike(like)
            )
        return stmt.order_by(UmeInventoryNE.ne_id.asc())

    if src == "managed":
        mq = _managed_query()
        total = int(mq.count())
        rows = mq.offset(offset).limit(page_size).all()
        items = [_managed_item(x) for x in rows]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    if src == "ume":
        uq = _ume_query()
        total = int(uq.count())
        rows = uq.offset(offset).limit(page_size).all()
        items = [_ume_item(inv, ov) for inv, ov in rows]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    # source=all: managed first, then UME, with correct cross-list pagination
    mq = _managed_query()
    uq = _ume_query()
    m_total = int(mq.count())
    u_total = int(uq.count())
    total = m_total + u_total
    items: list[dict[str, Any]] = []
    if offset < m_total:
        take = min(page_size, m_total - offset)
        for row in mq.offset(offset).limit(take).all():
            items.append(_managed_item(row))
        need = page_size - len(items)
        if need > 0 and u_total > 0:
            for inv, ov in uq.offset(0).limit(need).all():
                items.append(_ume_item(inv, ov))
    else:
        u_off = offset - m_total
        for inv, ov in uq.offset(u_off).limit(page_size).all():
            items.append(_ume_item(inv, ov))

    return {"items": items, "total": total, "page": page, "page_size": page_size}


def cli_meta(db: Session) -> dict[str, Any]:
    return {
        "credentials_configured": credentials_configured(),
        "default_profile_configured": get_default_profile(db) is not None,
        "cli_profile_ready": cli_profile_ready(db),
    }
