"""Discover job lifecycle: start, background run, stale reclaim."""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .models import LldpCollectPolicy, TopoDiscoverJob, TopoDiscoverJobItem, TopoFabricStats
from .topology_common import _JOB_LOCK, _RUNNING_JOBS, _utcnow
from .topology_discover_common import (
    _job_out,
    _resolve_scan_targets,
    prune_discover_jobs,
)
from .topology_discover_scan import _discover_one_target, _preensure_discover_targets
from .topology_fabric import (
    _apply_missing_and_purge,
    merge_duplicate_fabric_nodes,
    refresh_fabric_stats,
)
from .topology_schemas import FabricDiscoverJobOut, FabricDiscoverRequest

_log = logging.getLogger("netx.topology.discover")


def _run_discover_job(job_id: str, body: FabricDiscoverRequest) -> None:
    db = SessionLocal()
    try:
        job = db.get(TopoDiscoverJob, job_id)
        if job is None:
            return
        job.status = "running"
        job.started_at = _utcnow()
        job.updated_at = job.started_at
        try:
            targets = _resolve_scan_targets(db, body)
        except HTTPException as exc:
            job.status = "failed"
            job.error = str(exc.detail or "resolve_failed")[:1024]
            job.ended_at = _utcnow()
            job.updated_at = job.ended_at
            db.commit()
            return
        job.total = len(targets)
        db.commit()

        # Reduce cross-worker races on self nodes before concurrent SSH/apply.
        try:
            _preensure_discover_targets(db, targets)
        except Exception:  # noqa: BLE001
            db.rollback()

        from .cli_budget import clamp_cli_workers

        concurrency = clamp_cli_workers(int(body.concurrency or 4), hard_cap=32)
        added = 0
        updated = 0
        stale = 0
        scanned_ok: set[str] = set()
        touched_edges: set[str] = set()

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futs = {
                pool.submit(
                    _discover_one_target, t, auto_add_unmatched=bool(body.auto_add_unmatched)
                ): t
                for t in targets
            }
            for fut in as_completed(futs):
                result = fut.result()
                item = TopoDiscoverJobItem(
                    id=uuid4().hex,
                    job_id=job_id,
                    ne_id=str(result.get("ne_id") or ""),
                    ume_ne_id=str(result.get("ume_ne_id") or ""),
                    fabric_node_id=str(result.get("fabric_node_id") or ""),
                    ne_name=str(result.get("ne_name") or "")[:256],
                    ne_ip=str(result.get("ne_ip") or "")[:128],
                    ok=bool(result.get("ok")),
                    command=str(result.get("command") or "")[:256],
                    neighbors=int(result.get("neighbors") or 0),
                    edges_added=int(result.get("edges_added") or 0),
                    edges_updated=int(result.get("edges_updated") or 0),
                    unmatched_count=int(result.get("unmatched_count") or 0),
                    unmatched_json=list(result.get("unmatched") or []),
                    parser_key=str(result.get("parser_key") or "")[:64],
                    parser_stub=bool(result.get("parser_stub")),
                    error=str(result.get("error") or "")[:1024],
                    raw_preview=str(result.get("raw_preview") or ""),
                    created_at=_utcnow(),
                )
                db.add(item)
                added += int(result.get("edges_added") or 0)
                updated += int(result.get("edges_updated") or 0)
                if result.get("ok") and result.get("scanned_node_id"):
                    scanned_ok.add(str(result["scanned_node_id"]))
                for eid in result.get("touched_edge_ids") or []:
                    touched_edges.add(str(eid))
                # Cutover edges already marked missing — skip same-job miss bump.
                for eid in result.get("replaced_edge_ids") or []:
                    touched_edges.add(str(eid))
                job.done = int(job.done or 0) + 1
                job.edges_added = added
                job.edges_updated = updated
                job.updated_at = _utcnow()
                db.commit()

        # Absent on a successfully scanned endpoint → missing; purge after N cycles.
        if scanned_ok:
            newly_missing, purged = _apply_missing_and_purge(
                db,
                scanned_ok=scanned_ok,
                touched_edge_ids=touched_edges,
            )
            stale = newly_missing + purged
            job.edges_stale = stale
            db.commit()

        stats = db.get(TopoFabricStats, "global")
        if stats is None:
            stats = TopoFabricStats(id="global")
            db.add(stats)
        stats.last_discover_at = _utcnow()
        db.commit()
        merge_duplicate_fabric_nodes(db)
        refresh_fabric_stats(db)

        job = db.get(TopoDiscoverJob, job_id)
        if job is not None:
            job.status = "done"
            job.ended_at = _utcnow()
            job.updated_at = job.ended_at
            job.edges_added = added
            job.edges_updated = updated
            job.edges_stale = stale
            db.commit()
        try:
            from .lldp_collect_service import DEFAULT_HISTORY_KEEP, ensure_policy

            keep = int(getattr(ensure_policy(db), "history_keep", DEFAULT_HISTORY_KEEP) or 0)
            prune_discover_jobs(db, keep=keep)
        except Exception:  # noqa: BLE001
            _log.warning("prune_discover_jobs failed job=%s", job_id, exc_info=True)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        job = db.get(TopoDiscoverJob, job_id)
        if job is not None:
            job.status = "failed"
            job.error = str(exc)[:1024]
            job.ended_at = _utcnow()
            job.updated_at = job.ended_at
            db.commit()
    finally:
        db.close()
        with _JOB_LOCK:
            _RUNNING_JOBS.discard(job_id)


