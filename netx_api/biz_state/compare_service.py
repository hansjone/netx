"""Compare templates, port mappings, jobs, and runs."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import (
    BizCompareJob,
    BizCompareRun,
    BizCompareTemplate,
    BizPortMapping,
    BizPortMappingRow,
    BizStateBatch,
    BizStateLldpNeighbor,
)
from ..timeutil import utcnow_naive
from .compare_engine import compare_rows, mapping_stats
from .profiles import metric_field_map


def _utcnow() -> datetime:
    return utcnow_naive()


def _default_lldp_template_fields() -> dict[str, list[str]]:
    fields = metric_field_map().get("lldp_neighbor") or []
    keys = [f.name for f in fields if f.is_key]
    ifaces = [f.name for f in fields if f.is_interface]
    compare = [f.name for f in fields if f.name not in keys or f.name in ifaces]
    # Prefer comparing identity+meta that aren't pure key-only if listed
    compare = [f.name for f in fields if f.role in ("identity", "state", "meta") or f.is_key]
    # Deduplicate while keeping order
    seen: set[str] = set()
    cmp_out: list[str] = []
    for n in compare:
        if n not in seen:
            seen.add(n)
            cmp_out.append(n)
    return {
        "key_fields": keys or ["local_if", "remote_sys", "remote_if"],
        "iface_fields": ifaces or ["local_if"],
        "compare_fields": cmp_out or ["remote_sys", "remote_if", "remote_ip", "protocol"],
    }


def ensure_default_lldp_template(db: Session) -> BizCompareTemplate:
    row = (
        db.query(BizCompareTemplate)
        .filter(BizCompareTemplate.metric_id == "lldp_neighbor", BizCompareTemplate.name == "LLDP default")
        .one_or_none()
    )
    if row:
        return row
    defs = _default_lldp_template_fields()
    row = BizCompareTemplate(
        id=uuid4().hex,
        name="LLDP default",
        metric_id="lldp_neighbor",
        key_fields=defs["key_fields"],
        iface_fields=defs["iface_fields"],
        compare_fields=defs["compare_fields"],
        ignore_fields=[],
        note="Built-in template for LLDP neighbor cutover compare",
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _template_out(t: BizCompareTemplate) -> dict[str, Any]:
    return {
        "id": t.id,
        "name": t.name,
        "metric_id": t.metric_id,
        "key_fields": list(t.key_fields or []),
        "iface_fields": list(t.iface_fields or []),
        "compare_fields": list(t.compare_fields or []),
        "ignore_fields": list(t.ignore_fields or []),
        "note": t.note,
        "updated_at": t.updated_at.isoformat() + "Z" if t.updated_at else None,
    }


def list_templates(db: Session) -> list[dict[str, Any]]:
    ensure_default_lldp_template(db)
    rows = db.query(BizCompareTemplate).order_by(BizCompareTemplate.name.asc()).all()
    return [_template_out(t) for t in rows]


def create_template(db: Session, body: dict[str, Any]) -> dict[str, Any]:
    metric_id = str(body.get("metric_id") or "").strip()
    if not metric_id:
        raise HTTPException(status_code=400, detail="metric_id_required")
    key_fields = [str(x) for x in (body.get("key_fields") or []) if str(x).strip()]
    if not key_fields:
        raise HTTPException(status_code=400, detail="key_fields_required")
    t = BizCompareTemplate(
        id=uuid4().hex,
        name=str(body.get("name") or metric_id)[:256],
        metric_id=metric_id,
        key_fields=key_fields,
        iface_fields=[str(x) for x in (body.get("iface_fields") or []) if str(x).strip()],
        compare_fields=[str(x) for x in (body.get("compare_fields") or []) if str(x).strip()],
        ignore_fields=[str(x) for x in (body.get("ignore_fields") or []) if str(x).strip()],
        note=str(body.get("note") or "")[:512],
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(t)
    db.commit()
    return _template_out(t)


def update_template(db: Session, template_id: str, body: dict[str, Any]) -> dict[str, Any]:
    t = db.get(BizCompareTemplate, template_id)
    if not t:
        raise HTTPException(status_code=404, detail="template_not_found")
    if "name" in body:
        t.name = str(body.get("name") or "")[:256]
    if "key_fields" in body:
        keys = [str(x) for x in (body.get("key_fields") or []) if str(x).strip()]
        if not keys:
            raise HTTPException(status_code=400, detail="key_fields_required")
        t.key_fields = keys
    if "iface_fields" in body:
        t.iface_fields = [str(x) for x in (body.get("iface_fields") or []) if str(x).strip()]
    if "compare_fields" in body:
        t.compare_fields = [str(x) for x in (body.get("compare_fields") or []) if str(x).strip()]
    if "ignore_fields" in body:
        t.ignore_fields = [str(x) for x in (body.get("ignore_fields") or []) if str(x).strip()]
    if "note" in body:
        t.note = str(body.get("note") or "")[:512]
    t.updated_at = _utcnow()
    db.commit()
    return _template_out(t)


def delete_template(db: Session, template_id: str) -> None:
    t = db.get(BizCompareTemplate, template_id)
    if not t:
        raise HTTPException(status_code=404, detail="template_not_found")
    db.delete(t)
    db.commit()


def _mapping_out(db: Session, m: BizPortMapping) -> dict[str, Any]:
    rows = (
        db.query(BizPortMappingRow)
        .filter(BizPortMappingRow.mapping_id == m.id)
        .order_by(BizPortMappingRow.before_if.asc())
        .all()
    )
    return {
        "id": m.id,
        "name": m.name,
        "note": m.note,
        "rows": [{"id": r.id, "before_if": r.before_if, "after_if": r.after_if} for r in rows],
        "updated_at": m.updated_at.isoformat() + "Z" if m.updated_at else None,
    }


def list_mappings(db: Session) -> list[dict[str, Any]]:
    rows = db.query(BizPortMapping).order_by(BizPortMapping.name.asc()).all()
    return [_mapping_out(db, m) for m in rows]


def create_mapping(db: Session, body: dict[str, Any]) -> dict[str, Any]:
    m = BizPortMapping(
        id=uuid4().hex,
        name=str(body.get("name") or "port map")[:256],
        note=str(body.get("note") or "")[:512],
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(m)
    db.flush()
    _replace_mapping_rows(db, m.id, list(body.get("rows") or []))
    db.commit()
    return _mapping_out(db, m)


def update_mapping(db: Session, mapping_id: str, body: dict[str, Any]) -> dict[str, Any]:
    m = db.get(BizPortMapping, mapping_id)
    if not m:
        raise HTTPException(status_code=404, detail="mapping_not_found")
    if "name" in body:
        m.name = str(body.get("name") or "")[:256]
    if "note" in body:
        m.note = str(body.get("note") or "")[:512]
    if "rows" in body:
        _replace_mapping_rows(db, m.id, list(body.get("rows") or []))
    m.updated_at = _utcnow()
    db.commit()
    return _mapping_out(db, m)


def _replace_mapping_rows(db: Session, mapping_id: str, rows_in: list[dict[str, Any]]) -> None:
    db.query(BizPortMappingRow).filter(BizPortMappingRow.mapping_id == mapping_id).delete()
    seen: set[str] = set()
    for raw in rows_in:
        before = str(raw.get("before_if") or "").strip()
        after = str(raw.get("after_if") or "").strip()
        if not before or not after:
            continue
        if before in seen:
            raise HTTPException(status_code=400, detail=f"duplicate_before_if:{before}")
        seen.add(before)
        db.add(
            BizPortMappingRow(
                id=uuid4().hex,
                mapping_id=mapping_id,
                before_if=before[:128],
                after_if=after[:128],
            )
        )


def delete_mapping(db: Session, mapping_id: str) -> None:
    m = db.get(BizPortMapping, mapping_id)
    if not m:
        raise HTTPException(status_code=404, detail="mapping_not_found")
    db.query(BizPortMappingRow).filter(BizPortMappingRow.mapping_id == mapping_id).delete()
    db.delete(m)
    db.commit()


def _port_map_dict(db: Session, mapping_id: str) -> dict[str, str]:
    if not mapping_id:
        return {}
    rows = db.query(BizPortMappingRow).filter(BizPortMappingRow.mapping_id == mapping_id).all()
    return {str(r.before_if): str(r.after_if) for r in rows if r.before_if and r.after_if}


def _load_metric_rows(db: Session, *, batch_id: str, metric_id: str) -> list[dict[str, Any]]:
    if metric_id != "lldp_neighbor":
        raise HTTPException(status_code=400, detail=f"unsupported_metric:{metric_id}")
    rows = (
        db.query(BizStateLldpNeighbor)
        .filter(BizStateLldpNeighbor.batch_id == batch_id)
        .all()
    )
    return [
        {
            "local_if": n.local_if,
            "remote_sys": n.remote_sys,
            "remote_if": n.remote_if,
            "remote_ip": n.remote_ip,
            "protocol": n.protocol,
        }
        for n in rows
    ]


def validate_mapping(
    db: Session,
    *,
    mapping_id: str,
    before_batch_id: str,
    after_batch_id: str,
    template_id: str = "",
) -> dict[str, Any]:
    tpl = db.get(BizCompareTemplate, template_id) if template_id else ensure_default_lldp_template(db)
    if not tpl:
        raise HTTPException(status_code=404, detail="template_not_found")
    before = _load_metric_rows(db, batch_id=before_batch_id, metric_id=tpl.metric_id)
    after = _load_metric_rows(db, batch_id=after_batch_id, metric_id=tpl.metric_id)
    pmap = _port_map_dict(db, mapping_id)
    return mapping_stats(
        before_rows=before,
        after_rows=after,
        iface_fields=list(tpl.iface_fields or []),
        port_map=pmap,
    )


def _job_out(j: BizCompareJob) -> dict[str, Any]:
    return {
        "id": j.id,
        "name": j.name,
        "template_id": j.template_id,
        "mapping_id": j.mapping_id,
        "before_task_id": j.before_task_id,
        "after_task_id": j.after_task_id,
        "before_batch_id": j.before_batch_id,
        "after_batch_id": j.after_batch_id,
        "mode": j.mode,
        "status": j.status,
        "note": j.note,
        "updated_at": j.updated_at.isoformat() + "Z" if j.updated_at else None,
    }


def list_jobs(db: Session) -> list[dict[str, Any]]:
    rows = db.query(BizCompareJob).order_by(BizCompareJob.updated_at.desc()).all()
    return [_job_out(j) for j in rows]


def create_job(db: Session, body: dict[str, Any]) -> dict[str, Any]:
    ensure_default_lldp_template(db)
    template_id = str(body.get("template_id") or "").strip()
    if not template_id:
        tpl = ensure_default_lldp_template(db)
        template_id = tpl.id
    else:
        if not db.get(BizCompareTemplate, template_id):
            raise HTTPException(status_code=404, detail="template_not_found")
    j = BizCompareJob(
        id=uuid4().hex,
        name=str(body.get("name") or "compare")[:256],
        template_id=template_id,
        mapping_id=str(body.get("mapping_id") or ""),
        before_task_id=str(body.get("before_task_id") or ""),
        after_task_id=str(body.get("after_task_id") or ""),
        before_batch_id=str(body.get("before_batch_id") or ""),
        after_batch_id=str(body.get("after_batch_id") or ""),
        mode=str(body.get("mode") or "manual")[:16],
        status="ready",
        note=str(body.get("note") or "")[:512],
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    if j.mode == "auto":
        j.status = "auto"
    db.add(j)
    db.commit()
    return _job_out(j)


def update_job(db: Session, job_id: str, body: dict[str, Any]) -> dict[str, Any]:
    j = db.get(BizCompareJob, job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job_not_found")
    for key in (
        "name",
        "template_id",
        "mapping_id",
        "before_task_id",
        "after_task_id",
        "before_batch_id",
        "after_batch_id",
        "mode",
        "status",
        "note",
    ):
        if key in body and body.get(key) is not None:
            setattr(j, key, str(body.get(key) or ""))
    j.updated_at = _utcnow()
    db.commit()
    return _job_out(j)


def delete_job(db: Session, job_id: str) -> None:
    j = db.get(BizCompareJob, job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job_not_found")
    db.query(BizCompareRun).filter(BizCompareRun.job_id == job_id).delete()
    db.delete(j)
    db.commit()


def _resolve_after_batch(db: Session, job: BizCompareJob) -> str:
    if job.mode != "auto":
        return str(job.after_batch_id or "")
    task_id = str(job.after_task_id or "")
    if not task_id:
        return str(job.after_batch_id or "")
    latest = (
        db.query(BizStateBatch)
        .filter(
            BizStateBatch.task_id == task_id,
            BizStateBatch.status.in_(("success", "partial")),
        )
        .order_by(BizStateBatch.started_at.desc())
        .first()
    )
    return str(latest.id) if latest else ""


def run_compare(db: Session, job_id: str, *, force_after_batch_id: str = "") -> dict[str, Any]:
    j = db.get(BizCompareJob, job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job_not_found")
    tpl = db.get(BizCompareTemplate, j.template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="template_not_found")
    before_batch_id = str(j.before_batch_id or "")
    after_batch_id = str(force_after_batch_id or "").strip() or _resolve_after_batch(db, j)
    if not before_batch_id or not after_batch_id:
        raise HTTPException(status_code=400, detail="before_and_after_batch_required")
    if not db.get(BizStateBatch, before_batch_id) or not db.get(BizStateBatch, after_batch_id):
        raise HTTPException(status_code=404, detail="batch_not_found")

    key_fields = list(tpl.key_fields or [])
    iface_fields = list(tpl.iface_fields or [])
    compare_fields = list(tpl.compare_fields or [])
    ignore = set(str(x) for x in (tpl.ignore_fields or []))
    compare_fields = [f for f in compare_fields if f not in ignore]
    if not compare_fields:
        compare_fields = [f for f in key_fields if f not in ignore]

    before_rows = _load_metric_rows(db, batch_id=before_batch_id, metric_id=tpl.metric_id)
    after_rows = _load_metric_rows(db, batch_id=after_batch_id, metric_id=tpl.metric_id)
    pmap = _port_map_dict(db, j.mapping_id)

    result = compare_rows(
        before_rows=before_rows,
        after_rows=after_rows,
        key_fields=key_fields,
        iface_fields=iface_fields,
        compare_fields=compare_fields,
        port_map=pmap,
    )

    run = BizCompareRun(
        id=uuid4().hex,
        job_id=j.id,
        template_id=tpl.id,
        mapping_id=j.mapping_id,
        before_batch_id=before_batch_id,
        after_batch_id=after_batch_id,
        metric_id=tpl.metric_id,
        status="success",
        summary_json=result["summary"],
        diffs_json=result["diffs"],
        mapping_stats_json=result["mapping_stats"],
        message="",
        created_at=_utcnow(),
    )
    db.add(run)
    j.updated_at = _utcnow()
    if j.mode == "manual":
        j.after_batch_id = after_batch_id
    db.commit()
    return get_run(db, run.id)


def get_run(db: Session, run_id: str) -> dict[str, Any]:
    r = db.get(BizCompareRun, run_id)
    if not r:
        raise HTTPException(status_code=404, detail="run_not_found")
    return {
        "id": r.id,
        "job_id": r.job_id,
        "template_id": r.template_id,
        "mapping_id": r.mapping_id,
        "before_batch_id": r.before_batch_id,
        "after_batch_id": r.after_batch_id,
        "metric_id": r.metric_id,
        "status": r.status,
        "summary": r.summary_json or {},
        "diffs": r.diffs_json or [],
        "mapping_stats": r.mapping_stats_json or {},
        "message": r.message,
        "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
    }


def list_runs(db: Session, job_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = (
        db.query(BizCompareRun)
        .filter(BizCompareRun.job_id == job_id)
        .order_by(BizCompareRun.created_at.desc())
        .limit(max(1, min(100, int(limit))))
        .all()
    )
    return [
        {
            "id": r.id,
            "before_batch_id": r.before_batch_id,
            "after_batch_id": r.after_batch_id,
            "status": r.status,
            "summary": r.summary_json or {},
            "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
        }
        for r in rows
    ]


def try_auto_compare_for_task(db: Session, task_id: str, batch_id: str) -> int:
    """When a new after batch lands, run auto jobs pinned to that after task."""
    jobs = (
        db.query(BizCompareJob)
        .filter(BizCompareJob.mode == "auto", BizCompareJob.after_task_id == task_id)
        .all()
    )
    n = 0
    for j in jobs:
        if not j.before_batch_id:
            continue
        try:
            run_compare(db, j.id, force_after_batch_id=batch_id)
            n += 1
        except Exception:
            continue
    return n
