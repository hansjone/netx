"""LLDP collect policy + dashboard (network topology management)."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .lldp_collect_schemas import (
    LldpCollectDashboardOut,
    LldpCollectJobSummary,
    LldpCollectPolicyOut,
    LldpCollectPolicyUpdate,
    LldpCollectStartBody,
    LldpCollectTargetRef,
)
from .models import LldpCollectPolicy, TopoDiscoverJob, TopoDiscoverJobItem, TopoFabricStats
from .topology_schemas import FabricDiscoverRequest
from .topology_service import (
    get_discover_job,
    pause_discover_job,
    prune_discover_jobs,
    reclaim_stale_discover_jobs,
    refresh_fabric_stats,
    resume_discover_job,
    start_discover_job,
    stop_discover_job,
)

POLICY_ID = 1
DEFAULT_HISTORY_KEEP = 30
MAX_INTERVAL_HOURS = 8760  # 365d

# Soft failures: CLI ran but produced no trustworthy LLDP evidence (parity with
# config-sync empty_config_output — retryable as "no valid new data").
_WEAK_LLDP_ERRORS = frozenset(
    {"parser_stub", "empty_cli_output", "vendor_or_device_type_required"}
)


def _utcnow() -> datetime:
    return datetime.utcnow()


def _normalize_interval_hours(row: LldpCollectPolicy) -> int:
    hours = int(getattr(row, "interval_hours", 0) or 0)
    if hours <= 0:
        hours = max(1, int(row.interval_days or 1)) * 24
    return max(1, min(MAX_INTERVAL_HOURS, hours))


def ensure_policy(db: Session) -> LldpCollectPolicy:
    row = db.get(LldpCollectPolicy, POLICY_ID)
    if row is None:
        row = LldpCollectPolicy(
            id=POLICY_ID,
            enabled=False,
            interval_days=1,
            interval_hours=24,
            concurrency=4,
            scope_mode="all",
            selected_targets=[],
            auto_add_unmatched=True,
            history_keep=DEFAULT_HISTORY_KEEP,
            updated_at=_utcnow(),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    # Heal legacy rows missing hours.
    if int(getattr(row, "interval_hours", 0) or 0) <= 0:
        row.interval_hours = max(1, int(row.interval_days or 1)) * 24
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
    keep = getattr(row, "history_keep", None)
    if keep is None:
        keep = DEFAULT_HISTORY_KEEP
    hours = _normalize_interval_hours(row)
    days = max(1, min(365, (hours + 23) // 24))
    return LldpCollectPolicyOut(
        enabled=bool(row.enabled),
        interval_days=days,
        interval_hours=hours,
        concurrency=int(row.concurrency or 4),
        scope_mode="selected" if str(row.scope_mode or "") == "selected" else "all",
        selected_targets=refs,
        auto_add_unmatched=bool(row.auto_add_unmatched),
        history_keep=max(0, min(200, int(keep))),
        updated_at=row.updated_at,
    )


def get_policy(db: Session) -> LldpCollectPolicyOut:
    return _policy_out(ensure_policy(db))


def update_policy(db: Session, body: LldpCollectPolicyUpdate) -> LldpCollectPolicyOut:
    row = ensure_policy(db)
    data = body.model_dump(exclude_unset=True)
    if "enabled" in data and data["enabled"] is not None:
        row.enabled = bool(data["enabled"])
    if "interval_hours" in data and data["interval_hours"] is not None:
        hours = max(1, min(MAX_INTERVAL_HOURS, int(data["interval_hours"])))
        row.interval_hours = hours
        row.interval_days = max(1, min(365, (hours + 23) // 24))
    elif "interval_days" in data and data["interval_days"] is not None:
        days = max(1, min(365, int(data["interval_days"])))
        row.interval_days = days
        row.interval_hours = days * 24
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
    if "history_keep" in data and data["history_keep"] is not None:
        row.history_keep = max(0, min(200, int(data["history_keep"])))
    row.updated_at = _utcnow()
    db.commit()
    db.refresh(row)
    prune_discover_jobs(db, keep=int(row.history_keep or 0))
    return _policy_out(row)


def item_needs_retry(item: TopoDiscoverJobItem) -> bool:
    """True when the NE failed or produced no trustworthy LLDP evidence."""
    if not bool(item.ok):
        return True
    if bool(item.parser_stub):
        return True
    err = str(item.error or "").strip()
    return err in _WEAK_LLDP_ERRORS


def _retryable_item_filter():
    return or_(
        TopoDiscoverJobItem.ok.is_(False),
        TopoDiscoverJobItem.parser_stub.is_(True),
        TopoDiscoverJobItem.error.in_(list(_WEAK_LLDP_ERRORS)),
    )


def _count_job_outcomes(db: Session, job_id: str) -> tuple[int, int]:
    """Return (success_count, fail_count) for a discover job's items."""
    items = (
        db.query(TopoDiscoverJobItem.ok, TopoDiscoverJobItem.parser_stub, TopoDiscoverJobItem.error)
        .filter(TopoDiscoverJobItem.job_id == job_id)
        .all()
    )
    fail = 0
    for ok, stub, error in items:
        if (not bool(ok)) or bool(stub) or str(error or "").strip() in _WEAK_LLDP_ERRORS:
            fail += 1
    total = len(items)
    return max(0, total - fail), fail


