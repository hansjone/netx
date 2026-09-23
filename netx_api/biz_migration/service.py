"""CRUD + evaluate for cutover migration monitor."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..biz_state.compare_rules import apply_row_filters
from ..biz_state.compare_service import (
    _load_metric_rows,
    _port_map_dict,
    batch_metric_collect_ok,
    sheet_key,
    sheet_title,
    template_metrics,
)
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
    override_for_sheet,
    parse_expect_set,
    port_sheet_def,
)

PURPOSE_CUTOVER_HF = "cutover_hf"


def _parse_dt(raw: Any) -> datetime | None:
    """Parse ISO datetime to naive UTC for DB storage.

    Accepts ``Z`` / offset-aware ISO (preferred from clients) or naive ISO
    (treated as already-UTC). Comparisons use :func:`utcnow_naive`.
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        if raw.tzinfo is not None:
            return raw.astimezone(timezone.utc).replace(tzinfo=None)
        return raw
    s = str(raw).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except ValueError:
        return None


def _dt_iso(dt: datetime | None) -> str | None:
    """Serialize naive-UTC datetime with explicit ``Z`` for global clients."""
    if not dt:
        return None
    return dt.replace(tzinfo=None).isoformat() + "Z"


def _task_looks_like_hf(task: BizStateTask | None) -> bool:
    """Legacy recovery heuristic. Prefer purpose; note match only when purpose empty."""
    if not task:
        return False
    purpose = str(getattr(task, "purpose", None) or "").strip()
    if purpose == PURPOSE_CUTOVER_HF:
        return True
    if purpose:
        return False
    note = str(task.note or "")
    return note.startswith("割接高频")


def _find_portrait_sibling(db: Session, hf_task: BizStateTask) -> BizStateTask | None:
    """Prefer a non-HF sibling task on the same NE (legacy recovery)."""
    rows = (
        db.query(BizStateTask)
        .filter(
            BizStateTask.source == hf_task.source,
            BizStateTask.ne_id == hf_task.ne_id,
            BizStateTask.id != hf_task.id,
        )
        .order_by(BizStateTask.created_at.asc())
        .all()
    )
    for r in rows:
        if not _task_looks_like_hf(r):
            return r
    return None


def _migrate_legacy_hf_slot(db: Session, proj: BizMigrationProject, side: str) -> None:
    """If portrait slot holds an HF task and hf slot empty, move HF id; restore portrait sibling if found."""
    portrait_attr = f"{side}_task_id"
    hf_attr = f"{side}_hf_task_id"
    portrait_id = str(getattr(proj, portrait_attr, None) or "").strip()
    hf_id = str(getattr(proj, hf_attr, None) or "").strip()
    if hf_id or not portrait_id:
        return
    task = db.get(BizStateTask, portrait_id)
    if not task or not _task_looks_like_hf(task):
        return
    setattr(proj, hf_attr, portrait_id)
    sibling = _find_portrait_sibling(db, task)
    if sibling:
        setattr(proj, portrait_attr, sibling.id)
    # else keep portrait_id pointing at HF until user rebinds — hf slot is correct


def migrate_project_hf_slots(db: Session, proj: BizMigrationProject, *, commit: bool = False) -> BizMigrationProject:
    """Idempotent legacy repair: split HF out of portrait slots when needed."""
    before = (proj.old_task_id, proj.new_task_id, getattr(proj, "old_hf_task_id", None), getattr(proj, "new_hf_task_id", None))
    _migrate_legacy_hf_slot(db, proj, "old")
    _migrate_legacy_hf_slot(db, proj, "new")
    after = (proj.old_task_id, proj.new_task_id, getattr(proj, "old_hf_task_id", None), getattr(proj, "new_hf_task_id", None))
    if before != after:
        proj.updated_at = utcnow_naive()
        if commit:
            db.commit()
            db.refresh(proj)
    return proj


def portrait_task_id(proj: BizMigrationProject, side: str) -> str:
    return str(getattr(proj, f"{side}_task_id", None) or "").strip()


def hf_task_id(proj: BizMigrationProject, side: str) -> str:
    return str(getattr(proj, f"{side}_hf_task_id", None) or "").strip()


def current_task_id(proj: BizMigrationProject, side: str) -> str:
    """Evaluate current batches: prefer HF, fall back to portrait."""
    return hf_task_id(proj, side) or portrait_task_id(proj, side)


def _metric_interval_map(proj: BizMigrationProject) -> dict[str, int]:
    raw = getattr(proj, "metric_interval_sec_json", None)
    out: dict[str, int] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            mid = str(k).strip()
            if not mid:
                continue
            try:
                out[mid] = max(60, int(v))
            except (TypeError, ValueError):
                continue
    return out


