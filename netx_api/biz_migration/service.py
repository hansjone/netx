"""CRUD + evaluate for cutover migration monitor."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..biz_state.compare_rules import apply_row_filters
from ..biz_state.compare_service import _load_metric_rows, _port_map_dict, template_metrics
from ..models import (
    BizCompareTemplate,
    BizMigrationBatch,
    BizMigrationDiff,
    BizMigrationProject,
    BizMigrationRedTicket,
    BizMigrationRun,
    BizMonitorTemplate,
    BizPortMapping,
    BizStateBatch,
    BizStateTask,
)
from ..timeutil import utcnow_naive
from . import monitor_templates as mon_tpl
from .evaluate import (
    PORT_METRIC_ID,
    evaluate_metric_dual,
    override_for_metric,
    parse_expect_set,
    port_sheet_def,
)


def _task_brief(db: Session, task_id: str) -> dict[str, Any]:
    t = db.get(BizStateTask, task_id) if task_id else None
    if not t:
        return {"id": task_id or "", "ne_name": "", "ne_ip": "", "vendor": ""}
    return {
        "id": t.id,
        "ne_name": t.ne_name,
        "ne_ip": t.ne_ip,
        "vendor": t.vendor,
        "status": t.status,
        "note": t.note,
        "interval_sec": t.interval_sec,
        "collect_running": bool(t.collect_running),
    }


def _batch_brief(db: Session, batch_id: str) -> dict[str, Any]:
    b = db.get(BizStateBatch, batch_id) if batch_id else None
    if not b:
        return {"id": batch_id or "", "status": "", "started_at": None}
    return {
        "id": b.id,
        "status": b.status,
        "started_at": b.started_at.isoformat() if b.started_at else None,
        "row_count": b.row_count,
    }


def _monitor_template_brief(db: Session, template_id: str) -> dict[str, Any]:
    tid = str(template_id or "").strip()
    if not tid:
        return {"id": "", "name": "", "compare_template_id": "", "compare_template_name": ""}
    row = db.get(BizMonitorTemplate, tid)
    if not row:
        return {"id": tid, "name": "", "compare_template_id": "", "compare_template_name": ""}
    cmp_name = ""
    if row.compare_template_id:
        ct = db.get(BizCompareTemplate, row.compare_template_id)
        cmp_name = (ct.name if ct else "") or ""
    return {
        "id": row.id,
        "name": row.name or "",
        "compare_template_id": row.compare_template_id or "",
        "compare_template_name": cmp_name,
        "collect_metric_ids": list(row.collect_metric_ids_json or []),
    }


def resolve_project_monitor_template(db: Session, proj: BizMigrationProject) -> BizMonitorTemplate:
    """Return monitor template for project; seed default port template if unbound."""
    mon_tpl.ensure_default_monitor_templates(db)
    tid = str(getattr(proj, "monitor_template_id", None) or "").strip()
    row = db.get(BizMonitorTemplate, tid) if tid else None
    if row:
        return row
    default_id = mon_tpl.default_port_monitor_template_id(db)
    row = db.get(BizMonitorTemplate, default_id) if default_id else None
    if not row:
        raise HTTPException(status_code=400, detail="monitor_template_required")
    # Persist default on first use so UI shows binding
    if not tid:
        proj.monitor_template_id = row.id
        proj.updated_at = utcnow_naive()
        db.commit()
        db.refresh(proj)
    return row


def resolve_evaluate_sheets(
    db: Session, mt: BizMonitorTemplate
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Return (sheets, sheet_overrides, defaults) from monitor → compare template."""
    overrides = list(mt.sheet_overrides_json or []) if isinstance(mt.sheet_overrides_json, list) else []
    defaults = dict(mt.defaults_json or {}) if isinstance(mt.defaults_json, dict) else {}
    cid = str(mt.compare_template_id or "").strip()
    if cid:
        ct = db.get(BizCompareTemplate, cid)
        if ct:
            sheets = template_metrics(ct)
            if sheets:
                return sheets, overrides, defaults
    # Fallback: built-in port sheet (legacy)
    return [port_sheet_def()], overrides, defaults


def resolve_collect_metric_ids(db: Session, proj: BizMigrationProject) -> list[str]:
    mt = resolve_project_monitor_template(db, proj)
    collect = [str(x).strip() for x in (mt.collect_metric_ids_json or []) if str(x).strip()]
    if collect:
        return collect
    sheets, _, _ = resolve_evaluate_sheets(db, mt)
    return [str(s.get("metric_id") or "").strip() for s in sheets if str(s.get("metric_id") or "").strip()]


