from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from .collection_job_state import finalize_collection_job, reconcile_stale_collection_job
from .models import NeCollectionJob, NeCollectionRun
from .ne_collect_runner import schedule_collection_runs

_log = logging.getLogger("netx.collection.recovery")


def _parse_commands(text: str) -> list[str]:
    lines: list[str] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return lines


def recover_collection_jobs_on_startup(db: Session) -> int:
    """Re-queue pending runs for running jobs after API restart; reconcile stale rows."""
    jobs = db.query(NeCollectionJob).filter(NeCollectionJob.status == "running").all()
    resumed = 0
    for job in jobs:
        job_id = str(job.id)
        reconcile_stale_collection_job(db, job_id)
        db.refresh(job)
        if str(job.status or "") in ("done", "failed"):
            continue
        commands = _parse_commands(str(job.commands or ""))
        if not commands:
            job.status = "failed"
            job.error_message = "commands_empty_on_recovery"
            db.commit()
            continue
        pending = (
            db.query(NeCollectionRun)
            .filter(NeCollectionRun.job_id == job_id, NeCollectionRun.status == "pending")
            .all()
        )
        if not pending:
            finalize_collection_job(db, job_id)
            continue
        run_ids = [str(r.id) for r in pending]
        schedule_collection_runs(job_id, run_ids, commands)
        resumed += len(run_ids)
        _log.info("resumed collection job=%s pending_runs=%s", job_id, len(run_ids))
    return resumed
