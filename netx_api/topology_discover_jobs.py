"""Discover job lifecycle: start, pause/resume/stop, background run, stale reclaim."""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
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

_ACTIVE_STATUSES = ("pending", "running", "paused")
_FINISHED_STATUSES = ("done", "failed", "cancelled")


def _target_key(t: dict) -> str:
    ume = str(t.get("ume_ne_id") or "").strip()
    if ume:
        return f"ume:{ume}"
    return f"managed:{str(t.get('ne_id') or '').strip()}"


def _item_key(item: TopoDiscoverJobItem) -> str:
    ume = str(item.ume_ne_id or "").strip()
    if ume:
        return f"ume:{ume}"
    return f"managed:{str(item.ne_id or '').strip()}"


def _read_job_status(db: Session, job_id: str) -> str:
    job = db.get(TopoDiscoverJob, job_id)
    if job is None:
        return "cancelled"
    return str(job.status or "")


def _request_from_job(db: Session, job: TopoDiscoverJob) -> FabricDiscoverRequest:
    """Rebuild a discover request from a stored job (resume after worker death)."""
    pol = db.get(LldpCollectPolicy, 1)
    concurrency = max(1, min(32, int(getattr(pol, "concurrency", None) or 4)))
    auto_add = bool(getattr(pol, "auto_add_unmatched", True)) if pol is not None else True
    scope = str(job.scope or "ne_ids").strip().lower() or "ne_ids"
    stored = list(job.ne_ids_json or [])
    managed_ids: list[str] = []
    ume_ids: list[str] = []
    legacy: list[str] = []
    for raw in stored:
        s = str(raw or "").strip()
        if not s:
            continue
        if s.startswith("managed:"):
            managed_ids.append(s[len("managed:") :])
        elif s.startswith("ume:"):
            ume_ids.append(s[len("ume:") :])
        else:
            legacy.append(s)
    if scope == "all_inventory":
        return FabricDiscoverRequest(
            scope="all_inventory",
            ne_ids=[],
            managed_ne_ids=[],
            ume_ne_ids=[],
            concurrency=concurrency,
            auto_add_unmatched=auto_add,
            trigger_mode=str(job.trigger_mode or "manual"),
        )
    if managed_ids or ume_ids:
        return FabricDiscoverRequest(
            scope="ne_ids",
            ne_ids=[],
            managed_ne_ids=managed_ids,
            ume_ne_ids=ume_ids,
            concurrency=concurrency,
            auto_add_unmatched=auto_add,
            trigger_mode=str(job.trigger_mode or "manual"),
        )
    return FabricDiscoverRequest(
        scope="ne_ids",
        ne_ids=legacy,
        managed_ne_ids=[],
        ume_ne_ids=[],
        concurrency=concurrency,
        auto_add_unmatched=auto_add,
        trigger_mode=str(job.trigger_mode or "manual"),
    )


def _record_item(
    db: Session,
    job: TopoDiscoverJob,
    job_id: str,
    result: dict,
    *,
    added: int,
    updated: int,
) -> tuple[int, int]:
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
    job.done = int(job.done or 0) + 1
    job.edges_added = added
    job.edges_updated = updated
    job.updated_at = _utcnow()
    db.commit()
    if int(job.done or 0) % 50 == 0:
        try:
            refresh_fabric_stats(db)
        except Exception:  # noqa: BLE001
            db.rollback()
    return added, updated


def _finalize_success(
    db: Session,
    job_id: str,
    *,
    added: int,
    updated: int,
    stale: int,
) -> None:
    stats = db.get(TopoFabricStats, "global")
    if stats is None:
        stats = TopoFabricStats(id="global")
        db.add(stats)
    stats.last_discover_at = _utcnow()
    db.commit()
    merge_duplicate_fabric_nodes(db)
    refresh_fabric_stats(db)

    job = db.get(TopoDiscoverJob, job_id)
    if job is None:
        return
    # Stop/pause may have won the race while we were finishing.
    if str(job.status or "") in ("cancelled", "paused"):
        return
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


def _finalize_cancelled(db: Session, job_id: str, *, added: int, updated: int) -> None:
    try:
        refresh_fabric_stats(db)
    except Exception:  # noqa: BLE001
        db.rollback()
    job = db.get(TopoDiscoverJob, job_id)
    if job is None:
        return
    if str(job.status or "") != "cancelled":
        job.status = "cancelled"
        job.error = (str(job.error or "").strip() or "stopped_by_user")[:1024]
    job.ended_at = _utcnow()
    job.updated_at = job.ended_at
    job.edges_added = added
    job.edges_updated = updated
    db.commit()
    try:
        from .lldp_collect_service import DEFAULT_HISTORY_KEEP, ensure_policy

        keep = int(getattr(ensure_policy(db), "history_keep", DEFAULT_HISTORY_KEEP) or 0)
        prune_discover_jobs(db, keep=keep)
    except Exception:  # noqa: BLE001
        _log.warning("prune_discover_jobs failed job=%s", job_id, exc_info=True)