def _normalize_metric_intervals(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for k, v in raw.items():
        mid = str(k).strip()
        if not mid:
            continue
        try:
            out[mid] = max(60, int(v))
        except (TypeError, ValueError):
            continue
    return out


def _group_metrics_by_interval(
    metric_ids: list[str],
    *,
    default_interval: int,
    by_metric: dict[str, int],
) -> list[tuple[int, list[str]]]:
    """Group metrics by effective interval (ascending)."""
    groups: dict[int, list[str]] = {}
    default_iv = max(60, int(default_interval or 60))
    for mid in metric_ids:
        m = str(mid).strip()
        if not m:
            continue
        iv = max(60, int(by_metric.get(m) or default_iv))
        groups.setdefault(iv, []).append(m)
    return sorted(((iv, mids) for iv, mids in groups.items()), key=lambda x: x[0])


def _hf_bindings(proj: BizMigrationProject, side: str) -> list[dict[str, Any]]:
    attr = f"{side}_hf_bindings_json"
    raw = getattr(proj, attr, None)
    if isinstance(raw, list) and raw:
        out: list[dict[str, Any]] = []
        for row in raw:
            if not isinstance(row, dict):
                continue
            tid = str(row.get("task_id") or "").strip()
            if not tid:
                continue
            mids = [str(x).strip() for x in (row.get("metric_ids") or []) if str(x).strip()]
            try:
                iv = max(60, int(row.get("interval_sec") or 60))
            except (TypeError, ValueError):
                iv = 60
            out.append({"task_id": tid, "metric_ids": mids, "interval_sec": iv})
        if out:
            return out
    # Legacy single slot
    tid = hf_task_id(proj, side)
    if tid:
        return [
            {
                "task_id": tid,
                "metric_ids": _project_collect_override(proj),
                "interval_sec": int(getattr(proj, "hf_interval_sec", None) or 60),
            }
        ]
    return []


def _all_hf_task_ids(proj: BizMigrationProject, side: str) -> list[str]:
    seen: list[str] = []
    for b in _hf_bindings(proj, side):
        tid = str(b.get("task_id") or "").strip()
        if tid and tid not in seen:
            seen.append(tid)
    return seen


def _hf_task_id_for_metric(proj: BizMigrationProject, side: str, metric_id: str) -> str:
    mid = str(metric_id or "").strip()
    for b in _hf_bindings(proj, side):
        mids = b.get("metric_ids") or []
        if mid and mid in mids:
            return str(b.get("task_id") or "").strip()
        if not mids:
            # empty metric_ids = covers all for that binding
            return str(b.get("task_id") or "").strip()
    return hf_task_id(proj, side) or portrait_task_id(proj, side)


def _current_batch_for_metric(
    db: Session,
    proj: BizMigrationProject,
    side: str,
    metric_id: str,
    *,
    pinned_batch_id: str = "",
) -> BizStateBatch | None:
    tid = _hf_task_id_for_metric(proj, side, metric_id)
    pin = pinned_batch_id.strip()
    if pin:
        pinned = db.get(BizStateBatch, pin)
        # Only honor pin when it belongs to this metric's HF task (multi-interval safe)
        if pinned and (not tid or str(pinned.task_id or "") == tid):
            # Pinned partial / failed metric → treat as missing (avoid false red)
            if not batch_metric_collect_ok(db, pinned.id, metric_id):
                return None
            return pinned
    batch = _latest_success_batch(db, tid) if tid else None
    if batch and not batch_metric_collect_ok(db, batch.id, metric_id):
        return None
    return batch


def find_portrait_task_for_ne(db: Session, *, source: str, ne_id: str) -> BizStateTask | None:
    """Best portrait (non-HF) biz_state task for an NE — for baseline binding."""
    src = str(source or "managed").strip().lower() or "managed"
    nid = str(ne_id or "").strip()
    if not nid:
        return None
    rows = (
        db.query(BizStateTask)
        .filter(BizStateTask.source == src, BizStateTask.ne_id == nid)
        .order_by(BizStateTask.created_at.asc())
        .all()
    )
    for r in rows:
        if not _task_looks_like_hf(r):
            return r
    return None


def list_ne_portrait_options(db: Session, *, source: str, ne_id: str, limit: int = 30) -> dict[str, Any]:
    """Portrait task + recent batches for baseline picker when NE is selected."""
    task = find_portrait_task_for_ne(db, source=source, ne_id=ne_id)
    if not task:
        return {
            "source": source,
            "ne_id": ne_id,
            "task": None,
            "batches": [],
            "hint": "no_portrait_task",
        }
    batches = (
        db.query(BizStateBatch)
        .filter(
            BizStateBatch.task_id == task.id,
            BizStateBatch.status.in_(("success", "partial")),
        )
        .order_by(BizStateBatch.started_at.desc())
        .limit(max(1, min(100, int(limit or 30))))
        .all()
    )
    return {
        "source": source,
        "ne_id": ne_id,
        "task": _task_brief(db, task.id),
        "batches": [_batch_brief(db, b.id) for b in batches],
        "hint": "",
    }


def _project_collect_override(proj: BizMigrationProject) -> list[str]:
    raw = getattr(proj, "collect_metric_ids_json", None)
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return []


def _task_brief(db: Session, task_id: str) -> dict[str, Any]:
    t = db.get(BizStateTask, task_id) if task_id else None
    if not t:
        return {
            "id": task_id or "",
            "ne_id": "",
            "ne_name": "",
            "ne_ip": "",
            "vendor": "",
            "purpose": "",
        }
    return {
        "id": t.id,
        "ne_id": t.ne_id or "",
        "ne_name": t.ne_name,
        "ne_ip": t.ne_ip,
        "vendor": t.vendor,
        "status": t.status,
        "note": t.note,
        "purpose": str(getattr(t, "purpose", None) or ""),
        "interval_sec": t.interval_sec,
        "collect_running": bool(t.collect_running),
    }


def _batch_brief(db: Session, batch_id: str) -> dict[str, Any]:
    b = db.get(BizStateBatch, batch_id) if batch_id else None
    if not b:
        return {"id": batch_id or "", "status": "", "started_at": None, "alias": ""}
    return {
        "id": b.id,
        "status": b.status,
        "started_at": b.started_at.isoformat() if b.started_at else None,
        "row_count": b.row_count,
        "alias": str(getattr(b, "alias", "") or ""),
    }


def _monitor_template_brief(db: Session, template_id: str) -> dict[str, Any]:
    tid = str(template_id or "").strip()
    if not tid:
        return {
            "id": "",
            "name": "",
            "compare_template_id": "",
            "compare_template_name": "",
            "collect_metric_ids": [],
            "collect_metric_ids_effective": [],
        }
    row = db.get(BizMonitorTemplate, tid)
    if not row:
        return {
            "id": tid,
            "name": "",
            "compare_template_id": "",
            "compare_template_name": "",
            "collect_metric_ids": [],
            "collect_metric_ids_effective": [],
        }
    cmp_name = ""
    if row.compare_template_id:
        ct = db.get(BizCompareTemplate, row.compare_template_id)
        cmp_name = (ct.name if ct else "") or ""
    return mon_tpl._out(row, cmp_name, db=db)  # noqa: SLF001


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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], list[dict[str, str]]]:
    """Return (sheets, sheet_overrides, defaults, iface_normalize_rules)."""
    from ..biz_state.compare_service import template_iface_normalize

    overrides = list(mt.sheet_overrides_json or []) if isinstance(mt.sheet_overrides_json, list) else []
    defaults = dict(mt.defaults_json or {}) if isinstance(mt.defaults_json, dict) else {}
    cid = str(mt.compare_template_id or "").strip()
    if cid:
        ct = db.get(BizCompareTemplate, cid)
        if ct:
            sheets = template_metrics(ct)
            if sheets:
                return sheets, overrides, defaults, template_iface_normalize(ct)
    # Fallback: built-in port sheet (legacy)
    return [port_sheet_def()], overrides, defaults, []


def resolve_collect_metric_ids(db: Session, proj: BizMigrationProject) -> list[str]:
    """Project-level override wins; else monitor template; else sheet metric ids."""
    override = _project_collect_override(proj)
    if override:
        return override
    mt = resolve_project_monitor_template(db, proj)
    collect = [str(x).strip() for x in (mt.collect_metric_ids_json or []) if str(x).strip()]
    if collect:
        return collect
    sheets, _, _, _ = resolve_evaluate_sheets(db, mt)
    seen: list[str] = []
    for s in sheets:
        mid = str(s.get("metric_id") or "").strip()
        if mid and mid not in seen:
            seen.append(mid)
    return seen


