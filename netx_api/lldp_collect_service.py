"""LLDP collect policy + dashboard (network topology management)."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .lldp_collect_schemas import (
    LldpCollectDashboardOut,
    LldpCollectJobSummary,
    LldpCollectPolicyOut,
    LldpCollectPolicyUpdate,
    LldpCollectTargetRef,
)
from .models import LldpCollectPolicy, TopoDiscoverJob, TopoFabricStats
from .topology_schemas import FabricDiscoverRequest
from .topology_service import get_discover_job, start_discover_job

POLICY_ID = 1


def _utcnow() -> datetime:
    return datetime.utcnow()


def ensure_policy(db: Session) -> LldpCollectPolicy:
    row = db.get(LldpCollectPolicy, POLICY_ID)
    if row is None:
        row = LldpCollectPolicy(
            id=POLICY_ID,
            enabled=False,
            interval_days=1,
            concurrency=4,
            scope_mode="all",
            selected_targets=[],
            auto_add_unmatched=True,
            updated_at=_utcnow(),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _policy_out(row: LldpCollectPolicy) -> LldpCollectPolicyOut:
    refs: list[LldpCollectTargetRef] = []
    for raw in row.selected_targets or []:
        if not isinstance(raw, dict):
            continue
        tid = str(raw.get("id") or "").strip()
        if not tid:
            continue
        src = str(raw.get("source") or "managed").strip().lower() or "managed"
        if src not in {"managed", "ume"}:
            src = "managed"
        refs.append(LldpCollectTargetRef(source=src, id=tid))
    return LldpCollectPolicyOut(
        enabled=bool(row.enabled),
        interval_days=int(row.interval_days or 1),
        concurrency=int(row.concurrency or 4),
        scope_mode="selected" if str(row.scope_mode or "") == "selected" else "all",
        selected_targets=refs,
        auto_add_unmatched=bool(row.auto_add_unmatched),
        updated_at=row.updated_at,
    )


def get_policy(db: Session) -> LldpCollectPolicyOut:
    return _policy_out(ensure_policy(db))


def update_policy(db: Session, body: LldpCollectPolicyUpdate) -> LldpCollectPolicyOut:
    row = ensure_policy(db)
    data = body.model_dump(exclude_unset=True)
    if "enabled" in data and data["enabled"] is not None:
        row.enabled = bool(data["enabled"])
    if "interval_days" in data and data["interval_days"] is not None:
        row.interval_days = max(1, min(365, int(data["interval_days"])))
    if "concurrency" in data and data["concurrency"] is not None:
        row.concurrency = max(1, min(32, int(data["concurrency"])))
    if "scope_mode" in data and data["scope_mode"] is not None:
        mode = str(data["scope_mode"] or "").strip().lower()
        if mode not in {"all", "selected"}:
            raise HTTPException(status_code=400, detail="invalid_scope_mode")
        row.scope_mode = mode
    if "selected_targets" in data and data["selected_targets"] is not None:
        cleaned: list[dict[str, str]] = []
        for ref in data["selected_targets"] or []:
            if isinstance(ref, LldpCollectTargetRef):
                tid = ref.id.strip()
                src = ref.source.strip().lower() or "managed"
            elif isinstance(ref, dict):
                tid = str(ref.get("id") or "").strip()
                src = str(ref.get("source") or "managed").strip().lower() or "managed"
            else:
                continue
            if not tid:
                continue
            if src not in {"managed", "ume"}:
                src = "managed"
            cleaned.append({"source": src, "id": tid})
        row.selected_targets = cleaned
    if "auto_add_unmatched" in data and data["auto_add_unmatched"] is not None:
        row.auto_add_unmatched = bool(data["auto_add_unmatched"])
    row.updated_at = _utcnow()
    db.commit()
    db.refresh(row)
    return _policy_out(row)


def _job_summary(job: TopoDiscoverJob | None) -> LldpCollectJobSummary | None:
    if job is None:
        return None
    return LldpCollectJobSummary(
        id=job.id,
        scope=job.scope or "",
        trigger_mode=getattr(job, "trigger_mode", None) or "manual",
        status=job.status or "",
        total=int(job.total or 0),
        done=int(job.done or 0),
        edges_added=int(job.edges_added or 0),
        edges_updated=int(job.edges_updated or 0),
        edges_stale=int(job.edges_stale or 0),
        error=job.error or "",
        started_at=job.started_at,
        ended_at=job.ended_at,
        created_at=job.created_at,
    )


def has_running_job(db: Session) -> TopoDiscoverJob | None:
    return (
        db.query(TopoDiscoverJob)
        .filter(TopoDiscoverJob.status.in_(["pending", "running"]))
        .order_by(TopoDiscoverJob.created_at.desc())
        .first()
    )


def last_finished_job(db: Session) -> TopoDiscoverJob | None:
    return (
        db.query(TopoDiscoverJob)
        .filter(TopoDiscoverJob.status.in_(["done", "failed"]))
        .order_by(TopoDiscoverJob.created_at.desc())
        .first()
    )


def next_due_at(db: Session, policy: LldpCollectPolicy) -> datetime | None:
    if not policy.enabled:
        return None
    days = max(1, int(policy.interval_days or 1))
    last = (
        db.query(TopoDiscoverJob)
        .filter(TopoDiscoverJob.status == "done", TopoDiscoverJob.ended_at.isnot(None))
        .order_by(TopoDiscoverJob.ended_at.desc())
        .first()
    )
    if last is None or last.ended_at is None:
        return _utcnow()
    return last.ended_at + timedelta(days=days)


def build_discover_request(policy: LldpCollectPolicy) -> FabricDiscoverRequest:
    concurrency = max(1, min(32, int(policy.concurrency or 4)))
    auto_add = bool(policy.auto_add_unmatched)
    if str(policy.scope_mode or "") == "selected":
        ne_ids: list[str] = []
        for raw in policy.selected_targets or []:
            if isinstance(raw, dict):
                tid = str(raw.get("id") or "").strip()
                if tid:
                    ne_ids.append(tid)
        if not ne_ids:
            raise HTTPException(status_code=400, detail="no_selected_targets")
        return FabricDiscoverRequest(
            scope="ne_ids",
            ne_ids=ne_ids,
            concurrency=concurrency,
            auto_add_unmatched=auto_add,
        )
    return FabricDiscoverRequest(
        scope="all_inventory",
        ne_ids=[],
        concurrency=concurrency,
        auto_add_unmatched=auto_add,
    )


def start_collect(db: Session, *, trigger_mode: str = "manual") -> dict:
    if has_running_job(db) is not None:
        raise HTTPException(status_code=409, detail="lldp_collect_already_running")
    policy = ensure_policy(db)
    body = build_discover_request(policy)
    job = start_discover_job(db, body, trigger_mode=trigger_mode)
    return {"ok": True, "job": job.model_dump()}


def get_dashboard(db: Session) -> LldpCollectDashboardOut:
    policy = ensure_policy(db)
    stats = db.get(TopoFabricStats, "global")
    running = has_running_job(db)
    last = last_finished_job(db)
    return LldpCollectDashboardOut(
        policy=_policy_out(policy),
        fabric_node_count=int(stats.node_count if stats else 0),
        fabric_edge_count=int(stats.edge_count if stats else 0),
        fabric_edge_active=int(stats.edge_active if stats else 0),
        fabric_edge_stale=int(stats.edge_stale if stats else 0),
        last_discover_at=stats.last_discover_at if stats else None,
        running_job=_job_summary(running),
        last_job=_job_summary(last),
        next_due_at=next_due_at(db, policy),
    )


def list_jobs(db: Session, *, page: int = 1, page_size: int = 20) -> dict:
    page = max(1, int(page or 1))
    page_size = max(1, min(100, int(page_size or 20)))
    q = db.query(TopoDiscoverJob).order_by(TopoDiscoverJob.created_at.desc())
    total = int(q.count())
    rows = q.offset((page - 1) * page_size).limit(page_size).all()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_job_summary(r).model_dump() for r in rows if _job_summary(r)],
    }


def get_job_detail(db: Session, job_id: str) -> dict:
    return get_discover_job(db, job_id).model_dump()