def _run_discover_job(
    job_id: str,
    body: FabricDiscoverRequest,
    *,
    resume: bool = False,
) -> None:
    db = SessionLocal()
    try:
        job = db.get(TopoDiscoverJob, job_id)
        if job is None:
            return
        if str(job.status or "") == "cancelled":
            return
        if str(job.status or "") != "paused":
            job.status = "running"
        if not job.started_at:
            job.started_at = _utcnow()
        job.updated_at = _utcnow()
        try:
            all_targets = _resolve_scan_targets(db, body)
        except HTTPException as exc:
            job.status = "failed"
            job.error = str(exc.detail or "resolve_failed")[:1024]
            job.ended_at = _utcnow()
            job.updated_at = job.ended_at
            db.commit()
            return

        prior_items = (
            db.query(TopoDiscoverJobItem).filter(TopoDiscoverJobItem.job_id == job_id).all()
        )
        done_keys = {_item_key(it) for it in prior_items if _item_key(it) not in {"managed:", "ume:"}}
        scanned_ok: set[str] = {
            str(it.fabric_node_id)
            for it in prior_items
            # Resume: only prior items with trustworthy LLDP evidence can miss-judge.
            if it.ok
            and str(it.fabric_node_id or "").strip()
            and not bool(it.parser_stub)
            and str(it.error or "").strip()
            not in {"parser_stub", "empty_cli_output", "vendor_or_device_type_required"}
        }
        touched_edges: set[str] = set()
        # After worker death we lost in-memory touched edges — skip miss to avoid false marks.
        skip_miss = bool(resume)

        if not prior_items:
            job.total = len(all_targets)
        elif int(job.total or 0) <= 0:
            job.total = len(all_targets)
        db.commit()

        targets = [t for t in all_targets if _target_key(t) not in done_keys]
        try:
            _preensure_discover_targets(db, targets)
        except Exception:  # noqa: BLE001
            db.rollback()

        from .cli_budget import clamp_cli_workers

        concurrency = clamp_cli_workers(int(body.concurrency or 4))
        added = int(job.edges_added or 0)
        updated = int(job.edges_updated or 0)
        stale = int(job.edges_stale or 0)
        remaining = list(targets)
        in_flight: dict = {}
        cancelled = False

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            while remaining or in_flight:
                db.expire_all()
                status = _read_job_status(db, job_id)

                if status == "cancelled":
                    cancelled = True
                    for fut in list(in_flight):
                        fut.cancel()
                    # Drain in-flight that already started (cancel is best-effort).
                    while in_flight:
                        done_set, _ = wait(in_flight.keys(), return_when=FIRST_COMPLETED)
                        for fut in done_set:
                            tgt = in_flight.pop(fut, None)
                            if fut.cancelled():
                                continue
                            try:
                                result = fut.result()
                            except Exception as exc:  # noqa: BLE001
                                result = {
                                    "ne_id": str((tgt or {}).get("ne_id") or ""),
                                    "ume_ne_id": str((tgt or {}).get("ume_ne_id") or ""),
                                    "ne_name": str((tgt or {}).get("ne_name") or ""),
                                    "ne_ip": str((tgt or {}).get("ne_ip") or ""),
                                    "ok": False,
                                    "error": str(exc)[:1024],
                                }
                            job = db.get(TopoDiscoverJob, job_id)
                            if job is None:
                                break
                            added, updated = _record_item(
                                db, job, job_id, result, added=added, updated=updated
                            )
                    break

                if status == "paused":
                    if not in_flight:
                        time.sleep(0.5)
                        continue
                    # Let in-flight finish, but do not submit more.
                elif status in ("running", "pending"):
                    if status == "pending":
                        job = db.get(TopoDiscoverJob, job_id)
                        if job is not None:
                            job.status = "running"
                            job.updated_at = _utcnow()
                            db.commit()
                    while remaining and len(in_flight) < concurrency:
                        if _read_job_status(db, job_id) not in ("running", "pending"):
                            break
                        t = remaining.pop(0)
                        fut = pool.submit(
                            _discover_one_target,
                            t,
                            auto_add_unmatched=bool(body.auto_add_unmatched),
                        )
                        in_flight[fut] = t
                else:
                    # Unexpected terminal status.
                    cancelled = status == "cancelled"
                    break

                if not in_flight:
                    if status == "paused":
                        continue
                    break

                done_set, _ = wait(in_flight.keys(), timeout=0.5, return_when=FIRST_COMPLETED)
                if not done_set:
                    continue
                for fut in done_set:
                    tgt = in_flight.pop(fut, None)
                    if fut.cancelled():
                        continue
                    try:
                        result = fut.result()
                    except Exception as exc:  # noqa: BLE001
                        result = {
                            "ne_id": str((tgt or {}).get("ne_id") or ""),
                            "ume_ne_id": str((tgt or {}).get("ume_ne_id") or ""),
                            "ne_name": str((tgt or {}).get("ne_name") or ""),
                            "ne_ip": str((tgt or {}).get("ne_ip") or ""),
                            "ok": False,
                            "error": str(exc)[:1024],
                        }
                    job = db.get(TopoDiscoverJob, job_id)
                    if job is None:
                        cancelled = True
                        break
                    added, updated = _record_item(
                        db, job, job_id, result, added=added, updated=updated
                    )
                    if (
                        result.get("ok")
                        and result.get("lldp_evidence_ok")
                        and result.get("scanned_node_id")
                    ):
                        scanned_ok.add(str(result["scanned_node_id"]))
                    elif result.get("ok") and result.get("scanned_node_id"):
                        # Backward-compatible: older workers only set scanned_node_id
                        # when evidence was implied; still require no stub/empty errors.
                        err = str(result.get("error") or "").strip()
                        if not result.get("parser_stub") and err not in {
                            "parser_stub",
                            "empty_cli_output",
                        }:
                            scanned_ok.add(str(result["scanned_node_id"]))
                    for eid in result.get("touched_edge_ids") or []:
                        touched_edges.add(str(eid))
                    for eid in result.get("replaced_edge_ids") or []:
                        touched_edges.add(str(eid))

        if cancelled or _read_job_status(db, job_id) == "cancelled":
            _finalize_cancelled(db, job_id, added=added, updated=updated)
            return

        # May still be paused with no remaining work — treat as done.
        db.expire_all()
        status = _read_job_status(db, job_id)
        if status == "paused" and remaining:
            # Worker exiting while paused with work left — keep paused for later resume.
            job = db.get(TopoDiscoverJob, job_id)
            if job is not None:
                job.edges_added = added
                job.edges_updated = updated
                job.updated_at = _utcnow()
                db.commit()
            return

        if scanned_ok and not skip_miss:
            newly_missing, purged = _apply_missing_and_purge(
                db,
                scanned_ok=scanned_ok,
                touched_edge_ids=touched_edges,
            )
            stale = newly_missing + purged
            job = db.get(TopoDiscoverJob, job_id)
            if job is not None:
                job.edges_stale = stale
                db.commit()

        _finalize_success(db, job_id, added=added, updated=updated, stale=stale)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        job = db.get(TopoDiscoverJob, job_id)
        if job is not None and str(job.status or "") not in ("cancelled", "paused"):
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

    - ``force_all_open``: process restart — pending/running rows are dead (paused kept for resume).
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
        .filter(TopoDiscoverJob.status.in_(list(_ACTIVE_STATUSES)))
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


