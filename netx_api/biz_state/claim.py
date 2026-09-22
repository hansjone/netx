"""Enqueue / claim biz_state collect batches (Postgres SKIP LOCKED + NE mutex)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal
from ..models import BizStateBatch, BizStateTask, BizStateTaskItem
from ..timeutil import utcnow_naive

_log = logging.getLogger("netx.biz_state.claim")


def _utcnow() -> datetime:
    return utcnow_naive()


def max_concurrent_tasks() -> int:
    return max(1, int(getattr(settings, "biz_state_max_concurrent_tasks", 16) or 16))


def enqueue_collect(task_id: str, *, manual: bool = False) -> dict[str, Any]:
    """Create a queued batch and mark task collect_running. Does not open SSH.

    Returns keys: ok, queued, reason?, batch_id?, task_id
    """
    tid = str(task_id or "").strip()
    if not tid:
        return {"ok": False, "queued": False, "reason": "missing_task_id", "task_id": ""}

    db = SessionLocal()
    try:
        task = db.get(BizStateTask, tid)
        if not task:
            return {"ok": False, "queued": False, "reason": "task_not_found", "task_id": tid}
        if bool(task.collect_running):
            return {
                "ok": True,
                "queued": False,
                "reason": "already_collecting",
                "task_id": tid,
            }

        st = str(task.status or "").strip()
        if manual:
            if st in ("", "deleted"):
                return {"ok": False, "queued": False, "reason": "bad_status", "task_id": tid}
        else:
            if st != "running":
                return {"ok": False, "queued": False, "reason": "not_scheduled", "task_id": tid}

        items = (
            db.query(BizStateTaskItem)
            .filter(
                BizStateTaskItem.task_id == tid,
                BizStateTaskItem.enabled.is_(True),
            )
            .limit(1)
            .all()
        )
        if not items:
            task.last_error = "no enabled task items"
            task.updated_at = _utcnow()
            db.commit()
            return {
                "ok": False,
                "queued": False,
                "reason": "no_enabled_items",
                "task_id": tid,
            }

        now = _utcnow()
        task.collect_running = True
        task.last_collect_started_at = now
        task.last_error = ""
        task.updated_at = now
        if hasattr(task, "collect_queued_at"):
            task.collect_queued_at = now

        batch = BizStateBatch(
            id=uuid4().hex,
            task_id=task.id,
            source=task.source,
            ne_id=task.ne_id,
            ne_name=task.ne_name,
            vendor=task.vendor,
            status="queued",
            started_at=now,
            message="queued" if not manual else "queued_manual",
        )
        db.add(batch)
        db.commit()
        return {
            "ok": True,
            "queued": True,
            "batch_id": batch.id,
            "task_id": tid,
            "manual": bool(manual),
        }
    except Exception:
        _log.exception("enqueue_collect failed task=%s", tid)
        try:
            db.rollback()
        except Exception:
            pass
        return {"ok": False, "queued": False, "reason": "enqueue_error", "task_id": tid}
    finally:
        db.close()


def _dialect_supports_skip_locked(db: Session) -> bool:
    try:
        bind = db.get_bind()
        name = str(getattr(getattr(bind, "dialect", None), "name", "") or "").lower()
        return name == "postgresql"
    except Exception:
        return False


def count_running_batches(db: Session | None = None) -> int:
    own = db is None
    if own:
        db = SessionLocal()
    assert db is not None
    try:
        return int(
            db.query(BizStateBatch).filter(BizStateBatch.status == "running").count()
        )
    finally:
        if own:
            db.close()


def claim_queued_batches(limit: int | None = None) -> list[dict[str, Any]]:
    """Claim up to ``limit`` queued batches (SKIP LOCKED + same-NE mutex).

    Returns list of {batch_id, task_id, source, ne_id, vendor, device_type}.
    Global ceiling is always ``max_concurrent_tasks()``; ``limit`` only caps
    how many this caller wants in one call.
    """
    from sqlalchemy import text

    global_cap = max_concurrent_tasks()
    db = SessionLocal()
    try:
        running_n = (
            db.query(BizStateBatch).filter(BizStateBatch.status == "running").count()
        )
        slots = max(0, global_cap - int(running_n))
        if limit is not None:
            slots = min(slots, max(0, int(limit)))
        if slots <= 0:
            return []

        busy_nes = {
            str(r[0] or "").strip()
            for r in db.query(BizStateBatch.ne_id)
            .filter(BizStateBatch.status == "running")
            .all()
            if str(r[0] or "").strip()
        }

        q = (
            db.query(BizStateBatch)
            .filter(BizStateBatch.status == "queued")
            .order_by(BizStateBatch.started_at.asc())
        )
        pg = _dialect_supports_skip_locked(db)
        if pg:
            q = q.with_for_update(skip_locked=True)
        else:
            q = q.with_for_update()

        candidates = q.limit(max(slots * 4, slots)).all()
        claimed_rows: list[BizStateBatch] = []
        for batch in candidates:
            if len(claimed_rows) >= slots:
                break
            ne = str(batch.ne_id or "").strip()
            if ne and ne in busy_nes:
                continue
            # Serialize claims per NE across workers (Postgres).
            if ne and pg:
                try:
                    db.execute(
                        text("SELECT pg_advisory_xact_lock(hashtext(:ne))"),
                        {"ne": ne},
                    )
                except Exception:
                    _log.exception("advisory lock failed ne=%s", ne)
                conflict = (
                    db.query(BizStateBatch.id)
                    .filter(
                        BizStateBatch.status == "running",
                        BizStateBatch.ne_id == ne,
                        BizStateBatch.id != batch.id,
                    )
                    .first()
                )
                if conflict:
                    continue
            batch.status = "running"
            batch.message = ""
            claimed_rows.append(batch)
            if ne:
                busy_nes.add(ne)

        if not claimed_rows:
            db.rollback()
            return []

        # Autoflush so COUNT sees our running marks; trim if over global cap.
        try:
            db.flush()
        except Exception:
            pass
        n_running = int(
            db.query(BizStateBatch).filter(BizStateBatch.status == "running").count()
        )
        while n_running > global_cap and claimed_rows:
            demote = claimed_rows.pop()
            demote.status = "queued"
            demote.message = "queued"
            n_running -= 1

        if not claimed_rows:
            db.rollback()
            return []

        out: list[dict[str, Any]] = []
        for batch in claimed_rows:
            task = db.get(BizStateTask, batch.task_id)
            out.append(
                {
                    "batch_id": batch.id,
                    "task_id": str(batch.task_id or ""),
                    "source": str(
                        batch.source or (task.source if task else "") or "managed"
                    ),
                    "ne_id": str(batch.ne_id or (task.ne_id if task else "") or ""),
                    "vendor": str(batch.vendor or (task.vendor if task else "") or ""),
                    "device_type": str((task.device_type if task else "") or ""),
                }
            )
        db.commit()
        return out
    except Exception:
        _log.exception("claim_queued_batches failed")
        try:
            db.rollback()
        except Exception:
            pass
        return []
    finally:
        db.close()


def reclaim_stale_queued(*, max_age_sec: int = 3600) -> int:
    """Fail queued batches older than max_age_sec (no worker drained them)."""
    from datetime import timedelta

    age = max(60, int(max_age_sec))
    cutoff = _utcnow() - timedelta(seconds=age)
    db = SessionLocal()
    n = 0
    try:
        stale = (
            db.query(BizStateBatch)
            .filter(
                BizStateBatch.status == "queued",
                BizStateBatch.started_at < cutoff,
            )
            .all()
        )
        for b in stale:
            b.status = "failed"
            b.message = f"stale_queued_timeout ({age}s)"[:1020]
            b.ended_at = _utcnow()
            task = db.get(BizStateTask, b.task_id)
            if task and bool(task.collect_running):
                # Only clear if no other running/queued for this task.
                other = (
                    db.query(BizStateBatch)
                    .filter(
                        BizStateBatch.task_id == b.task_id,
                        BizStateBatch.id != b.id,
                        BizStateBatch.status.in_(("queued", "running")),
                    )
                    .first()
                )
                if not other:
                    task.collect_running = False
                    if hasattr(task, "collect_queued_at"):
                        task.collect_queued_at = None
                    task.last_collect_ended_at = _utcnow()
                    task.last_error = str(b.message)[:1020]
            n += 1
        if n:
            db.commit()
            _log.warning("reclaimed stale queued batches n=%s age_sec=%s", n, age)
        return n
    except Exception:
        _log.exception("reclaim_stale_queued failed")
        try:
            db.rollback()
        except Exception:
            pass
        return 0
    finally:
        db.close()


def open_slots() -> int:
    """How many new running batches we can still start under the ceiling."""
    return max(0, max_concurrent_tasks() - count_running_batches())
