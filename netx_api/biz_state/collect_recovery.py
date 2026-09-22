"""Recover biz-state collects interrupted by process restart."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from ..models import BizStateBatch, BizStateBatchCommand, BizStateTask
from ..timeutil import utcnow_naive

_log = logging.getLogger("netx.biz_state.recovery")

_INTERRUPT_MARK = "interrupted_by_restart"


def recover_interrupted_collects_on_startup(db: Session) -> dict[str, Any]:
    """Mark orphaned running batches as partial; clear stuck collect_running.

    Does **not** resume or re-dispatch collects — process crash mid-batch leaves
    whatever rows/commands were already persisted.
    """
    now = utcnow_naive()
    batch_n = 0
    cmd_n = 0
    task_n = 0

    stuck_batches = (
        db.query(BizStateBatch)
        .filter(BizStateBatch.status.in_(("running", "queued")))
        .all()
    )
    for b in stuck_batches:
        b.status = "partial"
        if not b.ended_at:
            b.ended_at = now
        msg = str(b.message or "").strip()
        if _INTERRUPT_MARK not in msg:
            b.message = f"{msg} | {_INTERRUPT_MARK}".strip(" |")[:1020]
        batch_n += 1

        running_cmds = (
            db.query(BizStateBatchCommand)
            .filter(
                BizStateBatchCommand.batch_id == b.id,
                BizStateBatchCommand.parse_status == "running",
            )
            .all()
        )
        for c in running_cmds:
            c.parse_status = "failed"
            cmsg = str(c.message or "").strip()
            if _INTERRUPT_MARK not in cmsg:
                c.message = f"{cmsg} | {_INTERRUPT_MARK}".strip(" |")[:1020]
            cmd_n += 1

    stuck_tasks = (
        db.query(BizStateTask).filter(BizStateTask.collect_running.is_(True)).all()
    )
    for t in stuck_tasks:
        t.collect_running = False
        if hasattr(t, "collect_queued_at"):
            t.collect_queued_at = None
        if not t.last_collect_ended_at:
            t.last_collect_ended_at = now
        err = str(t.last_error or "").strip()
        if _INTERRUPT_MARK not in err:
            t.last_error = f"{err} | {_INTERRUPT_MARK}".strip(" |")[:1020]
        task_n += 1

    if batch_n or task_n or cmd_n:
        db.commit()
        _log.info(
            "startup: biz_state recover interrupted batches=%s cmds=%s tasks=%s",
            batch_n,
            cmd_n,
            task_n,
        )
    return {"batches": batch_n, "commands": cmd_n, "tasks": task_n}