def _job_outcome_map(db: Session, job_ids: list[str]) -> dict[str, tuple[int, int]]:
    """Batch (success_count, fail_count) for many jobs."""
    if not job_ids:
        return {}
    rows = (
        db.query(
            TopoDiscoverJobItem.job_id,
            TopoDiscoverJobItem.ok,
            TopoDiscoverJobItem.parser_stub,
            TopoDiscoverJobItem.error,
        )
        .filter(TopoDiscoverJobItem.job_id.in_(job_ids))
        .all()
    )
    totals: dict[str, int] = {jid: 0 for jid in job_ids}
    fails: dict[str, int] = {jid: 0 for jid in job_ids}
    for job_id, ok, stub, error in rows:
        jid = str(job_id)
        totals[jid] = totals.get(jid, 0) + 1
        if (not bool(ok)) or bool(stub) or str(error or "").strip() in _WEAK_LLDP_ERRORS:
            fails[jid] = fails.get(jid, 0) + 1
    return {
        jid: (max(0, totals.get(jid, 0) - fails.get(jid, 0)), fails.get(jid, 0))
        for jid in job_ids
    }


def _job_summary(
    job: TopoDiscoverJob | None,
    *,
    success_count: int = 0,
    fail_count: int = 0,
) -> LldpCollectJobSummary | None:
    if job is None:
        return None
    return LldpCollectJobSummary(
        id=job.id,
        scope=job.scope or "",
        trigger_mode=getattr(job, "trigger_mode", None) or "manual",
        status=job.status or "",
        total=int(job.total or 0),
        done=int(job.done or 0),
        success_count=int(success_count or 0),
        fail_count=int(fail_count or 0),
        edges_added=int(job.edges_added or 0),
        edges_updated=int(job.edges_updated or 0),
        edges_stale=int(job.edges_stale or 0),
        edges_missing=int(job.edges_stale or 0),
        error=job.error or "",
        started_at=job.started_at,
        ended_at=job.ended_at,
        created_at=job.created_at,
    )


def _summarize_job(db: Session, job: TopoDiscoverJob | None) -> LldpCollectJobSummary | None:
    if job is None:
        return None
    ok_n, fail_n = _count_job_outcomes(db, job.id)
    return _job_summary(job, success_count=ok_n, fail_count=fail_n)


def collect_retry_targets(db: Session, src: TopoDiscoverJob) -> tuple[list[str], list[str]]:
    """Build managed/ume NE id lists from retryable items of a prior job."""
    items = (
        db.query(TopoDiscoverJobItem)
        .filter(TopoDiscoverJobItem.job_id == src.id)
        .order_by(TopoDiscoverJobItem.created_at.asc())
        .all()
    )
    managed: list[str] = []
    ume: list[str] = []
    seen: set[str] = set()
    for it in items:
        if not item_needs_retry(it):
            continue
        ume_id = str(it.ume_ne_id or "").strip()
        ne_id = str(it.ne_id or "").strip()
        if ume_id:
            key = f"ume:{ume_id}"
            if key in seen:
                continue
            seen.add(key)
            ume.append(ume_id)
        elif ne_id:
            key = f"managed:{ne_id}"
            if key in seen:
                continue
            seen.add(key)
            managed.append(ne_id)
    return managed, ume


