"""Biz-state batch retention: age/day policies + reference protection."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from ..models import (
    BizCompareJob,
    BizCompareRun,
    BizMigrationProject,
    BizMigrationRun,
    BizStateBatch,
    BizStateBatchCommand,
    BizStateLldpNeighbor,
    BizStateMetricRow,
    BizStateTask,
    BizStateVrfRouteSummary,
)


def _utcnow() -> datetime:
    return datetime.utcnow()


def protected_batch_map(db: Session, *, task_id: str = "") -> dict[str, list[str]]:
    """batch_id → reason codes. Empty task_id = all tasks."""
    out: dict[str, list[str]] = defaultdict(list)

    def add(bid: str, reason: str) -> None:
        b = str(bid or "").strip()
        if not b:
            return
        if reason not in out[b]:
            out[b].append(reason)

    q = db.query(BizStateBatch)
    if task_id:
        q = q.filter(BizStateBatch.task_id == task_id)
    for b in q.filter(BizStateBatch.is_baseline.is_(True)).all():
        add(b.id, "manual_baseline")

    for j in db.query(BizCompareJob).all():
        add(j.before_batch_id, "compare_job_before")
        add(j.after_batch_id, "compare_job_after")

    for r in db.query(BizCompareRun).all():
        add(r.before_batch_id, "compare_run_before")
        add(r.after_batch_id, "compare_run_after")

    for p in db.query(BizMigrationProject).all():
        add(p.old_baseline_batch_id, "migration_old_baseline")
        add(p.new_baseline_batch_id, "migration_new_baseline")

    for r in db.query(BizMigrationRun).all():
        add(r.old_batch_id, "migration_run_old")
        add(r.new_batch_id, "migration_run_new")

    return dict(out)


def protected_batch_ids(db: Session, *, task_id: str = "") -> set[str]:
    return set(protected_batch_map(db, task_id=task_id).keys())


def batch_protect_info(db: Session, batch_id: str) -> dict[str, Any]:
    reasons = protected_batch_map(db).get(batch_id, [])
    b = db.get(BizStateBatch, batch_id)
    if b and bool(getattr(b, "is_baseline", False)) and "manual_baseline" not in reasons:
        reasons = ["manual_baseline", *reasons]
    return {
        "protected": bool(reasons),
        "reasons": reasons,
        "is_baseline": bool(b and getattr(b, "is_baseline", False)),
    }


def delete_batch_data(db: Session, batch_id: str) -> None:
    """Hard-delete one batch and child rows (caller must check protection)."""
    bid = str(batch_id or "").strip()
    if not bid:
        return
    db.query(BizStateLldpNeighbor).filter(BizStateLldpNeighbor.batch_id == bid).delete()
    db.query(BizStateVrfRouteSummary).filter(BizStateVrfRouteSummary.batch_id == bid).delete()
    db.query(BizStateMetricRow).filter(BizStateMetricRow.batch_id == bid).delete()
    db.query(BizStateBatchCommand).filter(BizStateBatchCommand.batch_id == bid).delete()
    b = db.get(BizStateBatch, bid)
    if b:
        db.delete(b)


def purge_task_batches(db: Session, task: BizStateTask) -> dict[str, Any]:
    """Apply retention_days + optional daily_keep policy. Never deletes protected batches.

    Returns counts for logging/UI.
    """
    task_id = str(task.id)
    retention_days = max(1, int(getattr(task, "retention_days", None) or 30))
    daily_on = bool(getattr(task, "daily_keep_enabled", False))
    daily_n = max(1, int(getattr(task, "daily_keep_count", None) or 10))

    protected = protected_batch_ids(db, task_id=task_id)
    rows = (
        db.query(BizStateBatch)
        .filter(BizStateBatch.task_id == task_id)
        .order_by(BizStateBatch.started_at.desc())
        .all()
    )
    now = _utcnow()
    cutoff = now - timedelta(days=retention_days)
    today = now.date()

    to_drop: set[str] = set()

    # 1) Age: older than retention_days
    for b in rows:
        if b.id in protected:
            continue
        started = b.started_at or now
        if started < cutoff:
            to_drop.add(b.id)

    # 2) Daily keep (default off): for each past calendar day, keep N newest
    if daily_on:
        by_day: dict[Any, list[BizStateBatch]] = defaultdict(list)
        for b in rows:
            started = b.started_at or now
            d = started.date()
            if d >= today:
                continue  # 当天的多余留到第二天再清
            by_day[d].append(b)
        for _day, day_rows in by_day.items():
            # already ordered desc globally; re-sort
            day_rows.sort(key=lambda x: x.started_at or now, reverse=True)
            for b in day_rows[daily_n:]:
                if b.id not in protected:
                    to_drop.add(b.id)

    dropped = 0
    for bid in to_drop:
        delete_batch_data(db, bid)
        dropped += 1
    if dropped:
        db.commit()
    return {
        "task_id": task_id,
        "dropped": dropped,
        "protected": len(protected),
        "retention_days": retention_days,
        "daily_keep_enabled": daily_on,
        "daily_keep_count": daily_n,
    }


def purge_all_tasks(db: Session) -> dict[str, Any]:
    """Periodic sweep for all tasks (HF idle tasks included)."""
    tasks = db.query(BizStateTask).all()
    total = 0
    details: list[dict[str, Any]] = []
    for t in tasks:
        info = purge_task_batches(db, t)
        total += int(info.get("dropped") or 0)
        if info.get("dropped"):
            details.append(info)
    return {"dropped": total, "tasks": details}