def project_to_dict(db: Session, p: BizMigrationProject) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "old_task_id": p.old_task_id,
        "new_task_id": p.new_task_id,
        "old_baseline_batch_id": p.old_baseline_batch_id,
        "new_baseline_batch_id": p.new_baseline_batch_id,
        "mapping_id": p.mapping_id,
        "monitor_template_id": getattr(p, "monitor_template_id", None) or "",
        "monitor_template": _monitor_template_brief(db, getattr(p, "monitor_template_id", None) or ""),
        "status": p.status,
        "note": p.note,
        "old_task": _task_brief(db, p.old_task_id),
        "new_task": _task_brief(db, p.new_task_id),
        "old_baseline": _batch_brief(db, p.old_baseline_batch_id),
        "new_baseline": _batch_brief(db, p.new_baseline_batch_id),
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def batch_to_dict(b: BizMigrationBatch) -> dict[str, Any]:
    return {
        "id": b.id,
        "project_id": b.project_id,
        "batch_label": b.batch_label,
        "status": b.status,
        "expect_set": dict(b.expect_set_json or {}),
        "started_at": b.started_at.isoformat() if b.started_at else None,
        "ended_at": b.ended_at.isoformat() if b.ended_at else None,
        "accept_status": getattr(b, "accept_status", None) or "none",
        "accept_run_id": getattr(b, "accept_run_id", None) or "",
        "accept_summary": dict(getattr(b, "accept_summary_json", None) or {}),
        "note": b.note,
        "created_at": b.created_at.isoformat() if b.created_at else None,
        "updated_at": b.updated_at.isoformat() if b.updated_at else None,
    }


def list_projects(db: Session) -> list[dict[str, Any]]:
    rows = db.query(BizMigrationProject).order_by(BizMigrationProject.created_at.desc()).all()
    return [project_to_dict(db, p) for p in rows]