def reclaim_stale_discover_jobs(
    db: Session,
    *,
    force_all_open: bool = False,
    now: datetime | None = None,
) -> int:
    """Mark orphaned / hung discover jobs as failed so scheduling can proceed.

    - ``force_all_open``: process restart — all pending/running rows are dead.
    - Otherwise: pending older than pending_stale_sec, or running with stale updated_at.
    """
    now = now or _utcnow()
    run_sec = max(60, int(getattr(settings, "lldp_collect_stale_run_sec", 7200) or 7200))
    pend_sec = max(30, int(getattr(settings, "lldp_collect_pending_stale_sec", 300) or 300))
    open_jobs = (
        db.query(TopoDiscoverJob)
        .filter(TopoDiscoverJob.status.in_(["pending", "running"]))
        .all()
    )
    if not open_jobs:
        return 0
    closed = 0
    for job in open_jobs:
        status = str(job.status or "")
        if force_all_open:
            reason = "stale_running_reset_on_startup"
        elif status == "pending":
            created = job.created_at or job.updated_at or now
            if created > now - timedelta(seconds=pend_sec):
                continue
            reason = "pending_stale_timeout"
        else:
            touched = job.updated_at or job.started_at or job.created_at or now
            if touched > now - timedelta(seconds=run_sec):
                continue
            reason = "running_stale_timeout"
        job.status = "failed"
        job.ended_at = now
        job.updated_at = now
        msg = str(job.error or "").strip()
        job.error = (msg + ("; " if msg else "") + reason)[:1024]
        closed += 1
        with _JOB_LOCK:
            _RUNNING_JOBS.discard(job.id)
    if closed:
        db.commit()
    return closed


def start_discover_job(
    db: Session,
    body: FabricDiscoverRequest,
    *,
    trigger_mode: str = "manual",
) -> FabricDiscoverJobOut:
    reclaim_stale_discover_jobs(db)
    # Serialize multi-worker starts via singleton policy row lock (PG/SQLite FOR UPDATE).
    pol = db.get(LldpCollectPolicy, 1)
    if pol is None:
        pol = LldpCollectPolicy(
            id=1,
            enabled=False,
            interval_days=1,
            interval_hours=24,
            concurrency=4,
            scope_mode="all",
            selected_targets=[],
            auto_add_unmatched=True,
            history_keep=30,
            updated_at=_utcnow(),
        )
        db.add(pol)
        db.commit()
    db.query(LldpCollectPolicy).filter(LldpCollectPolicy.id == 1).with_for_update().one()
    if (
        db.query(TopoDiscoverJob)
        .filter(TopoDiscoverJob.status.in_(["pending", "running"]))
        .first()
        is not None
    ):
        raise HTTPException(status_code=409, detail="lldp_collect_already_running")
    scope = str(body.scope or "ne_ids").strip().lower() or "ne_ids"
    if scope not in {"all_inventory", "ne_ids"}:
        raise HTTPException(status_code=400, detail="invalid_scope")
    trig = str(trigger_mode or getattr(body, "trigger_mode", None) or "manual").strip().lower() or "manual"
    if trig not in {"manual", "schedule", "topology"}:
        trig = "manual"
    now = _utcnow()
    # Persist explicit source lists when present; keep legacy ne_ids for older clients.
    stored_ids = list(body.ne_ids or [])
    if body.managed_ne_ids or body.ume_ne_ids:
        stored_ids = [
            *(f"managed:{x}" for x in (body.managed_ne_ids or []) if str(x).strip()),
            *(f"ume:{x}" for x in (body.ume_ne_ids or []) if str(x).strip()),
        ]
    job = TopoDiscoverJob(
        id=uuid4().hex,
        scope=scope,
        trigger_mode=trig,
        ne_ids_json=stored_ids,
        status="pending",
        total=0,
        done=0,
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    with _JOB_LOCK:
        _RUNNING_JOBS.add(job.id)
    thread = threading.Thread(
        target=_run_discover_job,
        args=(job.id, body),
        name=f"topo-discover-{job.id[:8]}",
        daemon=True,
    )
    thread.start()
    return _job_out(db, job, include_items=False)