def find_retry_source_job(db: Session, job_id: str | None = None) -> TopoDiscoverJob:
    """Resolve the source job that still has retryable NE items."""
    src_id = str(job_id or "").strip()
    if src_id:
        src = db.get(TopoDiscoverJob, src_id)
        if src is None:
            raise HTTPException(status_code=404, detail="job_not_found")
        return src
    # Newest finished job that still has failed / weak-evidence items.
    candidate_ids = [
        row[0]
        for row in (
            db.query(TopoDiscoverJobItem.job_id)
            .filter(_retryable_item_filter())
            .distinct()
            .all()
        )
    ]
    if not candidate_ids:
        raise HTTPException(status_code=404, detail="no_failed_job")
    src = (
        db.query(TopoDiscoverJob)
        .filter(
            TopoDiscoverJob.id.in_(candidate_ids),
            TopoDiscoverJob.status.in_(["done", "failed", "cancelled"]),
        )
        .order_by(TopoDiscoverJob.created_at.desc())
        .first()
    )
    if src is None:
        raise HTTPException(status_code=404, detail="no_failed_job")
    return src


def has_running_job(db: Session) -> TopoDiscoverJob | None:
    """Active job including paused — blocks starting a new collect."""
    reclaim_stale_discover_jobs(db)
    return (
        db.query(TopoDiscoverJob)
        .filter(TopoDiscoverJob.status.in_(["pending", "running", "paused"]))
        .order_by(TopoDiscoverJob.created_at.desc())
        .first()
    )


def last_finished_job(db: Session) -> TopoDiscoverJob | None:
    return (
        db.query(TopoDiscoverJob)
        .filter(TopoDiscoverJob.status.in_(["done", "failed", "cancelled"]))
        .order_by(TopoDiscoverJob.created_at.desc())
        .first()
    )


def next_due_at(db: Session, policy: LldpCollectPolicy) -> datetime | None:
    """Due time based on last *scheduled* successful collect only (manual must not reset)."""
    if not policy.enabled:
        return None
    hours = _normalize_interval_hours(policy)
    last = (
        db.query(TopoDiscoverJob)
        .filter(
            TopoDiscoverJob.status == "done",
            TopoDiscoverJob.trigger_mode == "schedule",
            TopoDiscoverJob.ended_at.isnot(None),
        )
        .order_by(TopoDiscoverJob.ended_at.desc())
        .first()
    )
    if last is None or last.ended_at is None:
        return _utcnow()
    return last.ended_at + timedelta(hours=hours)


def build_discover_request(policy: LldpCollectPolicy) -> FabricDiscoverRequest:
    concurrency = max(1, min(32, int(policy.concurrency or 4)))
    auto_add = bool(policy.auto_add_unmatched)
    if str(policy.scope_mode or "") == "selected":
        managed_ids: list[str] = []
        ume_ids: list[str] = []
        for raw in policy.selected_targets or []:
            if not isinstance(raw, dict):
                continue
            tid = str(raw.get("id") or "").strip()
            if not tid:
                continue
            src = str(raw.get("source") or "managed").strip().lower() or "managed"
            if src == "ume":
                ume_ids.append(tid)
            else:
                managed_ids.append(tid)
        if not managed_ids and not ume_ids:
            raise HTTPException(status_code=400, detail="no_selected_targets")
        return FabricDiscoverRequest(
            scope="ne_ids",
            ne_ids=[],
            managed_ne_ids=managed_ids,
            ume_ne_ids=ume_ids,
            concurrency=concurrency,
            auto_add_unmatched=auto_add,
        )
    return FabricDiscoverRequest(
        scope="all_inventory",
        ne_ids=[],
        managed_ne_ids=[],
        ume_ne_ids=[],
        concurrency=concurrency,
        auto_add_unmatched=auto_add,
    )


