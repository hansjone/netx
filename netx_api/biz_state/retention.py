"""Biz-state batch retention: age/day policies + reference protection."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
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


def protected_batch_map(
    db: Session, *, task_id: str = "", batch_ids: list[str] | None = None,
) -> dict[str, list[str]]:
    """Only read reference columns, scoped to the requested task/batches when given."""
    out: dict[str, list[str]] = defaultdict(list)
    wanted = set(batch_ids) if batch_ids is not None else None
    if wanted == set():
        return {}
    scope = select(BizStateBatch.id)
    q = db.query(BizStateBatch.id, BizStateBatch.is_baseline, BizStateBatch.status)
    if task_id:
        scope = scope.where(BizStateBatch.task_id == task_id)
        q = q.filter(BizStateBatch.task_id == task_id)
    if wanted is not None:
        scope = scope.where(BizStateBatch.id.in_(wanted))
        q = q.filter(BizStateBatch.id.in_(wanted))
    batch_rows = q.all() if task_id or wanted is not None else q.filter(or_(
        BizStateBatch.is_baseline.is_(True), BizStateBatch.status.in_(("queued", "running")),
    )).all()
    if task_id:
        wanted = {row.id for row in batch_rows}

    def add(bid: str, reason: str) -> None:
        b = str(bid or "").strip()
        if not b or (wanted is not None and b not in wanted):
            return
        if reason not in out[b]:
            out[b].append(reason)

    for b in batch_rows:
        if b.is_baseline:
            add(b.id, "manual_baseline")
        if b.status in ("queued", "running"):
            add(b.id, "active_collection")

    references = (
        (BizCompareJob.before_batch_id, BizCompareJob.after_batch_id, "compare_job_before", "compare_job_after"),
        (BizCompareRun.before_batch_id, BizCompareRun.after_batch_id, "compare_run_before", "compare_run_after"),
        (BizMigrationProject.old_baseline_batch_id, BizMigrationProject.new_baseline_batch_id, "migration_old_baseline", "migration_new_baseline"),
        (BizMigrationRun.old_batch_id, BizMigrationRun.new_batch_id, "migration_run_old", "migration_run_new"),
    )
    for before, after, before_reason, after_reason in references:
        refs = db.query(before, after)
        if task_id or batch_ids is not None:
            refs = refs.filter(or_(before.in_(scope), after.in_(scope)))
        for before_id, after_id in refs:
            add(before_id, before_reason)
            add(after_id, after_reason)

    return dict(out)


def protected_batch_ids(db: Session, *, task_id: str = "") -> set[str]:
    return set(protected_batch_map(db, task_id=task_id).keys())


def batch_protect_info(db: Session, batch_id: str) -> dict[str, Any]:
    reasons = protected_batch_map(db, batch_ids=[batch_id]).get(batch_id, [])
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
