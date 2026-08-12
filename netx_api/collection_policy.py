"""NE batch-collect policy, prune-by-count, and schedule due helpers."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .collection_schemas import (
    CollectionPolicyOut,
    CollectionPolicyUpdate,
    CollectionTargetRef,
)
from .models import ManagedNE, NeCollectionJob, NeCollectionPolicy, NeCollectionRun, UmeInventoryNE
from .ne_collection_paths import collection_data_root
from .timeutil import utcnow_naive  # used by ensure_policy.updated_at

_log = logging.getLogger("netx.collection.policy")

POLICY_ID = 1
DEFAULT_HISTORY_KEEP = 3
MAX_INTERVAL_HOURS = 8760  # 365d


def _utcnow() -> datetime:
    return datetime.now()


def _normalize_interval_hours(row: NeCollectionPolicy) -> int:
    hours = int(getattr(row, "interval_hours", 0) or 0)
    if hours <= 0:
        hours = max(1, int(row.interval_days or 1)) * 24
    return max(1, min(MAX_INTERVAL_HOURS, hours))


def ensure_policy(db: Session) -> NeCollectionPolicy:
    row = db.get(NeCollectionPolicy, POLICY_ID)
    if row is None:
        row = NeCollectionPolicy(
            id=POLICY_ID,
            enabled=False,
            interval_days=1,
            interval_hours=24,
            scope_mode="all",
            selected_targets=[],
            title="",
            commands="",
            history_keep=DEFAULT_HISTORY_KEEP,
            updated_at=_utcnow(),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    if int(getattr(row, "interval_hours", 0) or 0) <= 0:
        row.interval_hours = max(1, int(row.interval_days or 1)) * 24
        db.commit()
        db.refresh(row)
    return row


def _targets_from_json(raw: Any) -> list[CollectionTargetRef]:
    items: list[CollectionTargetRef] = []
    if not isinstance(raw, list):
        return items
    for x in raw:
        if not isinstance(x, dict):
            continue
        tid = str(x.get("id") or "").strip()
        if not tid:
            continue
        src = str(x.get("source") or "managed").strip().lower() or "managed"
        if src not in {"managed", "ume"}:
            src = "managed"
        items.append(CollectionTargetRef(source=src, id=tid))
    return items


def policy_to_out(row: NeCollectionPolicy) -> CollectionPolicyOut:
    hours = _normalize_interval_hours(row)
    days = max(1, min(365, (hours + 23) // 24))
    keep = getattr(row, "history_keep", None)
    if keep is None:
        keep = DEFAULT_HISTORY_KEEP
    return CollectionPolicyOut(
        enabled=bool(row.enabled),
        interval_days=days,
        interval_hours=hours,
        scope_mode="selected" if str(row.scope_mode or "") == "selected" else "all",
        selected_targets=_targets_from_json(row.selected_targets),
        title=str(row.title or ""),
        commands=str(row.commands or ""),
        history_keep=max(0, min(200, int(keep))),
        updated_at=row.updated_at,
    )


def get_policy(db: Session) -> CollectionPolicyOut:
    return policy_to_out(ensure_policy(db))


def history_keep_value(row: NeCollectionPolicy | None = None) -> int:
    if row is None:
        return DEFAULT_HISTORY_KEEP
    keep = getattr(row, "history_keep", None)
    if keep is None:
        keep = DEFAULT_HISTORY_KEEP
    return max(0, min(200, int(keep)))


def prune_collection_jobs(db: Session, *, keep: int = DEFAULT_HISTORY_KEEP) -> int:
    """Delete finished jobs beyond ``keep`` (newest kept). Active jobs always retained."""
    keep = max(0, min(200, int(keep)))
    finished = (
        db.query(NeCollectionJob)
        .filter(NeCollectionJob.status.in_(("done", "failed")))
        .order_by(NeCollectionJob.created_at.desc())
        .all()
    )
    to_drop = finished if keep == 0 else finished[keep:]
    if not to_drop:
        return 0
    root = collection_data_root().resolve()
    dropped = 0
    for job in to_drop:
        jid = str(job.id)
        db.query(NeCollectionRun).filter(NeCollectionRun.job_id == jid).delete(
            synchronize_session=False
        )
        db.delete(job)
        dropped += 1
        job_dir = (root / jid).resolve()
        if str(job_dir).startswith(str(root)) and job_dir.is_dir():
            shutil.rmtree(job_dir, ignore_errors=True)
    if dropped:
        db.commit()
        _log.info("pruned %s ne_collection job(s); keep=%s", dropped, keep)
    return dropped


def update_policy(db: Session, body: CollectionPolicyUpdate) -> CollectionPolicyOut:
    row = ensure_policy(db)
    data = body.model_dump(exclude_unset=True)
    if "enabled" in data and data["enabled"] is not None:
        row.enabled = bool(data["enabled"])
    if "interval_hours" in data and data["interval_hours"] is not None:
        hours = max(1, min(MAX_INTERVAL_HOURS, int(data["interval_hours"])))
        row.interval_hours = hours
        row.interval_days = max(1, min(365, (hours + 23) // 24))
    elif "interval_days" in data and data["interval_days"] is not None:
        days = max(1, min(365, int(data["interval_days"])))
        row.interval_days = days
        row.interval_hours = days * 24
    if "scope_mode" in data and data["scope_mode"] is not None:
        mode = str(data["scope_mode"] or "").strip().lower()
        if mode not in {"all", "selected"}:
            raise HTTPException(status_code=400, detail="invalid_scope_mode")
        row.scope_mode = mode
    if "selected_targets" in data and data["selected_targets"] is not None:
        cleaned: list[dict[str, str]] = []
        for ref in data["selected_targets"] or []:
            if isinstance(ref, CollectionTargetRef):
                tid = ref.id.strip()
                src = (ref.source or "managed").strip().lower() or "managed"
            elif isinstance(ref, dict):
                tid = str(ref.get("id") or "").strip()
                src = str(ref.get("source") or "managed").strip().lower() or "managed"
            else:
                continue
            if not tid:
                continue
            if src not in {"managed", "ume"}:
                src = "managed"
            cleaned.append({"source": src, "id": tid})
        row.selected_targets = cleaned
    if "title" in data and data["title"] is not None:
        row.title = str(data["title"] or "").strip()[:256]
    if "commands" in data and data["commands"] is not None:
        row.commands = str(data["commands"] or "")
    if "history_keep" in data and data["history_keep"] is not None:
        row.history_keep = max(0, min(200, int(data["history_keep"])))
    if bool(row.enabled):
        from .collection_service import _parse_commands

        if not _parse_commands(str(row.commands or "")):
            raise HTTPException(status_code=400, detail="commands_required_for_schedule")
        if str(row.scope_mode or "") == "selected" and not (row.selected_targets or []):
            raise HTTPException(status_code=400, detail="no_selected_targets")
    row.updated_at = _utcnow()
    db.commit()
    db.refresh(row)
    prune_collection_jobs(db, keep=history_keep_value(row))
    return policy_to_out(row)


def next_due_at(db: Session, policy: NeCollectionPolicy | None = None) -> datetime | None:
    """Due time based on last *scheduled* successful collect only (manual must not reset)."""
    pol = policy or ensure_policy(db)
    if not pol.enabled:
        return None
    hours = _normalize_interval_hours(pol)
    last = (
        db.query(NeCollectionJob)
        .filter(
            NeCollectionJob.status == "done",
            NeCollectionJob.trigger_mode == "schedule",
            NeCollectionJob.ended_at.isnot(None),
        )
        .order_by(NeCollectionJob.ended_at.desc())
        .first()
    )
    if last is None or last.ended_at is None:
        return _utcnow()
    return last.ended_at + timedelta(hours=hours)


def expand_policy_targets(db: Session, policy: NeCollectionPolicy) -> list[tuple[str, str, str, str]]:
    """Return list of (source, id, name, ip) for a policy."""
    from .device_types import WEBCRT_NE_SOURCE

    mode = str(policy.scope_mode or "all").strip().lower()
    out: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(source: str, tid: str, name: str, ip: str) -> None:
        key = (source, tid)
        if key in seen:
            return
        seen.add(key)
        out.append((source, tid, name, ip))

    if mode == "selected":
        for ref in _targets_from_json(policy.selected_targets):
            if ref.source == "managed":
                ne = db.get(ManagedNE, ref.id)
                if ne:
                    _add(
                        "managed",
                        str(ne.id),
                        str(ne.name or ne.ip_address or ""),
                        str(ne.ip_address or ""),
                    )
            else:
                inv = db.get(UmeInventoryNE, ref.id)
                if inv:
                    name = str(inv.host_name or inv.user_label or inv.ne_name or inv.ip_address or inv.ne_id)
                    _add("ume", str(inv.ne_id), name, str(inv.ip_address or ""))
        return out

    for ne in (
        db.query(ManagedNE)
        .filter(ManagedNE.source != WEBCRT_NE_SOURCE)
        .order_by(ManagedNE.name.asc())
        .all()
    ):
        _add("managed", str(ne.id), str(ne.name or ne.ip_address or ""), str(ne.ip_address or ""))
    for inv in db.query(UmeInventoryNE).order_by(UmeInventoryNE.host_name.asc()).all():
        if not str(inv.ip_address or "").strip():
            continue
        name = str(inv.host_name or inv.user_label or inv.ne_name or inv.ip_address or inv.ne_id)
        _add("ume", str(inv.ne_id), name, str(inv.ip_address or ""))
    return out