def project_to_dict(db: Session, p: BizMigrationProject) -> dict[str, Any]:
    """Serialize project. Does not migrate slots (GET stays read-only)."""
    collect_override = _project_collect_override(p)
    mt_brief = _monitor_template_brief(db, getattr(p, "monitor_template_id", None) or "")
    # Same resolution as ensure/evaluate (empty → compare sheets)
    effective_collect = resolve_collect_metric_ids(db, p)
    return {
        "id": p.id,
        "name": p.name,
        "old_task_id": p.old_task_id,
        "new_task_id": p.new_task_id,
        "old_hf_task_id": getattr(p, "old_hf_task_id", None) or "",
        "new_hf_task_id": getattr(p, "new_hf_task_id", None) or "",
        "old_hf_bindings": _hf_bindings(p, "old"),
        "new_hf_bindings": _hf_bindings(p, "new"),
        "old_baseline_batch_id": p.old_baseline_batch_id,
        "new_baseline_batch_id": p.new_baseline_batch_id,
        "mapping_id": p.mapping_id,
        "monitor_template_id": getattr(p, "monitor_template_id", None) or "",
        "monitor_template": mt_brief,
        "collect_metric_ids": collect_override,
        "collect_metric_ids_effective": effective_collect,
        "metric_interval_sec": _metric_interval_map(p),
        "hf_interval_sec": int(getattr(p, "hf_interval_sec", None) or 60),
        "hf_start_at": _dt_iso(getattr(p, "hf_start_at", None)),
        "hf_end_at": _dt_iso(getattr(p, "hf_end_at", None)),
        "status": p.status,
        "note": p.note,
        "old_task": _task_brief(db, p.old_task_id),
        "new_task": _task_brief(db, p.new_task_id),
        "old_hf_task": _task_brief(db, getattr(p, "old_hf_task_id", None) or ""),
        "new_hf_task": _task_brief(db, getattr(p, "new_hf_task_id", None) or ""),
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
    """One-shot cutover project: NE + monitor template + metrics + schedule + baseline + mapping.

    Auto-binds portrait tasks when found for the NEs (baselines come from them).
    Always creates HF biz_state tasks from the monitor template collect set.
    """
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name_required")

    old_ne = body.get("old_ne") if isinstance(body.get("old_ne"), dict) else None
    new_ne = body.get("new_ne") if isinstance(body.get("new_ne"), dict) else None
    old_task_id = str(body.get("old_task_id") or body.get("old_portrait_task_id") or "").strip()
    new_task_id = str(body.get("new_task_id") or body.get("new_portrait_task_id") or "").strip()

    # Auto-bind portrait by NE when not explicitly provided
    if not old_task_id and old_ne and old_ne.get("ne_id"):
        pt = find_portrait_task_for_ne(
            db,
            source=str(old_ne.get("source") or "managed"),
            ne_id=str(old_ne.get("ne_id") or ""),
        )
        if pt:
            old_task_id = pt.id
    if not new_task_id and new_ne and new_ne.get("ne_id"):
        pt = find_portrait_task_for_ne(
            db,
            source=str(new_ne.get("source") or "managed"),
            ne_id=str(new_ne.get("ne_id") or ""),
        )
        if pt:
            new_task_id = pt.id

    if old_task_id and not db.get(BizStateTask, old_task_id):
        raise HTTPException(status_code=404, detail="old_task_not_found")
    if new_task_id and not db.get(BizStateTask, new_task_id):
        raise HTTPException(status_code=404, detail="new_task_not_found")

    has_old = bool(old_task_id or (old_ne and old_ne.get("ne_id")))
    has_new = bool(new_task_id or (new_ne and new_ne.get("ne_id")))
    if not has_old or not has_new:
        raise HTTPException(status_code=400, detail="old_new_ne_required")

    mapping_id = str(body.get("mapping_id") or "").strip()
    if mapping_id and not db.get(BizPortMapping, mapping_id):
        raise HTTPException(status_code=404, detail="mapping_not_found")

    # Inline mapping rows → create BizPortMapping
    mapping_rows = body.get("mapping_rows")
    mapping_name = str(body.get("mapping_name") or "").strip()
    if not mapping_id and isinstance(mapping_rows, list) and mapping_rows:
        from ..biz_state import compare_service as cmp_svc

        created_map = cmp_svc.create_mapping(
            db,
            {
                "name": mapping_name or f"{name}-ports",
                "rows": mapping_rows,
            },
        )
        mapping_id = str(created_map.get("id") or "")

    monitor_template_id = str(body.get("monitor_template_id") or "").strip()
    if monitor_template_id:
        if not db.get(BizMonitorTemplate, monitor_template_id):
            raise HTTPException(status_code=404, detail="monitor_template_not_found")
    else:
        monitor_template_id = mon_tpl.default_port_monitor_template_id(db)
        if not monitor_template_id:
            raise HTTPException(status_code=400, detail="monitor_template_required")

    collect_ids = body.get("collect_metric_ids")
    if not isinstance(collect_ids, list):
        collect_ids = []
    collect_ids = [str(x).strip() for x in collect_ids if str(x).strip()]

    metric_intervals = _normalize_metric_intervals(body.get("metric_interval_sec"))
    hf_interval = max(60, int(body.get("hf_interval_sec") or body.get("interval_sec") or 60))
    hf_start = _parse_dt(body.get("hf_start_at"))
    hf_end = _parse_dt(body.get("hf_end_at"))

    old_baseline = str(body.get("old_baseline_batch_id") or "").strip()
    new_baseline = str(body.get("new_baseline_batch_id") or "").strip()
    if old_baseline and not db.get(BizStateBatch, old_baseline):
        raise HTTPException(status_code=404, detail="old_baseline_not_found")
    if new_baseline and not db.get(BizStateBatch, new_baseline):
        raise HTTPException(status_code=404, detail="new_baseline_not_found")

    p = BizMigrationProject(
        id=uuid4().hex,
        name=name,
        old_task_id=old_task_id,
        new_task_id=new_task_id,
        old_hf_task_id="",
        new_hf_task_id="",
        old_baseline_batch_id=old_baseline,
        new_baseline_batch_id=new_baseline,
        mapping_id=mapping_id,
        monitor_template_id=monitor_template_id,
        collect_metric_ids_json=collect_ids,
        metric_interval_sec_json=metric_intervals,
        old_hf_bindings_json=[],
        new_hf_bindings_json=[],
        hf_interval_sec=hf_interval,
        hf_start_at=hf_start,
        hf_end_at=hf_end,
        status=str(body.get("status") or "active").strip() or "active",
        note=str(body.get("note") or "")[:500],
    )
    db.add(p)
    db.commit()
    db.refresh(p)

    # Always create HF from NE / portrait — this is the cutover collect path
    ensure_highfreq(
        db,
        p.id,
        interval_sec=hf_interval,
        retention_days=int(body.get("retention_days") or 7),
        collect_now=bool(body.get("collect_now", False)),
        old_ne=old_ne,
        new_ne=new_ne,
    )
    p = db.get(BizMigrationProject, p.id) or p
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
    if "old_task_id" in body and body["old_task_id"] is not None:
        tid = str(body["old_task_id"] or "").strip()
        if tid and not db.get(BizStateTask, tid):
            raise HTTPException(status_code=404, detail="task_not_found")
        p.old_task_id = tid
    if "new_task_id" in body and body["new_task_id"] is not None:
        tid = str(body["new_task_id"] or "").strip()
        if tid and not db.get(BizStateTask, tid):
            raise HTTPException(status_code=404, detail="task_not_found")
        p.new_task_id = tid
    if "old_hf_task_id" in body and body["old_hf_task_id"] is not None:
        tid = str(body["old_hf_task_id"] or "").strip()
        if tid and not db.get(BizStateTask, tid):
            raise HTTPException(status_code=404, detail="task_not_found")
        p.old_hf_task_id = tid
    if "new_hf_task_id" in body and body["new_hf_task_id"] is not None:
        tid = str(body["new_hf_task_id"] or "").strip()
        if tid and not db.get(BizStateTask, tid):
            raise HTTPException(status_code=404, detail="task_not_found")
        p.new_hf_task_id = tid
    if "collect_metric_ids" in body and body["collect_metric_ids"] is not None:
        raw = body["collect_metric_ids"]
        if not isinstance(raw, list):
            raise HTTPException(status_code=400, detail="collect_metric_ids_must_be_list")
        p.collect_metric_ids_json = [str(x).strip() for x in raw if str(x).strip()]
    if "metric_interval_sec" in body and body["metric_interval_sec"] is not None:
        p.metric_interval_sec_json = _normalize_metric_intervals(body["metric_interval_sec"])
    if "hf_interval_sec" in body and body["hf_interval_sec"] is not None:
        p.hf_interval_sec = max(60, int(body["hf_interval_sec"] or 60))
    if "hf_start_at" in body:
        p.hf_start_at = _parse_dt(body.get("hf_start_at"))
    if "hf_end_at" in body:
        p.hf_end_at = _parse_dt(body.get("hf_end_at"))
    p.updated_at = utcnow_naive()
    db.commit()
    db.refresh(p)
    # Status / schedule changes should stop or resume HF without waiting for scheduler tick
    if any(k in body for k in ("status", "hf_start_at", "hf_end_at")):
        _apply_hf_window_to_tasks(db, p)
        db.refresh(p)
    return project_to_dict(db, p)


def delete_project(db: Session, project_id: str) -> dict[str, Any]:
    from ..biz_state import service as biz_svc

    p = db.get(BizMigrationProject, project_id)
    if not p:
        raise HTTPException(status_code=404, detail="project_not_found")
    # Stop cutover HF collects before dropping the project
    for tid in _all_hf_task_ids(p, "old") + _all_hf_task_ids(p, "new"):
        t = db.get(BizStateTask, tid)
        if t and t.status == "running":
            try:
                biz_svc.update_task(db, tid, {"status": "paused"})
            except Exception:  # noqa: BLE001
                pass
    db.query(BizMigrationRedTicket).filter(BizMigrationRedTicket.project_id == project_id).delete()
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
    """Latest fully successful batch only — never use ``partial`` as current.

    Partial batches omit failed-command metrics and cause mass false ``removed``.
    """
    return (
        db.query(BizStateBatch)
        .filter(
            BizStateBatch.task_id == task_id,
            BizStateBatch.status == "success",
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
    if not proj.new_baseline_batch_id:
        raise HTTPException(status_code=400, detail="new_baseline_required")

    old_batch = db.get(BizStateBatch, old_batch_id.strip()) if old_batch_id.strip() else None
    new_batch = db.get(BizStateBatch, new_batch_id.strip()) if new_batch_id.strip() else None
    # Primary current batch ids (for run record / pin UX); per-metric may differ when multi-HF
    if not old_batch:
        for tid in _all_hf_task_ids(proj, "old") or [current_task_id(proj, "old")]:
            old_batch = _latest_success_batch(db, tid)
            if old_batch:
                break
    if not new_batch:
        for tid in _all_hf_task_ids(proj, "new") or [current_task_id(proj, "new")]:
            new_batch = _latest_success_batch(db, tid)
            if new_batch:
                break
    if not old_batch:
        raise HTTPException(status_code=400, detail="old_current_batch_required")
    if not new_batch:
        raise HTTPException(status_code=400, detail="new_current_batch_required")
    old_cur = old_batch.id
    new_cur = new_batch.id
    pin_old = old_batch_id.strip()
    pin_new = new_batch_id.strip()

    port_map = _port_map_dict(db, proj.mapping_id)
    expect = parse_expect_set(mb.expect_set_json if isinstance(mb.expect_set_json, dict) else {})
    # Final acceptance: window closed → unfinished expect = red
    window_active = (mb.status == "active") and (not acceptance)
    mt = resolve_project_monitor_template(db, proj)
    sheets, sheet_overrides, defaults, iface_norm = resolve_evaluate_sheets(db, mt)
    out_of_expect = str((defaults or {}).get("out_of_expect") or "strict").strip().lower()
    collect_ids = set(resolve_collect_metric_ids(db, proj))

    sheet_cards: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    metric_batches: dict[str, dict[str, str]] = {}
    seq = 0
    verdict_counts: dict[str, int] = {}

    for sheet in sheets:
        mid = str(sheet.get("metric_id") or "").strip()
        sid = sheet_key(sheet)
        key_fields = list(sheet.get("key_fields") or [])
        if not mid or not key_fields:
            continue
        iface_fields = list(sheet.get("iface_fields") or [])
        compare_fields = list(sheet.get("compare_fields") or [])
        row_filters = list(sheet.get("row_filters") or [])
        field_rules = list(sheet.get("field_rules") or [])
        sheet_ov = override_for_sheet(sheet_overrides, sheet_id=sid, metric_id=mid)
        if sheet_ov.get("skip_dual"):
            continue

        # Metric not in HF collect set → skip dual eval (avoid false red)
        if collect_ids and mid not in collect_ids:
            sheet_cards.append(
                {
                    "metric_id": mid,
                    "sheet_id": sid,
                    "title": sheet_title(sheet),
                    "progress_ok": 0,
                    "progress_total": 0,
                    "anomaly": 0,
                    "anomaly_in_expect": 0,
                    "old_summary": {},
                    "new_summary": {},
                    "new_baseline_mode": "skipped",
                    "new_baseline_missing": False,
                    "collect_skipped": True,
                }
            )
            continue

        old_cur_batch = _current_batch_for_metric(db, proj, "old", mid, pinned_batch_id=pin_old)
        new_cur_batch = _current_batch_for_metric(db, proj, "new", mid, pinned_batch_id=pin_new)
        # Do NOT fall back to another HF task's batch — empty/wrong metric → false red
        if not old_cur_batch or not new_cur_batch:
            sheet_cards.append(
                {
                    "metric_id": mid,
                    "sheet_id": sid,
                    "title": sheet_title(sheet),
                    "progress_ok": 0,
                    "progress_total": 0,
                    "anomaly": 0,
                    "anomaly_in_expect": 0,
                    "old_summary": {},
                    "new_summary": {},
                    "new_baseline_mode": "missing_current",
                    "new_baseline_missing": False,
                    "current_missing": True,
                    "collect_incomplete": True,
                    "collect_skipped": False,
                }
            )
            continue
        old_cur_mid = old_cur_batch.id
        new_cur_mid = new_cur_batch.id

        old_base = apply_row_filters(
            _load_metric_rows(db, batch_id=proj.old_baseline_batch_id, metric_id=mid),
            row_filters,
        )
        old_now = apply_row_filters(
            _load_metric_rows(db, batch_id=old_cur_mid, metric_id=mid),
            row_filters,
        )
        new_base_rows = apply_row_filters(
            _load_metric_rows(db, batch_id=proj.new_baseline_batch_id, metric_id=mid),
            row_filters,
        )
        new_now = apply_row_filters(
            _load_metric_rows(db, batch_id=new_cur_mid, metric_id=mid),
            row_filters,
        )

        one = evaluate_metric_dual(
            metric_id=mid,
            sheet_id=sid,
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
            out_of_expect=out_of_expect,
            iface_normalize_rules=iface_norm,
        )
        old_tid = _hf_task_id_for_metric(proj, "old", mid) or current_task_id(proj, "old")
        new_tid = _hf_task_id_for_metric(proj, "new", mid) or current_task_id(proj, "new")
        old_task_brief = _task_brief(db, old_tid)
        new_task_brief = _task_brief(db, new_tid)
        metric_batches[mid] = {
            "old_batch_id": old_cur_mid,
            "new_batch_id": new_cur_mid,
            "old_task_id": old_tid,
            "new_task_id": new_tid,
        }
        sheet_cards.append(
            {
                "metric_id": mid,
                "sheet_id": sid,
                "title": sheet_title(sheet),
                "progress_ok": one["progress_ok"],
                "progress_total": one["progress_total"],
                "anomaly": one["anomaly"],
                "anomaly_in_expect": int(one.get("anomaly_in_expect") or 0),
                "old_summary": one["old_summary"],
                "new_summary": one["new_summary"],
                "new_baseline_mode": one.get("new_baseline_mode") or "provided",
                "new_baseline_missing": bool(one.get("new_baseline_missing")),
                "collect_skipped": False,
                "old_batch_id": old_cur_mid,
                "new_batch_id": new_cur_mid,
                "duplicate_key_list": list(one.get("duplicate_key_list") or []),
                "duplicate_keys_before": int(one.get("duplicate_keys_before") or 0),
                "duplicate_keys_after": int(one.get("duplicate_keys_after") or 0),
            }
        )
        for r in one["rows"]:
            _enrich_row_evidence(
                db,
                r,
                old_batch_id=old_cur_mid,
                new_batch_id=new_cur_mid,
                old_task=old_task_brief,
                new_task=new_task_brief,
            )
            r["seq"] = seq
            r["sheet_id"] = sid
            seq += 1
            all_rows.append(r)
            v = str(r.get("verdict") or "")
            if v:
                verdict_counts[v] = verdict_counts.get(v, 0) + 1

    active_cards = [
        c for c in sheet_cards if not c.get("collect_skipped") and not c.get("current_missing")
    ]
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
            "metric_batches": metric_batches,
            "sheet_cards": sheet_cards,
            "progress": {
                "ok": sum(c["progress_ok"] for c in active_cards),
                "total": sum(c["progress_total"] for c in active_cards),
            },
            "anomaly": sum(c["anomaly"] for c in active_cards),
            "new_baseline_missing": any(bool(c.get("new_baseline_missing")) for c in active_cards),
            "missing_metrics": [
                str(c.get("title") or c.get("sheet_id") or c.get("metric_id") or "")
                for c in active_cards
                if c.get("new_baseline_missing")
            ],
            "current_missing_metrics": [
                str(c.get("title") or c.get("sheet_id") or c.get("metric_id") or "")
                for c in sheet_cards
                if c.get("current_missing")
            ],
            "collect_skipped_metrics": [
                str(c.get("title") or c.get("sheet_id") or c.get("metric_id") or "")
                for c in sheet_cards
                if c.get("collect_skipped")
            ],
            "verdict_counts": verdict_counts,
            "window_active": window_active,
            "expect_ports": sorted(expect.get("_ports") or ()),
            "old_current_task_id": current_task_id(proj, "old"),
            "new_current_task_id": current_task_id(proj, "new"),
        },
        message="",
    )
    db.add(run)
    db.flush()
    for r in all_rows:
        key_list = r.get("key") or []
        ev = r.get("evidence") if isinstance(r.get("evidence"), dict) else {}
        pm = ev.get("port_map") if isinstance(ev.get("port_map"), dict) else {}
        search = " ".join(
            [
                str(r.get("old_key") or r.get("key_str") or ""),
                str(r.get("new_key") or r.get("new_key_str") or ""),
                str(r.get("match_old_key") or ""),
                str(r.get("match_new_key") or ""),
                str(pm.get("match_before") or ""),
                str(pm.get("match_after") or ""),
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
                    "old_key": r.get("old_key") or r.get("key_str"),
                    "new_key": r.get("new_key") or r.get("new_key_str"),
                    "key_str": r.get("old_key") or r.get("key_str"),
                    "new_key_str": r.get("new_key") or r.get("new_key_str"),
                    "match_old_key": r.get("match_old_key") or "",
                    "match_new_key": r.get("match_new_key") or "",
                    "old_status": r.get("old_status"),
                    "new_status": r.get("new_status"),
                    "rule_hit": r.get("rule_hit") or "",
                    "sheet_id": r.get("sheet_id") or "",
                    "evidence": r.get("evidence") or {},
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


def _command_brief(db: Session, command_id: str) -> dict[str, Any]:
    from ..models import BizStateBatchCommand

    cid = str(command_id or "").strip()
    if not cid:
        return {}
    c = db.get(BizStateBatchCommand, cid)
    if not c:
        return {"command_id": cid}
    return {
        "command_id": c.id,
        "raw_command": c.raw_command or "",
        "parse_status": c.parse_status or "",
        "row_count": int(c.row_count or 0),
        "profile_id": c.profile_id or "",
        "parser_id": c.parser_id or "",
        "metric_id": c.metric_id or "",
        "message": (c.message or "")[:300],
    }


def _enrich_row_evidence(
    db: Session,
    row: dict[str, Any],
    *,
    old_batch_id: str,
    new_batch_id: str,
    old_task: dict[str, Any] | None,
    new_task: dict[str, Any] | None,
) -> None:
    """Attach device / collect / show-command onto evaluate evidence (in-place)."""
    ev = dict(row.get("evidence") or {})
    for side, brief, batch_id in (
        ("old", old_task or {}, old_batch_id),
        ("new", new_task or {}, new_batch_id),
    ):
        side_ev = dict(ev.get(side) or {})
        netx = dict(side_ev.get("netx") or {})
        cmd = _command_brief(db, str(netx.get("batch_command_id") or ""))
        side_ev["device"] = {
            "ne_id": str(brief.get("ne_id") or netx.get("ne_id") or ""),
            "ne_name": str(brief.get("ne_name") or ""),
            "ne_ip": str(brief.get("ne_ip") or ""),
            "side": side,
        }
        # Prefer the metric row's own batch (baseline vs current); fall back to
        # the side's evaluate batch id used for this sheet.
        side_ev["collect"] = {
            "task_id": str(netx.get("task_id") or brief.get("id") or ""),
            "batch_id": str(netx.get("batch_id") or batch_id or ""),
            "side_batch_id": str(batch_id or ""),
            "collected_at": netx.get("collected_at"),
            "parse_status": str(cmd.get("parse_status") or ""),
        }
        side_ev["command"] = cmd
        ev[side] = side_ev
    row["evidence"] = ev


def diff_to_dict(d: BizMigrationDiff) -> dict[str, Any]:
    kj = d.key_json if isinstance(d.key_json, dict) else {}
    old_key = str(kj.get("old_key") or kj.get("key_str") or "")
    new_key = str(kj.get("new_key") or kj.get("new_key_str") or "")
    return {
        "id": d.id,
        "metric_id": d.metric_id,
        "sheet_id": kj.get("sheet_id") or d.metric_id or "",
        "seq": d.seq,
        "verdict": d.verdict,
        "color": d.color,
        "key": kj,
        "old_key": old_key,
        "new_key": new_key,
        "key_str": old_key,
        "new_key_str": new_key,
        "match_old_key": str(kj.get("match_old_key") or ""),
        "match_new_key": str(kj.get("match_new_key") or ""),
        "old_status": kj.get("old_status") or "",
        "new_status": kj.get("new_status") or "",
        "rule_hit": kj.get("rule_hit") or "",
        "evidence": dict(kj.get("evidence") or {}),
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
    sheets, sheet_overrides, _defaults, _norm = resolve_evaluate_sheets(db, mt)
    collect_ids = set(resolve_collect_metric_ids(db, p))
    port_map = _port_map_dict(db, p.mapping_id)
    out_sheets: list[dict[str, Any]] = []
    for sheet in sheets:
        mid = str(sheet.get("metric_id") or "").strip()
        sid = sheet_key(sheet)
        key_fields = [str(k) for k in (sheet.get("key_fields") or []) if str(k).strip()]
        if not mid or not key_fields:
            continue
        # Only offer expect keys for metrics that are actually HF-collected
        if collect_ids and mid not in collect_ids:
            continue
        sheet_ov = override_for_sheet(sheet_overrides, sheet_id=sid, metric_id=mid)
        if sheet_ov.get("skip_dual"):
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
                "sheet_id": sid,
                "title": sheet_title(sheet),
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
            if getattr(p, "placeholders", None):
                ph = ",".join(str(x.name) for x in (p.placeholders or []) if getattr(x, "name", None))
                raise HTTPException(
                    status_code=400,
                    detail=f"metric_needs_bindings:{mid}:{ph or 'required'}",
                )
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
    """True when task is cutover HF with matching metrics (or legacy short-interval match)."""
    if str(getattr(task, "purpose", None) or "").strip() == PURPOSE_CUTOVER_HF:
        metrics = _enabled_metric_ids(db, task.id)
        return metrics == set(want_metrics)
    metrics = _enabled_metric_ids(db, task.id)
    return metrics == set(want_metrics) and int(task.interval_sec or 0) <= 300


def _is_port_highfreq_task(db: Session, task: BizStateTask) -> bool:
    """Back-compat: interface_brief-only HF."""
    return _is_highfreq_task(db, task, {PORT_METRIC_ID})


def _ne_spec_to_template_fields(db: Session, ne: dict[str, Any]) -> dict[str, Any]:
    """Resolve NE dict into fields suitable for create_task."""
    from ..biz_state import service as biz_svc

    source = str(ne.get("source") or "managed").strip().lower() or "managed"
    ne_id = str(ne.get("ne_id") or "").strip()
    if not ne_id:
        raise HTTPException(status_code=400, detail="ne_id_required")
    meta = biz_svc._ne_meta(db, source=source, ne_id=ne_id)  # noqa: SLF001
    return {
        "source": source,
        "ne_id": ne_id,
        "ne_name": str(ne.get("ne_name") or meta.get("ne_name") or ""),
        "ne_ip": str(ne.get("ne_ip") or meta.get("ne_ip") or ""),
        "vendor": str(ne.get("vendor") or meta.get("vendor") or ""),
        "device_type": str(ne.get("device_type") or meta.get("device_type") or ""),
    }


def _template_from_task_or_ne(
    db: Session,
    *,
    portrait: BizStateTask | None,
    existing_hf: BizStateTask | None,
    ne: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build NE identity for HF create: prefer portrait, else existing HF, else NE spec."""
    if portrait and not _task_looks_like_hf(portrait):
        return {
            "source": portrait.source,
            "ne_id": portrait.ne_id,
            "ne_name": portrait.ne_name,
            "ne_ip": portrait.ne_ip,
            "vendor": portrait.vendor,
            "device_type": portrait.device_type,
        }
    if existing_hf:
        return {
            "source": existing_hf.source,
            "ne_id": existing_hf.ne_id,
            "ne_name": existing_hf.ne_name,
            "ne_ip": existing_hf.ne_ip,
            "vendor": existing_hf.vendor,
            "device_type": existing_hf.device_type,
        }
    if portrait:
        return {
            "source": portrait.source,
            "ne_id": portrait.ne_id,
            "ne_name": portrait.ne_name,
            "ne_ip": portrait.ne_ip,
            "vendor": portrait.vendor,
            "device_type": portrait.device_type,
        }
    if ne:
        return _ne_spec_to_template_fields(db, ne)
    raise HTTPException(status_code=400, detail="old_new_ne_or_task_required")


def _ensure_side_highfreq(
    db: Session,
    *,
    existing_hf: BizStateTask | None,
    template_fields: dict[str, Any],
    project_name: str,
    interval_sec: int,
    retention_days: int,
    metric_ids: list[str],
    status: str = "running",
) -> tuple[BizStateTask, bool]:
    """Return (hf_task, created). Reuse existing HF if metrics match; never mutate portrait."""
    from ..biz_state import service as biz_svc

    want = {str(m).strip() for m in metric_ids if str(m).strip()}
    if not want:
        want = {PORT_METRIC_ID}

    if existing_hf and _is_highfreq_task(db, existing_hf, want):
        patch: dict[str, Any] = {
            "interval_sec": interval_sec,
            "retention_days": retention_days,
            "purpose": PURPOSE_CUTOVER_HF,
        }
        if status:
            patch["status"] = status
        biz_svc.update_task(db, existing_hf.id, patch)
        refreshed = db.get(BizStateTask, existing_hf.id)
        return refreshed or existing_hf, False

    # Existing HF (purpose already cutover_hf) with wrong metrics → update items in place.
    # Never rewrite a task that only "looks like" HF by note — create a sibling instead.
    if existing_hf and str(getattr(existing_hf, "purpose", None) or "").strip() == PURPOSE_CUTOVER_HF:
        items = [
            _catalog_item_for_metric(
                vendor=str(template_fields.get("vendor") or existing_hf.vendor),
                device_type=str(template_fields.get("device_type") or existing_hf.device_type),
                metric_id=mid,
            )
            for mid in sorted(want)
        ]
        note = f"割接高频/{'+'.join(sorted(want)[:3])}/{project_name}"[:256]
        biz_svc.update_task(
            db,
            existing_hf.id,
            {
                "note": note,
                "purpose": PURPOSE_CUTOVER_HF,
                "status": status or "running",
                "interval_sec": interval_sec,
                "retention_days": retention_days,
                "items": items,
            },
        )
        refreshed = db.get(BizStateTask, existing_hf.id)
        return refreshed or existing_hf, False

    items = [
        _catalog_item_for_metric(
            vendor=str(template_fields.get("vendor") or ""),
            device_type=str(template_fields.get("device_type") or ""),
            metric_id=mid,
        )
        for mid in sorted(want)
    ]
    note = f"割接高频/{'+'.join(sorted(want)[:3])}/{project_name}"[:256]
    created = biz_svc.create_task(
        db,
        {
            "source": template_fields["source"],
            "ne_id": template_fields["ne_id"],
            "ne_name": template_fields.get("ne_name") or "",
            "ne_ip": template_fields.get("ne_ip") or "",
            "vendor": template_fields.get("vendor") or "",
            "device_type": template_fields.get("device_type") or "",
            "note": note,
            "purpose": PURPOSE_CUTOVER_HF,
            "status": status or "running",
            "interval_sec": interval_sec,
            "retention_days": retention_days,
            "items": items,
        },
    )
    task = db.get(BizStateTask, str(created.get("id") or ""))
    if not task:
        raise HTTPException(status_code=500, detail="highfreq_task_create_failed")
    return task, True


def _hf_window_status(proj: BizMigrationProject) -> str:
    """Return desired HF task status based on project window: running|paused."""
    status = str(getattr(proj, "status", None) or "").strip().lower()
    if status in ("done", "completed", "archived", "cancelled"):
        return "paused"
    now = utcnow_naive()
    start = getattr(proj, "hf_start_at", None)
    end = getattr(proj, "hf_end_at", None)
    if end and now > end:
        return "paused"
    if start and now < start:
        return "paused"
    return "running"


def _apply_hf_window_to_tasks(db: Session, proj: BizMigrationProject) -> None:
    """Immediately pause/resume bound cutover HF tasks to match project window/status."""
    import logging

    from ..biz_state import service as biz_svc

    log = logging.getLogger("netx.biz_migration.hf_window")
    want = _hf_window_status(proj)
    for tid in _all_hf_task_ids(proj, "old") + _all_hf_task_ids(proj, "new"):
        task = db.get(BizStateTask, tid)
        if not task:
            continue
        purpose = str(getattr(task, "purpose", None) or "").strip()
        if purpose and purpose != PURPOSE_CUTOVER_HF:
            continue
        patch: dict[str, Any] = {}
        # Legacy empty-purpose HF: stamp purpose so resume skips bindings assert
        if purpose != PURPOSE_CUTOVER_HF:
            patch["purpose"] = PURPOSE_CUTOVER_HF
        if want == "paused" and task.status == "running":
            patch["status"] = "paused"
        elif want == "running" and task.status == "paused":
            patch["status"] = "running"
        if not patch:
            continue
        try:
            biz_svc.update_task(db, tid, patch)
        except Exception:  # noqa: BLE001
            log.exception("hf window sync failed project=%s task=%s want=%s", proj.id, tid, want)


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
    interval_sec: int | None = None,
    retention_days: int = 7,
    collect_now: bool = True,
    old_ne: dict[str, Any] | None = None,
    new_ne: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create/bind HF tasks (one per interval group) — never overwrites portrait ids."""
    from ..biz_state.collect_runner import dispatch_collect

    proj = db.get(BizMigrationProject, project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="project_not_found")
    migrate_project_hf_slots(db, proj, commit=False)

    if interval_sec is not None:
        proj.hf_interval_sec = max(60, int(interval_sec))
    iv_default = max(60, int(getattr(proj, "hf_interval_sec", None) or interval_sec or 60))
    ret = max(1, int(retention_days or 7))
    metric_ids = resolve_collect_metric_ids(db, proj)
    if not metric_ids:
        metric_ids = [PORT_METRIC_ID]
    by_metric = _metric_interval_map(proj)
    groups = _group_metrics_by_interval(metric_ids, default_interval=iv_default, by_metric=by_metric)
    want_status = _hf_window_status(proj)

    old_portrait = db.get(BizStateTask, proj.old_task_id) if proj.old_task_id else None
    new_portrait = db.get(BizStateTask, proj.new_task_id) if proj.new_task_id else None

    # Reuse existing bindings by interval when possible
    def _existing_by_interval(side: str) -> dict[int, BizStateTask]:
        out: dict[int, BizStateTask] = {}
        for b in _hf_bindings(proj, side):
            tid = str(b.get("task_id") or "").strip()
            t = db.get(BizStateTask, tid) if tid else None
            if not t:
                continue
            try:
                iv = max(60, int(b.get("interval_sec") or t.interval_sec or 60))
            except (TypeError, ValueError):
                iv = 60
            out[iv] = t
        # legacy primary slot
        primary = db.get(BizStateTask, hf_task_id(proj, side)) if hf_task_id(proj, side) else None
        if primary and iv_default not in out:
            out[iv_default] = primary
        return out

    old_existing = _existing_by_interval("old")
    new_existing = _existing_by_interval("new")
    old_prev_ids = set(_all_hf_task_ids(proj, "old"))
    new_prev_ids = set(_all_hf_task_ids(proj, "new"))

    old_fields = _template_from_task_or_ne(
        db,
        portrait=old_portrait,
        existing_hf=next(iter(old_existing.values()), None),
        ne=old_ne,
    )
    new_fields = _template_from_task_or_ne(
        db,
        portrait=new_portrait,
        existing_hf=next(iter(new_existing.values()), None),
        ne=new_ne,
    )

    old_bindings: list[dict[str, Any]] = []
    new_bindings: list[dict[str, Any]] = []
    old_any_created = False
    new_any_created = False

    for g_iv, g_metrics in groups:
        old_task, old_created = _ensure_side_highfreq(
            db,
            existing_hf=old_existing.get(g_iv),
            template_fields=old_fields,
            project_name=proj.name,
            interval_sec=g_iv,
            retention_days=ret,
            metric_ids=g_metrics,
            status=want_status,
        )
        new_task, new_created = _ensure_side_highfreq(
            db,
            existing_hf=new_existing.get(g_iv),
            template_fields=new_fields,
            project_name=proj.name,
            interval_sec=g_iv,
            retention_days=ret,
            metric_ids=g_metrics,
            status=want_status,
        )
        old_any_created = old_any_created or old_created
        new_any_created = new_any_created or new_created
        old_bindings.append(
            {"task_id": old_task.id, "metric_ids": list(g_metrics), "interval_sec": g_iv}
        )
        new_bindings.append(
            {"task_id": new_task.id, "metric_ids": list(g_metrics), "interval_sec": g_iv}
        )

    proj = db.get(BizMigrationProject, project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="project_not_found")
    proj.old_hf_bindings_json = old_bindings
    proj.new_hf_bindings_json = new_bindings
    proj.old_hf_task_id = old_bindings[0]["task_id"] if old_bindings else ""
    proj.new_hf_task_id = new_bindings[0]["task_id"] if new_bindings else ""
    proj.hf_interval_sec = iv_default
    proj.updated_at = utcnow_naive()
    db.commit()

    # Pause HF tasks dropped from bindings (interval regroup / metric shrink)
    from ..biz_state import service as biz_svc

    keep_ids = {b["task_id"] for b in old_bindings} | {b["task_id"] for b in new_bindings}
    for tid in (old_prev_ids | new_prev_ids) - keep_ids:
        t = db.get(BizStateTask, tid)
        if not t:
            continue
        if str(getattr(t, "purpose", None) or "").strip() not in ("", PURPOSE_CUTOVER_HF):
            continue
        if t.status == "running":
            try:
                biz_svc.update_task(db, tid, {"status": "paused"})
            except Exception:  # noqa: BLE001
                pass

    collect: dict[str, Any] = {"old": [], "new": []}
    if collect_now and want_status == "running":
        for side, binds in (("old", old_bindings), ("new", new_bindings)):
            for b in binds:
                tid = b["task_id"]
                try:
                    dispatch_collect(tid)
                    collect[side].append({"ok": True, "task_id": tid})
                except Exception as exc:  # noqa: BLE001
                    collect[side].append({"ok": False, "task_id": tid, "error": str(exc)[:200]})
    elif collect_now and want_status != "running":
        collect = {
            "old": [{"ok": False, "error": "hf_window_inactive"}],
            "new": [{"ok": False, "error": "hf_window_inactive"}],
        }

    return {
        "project": project_to_dict(db, proj),
        "old_task": _task_brief(db, proj.old_hf_task_id),
        "new_task": _task_brief(db, proj.new_hf_task_id),
        "old_hf_task": _task_brief(db, proj.old_hf_task_id),
        "new_hf_task": _task_brief(db, proj.new_hf_task_id),
        "old_hf_bindings": old_bindings,
        "new_hf_bindings": new_bindings,
        "old_created": old_any_created,
        "new_created": new_any_created,
        "interval_sec": iv_default,
        "hf_status": want_status,
        "collect_metric_ids": metric_ids,
        "collect": collect,
    }


def collect_project_now(db: Session, project_id: str) -> dict[str, Any]:
    """Trigger immediate collect on all HF binding tasks (never portrait)."""
    from ..biz_state.collect_runner import dispatch_collect

    proj = db.get(BizMigrationProject, project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="project_not_found")
    migrate_project_hf_slots(db, proj, commit=True)
    want_status = _hf_window_status(proj)
    all_ids = _all_hf_task_ids(proj, "old") + _all_hf_task_ids(proj, "new")
    if want_status != "running":
        from ..biz_state import service as biz_svc

        for tid in all_ids:
            t = db.get(BizStateTask, tid)
            if t and t.status == "running":
                biz_svc.update_task(db, tid, {"status": "paused"})
        return {
            "old": [{"ok": False, "error": "hf_window_inactive"}],
            "new": [{"ok": False, "error": "hf_window_inactive"}],
            "hf_status": want_status,
        }

    out: dict[str, Any] = {"old": [], "new": [], "hf_status": want_status}
    for side in ("old", "new"):
        tids = _all_hf_task_ids(proj, side)
        if not tids:
            out[side] = [{"ok": False, "error": "hf_task_missing"}]
            continue
        for tid in tids:
            task = db.get(BizStateTask, tid)
            if not task:
                out[side].append({"ok": False, "error": "task_not_found", "task_id": tid})
                continue
            if bool(task.collect_running):
                out[side].append(
                    {"ok": False, "error": "collect_already_running", "task_id": tid}
                )
                continue
            try:
                dispatch_collect(tid)
                out[side].append({"ok": True, "task_id": tid})
            except Exception as exc:  # noqa: BLE001
                out[side].append({"ok": False, "task_id": tid, "error": str(exc)[:200]})
    return out


def _red_ticket_to_dict(t: BizMigrationRedTicket) -> dict[str, Any]:
    detail = dict(t.detail_json or {})
    old_key = str(detail.get("old_key") or t.key_str or "")
    new_key = str(detail.get("new_key") or t.new_key_str or "")
    return {
        "id": t.id,
        "project_id": t.project_id,
        "batch_id": t.batch_id,
        "run_id": t.run_id,
        "metric_id": t.metric_id,
        "old_key": old_key,
        "new_key": new_key,
        "key_str": old_key,
        "new_key_str": new_key,
        "match_key_str": str(getattr(t, "match_key_str", "") or detail.get("match_old_key") or ""),
        "match_old_key": str(detail.get("match_old_key") or ""),
        "match_new_key": str(detail.get("match_new_key") or ""),
        "verdict": t.verdict,
        "color": t.color,
        "old_status": t.old_status,
        "new_status": t.new_status,
        "detail": detail,
        "evidence": dict(detail.get("evidence") or {}),
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
    """Upsert open/carried red tickets by (project, metric, match_key).

    Same issue across batches / re-acceptance updates one ticket instead of
    spawning duplicates. ``carried`` tickets are reopened when the key is still red.
    """
    diffs = (
        db.query(BizMigrationDiff)
        .filter(BizMigrationDiff.run_id == run_id, BizMigrationDiff.color == "red")
        .order_by(BizMigrationDiff.seq.asc())
        .all()
    )
    existing = (
        db.query(BizMigrationRedTicket)
        .filter(
            BizMigrationRedTicket.project_id == project_id,
            BizMigrationRedTicket.status.in_(("open", "carried")),
        )
        .all()
    )
    by_id: dict[tuple[str, str], BizMigrationRedTicket] = {}
    for t in existing:
        mk = str(getattr(t, "match_key_str", "") or "").strip()
        if not mk:
            detail = t.detail_json if isinstance(t.detail_json, dict) else {}
            mk = str(detail.get("match_old_key") or t.key_str or "").strip()
        if mk:
            by_id[(str(t.metric_id or ""), mk)] = t

    created_or_updated: list[BizMigrationRedTicket] = []
    seen: set[tuple[str, str]] = set()
    for d in diffs:
        kj = d.key_json if isinstance(d.key_json, dict) else {}
        mid = str(d.metric_id or "")
        match_key = str(
            kj.get("match_old_key") or kj.get("key_str") or kj.get("old_key") or ""
        ).strip()[:256]
        if not match_key:
            match_key = str(kj.get("key_str") or "")[:256]
        ident = (mid, match_key)
        detail = {
            "old_kind": d.old_kind,
            "new_kind": d.new_kind,
            "in_expect": d.in_expect,
            "old": d.old_json,
            "new": d.new_json,
            "old_key": kj.get("old_key") or kj.get("key_str") or "",
            "new_key": kj.get("new_key") or kj.get("new_key_str") or "",
            "match_old_key": kj.get("match_old_key") or match_key,
            "match_new_key": kj.get("match_new_key") or "",
            "evidence": dict(kj.get("evidence") or {}),
        }
        prev = by_id.get(ident)
        if prev is not None:
            prev.batch_id = batch_id
            prev.run_id = run_id
            prev.key_str = str(kj.get("key_str") or "")[:256]
            prev.new_key_str = str(kj.get("new_key_str") or "")[:256]
            prev.match_key_str = match_key
            prev.verdict = d.verdict
            prev.color = d.color or "red"
            prev.old_status = str(kj.get("old_status") or "")[:64]
            prev.new_status = str(kj.get("new_status") or "")[:64]
            prev.detail_json = detail
            prev.status = "open"
            prev.carried_to_batch_id = ""
            prev.resolved_at = None
            created_or_updated.append(prev)
            seen.add(ident)
            continue
        t = BizMigrationRedTicket(
            id=uuid4().hex,
            project_id=project_id,
            batch_id=batch_id,
            run_id=run_id,
            metric_id=mid,
            key_str=str(kj.get("key_str") or "")[:256],
            new_key_str=str(kj.get("new_key_str") or "")[:256],
            match_key_str=match_key,
            verdict=d.verdict,
            color=d.color or "red",
            old_status=str(kj.get("old_status") or "")[:64],
            new_status=str(kj.get("new_status") or "")[:64],
            detail_json=detail,
            status="open",
        )
        db.add(t)
        by_id[ident] = t
        created_or_updated.append(t)
        seen.add(ident)

    # Tickets open on this batch but no longer red → leave as open (operator resolves).
    # Carried tickets for keys not in this run stay carried.
    return created_or_updated


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
    missing_metrics = [str(x) for x in (summary.get("missing_metrics") or []) if str(x)]
    current_missing = [str(x) for x in (summary.get("current_missing_metrics") or []) if str(x)]
    fail_reasons: list[str] = []
    if total <= 0:
        fail_reasons.append("empty_expect")
    if missing_metrics:
        fail_reasons.append("new_baseline_missing")
    if current_missing:
        fail_reasons.append("current_missing")
    if anomaly:
        fail_reasons.append("anomaly")
    if total > 0 and ok < total:
        fail_reasons.append("incomplete")
    passed = not fail_reasons

    accept_summary = {
        "passed": passed,
        "fail_reasons": fail_reasons,
        "missing_metrics": missing_metrics,
        "current_missing_metrics": current_missing,
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

    # Upsert reds (no wipe — merge by metric+match_key across re-acceptance)
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


def get_monitor_context(
    db: Session,
    *,
    project_id: str = "",
    task_id: str = "",
) -> dict[str, Any]:
    """Fat read for AI / ops: project definition + templates + mapping + tasks.

    Prefer ``project_id`` (cutover). ``task_id`` alone returns that biz_state task
    and any cutover project that references it.
    """
    from ..biz_state import compare_service as cmp_svc
    from ..biz_state import service as biz_svc

    pid = str(project_id or "").strip()
    tid = str(task_id or "").strip()
    if not pid and not tid:
        raise HTTPException(status_code=400, detail="project_id_or_task_id_required")

    proj_row: BizMigrationProject | None = None
    if pid:
        proj_row = db.get(BizMigrationProject, pid)
        if not proj_row:
            raise HTTPException(status_code=404, detail="project_not_found")
    elif tid:
        proj_row = (
            db.query(BizMigrationProject)
            .filter(
                (BizMigrationProject.old_task_id == tid)
                | (BizMigrationProject.new_task_id == tid)
                | (BizMigrationProject.old_hf_task_id == tid)
                | (BizMigrationProject.new_hf_task_id == tid)
            )
            .order_by(BizMigrationProject.updated_at.desc())
            .first()
        )

    out: dict[str, Any] = {
        "project": None,
        "monitor_template": None,
        "compare_template": None,
        "port_mapping": None,
        "tasks": {},
        "task": None,
    }

    if tid:
        try:
            out["task"] = biz_svc.get_task(db, tid)
        except HTTPException:
            if not proj_row:
                raise

    if not proj_row:
        return out

    migrate_project_hf_slots(db, proj_row, commit=False)
    out["project"] = project_to_dict(db, proj_row)

    mt_id = str(getattr(proj_row, "monitor_template_id", None) or "").strip()
    if mt_id:
        try:
            out["monitor_template"] = mon_tpl.get_monitor_template(db, mt_id)
        except HTTPException:
            out["monitor_template"] = None

    ct_id = ""
    if isinstance(out.get("monitor_template"), dict):
        ct_id = str(out["monitor_template"].get("compare_template_id") or "").strip()
    if ct_id:
        ct = db.get(BizCompareTemplate, ct_id)
        if ct:
            out["compare_template"] = cmp_svc._template_out(ct)  # noqa: SLF001

    map_id = str(proj_row.mapping_id or "").strip()
    if map_id:
        m = db.get(BizPortMapping, map_id)
        if m:
            out["port_mapping"] = cmp_svc._mapping_out(db, m)  # noqa: SLF001

    task_ids = {
        "old_portrait": proj_row.old_task_id or "",
        "new_portrait": proj_row.new_task_id or "",
        "old_hf": getattr(proj_row, "old_hf_task_id", None) or "",
        "new_hf": getattr(proj_row, "new_hf_task_id", None) or "",
    }
    for side in ("old", "new"):
        for i, b in enumerate(_hf_bindings(proj_row, side)):
            bid = str(b.get("task_id") or "").strip()
            if bid:
                task_ids[f"{side}_hf_{i}"] = bid

    tasks: dict[str, Any] = {}
    for label, task_ref in task_ids.items():
        if not task_ref or task_ref in {t.get("id") for t in tasks.values() if isinstance(t, dict)}:
            # still key by label even if duplicate id
            pass
        if not task_ref:
            continue
        try:
            tasks[label] = biz_svc.get_task(db, task_ref)
        except HTTPException:
            tasks[label] = {"id": task_ref, "error": "task_not_found"}
    out["tasks"] = tasks
    return out
