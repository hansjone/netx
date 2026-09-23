"""biz_state service: tasks, profiles, batches, export."""

from __future__ import annotations

import io
import zipfile
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import String, cast, func, or_
from sqlalchemy.orm import Session

from ..lldp_shared import resolve_vendor_key
from ..models import (
    BizStateBatch,
    BizStateBatchCommand,
    BizStateCommandOverride,
    BizStateEvent,
    BizStateLldpNeighbor,
    BizStateMetricRow,
    BizStateTask,
    BizStateTaskItem,
    BizStateTaskItemBinding,
    ManagedNE,
)
from ..timeutil import utcnow_naive
from .command_match import (
    EXPAND_ALL_COMMAND,
    expand_from_bindings,
    match_command,
    normalize_command,
    preview_task_item,
)
from .collect_session import resolve_aux_command
from .profiles import (
    all_profiles,
    get_profile,
    metric_field_map,
    profile_to_public_dict,
    profiles_for_vendor,
)
from .retention import (
    batch_protect_info,
    delete_batch_data,
    protected_batch_map,
    purge_task_batches,
)


def _utcnow() -> datetime:
    return utcnow_naive()


def _override_map(db: Session) -> dict[str, dict[str, Any]]:
    rows = db.query(BizStateCommandOverride).all()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        ov: dict[str, Any] = {}
        if r.title:
            ov["title"] = r.title
        if r.command_template:
            ov["command_template"] = r.command_template
        if r.description:
            ov["description"] = r.description
        if r.sample_output:
            ov["sample_output"] = r.sample_output
        if r.enabled is not None:
            ov["enabled"] = bool(r.enabled)
        out[str(r.profile_id)] = ov
    return out


def list_profiles_public(db: Session, *, vendor_key: str = "") -> list[dict[str, Any]]:
    ov = _override_map(db)
    key = str(vendor_key or "").strip().lower()
    profiles = profiles_for_vendor(key) if key else [p for p in all_profiles() if p.enabled]
    # Also include disabled-in-code but enabled via override? keep simple: filter enabled after merge
    result = []
    for p in sorted(profiles, key=lambda x: (x.sort_order, x.profile_id)):
        d = profile_to_public_dict(p, overrides=ov.get(p.profile_id))
        if d.get("enabled"):
            result.append(d)
    return result


def upsert_profile_override(db: Session, profile_id: str, body: dict[str, Any]) -> dict[str, Any]:
    p = get_profile(profile_id)
    if not p:
        raise HTTPException(status_code=404, detail="profile_not_found")
    row = (
        db.query(BizStateCommandOverride)
        .filter(BizStateCommandOverride.profile_id == profile_id)
        .one_or_none()
    )
    if row is None:
        row = BizStateCommandOverride(id=uuid4().hex, profile_id=profile_id)
        db.add(row)
    if "title" in body:
        row.title = str(body.get("title") or "")
    if "command_template" in body:
        row.command_template = str(body.get("command_template") or "")
    if "description" in body:
        row.description = str(body.get("description") or "")
    if "sample_output" in body:
        row.sample_output = str(body.get("sample_output") or "")
    if "enabled" in body and body.get("enabled") is not None:
        row.enabled = bool(body.get("enabled"))
    row.updated_at = _utcnow()
    db.commit()
    return profile_to_public_dict(p, overrides=_override_map(db).get(profile_id))


def _ne_meta(db: Session, *, source: str, ne_id: str) -> dict[str, str]:
    src = str(source or "managed").strip().lower()
    nid = str(ne_id or "").strip()
    if src == "managed":
        row = db.get(ManagedNE, nid)
        if not row:
            raise HTTPException(status_code=404, detail="managed_ne_not_found")
        return {
            "ne_name": str(getattr(row, "name", "") or ""),
            "ne_ip": str(getattr(row, "ip_address", "") or ""),
            "vendor": str(getattr(row, "vendor", "") or ""),
            "device_type": str(getattr(row, "device_type", "") or ""),
        }
    if src == "ume":
        from ..models import UmeInventoryNE

        row = db.get(UmeInventoryNE, nid)
        if not row:
            raise HTTPException(status_code=404, detail="ume_ne_not_found")
        return {
            "ne_name": str(
                getattr(row, "user_label", "")
                or getattr(row, "ne_name", "")
                or getattr(row, "host_name", "")
                or getattr(row, "ip_address", "")
                or nid
            ),
            "ne_ip": str(getattr(row, "ip_address", "") or getattr(row, "ip", "") or ""),
            "vendor": str(getattr(row, "vendor", "") or ""),
            "device_type": str(getattr(row, "device_type", "") or getattr(row, "ne_type", "") or ""),
        }
    raise HTTPException(status_code=400, detail="invalid_source")


