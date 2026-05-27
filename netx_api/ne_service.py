from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any

import pandas as pd
from fastapi import HTTPException
from sqlalchemy.orm import Session

from .device_types import SUPPORTED_DEVICE_TYPES, SUPPORTED_VENDORS
from .models import ManagedNE
from .ne_crypto import CredentialCryptoError, credentials_configured, decrypt_secret, encrypt_secret
from .ne_schemas import ImportFailure, ImportResult, ManagedNeCreate, ManagedNeOut, ManagedNeUpdate

IMPORT_COLUMNS = (
    "device_type",
    "ip",
    "username",
    "password",
    "port",
    "protocol",
    "name",
    "vendor",
)


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
        connect_tested_at=row.connect_tested_at,
        tags=str(row.tags or ""),
        remark=str(row.remark or ""),
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
    if kw:
        stmt = stmt.filter(
            ManagedNE.name.contains(kw)
            | ManagedNE.ip_address.contains(kw)
            | ManagedNE.username.contains(kw)
            | ManagedNE.tags.contains(kw)
        )
    v = str(vendor or "").strip()
    if v:
        stmt = stmt.filter(ManagedNE.vendor == v)
    cs = str(connect_status or "").strip()
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
        password_enc=encrypt_secret(body.password),
        enable_secret_enc="",
        connect_status="unknown",
        tags=str(body.tags or "").strip(),
        remark=str(body.remark or "").strip(),
        created_at=now,
        updated_at=now,
    )
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
    row.updated_at = _now()
    db.commit()
    db.refresh(row)
    return row_to_out(row)


def delete_managed_ne(db: Session, ne_id: str) -> dict[str, bool]:
    row = db.get(ManagedNE, ne_id)
    if not row:
        raise HTTPException(status_code=404, detail="managed_ne_not_found")
    db.delete(row)
    db.commit()
    return {"ok": True}


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
            ip = _normalize_ip(str(row.get("ip", "")))
            if not ip:
                failed.append(ImportFailure(row=row_no, reason="ip_required"))
                continue
            device_type = str(row.get("device_type", "")).strip()
            if device_type not in SUPPORTED_DEVICE_TYPES:
                failed.append(ImportFailure(row=row_no, reason="unsupported_device_type"))
                continue
            username = str(row.get("username", "")).strip()
            password = str(row.get("password", "")).strip()
            if not username or not password:
                failed.append(ImportFailure(row=row_no, reason="username_password_required"))
                continue
            port_raw = row.get("port", 22)
            try:
                port = int(port_raw)
            except (TypeError, ValueError):
                port = 22
            protocol = _normalize_protocol(str(row.get("protocol", "ssh")))
            display_name = str(row.get("name", "") or "").strip() or ip
            vendor_raw = str(row.get("vendor", "") or "Other").strip()
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
            existing.password_enc = encrypt_secret(password)
            existing.updated_at = now
        except CredentialCryptoError as exc:
            failed.append(ImportFailure(row=row_no, reason=str(exc)))
        except Exception as exc:
            failed.append(ImportFailure(row=row_no, reason=str(exc)[:200]))
    db.commit()
    return ImportResult(inserted=inserted, updated=updated, failed=failed)


def get_device_credentials(row: ManagedNE) -> dict[str, Any]:
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
    }
