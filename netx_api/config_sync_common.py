"""Config sync shared helpers and policy ensure/prune."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from .config_sync_schemas import (
    ConfigSyncCycleOut,
    ConfigSyncPolicyOut,
    ConfigSyncTargetRef,
    ConfigSyncTaskOut,
)
from .models import ConfigSyncCycle, ConfigSyncPolicy, ConfigSyncTask
from .timeutil import utcnow_naive

_log = logging.getLogger("netx.config_sync")

POLICY_ID = 1
DEFAULT_CYCLE_KEEP = 30


def _utcnow() -> datetime:
    return utcnow_naive()


def ensure_policy(db: Session) -> ConfigSyncPolicy:
    row = db.get(ConfigSyncPolicy, POLICY_ID)
    if row is None:
        row = ConfigSyncPolicy(id=POLICY_ID, enabled=False)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def prune_config_sync_cycles(db: Session, *, keep: int = DEFAULT_CYCLE_KEEP) -> int:
    """Delete finished cycles beyond ``keep`` (newest kept). Active cycles always retained."""
    keep = max(0, min(200, int(keep)))
    finished = (
        db.query(ConfigSyncCycle)
        .filter(ConfigSyncCycle.status.in_(("success", "fail", "cancelled")))
        .order_by(ConfigSyncCycle.created_at.desc())
        .all()
    )
    to_drop = finished if keep == 0 else finished[keep:]
    if not to_drop:
        return 0
    dropped = 0
    for cycle in to_drop:
        cid = str(cycle.id)
        db.query(ConfigSyncTask).filter(ConfigSyncTask.cycle_id == cid).delete(
            synchronize_session=False
        )
        db.delete(cycle)
        dropped += 1
    if dropped:
        db.commit()
    return dropped


def _cycle_keep_value(row: ConfigSyncPolicy) -> int:
    return max(0, min(200, int(getattr(row, "cycle_keep", None) or DEFAULT_CYCLE_KEEP)))


def _targets_from_json(raw: Any) -> list[ConfigSyncTargetRef]:
    items: list[ConfigSyncTargetRef] = []
    if not isinstance(raw, list):
        return items
    for x in raw:
        if not isinstance(x, dict):
            continue
        src = str(x.get("source") or "").strip().lower()
        tid = str(x.get("id") or "").strip()
        if src not in ("managed", "ume") or not tid:
            continue
        items.append(ConfigSyncTargetRef(source=src, id=tid))  # type: ignore[arg-type]
    return items


def policy_to_out(row: ConfigSyncPolicy) -> ConfigSyncPolicyOut:
    return ConfigSyncPolicyOut(
        enabled=bool(row.enabled),
        interval_days=max(1, int(row.interval_days or 3)),
        concurrency=max(1, min(16, int(row.concurrency or 5))),
        scope_mode=str(row.scope_mode or "all"),
        selected_targets=_targets_from_json(row.selected_targets),
        history_keep=max(0, min(30, int(row.history_keep if row.history_keep is not None else 3))),
        cycle_keep=_cycle_keep_value(row),
        updated_at=row.updated_at,
    )



def cycle_to_out(row: ConfigSyncCycle) -> ConfigSyncCycleOut:
    return ConfigSyncCycleOut(
        id=str(row.id),
        trigger_mode=str(row.trigger_mode or ""),
        status=str(row.status or ""),
        concurrency=int(row.concurrency or 0),
        planned_count=int(row.planned_count or 0),
        success_count=int(row.success_count or 0),
        fail_count=int(row.fail_count or 0),
        skip_count=int(row.skip_count or 0),
        error_message=str(row.error_message or ""),
        started_at=row.started_at,
        ended_at=row.ended_at,
        created_at=row.created_at,
    )


def task_to_out(row: ConfigSyncTask) -> ConfigSyncTaskOut:
    return ConfigSyncTaskOut(
        id=str(row.id),
        cycle_id=str(row.cycle_id),
        source=str(row.source),
        target_id=str(row.target_id),
        ne_name=str(row.ne_name or ""),
        ne_ip=str(row.ne_ip or ""),
        vendor=str(row.vendor or ""),
        status=str(row.status or ""),
        message=str(row.message or ""),
        started_at=row.started_at,
        ended_at=row.ended_at,
    )