def create_task(db: Session, body: dict[str, Any]) -> dict[str, Any]:
    source = str(body.get("source") or "managed").strip().lower() or "managed"
    if source not in ("managed", "ume"):
        raise HTTPException(status_code=400, detail="invalid_source")
    ne_id = str(body.get("ne_id") or "").strip()
    if not ne_id:
        raise HTTPException(status_code=400, detail="ne_id_required")

    meta = _ne_meta(db, source=source, ne_id=ne_id)
    vendor = str(body.get("vendor") or meta["vendor"] or "")
    device_type = str(body.get("device_type") or meta["device_type"] or "")
    status = str(body.get("status") or "draft").strip() or "draft"
    if status not in ("draft", "running", "paused", "stopped"):
        status = "draft"
    task = BizStateTask(
        id=uuid4().hex,
        source=source,
        ne_id=ne_id,
        ne_name=str(body.get("ne_name") or meta["ne_name"] or ""),
        ne_ip=str(body.get("ne_ip") or meta["ne_ip"] or ""),
        vendor=vendor,
        device_type=device_type,
        note=str(body.get("note") or "")[:256],
        purpose=str(body.get("purpose") or "")[:32],
        status=status,
        interval_sec=max(60, int(body.get("interval_sec") or 3600)),
        retention_days=max(1, min(3650, int(body.get("retention_days") or 30))),
        daily_keep_enabled=bool(body.get("daily_keep_enabled") or False),
        daily_keep_count=max(1, min(1000, int(body.get("daily_keep_count") or 10))),
        # keep legacy column in sync for brownfield readers
        retention_batches=max(1, int(body.get("retention_days") or body.get("retention_batches") or 30)),
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(task)
    db.flush()

    items_in = list(body.get("items") or [])
    if not items_in:
        # Default: enable LLDP profile for this vendor
        vkey = resolve_vendor_key(vendor, device_type)
        for p in profiles_for_vendor(vkey):
            if p.metric_id == "lldp_neighbor" and p.kind == "collect":
                items_in.append(
                    {
                        "source_profile_id": p.profile_id,
                        "kind": "catalog",
                        "enabled": True,
                        "title": p.title,
                    }
                )
                break

    _replace_items(db, task.id, items_in)
    db.commit()
    return get_task(db, task.id)


def _replace_items(db: Session, task_id: str, items_in: list[dict[str, Any]]) -> None:
    old_items = db.query(BizStateTaskItem).filter(BizStateTaskItem.task_id == task_id).all()
    for it in old_items:
        db.query(BizStateTaskItemBinding).filter(BizStateTaskItemBinding.item_id == it.id).delete()
        db.delete(it)
    db.flush()

    for idx, raw in enumerate(items_in):
        kind = str(raw.get("kind") or "catalog").strip() or "catalog"
        item = BizStateTaskItem(
            id=uuid4().hex,
            task_id=task_id,
            source_profile_id=str(raw.get("source_profile_id") or "")[:128],
            kind=kind,
            enabled=bool(raw.get("enabled", True)),
            title=str(raw.get("title") or "")[:256],
            command_override=str(raw.get("command_override") or raw.get("command") or "")[:512],
            sort_order=int(raw.get("sort_order") if raw.get("sort_order") is not None else idx),
            created_at=_utcnow(),
        )
        db.add(item)
        db.flush()
        for b in list(raw.get("bindings") or []):
            ph = str(b.get("placeholder") or b.get("name") or "").strip()
            val = str(b.get("value") or "").strip()
            if not ph or not val:
                continue
            db.add(
                BizStateTaskItemBinding(
                    id=uuid4().hex,
                    item_id=item.id,
                    placeholder=ph[:64],
                    value=val[:256],
                    created_at=_utcnow(),
                )
            )


def set_task_status(db: Session, task_id: str, status: str) -> dict[str, Any]:
    """Set lifecycle status: draft | running | paused | stopped."""
    return update_task(db, task_id, {"status": status})


def update_task(db: Session, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
    task = db.get(BizStateTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task_not_found")
    # Offline import tasks have no inventory NE / credentials — keep paused.
    if str(task.source or "").strip().lower() == "import":
        if "status" in body and str(body.get("status") or "").strip() == "running":
            raise HTTPException(status_code=400, detail="import_offline_only")
    if "note" in body:
        task.note = str(body.get("note") or "")[:256]
    if "purpose" in body and body["purpose"] is not None:
        task.purpose = str(body.get("purpose") or "")[:32]
    if "interval_sec" in body:
        task.interval_sec = max(60, int(body.get("interval_sec") or 3600))
    if "retention_days" in body:
        task.retention_days = max(1, min(3650, int(body.get("retention_days") or 30)))
        task.retention_batches = task.retention_days  # legacy mirror
    elif "retention_batches" in body:
        # backward compat: treat as days if old clients still send it
        task.retention_days = max(1, min(3650, int(body.get("retention_batches") or 30)))
        task.retention_batches = task.retention_days
    if "daily_keep_enabled" in body:
        task.daily_keep_enabled = bool(body.get("daily_keep_enabled"))
    if "daily_keep_count" in body:
        task.daily_keep_count = max(1, min(1000, int(body.get("daily_keep_count") or 10)))
    if "items" in body:
        _replace_items(db, task.id, list(body.get("items") or []))
    if "status" in body:
        st = str(body.get("status") or "").strip()
        if st in ("draft", "running", "paused", "stopped"):
            if st == "running":
                # Cutover HF: NE-scoped catalogs; placeholder bindings optional / stamped later
                purpose = str(getattr(task, "purpose", None) or "").strip()
                note = str(getattr(task, "note", None) or "")
                if purpose != "cutover_hf" and not note.startswith("割接高频"):
                    _assert_bindings_ready(db, task.id)
            task.status = st
    task.updated_at = _utcnow()
    db.commit()
    return get_task(db, task_id)


def _assert_bindings_ready(db: Session, task_id: str) -> None:
    items = (
        db.query(BizStateTaskItem)
        .filter(BizStateTaskItem.task_id == task_id, BizStateTaskItem.enabled.is_(True))
        .all()
    )
    for it in items:
        if it.kind != "catalog":
            continue
        profile = get_profile(it.source_profile_id)
        if not profile or not profile.placeholders:
            continue
        # Optional discover placeholders: empty bindings → expand all at collect.
        if all(not ph.required for ph in profile.placeholders):
            continue
        binds = (
            db.query(BizStateTaskItemBinding)
            .filter(BizStateTaskItemBinding.item_id == it.id)
            .all()
        )
        if not binds:
            raise HTTPException(
                status_code=400,
                detail=f"bindings_required:{profile.profile_id}",
            )


def set_item_bindings(
    db: Session, task_id: str, item_id: str, bindings: list[dict[str, Any]]
) -> dict[str, Any]:
    item = db.get(BizStateTaskItem, item_id)
    if not item or item.task_id != task_id:
        raise HTTPException(status_code=404, detail="item_not_found")
    db.query(BizStateTaskItemBinding).filter(BizStateTaskItemBinding.item_id == item_id).delete()
    for b in bindings:
        ph = str(b.get("placeholder") or b.get("name") or "").strip()
        val = str(b.get("value") or "").strip()
        if not ph or not val:
            continue
        db.add(
            BizStateTaskItemBinding(
                id=uuid4().hex,
                item_id=item_id,
                placeholder=ph[:64],
                value=val[:256],
                created_at=_utcnow(),
            )
        )
    task = db.get(BizStateTask, task_id)
    if task:
        task.updated_at = _utcnow()
    db.commit()
    return get_task(db, task_id)


def get_task(db: Session, task_id: str) -> dict[str, Any]:
    task = db.get(BizStateTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task_not_found")
    items = (
        db.query(BizStateTaskItem)
        .filter(BizStateTaskItem.task_id == task_id)
        .order_by(BizStateTaskItem.sort_order.asc())
        .all()
    )
    item_out = []
    for it in items:
        binds = (
            db.query(BizStateTaskItemBinding)
            .filter(BizStateTaskItemBinding.item_id == it.id)
            .all()
        )
        item_out.append(
            {
                "id": it.id,
                "source_profile_id": it.source_profile_id,
                "kind": it.kind,
                "enabled": bool(it.enabled),
                "title": it.title,
                "command_override": it.command_override,
                "sort_order": it.sort_order,
                "bindings": [{"placeholder": b.placeholder, "value": b.value} for b in binds],
            }
        )
    return {
        "id": task.id,
        "source": task.source,
        "ne_id": task.ne_id,
        "ne_name": task.ne_name,
        "ne_ip": task.ne_ip,
        "vendor": task.vendor,
        "device_type": task.device_type,
        "note": task.note,
        "purpose": str(getattr(task, "purpose", None) or ""),
        "status": task.status,
        "interval_sec": task.interval_sec,
        "retention_days": int(getattr(task, "retention_days", None) or 30),
        "daily_keep_enabled": bool(getattr(task, "daily_keep_enabled", False)),
        "daily_keep_count": int(getattr(task, "daily_keep_count", None) or 10),
        "collect_running": bool(task.collect_running),
        "last_collect_started_at": task.last_collect_started_at.isoformat() + "Z"
        if task.last_collect_started_at
        else None,
        "last_collect_ended_at": task.last_collect_ended_at.isoformat() + "Z"
        if task.last_collect_ended_at
        else None,
        "last_error": task.last_error,
        "items": item_out,
    }


def list_tasks(db: Session, *, purpose: str | None = None) -> list[dict[str, Any]]:
    from sqlalchemy import or_

    q = db.query(BizStateTask)
    purpose_f = str(purpose or "").strip()
    if purpose_f:
        if purpose_f == "portrait":
            # Portrait = empty purpose or explicit portrait (exclude cutover_hf)
            q = q.filter(
                or_(
                    BizStateTask.purpose == "",
                    BizStateTask.purpose == "portrait",
                    BizStateTask.purpose.is_(None),
                )
            )
        else:
            q = q.filter(BizStateTask.purpose == purpose_f)
    rows = q.order_by(BizStateTask.updated_at.desc()).all()
    return [
        {
            "id": t.id,
            "source": t.source,
            "ne_id": t.ne_id,
            "ne_name": t.ne_name,
            "ne_ip": t.ne_ip,
            "vendor": t.vendor,
            "note": t.note,
            "purpose": str(getattr(t, "purpose", None) or ""),
            "status": t.status,
            "interval_sec": t.interval_sec,
            "collect_running": bool(t.collect_running),
            "last_error": t.last_error,
            "last_collect_started_at": t.last_collect_started_at.isoformat() + "Z"
            if t.last_collect_started_at
            else None,
            "last_collect_ended_at": t.last_collect_ended_at.isoformat() + "Z"
            if t.last_collect_ended_at
            else None,
        }
        for t in rows
    ]


def delete_task(db: Session, task_id: str) -> None:
    task = db.get(BizStateTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task_not_found")
    batches = db.query(BizStateBatch).filter(BizStateBatch.task_id == task_id).all()
    # Refuse if any batch is still referenced by compare/migration (manual baseline is OK to drop with task)
    pmap = protected_batch_map(db, task_id=task_id)
    blocked: list[dict[str, Any]] = []
    for b in batches:
        reasons = [r for r in pmap.get(b.id, []) if r != "manual_baseline"]
        if reasons:
            blocked.append({"batch_id": b.id, "reasons": reasons})
    if blocked:
        raise HTTPException(
            status_code=409,
            detail={"error": "batches_referenced", "items": blocked[:20]},
        )
    for b in batches:
        delete_batch_data(db, b.id)
    items = db.query(BizStateTaskItem).filter(BizStateTaskItem.task_id == task_id).all()
    for it in items:
        db.query(BizStateTaskItemBinding).filter(BizStateTaskItemBinding.item_id == it.id).delete()
        db.delete(it)
    db.query(BizStateEvent).filter(BizStateEvent.task_id == task_id).delete()
    db.delete(task)
    db.commit()


def _batch_list_item(b: BizStateBatch, protect: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": b.id,
        "status": b.status,
        "command_count": b.command_count,
        "row_count": b.row_count,
        "message": b.message,
        "ne_name": b.ne_name or "",
        "ne_id": b.ne_id or "",
        "alias": str(getattr(b, "alias", "") or ""),
        "started_at": b.started_at.isoformat() + "Z" if b.started_at else None,
        "ended_at": b.ended_at.isoformat() + "Z" if b.ended_at else None,
        "is_baseline": bool(getattr(b, "is_baseline", False)),
        "baseline_marked_at": b.baseline_marked_at.isoformat() + "Z"
        if getattr(b, "baseline_marked_at", None)
        else None,
        "protected": bool(protect.get("protected")),
        "protect_reasons": list(protect.get("reasons") or []),
    }


def list_batches(db: Session, task_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = (
        db.query(BizStateBatch)
        .filter(BizStateBatch.task_id == task_id)
        .order_by(BizStateBatch.started_at.desc())
        .limit(max(1, min(500, int(limit))))
        .all()
    )
    pmap = protected_batch_map(db, task_id=task_id)
    out: list[dict[str, Any]] = []
    for b in rows:
        reasons = list(pmap.get(b.id, []))
        if bool(getattr(b, "is_baseline", False)) and "manual_baseline" not in reasons:
            reasons = ["manual_baseline", *reasons]
        out.append(
            _batch_list_item(
                b,
                {"protected": bool(reasons), "reasons": reasons},
            )
        )
    return out


def set_batch_baseline(db: Session, batch_id: str, *, marked: bool) -> dict[str, Any]:
    b = db.get(BizStateBatch, batch_id)
    if not b:
        raise HTTPException(status_code=404, detail="batch_not_found")
    b.is_baseline = bool(marked)
    b.baseline_marked_at = _utcnow() if marked else None
    db.commit()
    return _batch_list_item(b, batch_protect_info(db, batch_id))


def set_batch_alias(db: Session, batch_id: str, *, alias: str) -> dict[str, Any]:
    b = db.get(BizStateBatch, batch_id)
    if not b:
        raise HTTPException(status_code=404, detail="batch_not_found")
    b.alias = str(alias or "").strip()[:128]
    db.commit()
    return _batch_list_item(b, batch_protect_info(db, batch_id))


def delete_batch(db: Session, batch_id: str) -> dict[str, Any]:
    b = db.get(BizStateBatch, batch_id)
    if not b:
        raise HTTPException(status_code=404, detail="batch_not_found")
    info = batch_protect_info(db, batch_id)
    if info.get("protected"):
        raise HTTPException(
            status_code=409,
            detail={"error": "batch_protected", "reasons": info.get("reasons") or []},
        )
    delete_batch_data(db, batch_id)
    db.commit()
    return {"ok": True, "batch_id": batch_id}


def delete_batches_bulk(db: Session, batch_ids: list[str]) -> dict[str, Any]:
    deleted: list[str] = []
    skipped: list[dict[str, Any]] = []
    for raw in batch_ids:
        bid = str(raw or "").strip()
        if not bid:
            continue
        b = db.get(BizStateBatch, bid)
        if not b:
            skipped.append({"batch_id": bid, "reasons": ["not_found"]})
            continue
        info = batch_protect_info(db, bid)
        if info.get("protected"):
            skipped.append({"batch_id": bid, "reasons": info.get("reasons") or []})
            continue
        delete_batch_data(db, bid)
        deleted.append(bid)
    if deleted:
        db.commit()
    return {"ok": True, "deleted": deleted, "skipped": skipped, "deleted_count": len(deleted)}


def run_purge_for_task(db: Session, task_id: str) -> dict[str, Any]:
    task = db.get(BizStateTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task_not_found")
    return purge_task_batches(db, task)


# Workbook sheets are keyed by metric_id. Multiple AF-specific collect profiles
# share one metric (e.g. all BGP summaries → bgp_peer); use a neutral title.
_METRIC_SHEET_TITLES: dict[str, str] = {
    "bgp_peer": "BGP Status Summary",
    "bgp_route": "BGP Neighbor Routes",
    "vrrp": "VRRP",
    "ip_route": "IPv4 Forwarding",
    "ipv6_route": "IPv6 Forwarding",
}


def _metric_sheet_title(metric_id: str, fallback: str = "") -> str:
    mid = str(metric_id or "").strip()
    if mid in _METRIC_SHEET_TITLES:
        return _METRIC_SHEET_TITLES[mid]
    fb = str(fallback or "").strip()
    return fb or mid


def _raw_line_count(raw: str | None) -> int:
    """CLI text lines collected (splitlines-compatible, no list materialization)."""
    s = raw or ""
    if not s:
        return 0
    return s.count("\n") + (0 if s.endswith("\n") else 1)


def _cmd_raw_line_count(cmd: Any) -> int:
    """Prefer full-file line count persisted before DB raw_text truncate."""
    stored = int(getattr(cmd, "raw_line_count", 0) or 0)
    if stored > 0:
        return stored
    return _raw_line_count(getattr(cmd, "raw_text", None))


def _cmd_declared_total(cmd: Any) -> int:
    return int(getattr(cmd, "declared_total", 0) or 0)


def get_batch(db: Session, batch_id: str) -> dict[str, Any]:
    """Batch workbook summary: meta + commands + sheet catalog (no metric row payload)."""
    b = db.get(BizStateBatch, batch_id)
    if not b:
        raise HTTPException(status_code=404, detail="batch_not_found")
    cmds = (
        db.query(BizStateBatchCommand)
        .filter(BizStateBatchCommand.batch_id == batch_id)
        .order_by(BizStateBatchCommand.created_at.asc())
        .all()
    )
    protect = batch_protect_info(db, batch_id)

    # Per-metric row counts (generic table)
    metric_counts: dict[str, int] = {}
    for mid, cnt in (
        db.query(BizStateMetricRow.metric_id, func.count(BizStateMetricRow.id))
        .filter(BizStateMetricRow.batch_id == batch_id)
        .group_by(BizStateMetricRow.metric_id)
        .all()
    ):
        key = str(mid or "").strip()
        if key:
            metric_counts[key] = int(cnt or 0)

    lldp_count = (
        db.query(func.count(BizStateLldpNeighbor.id))
        .filter(BizStateLldpNeighbor.batch_id == batch_id)
        .scalar()
    )
    lldp_n = int(lldp_count or 0)
    if lldp_n:
        metric_counts["lldp_neighbor"] = lldp_n

    cmd_payload: list[dict[str, Any]] = []
    sheets_order: list[str] = []
    sheet_cmds: dict[str, list[dict[str, Any]]] = {}
    sheet_titles: dict[str, str] = {}

    def _push_sheet(mid: str, cmd_info: dict[str, Any] | None = None, title: str = "") -> None:
        id_ = str(mid or "").strip()
        if not id_ or id_ in ("vrf_list", "commands"):
            return
        if id_ not in sheets_order:
            sheets_order.append(id_)
            sheet_cmds.setdefault(id_, [])
            sheet_titles[id_] = _metric_sheet_title(id_, title)
        if cmd_info is not None:
            # Prefer primary collect rows over aux / aux_cached for the same CLI
            sheet_cmds[id_].append(cmd_info)

    primary_cmds_by_cli: dict[str, dict[str, Any]] = {}
    for c in cmds:
        status = str(c.parse_status or "").strip().lower()
        is_aux = status.startswith("aux")
        raw = c.raw_text or ""
        info = {
            "id": c.id,
            "profile_id": c.profile_id,
            "parser_id": c.parser_id,
            "metric_id": c.metric_id,
            "raw_command": c.raw_command,
            "params": c.params_json or {},
            "parse_status": c.parse_status,
            "row_count": c.row_count,
            "raw_line_count": _cmd_raw_line_count(c),
            "declared_total": _cmd_declared_total(c),
            "message": c.message,
            "has_raw": bool(str(raw).strip()),
            "is_aux": is_aux,
        }
        cmd_n = normalize_command(str(c.raw_command or ""))
        if not is_aux and cmd_n:
            primary_cmds_by_cli.setdefault(cmd_n, info)
        # Commands sheet: hide successful aux when the same CLI already has a primary row;
        # keep failed/skipped aux visible so partial reasons are not hidden.
        if is_aux and cmd_n and cmd_n in primary_cmds_by_cli:
            st_l = status
            if st_l in ("aux_failed",) or "fail" in st_l or st_l.startswith("skipped"):
                cmd_payload.append(info)
            continue
        if is_aux and cmd_n:
            # aux may appear before primary in list — defer; second pass below
            continue
        cmd_payload.append(info)
        mid = str(c.metric_id or "").strip()
        title = ""
        pid = str(c.profile_id or "").strip()
        if pid:
            prof = get_profile(pid)
            if prof:
                title = str(prof.title or "")
                if not mid:
                    mid = str(prof.metric_id or "").strip()
        if mid and mid not in ("", "vrf_list") and not is_aux:
            _push_sheet(
                mid,
                {
                    "id": c.id,
                    "raw_command": c.raw_command,
                    "parse_status": c.parse_status,
                    "row_count": c.row_count,
                    "raw_line_count": info["raw_line_count"],
                    "declared_total": info["declared_total"],
                    "message": c.message,
                    "has_raw": info["has_raw"],
                    "profile_id": c.profile_id,
                },
                title=title,
            )

    # Include aux-only CLIs that had no primary counterpart
    for c in cmds:
        status = str(c.parse_status or "").strip().lower()
        if not status.startswith("aux"):
            continue
        cmd_n = normalize_command(str(c.raw_command or ""))
        if cmd_n and cmd_n in primary_cmds_by_cli:
            continue
        cmd_payload.append(
            {
                "id": c.id,
                "profile_id": c.profile_id,
                "parser_id": c.parser_id,
                "metric_id": c.metric_id,
                "raw_command": c.raw_command,
                "params": c.params_json or {},
                "parse_status": c.parse_status,
                "row_count": c.row_count,
                "raw_line_count": _cmd_raw_line_count(c),
                "declared_total": _cmd_declared_total(c),
                "message": c.message,
                "has_raw": bool(str(c.raw_text or "").strip()),
                "is_aux": True,
            }
        )

    for mid in metric_counts:
        if mid not in sheets_order:
            sheets_order.append(mid)
            sheet_cmds.setdefault(mid, [])
        if mid not in sheet_titles:
            # Best-effort title from any profile with this metric_id
            fallback = mid
            for p in all_profiles():
                if p.metric_id == mid and p.enabled:
                    fallback = str(p.title or mid)
                    break
            sheet_titles[mid] = _metric_sheet_title(mid, fallback)

    sheets = [
        {
            "metric_id": mid,
            "title": sheet_titles.get(mid) or mid,
            "row_count": int(metric_counts.get(mid) or 0),
            "commands": list(sheet_cmds.get(mid) or []),
        }
        for mid in sheets_order
    ]
    sheet_count = len(sheets)
    sheets_with_data = sum(1 for s in sheets if int(s.get("row_count") or 0) > 0)

    # Full-batch command stats (includes hidden successful aux).
    stats = {"total": 0, "ok": 0, "failed": 0, "aux_failed": 0, "skipped": 0, "other": 0}
    for c in cmds:
        stats["total"] += 1
        st = str(c.parse_status or "").strip().lower()
        if st in ("ok", "success", "aux", "aux_ok", "aux_cached"):
            stats["ok"] += 1
        elif st == "aux_failed" or (st.startswith("aux") and "fail" in st):
            stats["aux_failed"] += 1
            stats["failed"] += 1
        elif st in ("failed", "error", "fail") or st.endswith("_failed"):
            stats["failed"] += 1
        elif st.startswith("skipped"):
            stats["skipped"] += 1
        else:
            stats["other"] += 1

    return {
        "id": b.id,
        "task_id": b.task_id,
        "status": b.status,
        "command_count": b.command_count,
        "row_count": b.row_count,
        "message": b.message,
        "alias": str(getattr(b, "alias", "") or ""),
        "started_at": b.started_at.isoformat() + "Z" if b.started_at else None,
        "ended_at": b.ended_at.isoformat() + "Z" if b.ended_at else None,
        "is_baseline": bool(getattr(b, "is_baseline", False)),
        "baseline_marked_at": b.baseline_marked_at.isoformat() + "Z"
        if getattr(b, "baseline_marked_at", None)
        else None,
        "protected": bool(protect.get("protected")),
        "protect_reasons": list(protect.get("reasons") or []),
        # Workbook header: 对比项 / 有数据 / 表格
        "compare_item_count": sheet_count,
        "sheets_with_data": sheets_with_data,
        "sheet_count": sheet_count,
        "commands": cmd_payload,
        "command_stats": stats,
        "sheets": sheets,
    }


def list_batch_metric_rows(
    db: Session,
    batch_id: str,
    metric_id: str,
    *,
    page: int = 1,
    page_size: int = 50,
    kw: str = "",
    column: str = "",
) -> dict[str, Any]:
    """Paginated rows for one batch metric sheet (server-side filter)."""
    b = db.get(BizStateBatch, batch_id)
    if not b:
        raise HTTPException(status_code=404, detail="batch_not_found")
    mid = str(metric_id or "").strip()
    if not mid or mid in ("commands", "vrf_list"):
        raise HTTPException(status_code=400, detail="invalid_metric_id")

    page_n = max(1, int(page or 1))
    size_n = max(1, min(200, int(page_size or 50)))
    kw_n = str(kw or "").strip()
    col_n = str(column or "").strip()

    fields = metric_field_map().get(mid) or []
    columns = [
        {
            "key": f.name,
            "header": f.name,  # strict original field name (no display_name localization)
            "role": f.role,
            "is_key": bool(f.is_key),
        }
        for f in fields
    ]

    if mid == "lldp_neighbor":
        q = db.query(BizStateLldpNeighbor).filter(BizStateLldpNeighbor.batch_id == batch_id)
        if kw_n:
            like = f"%{kw_n}%"
            if col_n == "local_if":
                q = q.filter(BizStateLldpNeighbor.local_if.ilike(like))
            elif col_n == "remote_sys":
                q = q.filter(BizStateLldpNeighbor.remote_sys.ilike(like))
            elif col_n == "remote_if":
                q = q.filter(BizStateLldpNeighbor.remote_if.ilike(like))
            elif col_n == "remote_ip":
                q = q.filter(BizStateLldpNeighbor.remote_ip.ilike(like))
            elif col_n == "protocol":
                q = q.filter(BizStateLldpNeighbor.protocol.ilike(like))
            else:
                q = q.filter(
                    or_(
                        BizStateLldpNeighbor.local_if.ilike(like),
                        BizStateLldpNeighbor.remote_sys.ilike(like),
                        BizStateLldpNeighbor.remote_if.ilike(like),
                        BizStateLldpNeighbor.remote_ip.ilike(like),
                        BizStateLldpNeighbor.protocol.ilike(like),
                    )
                )
        total = int(q.count() or 0)
        rows_db = (
            q.order_by(BizStateLldpNeighbor.local_if.asc())
            .offset((page_n - 1) * size_n)
            .limit(size_n)
            .all()
        )
        items = [
            {
                "local_if": n.local_if,
                "remote_sys": n.remote_sys,
                "remote_if": n.remote_if,
                "remote_ip": n.remote_ip,
                "protocol": n.protocol,
            }
            for n in rows_db
        ]
        if not columns:
            columns = [
                {"key": "local_if", "header": "local_if", "role": "identity", "is_key": True},
                {"key": "remote_sys", "header": "remote_sys", "role": "identity", "is_key": True},
                {"key": "remote_if", "header": "remote_if", "role": "identity", "is_key": True},
                {"key": "remote_ip", "header": "remote_ip", "role": "meta", "is_key": False},
                {"key": "protocol", "header": "protocol", "role": "meta", "is_key": False},
            ]
    else:
        q = db.query(BizStateMetricRow).filter(
            BizStateMetricRow.batch_id == batch_id,
            BizStateMetricRow.metric_id == mid,
        )
        if kw_n:
            like = f"%{kw_n}%"
            if col_n:
                # JSON path as text — works on Postgres JSONB and SQLite JSON
                q = q.filter(cast(BizStateMetricRow.data_json[col_n], String).ilike(like))
            else:
                q = q.filter(cast(BizStateMetricRow.data_json, String).ilike(like))
        total = int(q.count() or 0)
        rows_db = (
            q.order_by(BizStateMetricRow.seq.asc(), BizStateMetricRow.id.asc())
            .offset((page_n - 1) * size_n)
            .limit(size_n)
            .all()
        )
        items = [dict(r.data_json or {}) for r in rows_db]
        if not columns and items:
            keys: list[str] = []
            for rec in items:
                for k in rec.keys():
                    if k not in keys:
                        keys.append(str(k))
            columns = [
                {"key": k, "header": k, "role": "identity", "is_key": False} for k in keys
            ]

    pages = max(1, (total + size_n - 1) // size_n) if total else 1
    return {
        "batch_id": batch_id,
        "metric_id": mid,
        "total": total,
        "page": page_n,
        "page_size": size_n,
        "pages": pages,
        "columns": columns,
        "items": items,
    }


def get_batch_command(db: Session, batch_id: str, command_id: str) -> dict[str, Any]:
    """Full CLI raw text for one collect command (AI / deep dive)."""
    from ..models import BizStateBatchCommand

    b = db.get(BizStateBatch, batch_id)
    if not b:
        raise HTTPException(status_code=404, detail="batch_not_found")
    c = db.get(BizStateBatchCommand, command_id)
    if not c or c.batch_id != batch_id:
        raise HTTPException(status_code=404, detail="command_not_found")
    task = db.get(BizStateTask, b.task_id) if b.task_id else None
    raw = c.raw_text or ""
    return {
        "id": c.id,
        "batch_id": batch_id,
        "task_id": b.task_id,
        "device": {
            "ne_id": task.ne_id if task else "",
            "ne_name": task.ne_name if task else "",
            "ne_ip": task.ne_ip if task else "",
        },
        "profile_id": c.profile_id,
        "parser_id": c.parser_id,
        "metric_id": c.metric_id,
        "raw_command": c.raw_command,
        "params": c.params_json or {},
        "parse_status": c.parse_status,
        "row_count": c.row_count,
        "raw_line_count": _cmd_raw_line_count(c),
        "declared_total": _cmd_declared_total(c),
        "message": c.message,
        "raw_text": raw,
        "collected_at": c.created_at.isoformat() + "Z" if c.created_at else None,
        "batch_started_at": b.started_at.isoformat() + "Z" if b.started_at else None,
        "batch_ended_at": b.ended_at.isoformat() + "Z" if b.ended_at else None,
    }


def export_batch_zip(db: Session, batch_id: str) -> bytes:
    detail = get_batch(db, batch_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        lines = [
            f"batch_id={detail['id']}",
            f"task_id={detail['task_id']}",
            f"status={detail['status']}",
            f"commands={detail['command_count']}",
            f"rows={detail['row_count']}",
            "",
            "commands:",
        ]
        for c in detail["commands"]:
            lines.append(
                f"- {c['raw_command']} | parse={c['parse_status']} | "
                f"profile={c['profile_id']} | rows={c['row_count']}"
            )
        zf.writestr("manifest.txt", "\n".join(lines) + "\n")

        for c in detail["commands"]:
            safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in c["raw_command"])[:80]
            row = db.get(BizStateBatchCommand, c["id"])
            raw = (row.raw_text if row else "") or ""
            if raw:
                zf.writestr(f"raw/{c['id']}_{safe}.full.txt", raw)
                zf.writestr(f"raw/{c['id']}_{safe}.txt", raw[:2000])

        # LLDP CSV
        neighbors = (
            db.query(BizStateLldpNeighbor)
            .filter(BizStateLldpNeighbor.batch_id == batch_id)
            .order_by(BizStateLldpNeighbor.local_if.asc())
            .all()
        )
        csv_lines = ["local_if,remote_sys,remote_if,remote_ip,protocol"]
        for n in neighbors:
            csv_lines.append(
                ",".join(
                    [
                        _csv(n.local_if),
                        _csv(n.remote_sys),
                        _csv(n.remote_if),
                        _csv(n.remote_ip),
                        _csv(n.protocol),
                    ]
                )
            )
        zf.writestr("tables/lldp_neighbor.csv", "\n".join(csv_lines) + "\n")

        # Generic metrics CSV (stream by metric_id)
        for sheet in detail.get("sheets") or []:
            mid = str(sheet.get("metric_id") or "").strip()
            if not mid or mid == "lldp_neighbor":
                continue
            rows = (
                db.query(BizStateMetricRow)
                .filter(
                    BizStateMetricRow.batch_id == batch_id,
                    BizStateMetricRow.metric_id == mid,
                )
                .order_by(BizStateMetricRow.seq.asc(), BizStateMetricRow.id.asc())
                .all()
            )
            if not rows:
                continue
            recs = [dict(r.data_json or {}) for r in rows]
            cols: list[str] = []
            for rec in recs:
                for k in rec.keys():
                    if k not in cols:
                        cols.append(str(k))
            out_lines = [",".join(_csv(c) for c in cols)]
            for rec in recs:
                out_lines.append(",".join(_csv(str(rec.get(c, "") or "")) for c in cols))
            safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in mid)[:80] or "metric"
            zf.writestr(f"tables/{safe}.csv", "\n".join(out_lines) + "\n")
    return buf.getvalue()


def _csv(v: str) -> str:
    s = str(v or "")
    if any(ch in s for ch in ",\"\n"):
        return '"' + s.replace('"', '""') + '"'
    return s


def preview_items(db: Session, *, vendor: str, device_type: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    vkey = resolve_vendor_key(vendor, device_type)
    out = []
    for raw in items:
        out.append(
            preview_task_item(
                vendor_key=vkey,
                profile_id=str(raw.get("source_profile_id") or ""),
                command=str(raw.get("command_override") or raw.get("command") or ""),
                bindings=list(raw.get("bindings") or []),
                kind=str(raw.get("kind") or "catalog"),
            )
        )
    return out


def _resolve_export_profile(profile_id: str):
    """Resolve collect profile; remap disabled if_intf → config_interface."""

    pid = str(profile_id or "").strip()
    if not pid:
        return None
    profile = get_profile(pid)
    if profile is None:
        return None
    if profile.enabled:
        return profile
    if profile.metric_id == "if_intf" or pid.endswith(".if_intf"):
        vk = str(profile.vendor_key or "zte").strip() or "zte"
        remapped = get_profile(f"{vk}.config_interface") or get_profile("zte.config_interface")
        if remapped and remapped.enabled:
            return remapped
    return None


def _append_aux_commands(
    section: dict[str, Any],
    profile: Any,
    *,
    params: dict[str, str],
) -> None:
    """Append resolved aux CLIs onto a plan section (section-local dedupe only)."""
    existing = {
        str(c.get("command") or "").strip()
        for c in section.get("commands") or []
        if str(c.get("role") or "") == "aux"
    }
    for aux in list(getattr(profile, "aux_commands", None) or []):
        try:
            ra = resolve_aux_command(aux, params=dict(params or {}))
        except ValueError as exc:
            section["notes"].append(f"aux {getattr(aux, 'key', '')}: {exc}")
            continue
        cmd = normalize_command(ra.command)
        if not cmd or cmd in existing:
            continue
        existing.add(cmd)
        section["commands"].append(
            {
                "command": cmd,
                "role": "aux",
                "aux_key": ra.key,
                "params": dict(params or {}),
                "profile_id": ra.profile_id,
            }
        )


def _flat_unique_commands(sections: list[dict[str, Any]]) -> list[str]:
    """Dedupe executable CLIs across items (templates excluded)."""
    flat: list[str] = []
    seen: set[str] = set()
    for sec in sections:
        for c in sec.get("commands") or []:
            role = str(c.get("role") or "primary")
            if role == "template":
                continue
            cmd = normalize_command(c.get("command"))
            if not cmd or cmd in seen:
                continue
            seen.add(cmd)
            flat.append(cmd)
    return flat


def plan_task_collect_commands(
    db: Session,
    task_id: str,
    *,
    enabled_only: bool = True,
    include_aux: bool = True,
) -> dict[str, Any]:
    """Plan concrete collect CLIs for a task (no device login).

    Each monitoring item lists its own primary + aux CLIs (aux repeated per
    item when shared). The top-level ``commands`` list is the deduped union
    for scripting. Pass include_aux=False for primary-only lists.
    """
    task = db.get(BizStateTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task_not_found")
    vendor_key = resolve_vendor_key(task.vendor or "", task.device_type or "")
    q = db.query(BizStateTaskItem).filter(BizStateTaskItem.task_id == task_id)
    if enabled_only:
        q = q.filter(BizStateTaskItem.enabled.is_(True))
    items = q.order_by(BizStateTaskItem.sort_order.asc()).all()

    sections: list[dict[str, Any]] = []

    for item in items:
        title = str(item.title or "").strip()
        kind = str(item.kind or "catalog")
        section: dict[str, Any] = {
            "item_id": item.id,
            "kind": kind,
            "source_profile_id": str(item.source_profile_id or ""),
            "title": title,
            "enabled": bool(item.enabled),
            "commands": [],
            "notes": [],
        }

        if kind == "custom_raw":
            cmd = normalize_command(item.command_override)
            if cmd:
                section["commands"].append(
                    {"command": cmd, "role": "primary", "params": {}, "profile_id": ""}
                )
            else:
                section["notes"].append("empty custom command")
            sections.append(section)
            continue

        profile = _resolve_export_profile(item.source_profile_id)
        if profile is None:
            section["notes"].append(
                f"skip profile {item.source_profile_id} (missing or disabled)"
            )
            sections.append(section)
            continue

        section["title"] = title or str(profile.title or profile.profile_id)
        section["source_profile_id"] = profile.profile_id
        binds = (
            db.query(BizStateTaskItemBinding)
            .filter(BizStateTaskItemBinding.item_id == item.id)
            .all()
        )
        binding_dicts = [
            {
                "placeholder": str(b.placeholder or "").strip(),
                "value": str(b.value or "").strip(),
            }
            for b in binds
            if str(b.placeholder or "").strip() and str(b.value or "").strip()
        ]

        try:
            pairs = expand_from_bindings(
                profile=profile,
                bindings=binding_dicts,
                command_override=item.command_override,
            )
        except ValueError as exc:
            section["notes"].append(str(exc))
            tmpl = normalize_command(
                item.command_override or profile.command_template or ""
            )
            if tmpl:
                section["commands"].append(
                    {
                        "command": tmpl,
                        "role": "template",
                        "params": {},
                        "profile_id": profile.profile_id,
                    }
                )
            if include_aux:
                _append_aux_commands(section, profile, params={})
            sections.append(section)
            continue

        if pairs and pairs[0][0] == EXPAND_ALL_COMMAND:
            tmpl = normalize_command(profile.command_template)
            section["notes"].append(
                "expand_all: no bindings; collect will expand discover values"
            )
            if tmpl:
                section["commands"].append(
                    {
                        "command": tmpl,
                        "role": "template",
                        "params": {"__expand_all__": "1"},
                        "profile_id": profile.profile_id,
                    }
                )
            if include_aux:
                _append_aux_commands(section, profile, params={})
            sections.append(section)
            continue

        for concrete, params in pairs:
            cmd = normalize_command(concrete)
            if not cmd:
                continue
            hit = match_command(vendor_key=vendor_key, command=cmd)
            pid = str(
                (hit.profile.profile_id if hit else profile.profile_id) or ""
            ).strip()
            section["commands"].append(
                {
                    "command": cmd,
                    "role": "primary",
                    "params": dict(params or {}),
                    "profile_id": pid,
                }
            )
            if include_aux:
                _append_aux_commands(
                    section,
                    hit.profile if hit else profile,
                    params=dict(params or {}),
                )

        # No concrete primary cmds → still show template as comment
        if not any(c.get("role") == "primary" for c in section["commands"]):
            tmpl = normalize_command(
                item.command_override or profile.command_template or ""
            )
            if tmpl and not any(
                c.get("role") == "template" and c.get("command") == tmpl
                for c in section["commands"]
            ):
                section["commands"].append(
                    {
                        "command": tmpl,
                        "role": "template",
                        "params": {},
                        "profile_id": profile.profile_id,
                    }
                )

        sections.append(section)

    flat = _flat_unique_commands(sections)
    return {
        "task_id": task.id,
        "ne_name": task.ne_name or "",
        "ne_ip": task.ne_ip or "",
        "vendor": task.vendor or "",
        "device_type": task.device_type or "",
        "command_count": len(flat),
        "commands": flat,
        "items": sections,
    }


def export_task_commands_text(
    db: Session,
    task_id: str,
    *,
    enabled_only: bool = True,
    include_aux: bool = True,
) -> str:
    """Plain-text export of planned collect commands (one CLI per line + section headers)."""
    plan = plan_task_collect_commands(
        db, task_id, enabled_only=enabled_only, include_aux=include_aux
    )
    lines = [
        "# biz-state collect commands",
        f"# task_id={plan['task_id']}",
        f"# ne={plan['ne_name'] or '-'} ({plan['ne_ip'] or '-'})",
        f"# vendor={plan['vendor'] or '-'} device_type={plan['device_type'] or '-'}",
        f"# command_count={plan['command_count']}",
        f"# include_aux={'1' if include_aux else '0'}",
        f"# enabled_only={'1' if enabled_only else '0'}",
        "",
    ]
    for sec in plan["items"]:
        title = str(sec.get("title") or sec.get("source_profile_id") or "item").strip()
        pid = str(sec.get("source_profile_id") or "").strip()
        header = f"## {title}"
        if pid and pid not in title:
            header = f"## {title} · {pid}"
        lines.append(header)
        for note in sec.get("notes") or []:
            lines.append(f"# note: {note}")
        cmds = list(sec.get("commands") or [])
        if not cmds and not (sec.get("notes") or []):
            lines.append("# (no commands)")
        for c in cmds:
            role = str(c.get("role") or "primary")
            cmd = str(c.get("command") or "").strip()
            if not cmd:
                continue
            if role == "aux":
                lines.append(f"# aux:{c.get('aux_key') or ''}")
                lines.append(cmd)
            elif role == "template":
                # No concrete CLI — keep template as a # comment line
                lines.append(f"# {cmd}")
            else:
                lines.append(cmd)
        lines.append("")
    # Flat unique list at end for easy copy into scripts
    lines.append("# ---- flat unique commands ----")
    for cmd in plan["commands"]:
        lines.append(str(cmd))
    lines.append("")
    return "\n".join(lines)
