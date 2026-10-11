"""Enqueue / claim biz_state collect batches (Postgres SKIP LOCKED + NE mutex)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
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
        if task.collect_group_id:
            group_id = task.collect_group_id
            db.close()
            return enqueue_collect_group(group_id, manual=manual, requested_task_id=tid)
        if str(task.source or "").strip().lower() == "import":
            return {
                "ok": False,
                "queued": False,
                "reason": "import_offline_only",
                "task_id": tid,
            }
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
            queued_at=now,
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


def enqueue_collect_group(group_id: str, *, manual: bool = False,
                          requested_task_id: str = "") -> dict[str, Any]:
    """Atomically enqueue both devices with one round identity, or neither."""
    db = SessionLocal()
    try:
        tasks = db.query(BizStateTask).filter(BizStateTask.collect_group_id == group_id).order_by(
            BizStateTask.id).with_for_update().populate_existing().all()
        result: dict[str, Any] = {"ok": False, "queued": False, "task_id": requested_task_id,
                                  "collect_group_id": group_id}
        if len(tasks) != 2 or len({t.ne_id for t in tasks}) != 2:
            return {**result, "reason": "collect_pair_invalid"}
        if max_concurrent_tasks() < 2:
            return {**result, "reason": "collect_pair_capacity"}
        from ..cli_budget import clamp_cli_workers

        worker_cap = clamp_cli_workers(int(getattr(settings, "biz_state_worker_collect_threads", None)
            or getattr(settings, "biz_state_dispatch_workers", 8) or 8))
        if worker_cap < 2:
            return {**result, "reason": "collect_pair_capacity"}
        if any(t.collect_running for t in tasks):
            return {**result, "reason": "collect_pair_busy"}
        for task in tasks:
            if task.source == "import" or (not manual and (task.status != "running" or task.collect_manual_only)):
                return {**result, "reason": "not_scheduled"}
            if task.status in ("", "deleted"):
                return {**result, "reason": "bad_status"}
            if not db.query(BizStateTaskItem.id).filter(BizStateTaskItem.task_id == task.id,
                                                       BizStateTaskItem.enabled.is_(True)).first():
                return {**result, "reason": "no_enabled_items"}
        now, round_id = _utcnow(), uuid4().hex
        batches = []
        for task in tasks:
            task.collect_running = True
            task.collect_queued_at = now
            task.last_error = ""
            task.updated_at = now
            batch = BizStateBatch(id=uuid4().hex, task_id=task.id, source=task.source,
                ne_id=task.ne_id, ne_name=task.ne_name, vendor=task.vendor, status="queued",
                started_at=now, queued_at=now, collect_group_id=group_id,
                collect_round_id=round_id, collect_priority=10 if task.collect_manual_only else 20,
                message="queued_manual" if manual else "queued")
            db.add(batch)
            batches.append({"batch_id": batch.id, "task_id": task.id})
        db.commit()
        selected = next((b for b in batches if b["task_id"] == requested_task_id), batches[0])
        return {**result, **selected, "ok": True, "queued": True, "collect_round_id": round_id,
                "batches": batches, "manual": manual}
    except Exception:
        db.rollback()
        _log.exception("enqueue collect pair failed group=%s", group_id)
        return {"ok": False, "queued": False, "reason": "enqueue_error", "task_id": requested_task_id}
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
    from sqlalchemy import and_, case, text

    global_cap = max_concurrent_tasks()
    db = SessionLocal()
    try:
        pg = _dialect_supports_skip_locked(db)
        if pg:
            # A short transaction lock makes capacity reservation and pair claims
            # atomic across worker processes. Never held during device I/O.
            db.execute(text("SELECT pg_advisory_xact_lock(hashtext('biz-state-claim-capacity'))"))
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

        # Manual route rounds yield to fresh monitoring, but must eventually
        # run even when a busy device always has regular metrics queued.
        priority = case((and_(BizStateBatch.collect_priority == 10,
            BizStateBatch.queued_at <= _utcnow() - timedelta(seconds=300)), 30),
            else_=BizStateBatch.collect_priority)
        q = (
            db.query(BizStateBatch)
            .filter(BizStateBatch.status == "queued")
            .order_by(priority.desc(), BizStateBatch.started_at.asc(), BizStateBatch.id.asc())
        )
        if pg:
            q = q.with_for_update(skip_locked=True)
        else:
            q = q.with_for_update()

        candidates = q.limit(max(slots * 8, 64)).all()
        claimed_rows: list[BizStateBatch] = []
        visited: set[str] = set()
        for batch in candidates:
            if batch.id in visited:
                continue
            if len(claimed_rows) >= slots:
                break
            group = [batch]
            if batch.collect_round_id:
                peers = db.query(BizStateBatch).filter(
                    BizStateBatch.collect_round_id == batch.collect_round_id,
                ).with_for_update(skip_locked=pg).all()
                if len(peers) != 2 or any(p.status != "queued" for p in peers):
                    continue
                group = peers
            visited.update(p.id for p in group)
            if len(group) > slots - len(claimed_rows):
                continue
            nes = {str(p.ne_id or "").strip() for p in group}
            if nes & busy_nes:
                continue
            now = _utcnow()
            for member in group:
                member.status = "running"
                member.message = ""
                if member.collect_round_id:
                    member.started_at = now
                    task = db.get(BizStateTask, member.task_id)
                    if task:
                        task.last_collect_started_at = now
                claimed_rows.append(member)
            busy_nes.update(nes - {""})

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
            last = claimed_rows[-1]
            demoted = [b for b in claimed_rows if b.collect_round_id == last.collect_round_id] if last.collect_round_id else [last]
            for demote in demoted:
                claimed_rows.remove(demote)
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
