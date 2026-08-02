"""Managed NE Excel import and UME inventory sync into managed_ne."""
from __future__ import annotations

from io import BytesIO
from typing import Any

import pandas as pd
from fastapi import HTTPException
from sqlalchemy.orm import Session

from .models import ManagedNE, UmeInventoryNE
from .ne_crypto import CredentialCryptoError, encrypt_secret
from .device_types import SUPPORTED_DEVICE_TYPES, SUPPORTED_VENDORS
from .ne_schemas import (
    ImportFailure,
    ImportResult,
    UmeManagedDeleteResult,
    UmeManagedSyncResult,
)
from .ne_service_common import (
    IMPORT_COLUMNS,
    UME_SYNC_SOURCE,
    UME_SYNC_TAG,
    _import_cell_str,
    _infer_managed_ne_type_vendor,
    _merge_tags,
    _normalize_ip,
    _normalize_protocol,
    _normalize_vendor,
    _now,
    _parse_import_bool,
    _require_crypto,
)

# Template lists all columns; CSV/XLS import only requires the core set.
_REQUIRED_IMPORT_COLUMNS = (
    "device_type",
    "ip",
    "username",
    "password",
    "port",
    "protocol",
    "name",
    "vendor",
)

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
        display_name = str(inv.host_name or "").strip() or str(inv.ne_name or "").strip() or ip
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
    from .topology_inventory_lifecycle import detach_fabric_from_managed

    stale = [
        row
        for row in db.query(ManagedNE).filter(ManagedNE.source == UME_SYNC_SOURCE).all()
        if (not str(row.source_ref or "").strip())
        or str(row.source_ref or "").strip() not in inventory_ids
    ]
    if stale:
        detach_fabric_from_managed(db, [str(r.id) for r in stale])
        for row in stale:
            db.delete(row)
    deleted = len(stale)
    db.commit()
    return UmeManagedSyncResult(
        inserted=inserted,
        updated=updated,
        deleted=deleted,
        total_inventory=len(inventory_ids),
    )


def delete_ume_synced_managed_ne(db: Session) -> UmeManagedDeleteResult:
    from .topology_inventory_lifecycle import detach_fabric_from_managed

    rows = db.query(ManagedNE).filter(ManagedNE.source == UME_SYNC_SOURCE).all()
    deleted = len(rows)
    if rows:
        detach_fabric_from_managed(db, [str(r.id) for r in rows])
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
    missing = [c for c in _REQUIRED_IMPORT_COLUMNS if c not in df.columns]
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