def create_project(db: Session, body: dict[str, Any]) -> dict[str, Any]:
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name_required")
    old_task_id = str(body.get("old_task_id") or "").strip()
    new_task_id = str(body.get("new_task_id") or "").strip()
    if not old_task_id or not new_task_id:
        raise HTTPException(status_code=400, detail="old_new_task_required")
    if not db.get(BizStateTask, old_task_id) or not db.get(BizStateTask, new_task_id):
        raise HTTPException(status_code=404, detail="task_not_found")
    mapping_id = str(body.get("mapping_id") or "").strip()
    if mapping_id and not db.get(BizPortMapping, mapping_id):
        raise HTTPException(status_code=404, detail="mapping_not_found")
    monitor_template_id = str(body.get("monitor_template_id") or "").strip()
    if monitor_template_id:
        if not db.get(BizMonitorTemplate, monitor_template_id):
            raise HTTPException(status_code=404, detail="monitor_template_not_found")
    else:
        monitor_template_id = mon_tpl.default_port_monitor_template_id(db)
    p = BizMigrationProject(
        id=uuid4().hex,
        name=name,
        old_task_id=old_task_id,
        new_task_id=new_task_id,
        old_baseline_batch_id=str(body.get("old_baseline_batch_id") or "").strip(),
        new_baseline_batch_id=str(body.get("new_baseline_batch_id") or "").strip(),
        mapping_id=mapping_id,
        monitor_template_id=monitor_template_id,
        status=str(body.get("status") or "draft").strip() or "draft",
        note=str(body.get("note") or "")[:500],
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return project_to_dict(db, p)


def get_project(db: Session, project_id: str) -> dict[str, Any]:
    p = db.get(BizMigrationProject, project_id)
    if not p:
        raise HTTPException(status_code=404, detail="project_not_found")
    return project_to_dict(db, p)


def patch_project(db: Session, project_id: str, body: dict[str, Any]) -> dict[str, Any]:
    p = db.get(BizMigrationProject, project_id)
    if not p:
        raise HTTPException(status_code=404, detail="project_not_found")
    if "name" in body and body["name"] is not None:
        p.name = str(body["name"]).strip() or p.name
    if "note" in body and body["note"] is not None:
        p.note = str(body["note"])[:500]
    if "status" in body and body["status"] is not None:
        p.status = str(body["status"]).strip() or p.status
    if "mapping_id" in body and body["mapping_id"] is not None:
        mid = str(body["mapping_id"] or "").strip()
        if mid and not db.get(BizPortMapping, mid):
            raise HTTPException(status_code=404, detail="mapping_not_found")
        p.mapping_id = mid
    if "old_baseline_batch_id" in body and body["old_baseline_batch_id"] is not None:
        bid = str(body["old_baseline_batch_id"] or "").strip()
        if bid and not db.get(BizStateBatch, bid):
            raise HTTPException(status_code=404, detail="batch_not_found")
        p.old_baseline_batch_id = bid
    if "new_baseline_batch_id" in body and body["new_baseline_batch_id"] is not None:
        bid = str(body["new_baseline_batch_id"] or "").strip()
        if bid and not db.get(BizStateBatch, bid):
            raise HTTPException(status_code=404, detail="batch_not_found")
        p.new_baseline_batch_id = bid
    if "monitor_template_id" in body and body["monitor_template_id"] is not None:
        mid = str(body["monitor_template_id"] or "").strip()
        if mid and not db.get(BizMonitorTemplate, mid):
            raise HTTPException(status_code=404, detail="monitor_template_not_found")
        p.monitor_template_id = mid
    p.updated_at = utcnow_naive()
    db.commit()
    db.refresh(p)
    return project_to_dict(db, p)


def delete_project(db: Session, project_id: str) -> dict[str, Any]:
    p = db.get(BizMigrationProject, project_id)
    if not p:
        raise HTTPException(status_code=404, detail="project_not_found")
    batches = db.query(BizMigrationBatch).filter(BizMigrationBatch.project_id == project_id).all()
    for b in batches:
        runs = db.query(BizMigrationRun).filter(BizMigrationRun.batch_id == b.id).all()
        for r in runs:
            db.query(BizMigrationDiff).filter(BizMigrationDiff.run_id == r.id).delete()
            db.delete(r)
        db.delete(b)
    db.delete(p)
    db.commit()
    return {"ok": True}


def list_batches(db: Session, project_id: str) -> list[dict[str, Any]]:
    if not db.get(BizMigrationProject, project_id):
        raise HTTPException(status_code=404, detail="project_not_found")
    rows = (
        db.query(BizMigrationBatch)
        .filter(BizMigrationBatch.project_id == project_id)
        .order_by(BizMigrationBatch.created_at.desc())
        .all()
    )
    return [batch_to_dict(b) for b in rows]


def create_batch(db: Session, project_id: str, body: dict[str, Any]) -> dict[str, Any]:
    if not db.get(BizMigrationProject, project_id):
        raise HTTPException(status_code=404, detail="project_not_found")
    label = str(body.get("batch_label") or "").strip() or "batch"
    expect = body.get("expect_set") if isinstance(body.get("expect_set"), dict) else {}
    b = BizMigrationBatch(
        id=uuid4().hex,
        project_id=project_id,
        batch_label=label,
        status=str(body.get("status") or "pending").strip() or "pending",
        expect_set_json=dict(expect or {}),
        note=str(body.get("note") or "")[:500],
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    out = batch_to_dict(b)
    out["open_red_count"] = count_open_red_tickets(db, project_id)
    return out


def _latest_success_batch(db: Session, task_id: str) -> BizStateBatch | None:
    return (
        db.query(BizStateBatch)
        .filter(
            BizStateBatch.task_id == task_id,
            BizStateBatch.status.in_(("success", "partial")),
        )
        .order_by(BizStateBatch.started_at.desc())
        .first()
    )


def pinned_baseline_batch_ids(db: Session) -> set[str]:
    """Batch IDs that must survive retention purge."""
    ids: set[str] = set()
    for p in db.query(BizMigrationProject).all():
        if p.old_baseline_batch_id:
            ids.add(p.old_baseline_batch_id)
        if p.new_baseline_batch_id:
            ids.add(p.new_baseline_batch_id)
    return ids


def run_evaluate(
    db: Session,
    *,
    batch_id: str,
    old_batch_id: str = "",
    new_batch_id: str = "",
    acceptance: bool = False,
    purpose: str = "manual",
) -> dict[str, Any]:
    mb = db.get(BizMigrationBatch, batch_id)
    if not mb:
        raise HTTPException(status_code=404, detail="batch_not_found")
    proj = db.get(BizMigrationProject, mb.project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="project_not_found")
    if not proj.old_baseline_batch_id:
        raise HTTPException(status_code=400, detail="old_baseline_required")

    old_batch = db.get(BizStateBatch, old_batch_id.strip()) if old_batch_id.strip() else None
    if not old_batch:
        old_batch = _latest_success_batch(db, proj.old_task_id)
    new_batch = db.get(BizStateBatch, new_batch_id.strip()) if new_batch_id.strip() else None
    if not new_batch:
        new_batch = _latest_success_batch(db, proj.new_task_id)
    if not old_batch:
        raise HTTPException(status_code=400, detail="old_current_batch_required")
    if not new_batch:
        raise HTTPException(status_code=400, detail="new_current_batch_required")
    old_cur = old_batch.id
    new_cur = new_batch.id

    port_map = _port_map_dict(db, proj.mapping_id)
    expect = parse_expect_set(mb.expect_set_json if isinstance(mb.expect_set_json, dict) else {})
    # Final acceptance: window closed → unfinished expect = red
    window_active = (mb.status == "active") and (not acceptance)
    mt = resolve_project_monitor_template(db, proj)
    sheets, sheet_overrides, _defaults = resolve_evaluate_sheets(db, mt)

    sheet_cards: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    seq = 0
    verdict_counts: dict[str, int] = {}

    for sheet in sheets:
        mid = str(sheet.get("metric_id") or "").strip()
        key_fields = list(sheet.get("key_fields") or [])
        if not mid or not key_fields:
            continue
        iface_fields = list(sheet.get("iface_fields") or [])
        compare_fields = list(sheet.get("compare_fields") or [])
        row_filters = list(sheet.get("row_filters") or [])
        field_rules = list(sheet.get("field_rules") or [])
        sheet_ov = override_for_metric(sheet_overrides, mid)

        old_base = apply_row_filters(
            _load_metric_rows(db, batch_id=proj.old_baseline_batch_id, metric_id=mid),
            row_filters,
        )
        old_now = apply_row_filters(
            _load_metric_rows(db, batch_id=old_cur, metric_id=mid),
            row_filters,
        )
        new_base_rows = None
        if proj.new_baseline_batch_id:
            new_base_rows = apply_row_filters(
                _load_metric_rows(db, batch_id=proj.new_baseline_batch_id, metric_id=mid),
                row_filters,
            )
        new_now = apply_row_filters(
            _load_metric_rows(db, batch_id=new_cur, metric_id=mid),
            row_filters,
        )

        one = evaluate_metric_dual(
            metric_id=mid,
            key_fields=key_fields,
            iface_fields=iface_fields,
            compare_fields=compare_fields,
            old_baseline_rows=old_base,
            old_current_rows=old_now,
            new_baseline_rows=new_base_rows,
            new_current_rows=new_now,
            port_map=port_map if iface_fields else {},
            expect=expect,
            window_active=window_active,
            acceptance=acceptance,
            field_rules=field_rules,
            sheet_override=sheet_ov,
        )
        sheet_cards.append(
            {
                "metric_id": mid,
                "title": mid,
                "progress_ok": one["progress_ok"],
                "progress_total": one["progress_total"],
                "anomaly": one["anomaly"],
                "old_summary": one["old_summary"],
                "new_summary": one["new_summary"],
            }
        )
        for r in one["rows"]:
            r["seq"] = seq
            seq += 1
            all_rows.append(r)
            v = str(r.get("verdict") or "")
            if v:
                verdict_counts[v] = verdict_counts.get(v, 0) + 1

    run = BizMigrationRun(
        id=uuid4().hex,
        project_id=proj.id,
        batch_id=mb.id,
        old_batch_id=old_cur,
        new_batch_id=new_cur,
        purpose=str(purpose or ("acceptance" if acceptance else "manual"))[:32],
        status="success",
        summary_json={
            "metric_focus": sheets[0].get("metric_id") if sheets else PORT_METRIC_ID,
            "monitor_template_id": mt.id,
            "compare_template_id": mt.compare_template_id or "",
            "acceptance": acceptance,
            "sheet_cards": sheet_cards,
            "progress": {
                "ok": sum(c["progress_ok"] for c in sheet_cards),
                "total": sum(c["progress_total"] for c in sheet_cards),
            },
            "anomaly": sum(c["anomaly"] for c in sheet_cards),
            "verdict_counts": verdict_counts,
            "window_active": window_active,
            "expect_ports": sorted(expect.get("_ports") or ()),
        },
        message="",
    )
    db.add(run)
    db.flush()
    for r in all_rows:
        key_list = r.get("key") or []
        search = " ".join(
            [
                str(r.get("key_str") or ""),
                str(r.get("new_key_str") or ""),
                str(r.get("verdict") or ""),
                str(r.get("old_status") or ""),
                str(r.get("new_status") or ""),
                str(r.get("metric_id") or ""),
            ]
        )
        db.add(
            BizMigrationDiff(
                id=uuid4().hex,
                run_id=run.id,
                metric_id=str(r.get("metric_id") or ""),
                seq=int(r.get("seq") or 0),
                verdict=str(r.get("verdict") or ""),
                color=str(r.get("color") or ""),
                key_json={
                    "key": key_list,
                    "key_str": r.get("key_str"),
                    "new_key_str": r.get("new_key_str"),
                    "old_status": r.get("old_status"),
                    "new_status": r.get("new_status"),
                },
                old_kind=str(r.get("old_kind") or ""),
                new_kind=str(r.get("new_kind") or ""),
                old_json=dict(r.get("old") or {}),
                new_json=dict(r.get("new") or {}),
                in_expect=bool(r.get("in_expect")),
                search_text=search[:2000],
            )
        )
    db.commit()
    db.refresh(run)
    return run_to_dict(db, run)


def run_to_dict(db: Session, run: BizMigrationRun, *, include_diffs: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": run.id,
        "project_id": run.project_id,
        "batch_id": run.batch_id,
        "old_batch_id": run.old_batch_id,
        "new_batch_id": run.new_batch_id,
        "purpose": getattr(run, "purpose", None) or "manual",
        "status": run.status,
        "summary": dict(run.summary_json or {}),
        "message": run.message,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "old_batch": _batch_brief(db, run.old_batch_id),
        "new_batch": _batch_brief(db, run.new_batch_id),
    }
    if include_diffs:
        diffs = (
            db.query(BizMigrationDiff)
            .filter(BizMigrationDiff.run_id == run.id)
            .order_by(BizMigrationDiff.seq.asc())
            .limit(5000)
            .all()
        )
        out["diffs"] = [diff_to_dict(d) for d in diffs]
    return out


def diff_to_dict(d: BizMigrationDiff) -> dict[str, Any]:
    kj = d.key_json if isinstance(d.key_json, dict) else {}
    return {
        "id": d.id,
        "metric_id": d.metric_id,
        "seq": d.seq,
        "verdict": d.verdict,
        "color": d.color,
        "key": kj,
        "key_str": kj.get("key_str") or "",
        "new_key_str": kj.get("new_key_str") or "",
        "old_status": kj.get("old_status") or "",
        "new_status": kj.get("new_status") or "",
        "old_kind": d.old_kind,
        "new_kind": d.new_kind,
        "old": d.old_json,
        "new": d.new_json,
        "in_expect": d.in_expect,
    }


def list_baseline_ports(db: Session, project_id: str) -> dict[str, Any]:
    """List interface names from project old baseline for expect-set picking."""
    p = db.get(BizMigrationProject, project_id)
    if not p:
        raise HTTPException(status_code=404, detail="project_not_found")
    if not p.old_baseline_batch_id:
        return {"batch_id": "", "ports": [], "mapped": {}}
    rows = _load_metric_rows(
        db, batch_id=p.old_baseline_batch_id, metric_id=PORT_METRIC_ID
    )
    port_map = _port_map_dict(db, p.mapping_id)
    ports: list[dict[str, Any]] = []
    for r in rows:
        name = str(r.get("interface") or "").strip()
        if not name:
            continue
        ports.append(
            {
                "interface": name,
                "admin": r.get("admin") or "",
                "phy": r.get("phy") or "",
                "prot": r.get("prot") or "",
                "description": r.get("description") or "",
                "mapped_to": port_map.get(name) or "",
            }
        )
    ports.sort(key=lambda x: str(x["interface"]))
    return {
        "batch_id": p.old_baseline_batch_id,
        "metric_id": PORT_METRIC_ID,
        "ports": ports,
        "mapped": port_map,
    }


def list_baseline_expect_objects(db: Session, project_id: str) -> dict[str, Any]:
    """Per-sheet baseline keys for multi-metric expect picking."""
    p = db.get(BizMigrationProject, project_id)
    if not p:
        raise HTTPException(status_code=404, detail="project_not_found")
    if not p.old_baseline_batch_id:
        return {"batch_id": "", "sheets": [], "mapped": {}}
    mt = resolve_project_monitor_template(db, p)
    sheets, _, _ = resolve_evaluate_sheets(db, mt)
    port_map = _port_map_dict(db, p.mapping_id)
    out_sheets: list[dict[str, Any]] = []
    for sheet in sheets:
        mid = str(sheet.get("metric_id") or "").strip()
        key_fields = [str(k) for k in (sheet.get("key_fields") or []) if str(k).strip()]
        if not mid or not key_fields:
            continue
        iface_fields = [str(k) for k in (sheet.get("iface_fields") or []) if str(k).strip()]
        rows_raw = apply_row_filters(
            _load_metric_rows(db, batch_id=p.old_baseline_batch_id, metric_id=mid),
            list(sheet.get("row_filters") or []),
        )
        items: list[dict[str, Any]] = []
        for r in rows_raw:
            keys = [str(r.get(f) or "").strip() for f in key_fields]
            if not any(keys):
                continue
            key_str = "|".join(keys)
            mapped = ""
            if iface_fields and key_fields and key_fields[0] in iface_fields:
                mapped = port_map.get(keys[0]) or ""
            items.append(
                {
                    "key": key_str,
                    "keys": keys,
                    "mapped_to": mapped,
                    "label": key_str,
                    "row": {f: r.get(f) for f in list(dict.fromkeys([*key_fields, *iface_fields, "description", "admin", "phy", "prot"])) if f in r},
                }
            )
        items.sort(key=lambda x: str(x["key"]))
        out_sheets.append(
            {
                "metric_id": mid,
                "key_fields": key_fields,
                "iface_fields": iface_fields,
                "items": items,
            }
        )
    return {
        "batch_id": p.old_baseline_batch_id,
        "monitor_template_id": mt.id,
        "sheets": out_sheets,
        "mapped": port_map,
    }


def _catalog_item_for_metric(*, vendor: str, device_type: str, metric_id: str) -> dict[str, Any]:
    from ..biz_state.profiles import profiles_for_vendor
    from ..lldp_shared import resolve_vendor_key

    mid = str(metric_id or "").strip()
    vkey = resolve_vendor_key(vendor, device_type)
    for p in profiles_for_vendor(vkey):
        if p.metric_id == mid and p.kind == "collect":
            return {
                "source_profile_id": p.profile_id,
                "kind": "catalog",
                "enabled": True,
                "title": p.title or mid,
            }
    raise HTTPException(
        status_code=400,
        detail=f"no_profile_for_metric:{mid}:{vkey or vendor or 'unknown'}",
    )


def _iface_brief_item(*, vendor: str, device_type: str) -> dict[str, Any]:
    return _catalog_item_for_metric(vendor=vendor, device_type=device_type, metric_id=PORT_METRIC_ID)


def _enabled_metric_ids(db: Session, task_id: str) -> set[str]:
    from ..biz_state.profiles import get_profile
    from ..models import BizStateTaskItem

    out: set[str] = set()
    items = (
        db.query(BizStateTaskItem)
        .filter(BizStateTaskItem.task_id == task_id, BizStateTaskItem.enabled.is_(True))
        .all()
    )
    for it in items:
        if it.kind == "custom_raw":
            out.add("__custom__")
            continue
        p = get_profile(str(it.source_profile_id or ""))
        if p and p.metric_id:
            out.add(p.metric_id)
    return out


def _is_highfreq_task(db: Session, task: BizStateTask, want_metrics: set[str]) -> bool:
    """True when task metrics match want set and interval is short (cutover HF)."""
    metrics = _enabled_metric_ids(db, task.id)
    return metrics == set(want_metrics) and int(task.interval_sec or 0) <= 300


def _is_port_highfreq_task(db: Session, task: BizStateTask) -> bool:
    """Back-compat: interface_brief-only HF."""
    return _is_highfreq_task(db, task, {PORT_METRIC_ID})


def _ensure_side_highfreq(
    db: Session,
    *,
    template: BizStateTask,
    project_name: str,
    interval_sec: int,
    retention_days: int,
    metric_ids: list[str],
) -> tuple[BizStateTask, bool]:
    """Return (task, created). Reuse if already HF for metric set; else create sibling."""
    from ..biz_state import service as biz_svc

    want = {str(m).strip() for m in metric_ids if str(m).strip()}
    if not want:
        want = {PORT_METRIC_ID}

    if _is_highfreq_task(db, template, want):
        if template.status != "running":
            biz_svc.update_task(db, template.id, {"status": "running"})
            refreshed = db.get(BizStateTask, template.id)
            return refreshed or template, False
        return template, False

    items = [
        _catalog_item_for_metric(
            vendor=template.vendor,
            device_type=template.device_type,
            metric_id=mid,
        )
        for mid in sorted(want)
    ]
    note = f"割接高频/{'+'.join(sorted(want)[:3])}/{project_name}"[:256]
    created = biz_svc.create_task(
        db,
        {
            "source": template.source,
            "ne_id": template.ne_id,
            "ne_name": template.ne_name,
            "ne_ip": template.ne_ip,
            "vendor": template.vendor,
            "device_type": template.device_type,
            "note": note,
            "status": "running",
            "interval_sec": interval_sec,
            "retention_days": retention_days,
            "items": items,
        },
    )
    task = db.get(BizStateTask, str(created.get("id") or ""))
    if not task:
        raise HTTPException(status_code=500, detail="highfreq_task_create_failed")
    return task, True


def ensure_port_highfreq(
    db: Session,
    project_id: str,
    *,
    interval_sec: int = 60,
    retention_days: int = 7,
    collect_now: bool = True,
) -> dict[str, Any]:
    """Create/bind high-freq biz_state tasks for old/new NEs using monitor collect_metric_ids."""
    return ensure_highfreq(
        db,
        project_id,
        interval_sec=interval_sec,
        retention_days=retention_days,
        collect_now=collect_now,
    )


def ensure_highfreq(
    db: Session,
    project_id: str,
    *,
    interval_sec: int = 60,
    retention_days: int = 7,
    collect_now: bool = True,
) -> dict[str, Any]:
    """Create/bind HF collect tasks from project's monitor template collect_metric_ids."""
    from ..biz_state.collect_runner import dispatch_collect

    proj = db.get(BizMigrationProject, project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="project_not_found")
    old_tpl = db.get(BizStateTask, proj.old_task_id) if proj.old_task_id else None
    new_tpl = db.get(BizStateTask, proj.new_task_id) if proj.new_task_id else None
    if not old_tpl or not new_tpl:
        raise HTTPException(status_code=400, detail="old_new_task_required")

    metric_ids = resolve_collect_metric_ids(db, proj)
    iv = max(60, int(interval_sec or 60))
    ret = max(1, int(retention_days or 7))
    old_task, old_created = _ensure_side_highfreq(
        db,
        template=old_tpl,
        project_name=proj.name,
        interval_sec=iv,
        retention_days=ret,
        metric_ids=metric_ids,
    )
    new_tpl = db.get(BizStateTask, proj.new_task_id)
    if not new_tpl:
        raise HTTPException(status_code=400, detail="new_task_required")
    new_task, new_created = _ensure_side_highfreq(
        db,
        template=new_tpl,
        project_name=proj.name,
        interval_sec=iv,
        retention_days=ret,
        metric_ids=metric_ids,
    )

    proj = db.get(BizMigrationProject, project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="project_not_found")
    proj.old_task_id = old_task.id
    proj.new_task_id = new_task.id
    proj.updated_at = utcnow_naive()
    db.commit()

    collect: dict[str, Any] = {"old": None, "new": None}
    if collect_now:
        for side, tid in (("old", old_task.id), ("new", new_task.id)):
            try:
                dispatch_collect(tid)
                collect[side] = {"ok": True, "task_id": tid}
            except Exception as exc:  # noqa: BLE001
                collect[side] = {"ok": False, "task_id": tid, "error": str(exc)[:200]}

    return {
        "project": project_to_dict(db, proj),
        "old_task": _task_brief(db, old_task.id),
        "new_task": _task_brief(db, new_task.id),
        "old_created": old_created,
        "new_created": new_created,
        "interval_sec": iv,
        "collect_metric_ids": metric_ids,
        "collect": collect,
    }


def collect_project_now(db: Session, project_id: str) -> dict[str, Any]:
    """Trigger immediate collect on project's old/new biz_state tasks."""
    from ..biz_state.collect_runner import dispatch_collect

    proj = db.get(BizMigrationProject, project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="project_not_found")
    out: dict[str, Any] = {"old": None, "new": None}
    for side, tid in (("old", proj.old_task_id), ("new", proj.new_task_id)):
        if not tid:
            out[side] = {"ok": False, "error": "task_missing"}
            continue
        task = db.get(BizStateTask, tid)
        if not task:
            out[side] = {"ok": False, "error": "task_not_found"}
            continue
        if bool(task.collect_running):
            out[side] = {"ok": False, "error": "collect_already_running", "task_id": tid}
            continue
        try:
            dispatch_collect(tid)
            out[side] = {"ok": True, "task_id": tid}
        except Exception as exc:  # noqa: BLE001
            out[side] = {"ok": False, "task_id": tid, "error": str(exc)[:200]}
    return out


def _red_ticket_to_dict(t: BizMigrationRedTicket) -> dict[str, Any]:
    return {
        "id": t.id,
        "project_id": t.project_id,
        "batch_id": t.batch_id,
        "run_id": t.run_id,
        "metric_id": t.metric_id,
        "key_str": t.key_str,
        "new_key_str": t.new_key_str,
        "verdict": t.verdict,
        "color": t.color,
        "old_status": t.old_status,
        "new_status": t.new_status,
        "detail": dict(t.detail_json or {}),
        "status": t.status,
        "carried_to_batch_id": t.carried_to_batch_id,
        "note": t.note,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
    }


def count_open_red_tickets(db: Session, project_id: str) -> int:
    return (
        db.query(BizMigrationRedTicket)
        .filter(
            BizMigrationRedTicket.project_id == project_id,
            BizMigrationRedTicket.status.in_(("open", "carried")),
        )
        .count()
    )


def list_red_tickets(
    db: Session,
    project_id: str,
    *,
    status: str = "",
    limit: int = 200,
) -> dict[str, Any]:
    if not db.get(BizMigrationProject, project_id):
        raise HTTPException(status_code=404, detail="project_not_found")
    q = db.query(BizMigrationRedTicket).filter(BizMigrationRedTicket.project_id == project_id)
    if status.strip():
        q = q.filter(BizMigrationRedTicket.status == status.strip())
    rows = (
        q.order_by(BizMigrationRedTicket.created_at.desc())
        .limit(min(500, max(1, limit)))
        .all()
    )
    return {
        "open_count": count_open_red_tickets(db, project_id),
        "items": [_red_ticket_to_dict(t) for t in rows],
    }


def resolve_red_ticket(db: Session, ticket_id: str, *, note: str = "") -> dict[str, Any]:
    t = db.get(BizMigrationRedTicket, ticket_id)
    if not t:
        raise HTTPException(status_code=404, detail="red_ticket_not_found")
    t.status = "resolved"
    t.resolved_at = utcnow_naive()
    if note:
        t.note = str(note)[:500]
    db.commit()
    db.refresh(t)
    return _red_ticket_to_dict(t)


def _persist_red_tickets_from_run(
    db: Session,
    *,
    project_id: str,
    batch_id: str,
    run_id: str,
) -> list[BizMigrationRedTicket]:
    """Create open red tickets from acceptance run red diffs (expect + anomaly)."""
    diffs = (
        db.query(BizMigrationDiff)
        .filter(BizMigrationDiff.run_id == run_id, BizMigrationDiff.color == "red")
        .order_by(BizMigrationDiff.seq.asc())
        .all()
    )
    created: list[BizMigrationRedTicket] = []
    for d in diffs:
        kj = d.key_json if isinstance(d.key_json, dict) else {}
        t = BizMigrationRedTicket(
            id=uuid4().hex,
            project_id=project_id,
            batch_id=batch_id,
            run_id=run_id,
            metric_id=d.metric_id,
            key_str=str(kj.get("key_str") or "")[:256],
            new_key_str=str(kj.get("new_key_str") or "")[:256],
            verdict=d.verdict,
            color=d.color or "red",
            old_status=str(kj.get("old_status") or "")[:64],
            new_status=str(kj.get("new_status") or "")[:64],
            detail_json={
                "old_kind": d.old_kind,
                "new_kind": d.new_kind,
                "in_expect": d.in_expect,
                "old": d.old_json,
                "new": d.new_json,
            },
            status="open",
        )
        db.add(t)
        created.append(t)
    return created


def finish_batch(
    db: Session,
    batch_id: str,
    *,
    mark_done: bool = False,
) -> dict[str, Any]:
    """本批完成：关窗 → 终验 → 验收小结 → 红单留痕（不硬卡下一批）."""
    mb = db.get(BizMigrationBatch, batch_id)
    if not mb:
        raise HTTPException(status_code=404, detail="batch_not_found")
    if mb.status not in ("active", "review"):
        raise HTTPException(
            status_code=400,
            detail=f"batch_not_finishable:{mb.status}",
        )

    # Close window first so acceptance uses non-active rules
    if mb.status == "active":
        mb.ended_at = utcnow_naive()
    mb.status = "done" if mark_done else "review"
    mb.updated_at = utcnow_naive()
    db.commit()

    run = run_evaluate(db, batch_id=batch_id, acceptance=True, purpose="acceptance")
    summary = dict(run.get("summary") or {})
    progress = dict(summary.get("progress") or {})
    anomaly = int(summary.get("anomaly") or 0)
    ok = int(progress.get("ok") or 0)
    total = int(progress.get("total") or 0)
    passed = anomaly == 0 and (total == 0 or ok >= total)

    accept_summary = {
        "passed": passed,
        "progress_ok": ok,
        "progress_total": total,
        "anomaly": anomaly,
        "verdict_counts": dict(summary.get("verdict_counts") or {}),
        "expect_ports": list(summary.get("expect_ports") or []),
        "run_id": run.get("id"),
        "old_batch_id": run.get("old_batch_id"),
        "new_batch_id": run.get("new_batch_id"),
        "finished_at": utcnow_naive().isoformat(),
    }

    mb = db.get(BizMigrationBatch, batch_id)
    if not mb:
        raise HTTPException(status_code=404, detail="batch_not_found")
    mb.accept_run_id = str(run.get("id") or "")
    mb.accept_status = "passed" if passed else "failed"
    mb.accept_summary_json = accept_summary
    mb.updated_at = utcnow_naive()

    # Replace prior open tickets from this batch's previous acceptance (re-finish)
    db.query(BizMigrationRedTicket).filter(
        BizMigrationRedTicket.batch_id == batch_id,
        BizMigrationRedTicket.status == "open",
    ).delete()
    reds = _persist_red_tickets_from_run(
        db,
        project_id=mb.project_id,
        batch_id=batch_id,
        run_id=str(run.get("id") or ""),
    )
    db.commit()
    db.refresh(mb)

    return {
        "batch": batch_to_dict(mb),
        "run": run,
        "accept_summary": accept_summary,
        "red_tickets": [_red_ticket_to_dict(t) for t in reds],
        "open_red_count": count_open_red_tickets(db, mb.project_id),
        "can_continue": True,  # 带红继续：永不硬卡
    }


def patch_batch(db: Session, batch_id: str, body: dict[str, Any]) -> dict[str, Any]:
    b = db.get(BizMigrationBatch, batch_id)
    if not b:
        raise HTTPException(status_code=404, detail="batch_not_found")
    if "batch_label" in body and body["batch_label"] is not None:
        b.batch_label = str(body["batch_label"]).strip() or b.batch_label
    if "note" in body and body["note"] is not None:
        b.note = str(body["note"])[:500]
    if "expect_set" in body and isinstance(body["expect_set"], dict):
        b.expect_set_json = dict(body["expect_set"])
    if "status" in body and body["status"] is not None:
        st = str(body["status"]).strip()
        # Prefer finish_batch for review/done from active (auto acceptance)
        if st in ("review", "done") and b.status == "active":
            return finish_batch(db, batch_id, mark_done=(st == "done"))["batch"]
        prev = b.status
        b.status = st or b.status
        if st == "active" and prev != "active":
            b.started_at = utcnow_naive()
            # 带红：标记既有 open 红单为 carried（不关闭）
            open_reds = (
                db.query(BizMigrationRedTicket)
                .filter(
                    BizMigrationRedTicket.project_id == b.project_id,
                    BizMigrationRedTicket.status == "open",
                )
                .all()
            )
            for t in open_reds:
                t.status = "carried"
                t.carried_to_batch_id = b.id
    b.updated_at = utcnow_naive()
    db.commit()
    db.refresh(b)
    return batch_to_dict(b)


def get_run(db: Session, run_id: str) -> dict[str, Any]:
    run = db.get(BizMigrationRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run_not_found")
    return run_to_dict(db, run, include_diffs=False)


def list_run_diffs(
    db: Session,
    run_id: str,
    *,
    metric_id: str = "",
    verdict: str = "",
    color: str = "",
    kw: str = "",
    offset: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    if not db.get(BizMigrationRun, run_id):
        raise HTTPException(status_code=404, detail="run_not_found")
    q = db.query(BizMigrationDiff).filter(BizMigrationDiff.run_id == run_id)
    if metric_id:
        q = q.filter(BizMigrationDiff.metric_id == metric_id)
    if verdict:
        q = q.filter(BizMigrationDiff.verdict == verdict)
    if color:
        q = q.filter(BizMigrationDiff.color == color)
    if kw.strip():
        like = f"%{kw.strip()}%"
        q = q.filter(BizMigrationDiff.search_text.ilike(like))
    total = q.count()
    rows = q.order_by(BizMigrationDiff.seq.asc()).offset(max(0, offset)).limit(min(500, max(1, limit))).all()
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": [diff_to_dict(d) for d in rows],
    }


def list_runs(db: Session, batch_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = (
        db.query(BizMigrationRun)
        .filter(BizMigrationRun.batch_id == batch_id)
        .order_by(BizMigrationRun.created_at.desc())
        .limit(min(100, max(1, limit)))
        .all()
    )
    return [run_to_dict(db, r) for r in rows]


def board(db: Session, batch_id: str, run_id: str = "") -> dict[str, Any]:
    """Progress / anomaly board for a migration batch (latest or given run)."""
    mb = db.get(BizMigrationBatch, batch_id)
    if not mb:
        raise HTTPException(status_code=404, detail="batch_not_found")
    proj = get_project(db, mb.project_id)
    run = None
    if run_id:
        run = db.get(BizMigrationRun, run_id)
        if not run or run.batch_id != batch_id:
            raise HTTPException(status_code=404, detail="run_not_found")
    else:
        run = (
            db.query(BizMigrationRun)
            .filter(BizMigrationRun.batch_id == batch_id)
            .order_by(BizMigrationRun.created_at.desc())
            .first()
        )
    return {
        "project": proj,
        "batch": batch_to_dict(mb),
        "run": run_to_dict(db, run) if run else None,
    }
