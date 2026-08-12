from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from .config import settings
from .models import NeCollectionJob, NeCollectionRun

_TERMINAL = frozenset({"success", "fail", "cancelled"})


def _sync_job_counts(job: NeCollectionJob, runs: list[NeCollectionRun]) -> None:
    job.success_count = sum(1 for r in runs if str(r.status) == "success")
    job.fail_count = sum(1 for r in runs if str(r.status) == "fail")
    # cancelled rows are tracked separately via ne_count - success - fail - active


def sync_job_progress(db: Session, job_id: str) -> None:
    """Refresh success/fail counters while a job is still running."""
    job = db.get(NeCollectionJob, job_id)
    if not job or str(job.status or "") != "running":
        return
    runs = db.query(NeCollectionRun).filter(NeCollectionRun.job_id == job_id).all()
    _sync_job_counts(job, runs)
    db.commit()


def finalize_collection_job(db: Session, job_id: str) -> None:
    runs = db.query(NeCollectionRun).filter(NeCollectionRun.job_id == job_id).all()
    if not runs:
        return
    if any(str(r.status or "") not in _TERMINAL for r in runs):
        return
    job = db.get(NeCollectionJob, job_id)
    if not job:
        return
    _sync_job_counts(job, runs)
    job_status = str(job.status or "")
    finish_at = datetime.now()
    if job_status == "paused":
        if not job.ended_at:
            job.ended_at = finish_at
    else:
        success = int(job.success_count or 0)
        job.status = "done" if success > 0 else "failed"
        if not job.ended_at:
            job.ended_at = finish_at
    job.last_run_at = job.ended_at or finish_at
    db.commit()
    try:
        from .collection_policy import ensure_policy, history_keep_value, prune_collection_jobs

        prune_collection_jobs(db, keep=history_keep_value(ensure_policy(db)))
    except Exception:  # noqa: BLE001
        pass


def reconcile_stale_collection_job(db: Session, job_id: str) -> bool:
    """Mark long-running pending/running rows as failed and finalize job if possible."""
    job = db.get(NeCollectionJob, job_id)
    if not job:
        return False
    if str(job.status or "") in ("done", "failed", "paused"):
        return False
    run_stale_sec = max(60, int(settings.ne_collect_stale_run_sec or 900))
    pending_stale_sec = max(30, int(settings.ne_collect_pending_stale_sec or 180))
    now = datetime.now()
    runs = db.query(NeCollectionRun).filter(NeCollectionRun.job_id == job_id).all()
    changed = False
    for run in runs:
        st = str(run.status or "")
        if st in _TERMINAL:
            continue
        if st == "pending":
            # Pending while job is running/queued, or draft job not started yet.
            if str(job.status or "") in ("running", "pending"):
                continue
            anchor = job.started_at or job.created_at
            limit = pending_stale_sec
            reason = "collection_pending_stale"
        else:
            anchor = run.started_at or job.started_at or job.created_at
            limit = run_stale_sec
            reason = "collection_timeout_stale"
        if not anchor:
            continue
        age = (now - anchor).total_seconds()
        if age < limit:
            continue
        run.status = "fail"
        run.message = f"{reason} ({int(age)}s)"
        run.ended_at = now
        changed = True
    if changed:
        db.commit()
        finalize_collection_job(db, job_id)
    return changed