def pause_discover_job(db: Session, job_id: str) -> FabricDiscoverJobOut:
    job = db.get(TopoDiscoverJob, str(job_id or "").strip())
    if job is None:
        raise HTTPException(status_code=404, detail="discover_job_not_found")
    if str(job.status or "") not in ("running", "pending"):
        raise HTTPException(status_code=400, detail="job_not_running")
    job.status = "paused"
    job.updated_at = _utcnow()
    db.commit()
    db.refresh(job)
    return _job_out(db, job, include_items=False)


def resume_discover_job(db: Session, job_id: str) -> FabricDiscoverJobOut:
    job = db.get(TopoDiscoverJob, str(job_id or "").strip())
    if job is None:
        raise HTTPException(status_code=404, detail="discover_job_not_found")
    if str(job.status or "") != "paused":
        raise HTTPException(status_code=400, detail="job_not_paused")
    other = (
        db.query(TopoDiscoverJob)
        .filter(
            TopoDiscoverJob.id != job.id,
            TopoDiscoverJob.status.in_(["pending", "running"]),
        )
        .first()
    )
    if other is not None:
        raise HTTPException(status_code=409, detail="lldp_collect_already_running")
    remaining = int(job.total or 0) - int(job.done or 0)
    if int(job.total or 0) > 0 and remaining <= 0:
        raise HTTPException(status_code=400, detail="no_pending_targets")
    job.status = "running"
    job.updated_at = _utcnow()
    db.commit()
    db.refresh(job)

    with _JOB_LOCK:
        alive = job.id in _RUNNING_JOBS
    if alive:
        return _job_out(db, job, include_items=False)

    body = _request_from_job(db, job)
    with _JOB_LOCK:
        _RUNNING_JOBS.add(job.id)
    thread = threading.Thread(
        target=_run_discover_job,
        args=(job.id, body),
        kwargs={"resume": True},
        name=f"topo-discover-{job.id[:8]}",
        daemon=True,
    )
    thread.start()
    return _job_out(db, job, include_items=False)


