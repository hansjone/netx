"""Discover job serializers, scan-target resolution, and history prune."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .cli_resolve import get_default_profile, infer_device_type_vendor
from .device_types import is_placeholder_ne_source
from .models import ManagedNE, TopoDiscoverJob, TopoDiscoverJobItem, UmeInventoryNE
from .topology_common import _RAW_PREVIEW_MAX
from .topology_schemas import (
    FabricDiscoverJobItemOut,
    FabricDiscoverJobOut,
    FabricDiscoverRequest,
    FabricDiscoverUnmatched,
)


def _raw_preview(raw: str, *, limit: int = _RAW_PREVIEW_MAX) -> str:
    text = str(raw or "")
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[truncated preview {limit}/{len(text)} chars]"


def _is_lldp_placeholder_ne(ne: ManagedNE) -> bool:
    """Incomplete placeholders (LLDP / topology) must not be used as discover targets."""
    return is_placeholder_ne_source(ne.source)


def _managed_target_dict(ne: ManagedNE) -> dict[str, str]:
    return {
        "ne_id": ne.id,
        "ume_ne_id": "",
        "ne_name": ne.name or "",
        "ne_ip": ne.ip_address or "",
        "vendor": ne.vendor or "",
        "device_type": ne.device_type or "",
    }


def _job_out(
    db: Session,
    job: TopoDiscoverJob,
    *,
    include_items: bool = True,
    page: int | None = None,
    page_size: int | None = None,
    item_status: str = "",
    item_keyword: str = "",
) -> FabricDiscoverJobOut:
    items_out: list[FabricDiscoverJobItemOut] = []
    items_total = 0
    items_page = 1
    items_page_size = 0
    if include_items:
        q = (
            db.query(TopoDiscoverJobItem)
            .filter(TopoDiscoverJobItem.job_id == job.id)
            .order_by(TopoDiscoverJobItem.created_at.asc())
        )
        st = str(item_status or "").strip().lower()
        if st in ("ok", "success", "pass"):
            q = q.filter(
                TopoDiscoverJobItem.ok.is_(True),
                TopoDiscoverJobItem.parser_stub.is_(False),
                TopoDiscoverJobItem.unmatched_count == 0,
            )
        elif st in ("fail", "failed", "error"):
            q = q.filter(
                or_(
                    TopoDiscoverJobItem.ok.is_(False),
                    TopoDiscoverJobItem.parser_stub.is_(True),
                )
            )
        elif st in ("warn", "warning"):
            q = q.filter(
                TopoDiscoverJobItem.ok.is_(True),
                TopoDiscoverJobItem.parser_stub.is_(False),
                TopoDiscoverJobItem.unmatched_count > 0,
            )
        kw = str(item_keyword or "").strip()
        if kw:
            like = f"%{kw}%"
            q = q.filter(
                or_(
                    TopoDiscoverJobItem.ne_name.ilike(like),
                    TopoDiscoverJobItem.ne_ip.ilike(like),
                    TopoDiscoverJobItem.ne_id.ilike(like),
                    TopoDiscoverJobItem.error.ilike(like),
                )
            )
        items_total = int(q.count())
        if page is not None and page_size is not None:
            items_page = max(1, int(page or 1))
            items_page_size = max(1, min(100, int(page_size or 20)))
            items = q.offset((items_page - 1) * items_page_size).limit(items_page_size).all()
        else:
            items = q.all()
            items_page = 1
            items_page_size = items_total
        for it in items:
            unmatched = [
                FabricDiscoverUnmatched.model_validate(x) for x in (it.unmatched_json or [])[:40]
            ]
            items_out.append(
                FabricDiscoverJobItemOut(
                    id=it.id,
                    job_id=it.job_id,
                    ne_id=it.ne_id or "",
                    ume_ne_id=it.ume_ne_id or "",
                    fabric_node_id=it.fabric_node_id or "",
                    ne_name=it.ne_name or "",
                    ne_ip=it.ne_ip or "",
                    ok=bool(it.ok),
                    command=it.command or "",
                    neighbors=int(it.neighbors or 0),
                    edges_added=int(it.edges_added or 0),
                    edges_updated=int(it.edges_updated or 0),
                    unmatched_count=int(it.unmatched_count or 0),
                    unmatched=unmatched,
                    parser_key=it.parser_key or "",
                    parser_stub=bool(it.parser_stub),
                    error=it.error or "",
                    raw_preview=it.raw_preview or "",
                )
            )
    return FabricDiscoverJobOut(
        id=job.id,
        scope=job.scope,
        trigger_mode=str(getattr(job, "trigger_mode", None) or "manual"),
        status=job.status,
        total=int(job.total or 0),
        done=int(job.done or 0),
        edges_added=int(job.edges_added or 0),
        edges_updated=int(job.edges_updated or 0),
        edges_stale=int(job.edges_stale or 0),
        edges_missing=int(job.edges_stale or 0),
        error=job.error or "",
        started_at=job.started_at,
        ended_at=job.ended_at,
        items=items_out,
        items_total=items_total,
        items_page=items_page,
        items_page_size=items_page_size,
    )


def get_discover_job(
    db: Session,
    job_id: str,
    *,
    page: int | None = None,
    page_size: int | None = None,
    item_status: str = "",
    item_keyword: str = "",
) -> FabricDiscoverJobOut:
    job = db.get(TopoDiscoverJob, str(job_id or "").strip())
    if job is None:
        raise HTTPException(status_code=404, detail="discover_job_not_found")
    return _job_out(
        db,
        job,
        page=page,
        page_size=page_size,
        item_status=item_status,
        item_keyword=item_keyword,
    )


def _ume_target_dict(db: Session, uid: str, default_profile: Any) -> dict[str, str] | None:
    ume = db.query(UmeInventoryNE).filter(UmeInventoryNE.ne_id == uid).one_or_none()
    if ume is None:
        return None
    if default_profile is not None:
        dtype, vendor = infer_device_type_vendor(str(ume.ne_type or ""), default_profile)
    else:
        dtype, vendor = "zte_zxros", (ume.vendor or "ZTE")
    name = (ume.host_name or ume.ne_name or ume.user_label or ume.ip_address or uid).strip()
    return {
        "ne_id": uid,
        "ume_ne_id": uid,
        "ne_name": name,
        "ne_ip": ume.ip_address or "",
        "vendor": vendor or (ume.vendor or "ZTE"),
        "device_type": dtype or "zte_zxros",
    }


def _resolve_scan_targets(
    db: Session, body: FabricDiscoverRequest
) -> list[dict[str, str]]:
    scope = str(body.scope or "ne_ids").strip().lower() or "ne_ids"
    default_profile = get_default_profile(db)
    targets: list[dict[str, str]] = []
    if scope == "all_inventory":
        # Managed inventory first; then UME NEs not already covered by the same management IP.
        # Skip LLDP placeholders (no login yet); once promoted they join normal inventory.
        managed_ips: set[str] = set()
        for ne in db.query(ManagedNE).all():
            if _is_lldp_placeholder_ne(ne):
                continue
            ip = str(ne.ip_address or "").strip()
            if ip:
                managed_ips.add(ip)
            targets.append(_managed_target_dict(ne))
        for ume in db.query(UmeInventoryNE).all():
            uid = str(ume.ne_id or "").strip()
            if not uid:
                continue
            ip = str(ume.ip_address or "").strip()
            if ip and ip in managed_ips:
                continue
            row = _ume_target_dict(db, uid, default_profile)
            if row is not None:
                targets.append(row)
        return targets

    managed_ids = [str(x).strip() for x in (body.managed_ne_ids or []) if str(x).strip()]
    ume_ids = [str(x).strip() for x in (body.ume_ne_ids or []) if str(x).strip()]
    if managed_ids or ume_ids:
        seen: set[str] = set()
        for mid in managed_ids:
            if mid in seen:
                continue
            ne = db.get(ManagedNE, mid)
            if ne is None or _is_lldp_placeholder_ne(ne):
                continue
            seen.add(mid)
            targets.append(_managed_target_dict(ne))
        for uid in ume_ids:
            key = f"ume:{uid}"
            if key in seen:
                continue
            row = _ume_target_dict(db, uid, default_profile)
            if row is None:
                continue
            seen.add(key)
            targets.append(row)
        if not targets:
            raise HTTPException(status_code=400, detail="ne_ids_required")
        return targets

    # Legacy mixed ne_ids: prefer ManagedNE, leftover treated as UME.
    filter_ids = {str(x).strip() for x in (body.ne_ids or []) if str(x).strip()}
    if not filter_ids:
        raise HTTPException(status_code=400, detail="ne_ids_required")
    for mid in list(filter_ids):
        ne = db.get(ManagedNE, mid)
        if ne is not None:
            filter_ids.discard(mid)
            if _is_lldp_placeholder_ne(ne):
                continue
            targets.append(_managed_target_dict(ne))
    for uid in list(filter_ids):
        row = _ume_target_dict(db, uid, default_profile)
        if row is not None:
            targets.append(row)
    return targets


def prune_discover_jobs(db: Session, *, keep: int = 30) -> int:
    """Delete finished discover jobs beyond ``keep`` (newest kept). Open jobs always retained."""
    keep = max(0, min(200, int(keep)))
    finished = (
        db.query(TopoDiscoverJob)
        .filter(TopoDiscoverJob.status.in_(["done", "failed", "cancelled"]))
        .order_by(TopoDiscoverJob.created_at.desc())
        .all()
    )
    to_drop = finished if keep == 0 else finished[keep:]
    if not to_drop:
        return 0
    dropped = 0
    for job in to_drop:
        db.query(TopoDiscoverJobItem).filter(TopoDiscoverJobItem.job_id == job.id).delete(
            synchronize_session=False
        )
        db.delete(job)
        dropped += 1
    if dropped:
        db.commit()
    return dropped