def start_collect(
    db: Session,
    *,
    trigger_mode: str = "manual",
    mode: str = "full",
    job_id: str | None = None,
) -> dict:
    if has_running_job(db) is not None:
        raise HTTPException(status_code=409, detail="lldp_collect_already_running")
    policy = ensure_policy(db)
    collect_mode = str(mode or "full").strip().lower() or "full"
    if collect_mode == "retry_failed":
        src = find_retry_source_job(db, job_id)
        managed_ids, ume_ids = collect_retry_targets(db, src)
        if not managed_ids and not ume_ids:
            raise HTTPException(status_code=400, detail="no_failed_targets")
        concurrency = max(1, min(32, int(policy.concurrency or 4)))
        body = FabricDiscoverRequest(
            scope="ne_ids",
            ne_ids=[],
            managed_ne_ids=managed_ids,
            ume_ne_ids=ume_ids,
            concurrency=concurrency,
            auto_add_unmatched=bool(policy.auto_add_unmatched),
        )
        trig = "retry_failed"
    else:
        body = build_discover_request(policy)
        trig = str(trigger_mode or "manual").strip().lower() or "manual"
        if trig == "retry_failed":
            trig = "manual"
    job = start_discover_job(db, body, trigger_mode=trig)
    prune_discover_jobs(db, keep=int(getattr(policy, "history_keep", DEFAULT_HISTORY_KEEP) or 0))
    return {"ok": True, "job": job.model_dump()}


def start_collect_from_body(db: Session, body: LldpCollectStartBody | None = None) -> dict:
    payload = body or LldpCollectStartBody()
    return start_collect(
        db,
        trigger_mode="manual",
        mode=str(payload.mode or "full"),
        job_id=payload.job_id,
    )


def pause_collect(db: Session, job_id: str) -> dict:
    return pause_discover_job(db, job_id).model_dump()


def resume_collect(db: Session, job_id: str) -> dict:
    return resume_discover_job(db, job_id).model_dump()


def stop_collect(db: Session, job_id: str) -> dict:
    return stop_discover_job(db, job_id).model_dump()


def get_dashboard(db: Session) -> LldpCollectDashboardOut:
    policy = ensure_policy(db)
    running = has_running_job(db)
    # While a collect job is writing Fabric, refresh KPIs from live tables so the
    # board tracks mid-job growth (job.edges_added already moves; cached stats did not).
    # Missing marks are still applied only at job end — stale/missing may lag until then.
    if running is not None:
        stats = refresh_fabric_stats(db)
    else:
        stats = db.get(TopoFabricStats, "global")
        if stats is None:
            stats = refresh_fabric_stats(db)
    last = last_finished_job(db)
    return LldpCollectDashboardOut(
        policy=_policy_out(policy),
        fabric_node_count=int(stats.node_count if stats else 0),
        fabric_edge_count=int(stats.edge_count if stats else 0),
        fabric_edge_active=int(stats.edge_active if stats else 0),
        fabric_edge_stale=int(stats.edge_stale if stats else 0),
        fabric_edge_missing=int(stats.edge_stale if stats else 0),
        last_discover_at=stats.last_discover_at if stats else None,
        running_job=_summarize_job(db, running),
        last_job=_summarize_job(db, last),
        next_due_at=next_due_at(db, policy),
    )


def list_jobs(db: Session, *, page: int = 1, page_size: int = 20) -> dict:
    page = max(1, int(page or 1))
    page_size = max(1, min(100, int(page_size or 20)))
    q = db.query(TopoDiscoverJob).order_by(TopoDiscoverJob.created_at.desc())
    total = int(q.count())
    rows = q.offset((page - 1) * page_size).limit(page_size).all()
    outcomes = _job_outcome_map(db, [r.id for r in rows])
    items: list[dict] = []
    for r in rows:
        ok_n, fail_n = outcomes.get(r.id, (0, 0))
        summary = _job_summary(r, success_count=ok_n, fail_count=fail_n)
        if summary is not None:
            items.append(summary.model_dump())
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
    }


def get_job_detail(
    db: Session, job_id: str, *, page: int | None = None, page_size: int | None = None
) -> dict:
    return get_discover_job(db, job_id, page=page, page_size=page_size).model_dump()
