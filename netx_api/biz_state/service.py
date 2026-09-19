"""biz_state service: tasks, profiles, batches, export."""

from __future__ import annotations

import io
import zipfile
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
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
    BizStateVrfRouteSummary,
    ManagedNE,
)
from ..timeutil import utcnow_naive
from .command_match import preview_task_item
from .profiles import all_profiles, get_profile, profile_to_public_dict, profiles_for_vendor
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


def update_task(db: Session, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
    task = db.get(BizStateTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task_not_found")
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


def get_batch(db: Session, batch_id: str) -> dict[str, Any]:
    b = db.get(BizStateBatch, batch_id)
    if not b:
        raise HTTPException(status_code=404, detail="batch_not_found")
    cmds = (
        db.query(BizStateBatchCommand)
        .filter(BizStateBatchCommand.batch_id == batch_id)
        .order_by(BizStateBatchCommand.created_at.asc())
        .all()
    )
    neighbors = (
        db.query(BizStateLldpNeighbor)
        .filter(BizStateLldpNeighbor.batch_id == batch_id)
        .order_by(BizStateLldpNeighbor.local_if.asc())
        .limit(5000)
        .all()
    )
    vrf_rows = (
        db.query(BizStateVrfRouteSummary)
        .filter(BizStateVrfRouteSummary.batch_id == batch_id)
        .order_by(BizStateVrfRouteSummary.vrf.asc(), BizStateVrfRouteSummary.source.asc())
        .limit(5000)
        .all()
    )
    metric_rows = (
        db.query(BizStateMetricRow)
        .filter(BizStateMetricRow.batch_id == batch_id)
        .order_by(
            BizStateMetricRow.metric_id.asc(),
            BizStateMetricRow.seq.asc(),
            BizStateMetricRow.id.asc(),
        )
        .limit(20000)
        .all()
    )
    metrics_by_id: dict[str, list[dict[str, Any]]] = {}
    for r in metric_rows:
        mid = str(r.metric_id or "")
        metrics_by_id.setdefault(mid, []).append(dict(r.data_json or {}))
    protect = batch_protect_info(db, batch_id)
    return {
        "id": b.id,
        "task_id": b.task_id,
        "status": b.status,
        "command_count": b.command_count,
        "row_count": b.row_count,
        "message": b.message,
        "started_at": b.started_at.isoformat() + "Z" if b.started_at else None,
        "ended_at": b.ended_at.isoformat() + "Z" if b.ended_at else None,
        "is_baseline": bool(getattr(b, "is_baseline", False)),
        "baseline_marked_at": b.baseline_marked_at.isoformat() + "Z"
        if getattr(b, "baseline_marked_at", None)
        else None,
        "protected": bool(protect.get("protected")),
        "protect_reasons": list(protect.get("reasons") or []),
        "commands": [
            {
                "id": c.id,
                "profile_id": c.profile_id,
                "parser_id": c.parser_id,
                "metric_id": c.metric_id,
                "raw_command": c.raw_command,
                "params": c.params_json or {},
                "parse_status": c.parse_status,
                "row_count": c.row_count,
                "message": c.message,
                "raw_text_preview": (c.raw_text or "")[:2000],
            }
            for c in cmds
        ],
        "lldp_neighbors": [
            {
                "local_if": n.local_if,
                "remote_sys": n.remote_sys,
                "remote_if": n.remote_if,
                "remote_ip": n.remote_ip,
                "protocol": n.protocol,
            }
            for n in neighbors
        ],
        "vrf_route_summary": [
            {"vrf": r.vrf, "source": r.source, "networks": r.networks} for r in vrf_rows
        ],
        "metrics": metrics_by_id,
    }


def export_batch_zip(db: Session, batch_id: str) -> bytes:
    detail = get_batch(db, batch_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # manifest
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
            zf.writestr(f"raw/{c['id']}_{safe}.txt", c.get("raw_text_preview") or "")
            # full raw from DB
            row = db.get(BizStateBatchCommand, c["id"])
            if row and row.raw_text:
                zf.writestr(f"raw/{c['id']}_{safe}.full.txt", row.raw_text)

        # CSV
        csv_lines = ["local_if,remote_sys,remote_if,remote_ip,protocol"]
        for n in detail["lldp_neighbors"]:
            csv_lines.append(
                ",".join(
                    [
                        _csv(n["local_if"]),
                        _csv(n["remote_sys"]),
                        _csv(n["remote_if"]),
                        _csv(n["remote_ip"]),
                        _csv(n["protocol"]),
                    ]
                )
            )
        zf.writestr("tables/lldp_neighbor.csv", "\n".join(csv_lines) + "\n")

        vrf_csv = ["vrf,source,networks"]
        for r in detail.get("vrf_route_summary") or []:
            vrf_csv.append(
                ",".join([_csv(r["vrf"]), _csv(r["source"]), _csv(str(r["networks"]))])
            )
        zf.writestr("tables/vrf_route_summary.csv", "\n".join(vrf_csv) + "\n")

        for mid, rows in sorted((detail.get("metrics") or {}).items()):
            if not rows:
                continue
            cols: list[str] = []
            for rec in rows:
                for k in rec.keys():
                    if k not in cols:
                        cols.append(str(k))
            lines = [",".join(_csv(c) for c in cols)]
            for rec in rows:
                lines.append(",".join(_csv(str(rec.get(c, "") or "")) for c in cols))
            safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in mid)[:80] or "metric"
            zf.writestr(f"tables/{safe}.csv", "\n".join(lines) + "\n")
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