def stop_discover_job(db: Session, job_id: str) -> FabricDiscoverJobOut:
    """Cancel remaining work and close the job (running/paused/pending)."""
    job = db.get(TopoDiscoverJob, str(job_id or "").strip())
    if job is None:
        raise HTTPException(status_code=404, detail="discover_job_not_found")
    if str(job.status or "") not in _ACTIVE_STATUSES:
        raise HTTPException(status_code=400, detail="job_not_active")
    now = _utcnow()
    job.status = "cancelled"
    job.error = "stopped_by_user"
    job.ended_at = now
    job.updated_at = now
    db.commit()
    db.refresh(job)
    # If no worker is attached (paused after restart), close immediately for clients.
    with _JOB_LOCK:
        alive = job.id in _RUNNING_JOBS
    if not alive:
        try:
            refresh_fabric_stats(db)
        except Exception:  # noqa: BLE001
            db.rollback()
        try:
            from .lldp_collect_service import DEFAULT_HISTORY_KEEP, ensure_policy

            keep = int(getattr(ensure_policy(db), "history_keep", DEFAULT_HISTORY_KEEP) or 0)
            prune_discover_jobs(db, keep=keep)
        except Exception:  # noqa: BLE001
            _log.warning("prune_discover_jobs after stop failed job=%s", job_id, exc_info=True)
    return _job_out(db, job, include_items=False)


def recover_lldp_discover_on_startup(db: Session) -> int:
    """Resume interrupted LLDP discover after process restart (config-sync style).

    - Keep the newest active job; mark older actives failed.
    - ``paused`` stays paused (no auto dispatch) but still occupies the slot.
    - ``pending`` / ``running`` are re-spawned with ``resume=True`` for remaining targets.
    """
    actives = (
        db.query(TopoDiscoverJob)
        .filter(TopoDiscoverJob.status.in_(list(_ACTIVE_STATUSES)))
        .order_by(TopoDiscoverJob.created_at.asc())
        .all()
    )
    if not actives:
        return 0

    primary = actives[-1]
    now = _utcnow()
    for stale in actives[:-1]:
        _log.warning(
            "lldp discover recovery closing older active job=%s (keep=%s)",
            stale.id,
            primary.id,
        )
        stale.status = "failed"
        stale.ended_at = now
        stale.updated_at = now
        msg = str(stale.error or "").strip()
        stale.error = (msg + ("; " if msg else "") + "superseded_active_job")[:1024]
        with _JOB_LOCK:
            _RUNNING_JOBS.discard(stale.id)
    db.commit()
    db.refresh(primary)

    if str(primary.status or "") == "paused":
        _log.info("lldp discover recovery job=%s stays paused (blocks new jobs)", primary.id)
        return 0

    total = int(primary.total or 0)
    done = int(primary.done or 0)
    if total > 0 and done >= total:
        primary.status = "done"
        primary.ended_at = now
        primary.updated_at = now
        db.commit()
        _log.info("lldp discover recovery job=%s already complete", primary.id)
        return 0

    primary.status = "running"
    if not primary.started_at:
        primary.started_at = now
    primary.updated_at = now
    db.commit()
    db.refresh(primary)

    body = _request_from_job(db, primary)
    with _JOB_LOCK:
        if primary.id in _RUNNING_JOBS:
            _log.info("lldp discover recovery job=%s already has worker", primary.id)
            return 0
        _RUNNING_JOBS.add(primary.id)
    thread = threading.Thread(
        target=_run_discover_job,
        args=(primary.id, body),
        kwargs={"resume": True},
        name=f"topo-discover-{primary.id[:8]}",
        daemon=True,
    )
    thread.start()
    _log.info(
        "lldp discover recovery resumed job=%s done=%s/%s",
        primary.id,
        done,
        total,
    )
    return 1
