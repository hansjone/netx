"""Config sync snapshot list/detail/export/history."""
from __future__ import annotations

import io
import re
import zipfile
from typing import Any

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .config_sync_codec import decompress_text
from .config_sync_schemas import (
    NeConfigHistoryOut,
    NeConfigSnapshotDetailOut,
    NeConfigSnapshotMetaOut,
)
from .models import NeConfigHistory, NeConfigSnapshot

def _snap_meta(row: NeConfigSnapshot) -> NeConfigSnapshotMetaOut:
    cmds = row.commands_json if isinstance(row.commands_json, list) else []
    return NeConfigSnapshotMetaOut(
        source=str(row.source),
        target_id=str(row.target_id),
        vendor=str(row.vendor or ""),
        device_type=str(row.device_type or ""),
        ne_name=str(row.ne_name or ""),
        ne_ip=str(row.ne_ip or ""),
        config_sha256=str(row.config_sha256 or ""),
        config_alt_sha256=str(row.config_alt_sha256 or ""),
        plain_size=int(row.plain_size or 0),
        plain_alt_size=int(row.plain_alt_size or 0),
        zlib_size=int(row.zlib_size or 0),
        zlib_alt_size=int(row.zlib_alt_size or 0),
        has_alt=bool(row.config_alt_zlib),
        commands=[str(c) for c in cmds],
        collected_at=row.collected_at,
        last_cycle_id=str(row.last_cycle_id or ""),
    )


def list_snapshots(
    db: Session,
    *,
    page: int,
    page_size: int,
    keyword: str = "",
    source: str = "",
    vendor: str = "",
) -> dict[str, Any]:
    q = db.query(NeConfigSnapshot)
    src = str(source or "").strip().lower()
    if src in ("managed", "ume"):
        q = q.filter(NeConfigSnapshot.source == src)
    vend = str(vendor or "").strip()
    if vend:
        q = q.filter(NeConfigSnapshot.vendor.ilike(f"%{vend}%"))
    kw = str(keyword or "").strip()
    if kw:
        like = f"%{kw}%"
        q = q.filter(
            or_(
                NeConfigSnapshot.ne_name.ilike(like),
                NeConfigSnapshot.ne_ip.ilike(like),
                NeConfigSnapshot.target_id.ilike(like),
            )
        )
    total = int(q.count())
    rows = q.order_by(NeConfigSnapshot.collected_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "page": page, "page_size": page_size, "items": [_snap_meta(r) for r in rows]}


def get_snapshot_detail(
    db: Session,
    source: str,
    target_id: str,
    *,
    field: str = "both",
) -> NeConfigSnapshotDetailOut:
    src = str(source or "").strip().lower()
    tid = str(target_id or "").strip()
    row = db.get(NeConfigSnapshot, {"source": src, "target_id": tid})
    if not row:
        raise HTTPException(status_code=404, detail="snapshot_not_found")
    meta = _snap_meta(row)
    primary = ""
    alt = ""
    f = str(field or "both").strip().lower()
    if f in ("primary", "both", ""):
        primary = decompress_text(row.config_zlib)
    if f in ("alt", "both") and row.config_alt_zlib:
        alt = decompress_text(row.config_alt_zlib)
    return NeConfigSnapshotDetailOut(**meta.model_dump(), config_text=primary, config_alt_text=alt)


def _safe_export_part(text: str) -> str:
    s = re.sub(r'[<>:"/\\|?*\s]+', "_", str(text or "").strip())
    return (s[:80] or "ne").strip("._") or "ne"


def build_snapshot_export(
    db: Session,
    source: str,
    target_id: str,
    *,
    field: str = "primary",
) -> tuple[str, bytes, str]:
    """Return (filename, payload, media_type) for download."""
    detail = get_snapshot_detail(db, source, target_id, field="both")
    name = _safe_export_part(detail.ne_name or detail.target_id)
    ip = _safe_export_part(detail.ne_ip or "ip")
    base = f"{name}-{ip}-{detail.source}"
    f = str(field or "primary").strip().lower()

    if f == "alt":
        if not detail.has_alt or not detail.config_alt_text:
            raise HTTPException(status_code=404, detail="alt_config_not_found")
        filename = f"{base}-hierarchical.txt"
        return filename, detail.config_alt_text.encode("utf-8"), "text/plain; charset=utf-8"

    if f == "both" and detail.has_alt and detail.config_alt_text:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{base}-set.txt", detail.config_text or "")
            zf.writestr(f"{base}-hierarchical.txt", detail.config_alt_text or "")
        return f"{base}-configs.zip", buf.getvalue(), "application/zip"

    filename = f"{base}-config.txt"
    return filename, (detail.config_text or "").encode("utf-8"), "text/plain; charset=utf-8"


def list_snapshot_history(
    db: Session,
    source: str,
    target_id: str,
    *,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    src = str(source or "").strip().lower()
    tid = str(target_id or "").strip()
    q = (
        db.query(NeConfigHistory)
        .filter(NeConfigHistory.source == src, NeConfigHistory.target_id == tid)
        .order_by(NeConfigHistory.collected_at.desc())
    )
    total = int(q.count())
    rows = q.offset((page - 1) * page_size).limit(page_size).all()
    items: list[NeConfigHistoryOut] = []
    for row in rows:
        cmds = row.commands_json if isinstance(row.commands_json, list) else []
        items.append(
            NeConfigHistoryOut(
                id=str(row.id),
                source=str(row.source),
                target_id=str(row.target_id),
                vendor=str(row.vendor or ""),
                device_type=str(row.device_type or ""),
                ne_name=str(row.ne_name or ""),
                ne_ip=str(row.ne_ip or ""),
                config_sha256=str(row.config_sha256 or ""),
                config_alt_sha256=str(row.config_alt_sha256 or ""),
                plain_size=int(row.plain_size or 0),
                plain_alt_size=int(row.plain_alt_size or 0),
                zlib_size=int(row.zlib_size or 0),
                zlib_alt_size=int(row.zlib_alt_size or 0),
                has_alt=bool(row.config_alt_zlib),
                commands=[str(c) for c in cmds],
                collected_at=row.collected_at,
                last_cycle_id=str(row.cycle_id or ""),
                cycle_id=str(row.cycle_id or ""),
            )
        )
    return {"total": total, "page": page, "page_size": page_size, "items": items}


