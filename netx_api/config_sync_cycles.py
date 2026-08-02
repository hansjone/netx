"""Config sync policy updates, cycles, and dashboard."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .cli_resolve import cli_profile_ready
from .config_sync_common import (
    DEFAULT_CYCLE_KEEP,
    _cycle_keep_value,
    _targets_from_json,
    _utcnow,
    cycle_to_out,
    ensure_policy,
    policy_to_out,
    prune_config_sync_cycles,
    task_to_out,
)
from .config_sync_schemas import (
    ConfigSyncCycleCreate,
    ConfigSyncCycleOut,
    ConfigSyncDashboardOut,
    ConfigSyncPolicyOut,
    ConfigSyncPolicyUpdate,
    ConfigSyncTaskOut,
)
from .models import (
    ConfigSyncCycle,
    ConfigSyncPolicy,
    ConfigSyncTask,
    ManagedNE,
    NeConfigSnapshot,
    UmeInventoryNE,
)

def get_policy(db: Session) -> ConfigSyncPolicyOut:
    return policy_to_out(ensure_policy(db))


def update_policy(db: Session, body: ConfigSyncPolicyUpdate) -> ConfigSyncPolicyOut:
    row = ensure_policy(db)
    data = body.model_dump(exclude_unset=True)
    if "enabled" in data and data["enabled"] is not None:
        row.enabled = bool(data["enabled"])
    if "interval_days" in data and data["interval_days"] is not None:
        row.interval_days = int(data["interval_days"])
    if "concurrency" in data and data["concurrency"] is not None:
        row.concurrency = max(1, min(30, int(data["concurrency"])))
    if "scope_mode" in data and data["scope_mode"] is not None:
        row.scope_mode = str(data["scope_mode"])
    if "selected_targets" in data and data["selected_targets"] is not None:
        refs = data["selected_targets"]
        row.selected_targets = [
            {"source": r.source if hasattr(r, "source") else r["source"], "id": r.id if hasattr(r, "id") else r["id"]}
            for r in refs
        ]
    if "history_keep" in data and data["history_keep"] is not None:
        row.history_keep = max(0, min(30, int(data["history_keep"])))
    if "cycle_keep" in data and data["cycle_keep"] is not None:
        row.cycle_keep = max(0, min(200, int(data["cycle_keep"])))
    row.updated_at = _utcnow()
    db.commit()
    db.refresh(row)
    prune_config_sync_cycles(db, keep=_cycle_keep_value(row))
    return policy_to_out(row)


def expand_targets(db: Session, policy: ConfigSyncPolicy) -> list[dict[str, str]]:
    """Return list of {source, id, ne_name, ne_ip, vendor, device_type}."""
    mode = str(policy.scope_mode or "all").strip().lower()
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(source: str, tid: str, name: str, ip: str, vendor: str, device_type: str) -> None:
        key = (source, tid)
        if key in seen:
            return
        seen.add(key)
        out.append(
            {
                "source": source,
                "id": tid,
                "ne_name": name,
                "ne_ip": ip,
                "vendor": vendor,
                "device_type": device_type,
            }
        )

    if mode == "selected":
        for ref in _targets_from_json(policy.selected_targets):
            if ref.source == "managed":
                ne = db.get(ManagedNE, ref.id)
                if ne:
                    _add("managed", str(ne.id), str(ne.name or ""), str(ne.ip_address or ""), str(ne.vendor or ""), str(ne.device_type or ""))
            else:
                inv = db.get(UmeInventoryNE, ref.id)
                if inv:
                    _add(
                        "ume",
                        str(inv.ne_id),
                        str(inv.host_name or inv.ne_name or inv.user_label or inv.ne_id or ""),
                        str(inv.ip_address or ""),
                        str(inv.vendor or ""),
                        str(inv.ne_type or ""),
                    )
        return out

    for ne in db.query(ManagedNE).order_by(ManagedNE.updated_at.desc()).all():
        _add("managed", str(ne.id), str(ne.name or ""), str(ne.ip_address or ""), str(ne.vendor or ""), str(ne.device_type or ""))

    if cli_profile_ready(db):
        for inv in db.query(UmeInventoryNE).order_by(UmeInventoryNE.ne_id.asc()).all():
            if not str(inv.ip_address or "").strip():
                continue
            _add(
                "ume",
                str(inv.ne_id),
                str(inv.host_name or inv.ne_name or inv.user_label or inv.ne_id or ""),
                str(inv.ip_address or ""),
                str(inv.vendor or ""),
                str(inv.ne_type or ""),
            )
    return out


def has_active_cycle(db: Session) -> ConfigSyncCycle | None:
    """Any non-terminal cycle occupies the single-flight slot (incl. paused)."""
    return (
        db.query(ConfigSyncCycle)
        .filter(ConfigSyncCycle.status.in_(("running", "pending", "paused")))
        .order_by(ConfigSyncCycle.created_at.desc())
        .first()
    )


def has_running_cycle(db: Session) -> ConfigSyncCycle | None:
    """Backward-compatible alias: treat paused as active so a new cycle cannot start."""
    return has_active_cycle(db)


def last_finished_cycle(db: Session) -> ConfigSyncCycle | None:
    return (
        db.query(ConfigSyncCycle)
        .filter(ConfigSyncCycle.status.in_(("success", "fail", "cancelled")))
        .order_by(ConfigSyncCycle.ended_at.desc().nullslast(), ConfigSyncCycle.created_at.desc())
        .first()
    )


def next_due_at(db: Session, policy: ConfigSyncPolicy | None = None) -> datetime | None:
    pol = policy or ensure_policy(db)
    if not pol.enabled:
        return None
    from .config_sync_scheduler import startup_grace_until

    last = (
        db.query(ConfigSyncCycle)
        .filter(ConfigSyncCycle.status == "success", ConfigSyncCycle.ended_at.isnot(None))
        .order_by(ConfigSyncCycle.ended_at.desc())
        .first()
    )
    days = max(1, int(pol.interval_days or 3))
    if last and last.ended_at:
        due = last.ended_at + timedelta(days=days)
    else:
        # Never synced successfully: do not fire immediately on enable / first boot.
        due = _utcnow() + timedelta(days=days)
    grace_until = startup_grace_until()
    if grace_until is not None and due < grace_until:
        return grace_until
    return due


def create_cycle(db: Session, body: ConfigSyncCycleCreate) -> ConfigSyncCycleOut:
    if has_running_cycle(db):
        raise HTTPException(status_code=409, detail="config_sync_cycle_already_running")
    policy = ensure_policy(db)
    mode = str(body.mode or "full").strip().lower()
    trigger = "retry_failed" if mode == "retry_failed" else "manual"
    concurrency = max(1, min(30, int(policy.concurrency or 5)))

    targets: list[dict[str, str]] = []
    if mode == "retry_failed":
        src_cycle_id = str(body.cycle_id or "").strip()
        src = None
        if src_cycle_id:
            src = db.get(ConfigSyncCycle, src_cycle_id)
        if src is None:
            src = (
                db.query(ConfigSyncCycle)
                .filter(ConfigSyncCycle.fail_count > 0)
                .order_by(ConfigSyncCycle.created_at.desc())
                .first()
            )
        if src is None:
            raise HTTPException(status_code=404, detail="no_failed_cycle")
        fails = (
            db.query(ConfigSyncTask)
            .filter(ConfigSyncTask.cycle_id == src.id, ConfigSyncTask.status == "fail")
            .all()
        )
        for t in fails:
            targets.append(
                {
                    "source": str(t.source),
                    "id": str(t.target_id),
                    "ne_name": str(t.ne_name or ""),
                    "ne_ip": str(t.ne_ip or ""),
                    "vendor": str(t.vendor or ""),
                    "device_type": "",
                }
            )
        if not targets:
            raise HTTPException(status_code=400, detail="no_failed_tasks")
    else:
        targets = expand_targets(db, policy)
        if not targets:
            raise HTTPException(status_code=400, detail="no_targets")

    cycle = ConfigSyncCycle(
        id=uuid4().hex,
        trigger_mode=trigger,
        status="running",
        concurrency=concurrency,
        planned_count=len(targets),
        started_at=_utcnow(),
        created_at=_utcnow(),
    )
    db.add(cycle)
    db.flush()
    for t in targets:
        db.add(
            ConfigSyncTask(
                id=uuid4().hex,
                cycle_id=cycle.id,
                source=t["source"],
                target_id=t["id"],
                ne_name=t.get("ne_name") or "",
                ne_ip=t.get("ne_ip") or "",
                vendor=t.get("vendor") or "",
                status="pending",
            )
        )
    db.commit()
    db.refresh(cycle)
    return cycle_to_out(cycle)


def list_cycles(db: Session, *, page: int, page_size: int) -> dict[str, Any]:
    q = db.query(ConfigSyncCycle).order_by(ConfigSyncCycle.created_at.desc())
    total = int(q.count())
    rows = q.offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "page": page, "page_size": page_size, "items": [cycle_to_out(r) for r in rows]}


def get_cycle(db: Session, cycle_id: str) -> ConfigSyncCycleOut:
    row = db.get(ConfigSyncCycle, cycle_id)
    if not row:
        raise HTTPException(status_code=404, detail="cycle_not_found")
    return cycle_to_out(row)


def list_cycle_tasks(
    db: Session,
    cycle_id: str,
    *,
    page: int,
    page_size: int,
    status: str = "",
    keyword: str = "",
) -> dict[str, Any]:
    if not db.get(ConfigSyncCycle, cycle_id):
        raise HTTPException(status_code=404, detail="cycle_not_found")
    q = db.query(ConfigSyncTask).filter(ConfigSyncTask.cycle_id == cycle_id)
    st = str(status or "").strip()
    if st:
        q = q.filter(ConfigSyncTask.status == st)
    kw = str(keyword or "").strip()
    if kw:
        like = f"%{kw}%"
        q = q.filter(
            or_(
                ConfigSyncTask.ne_name.ilike(like),
                ConfigSyncTask.ne_ip.ilike(like),
                ConfigSyncTask.target_id.ilike(like),
                ConfigSyncTask.message.ilike(like),
            )
        )
    total = int(q.count())
    rows = q.order_by(ConfigSyncTask.ne_name.asc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "page": page, "page_size": page_size, "items": [task_to_out(r) for r in rows]}


def pause_cycle(db: Session, cycle_id: str) -> ConfigSyncCycleOut:
    row = db.get(ConfigSyncCycle, cycle_id)
    if not row:
        raise HTTPException(status_code=404, detail="cycle_not_found")
    if str(row.status) not in ("running", "pending"):
        raise HTTPException(status_code=400, detail="cycle_not_running")
    row.status = "paused"
    db.commit()
    db.refresh(row)
    return cycle_to_out(row)


def resume_cycle(db: Session, cycle_id: str) -> ConfigSyncCycleOut:
    row = db.get(ConfigSyncCycle, cycle_id)
    if not row:
        raise HTTPException(status_code=404, detail="cycle_not_found")
    if str(row.status) != "paused":
        raise HTTPException(status_code=400, detail="cycle_not_paused")
    pending = (
        db.query(ConfigSyncTask)
        .filter(ConfigSyncTask.cycle_id == cycle_id, ConfigSyncTask.status == "pending")
        .count()
    )
    if pending <= 0:
        raise HTTPException(status_code=400, detail="no_pending_tasks")
    other = has_running_cycle(db)
    if other and str(other.id) != cycle_id:
        raise HTTPException(status_code=409, detail="config_sync_cycle_already_running")
    row.status = "running"
    db.commit()
    db.refresh(row)
    return cycle_to_out(row)


def stop_cycle(db: Session, cycle_id: str) -> ConfigSyncCycleOut:
    """Cancel remaining work and close the cycle (running/paused/pending)."""
    row = db.get(ConfigSyncCycle, cycle_id)
    if not row:
        raise HTTPException(status_code=404, detail="cycle_not_found")
    if str(row.status) not in ("running", "paused", "pending"):
        raise HTTPException(status_code=400, detail="cycle_not_active")
    now = _utcnow()
    pending = (
        db.query(ConfigSyncTask)
        .filter(
            ConfigSyncTask.cycle_id == cycle_id,
            ConfigSyncTask.status.in_(("pending", "running")),
        )
        .all()
    )
    for task in pending:
        # In-flight workers may still finish and overwrite; pending must not start.
        if str(task.status) == "pending":
            task.status = "cancelled"
            task.message = "stopped_by_user"
            task.ended_at = now
        else:
            task.message = (str(task.message or "") + " · stop_requested")[:1020]
    row.status = "cancelled"
    row.error_message = "stopped_by_user"
    row.ended_at = now
    db.commit()
    sync_cycle_progress(db, cycle_id)
    db.refresh(row)
    try:
        from .config_sync_runner import _release_pool

        _release_pool(cycle_id)
    except Exception:
        pass
    out = cycle_to_out(row)
    try:
        prune_config_sync_cycles(db, keep=_cycle_keep_value(ensure_policy(db)))
    except Exception:
        _log.exception("prune_config_sync_cycles after stop failed")
    return out


def dashboard(db: Session) -> ConfigSyncDashboardOut:
    policy = ensure_policy(db)
    snap_count = int(db.query(func.count()).select_from(NeConfigSnapshot).scalar() or 0)
    running = (
        db.query(ConfigSyncCycle)
        .filter(ConfigSyncCycle.status.in_(("running", "paused", "pending")))
        .order_by(ConfigSyncCycle.created_at.desc())
        .first()
    )
    last = last_finished_cycle(db)
    fail_by_vendor: dict[str, int] = {}
    if last:
        rows = (
            db.query(ConfigSyncTask.vendor, func.count())
            .filter(ConfigSyncTask.cycle_id == last.id, ConfigSyncTask.status == "fail")
            .group_by(ConfigSyncTask.vendor)
            .all()
        )
        for vendor, cnt in rows:
            fail_by_vendor[str(vendor or "unknown") or "unknown"] = int(cnt)
    return ConfigSyncDashboardOut(
        policy=policy_to_out(policy),
        snapshot_count=snap_count,
        last_cycle=cycle_to_out(last) if last else None,
        running_cycle=cycle_to_out(running) if running else None,
        next_due_at=next_due_at(db, policy),
        fail_by_vendor=fail_by_vendor,
    )

def sync_cycle_progress(db: Session, cycle_id: str) -> None:
    cycle = db.get(ConfigSyncCycle, cycle_id)
    if not cycle:
        return
    success = (
        db.query(func.count())
        .select_from(ConfigSyncTask)
        .filter(ConfigSyncTask.cycle_id == cycle_id, ConfigSyncTask.status == "success")
        .scalar()
        or 0
    )
    fail = (
        db.query(func.count())
        .select_from(ConfigSyncTask)
        .filter(ConfigSyncTask.cycle_id == cycle_id, ConfigSyncTask.status == "fail")
        .scalar()
        or 0
    )
    skip = (
        db.query(func.count())
        .select_from(ConfigSyncTask)
        .filter(ConfigSyncTask.cycle_id == cycle_id, ConfigSyncTask.status.in_(("skipped", "cancelled")))
        .scalar()
        or 0
    )
    cycle.success_count = int(success)
    cycle.fail_count = int(fail)
    cycle.skip_count = int(skip)
    db.commit()


def finalize_cycle(db: Session, cycle_id: str) -> None:
    cycle = db.get(ConfigSyncCycle, cycle_id)
    if not cycle:
        return
    if str(cycle.status) in ("paused", "cancelled"):
        return
    pending = (
        db.query(func.count())
        .select_from(ConfigSyncTask)
        .filter(ConfigSyncTask.cycle_id == cycle_id, ConfigSyncTask.status.in_(("pending", "running")))
        .scalar()
        or 0
    )
    if int(pending) > 0:
        return
    sync_cycle_progress(db, cycle_id)
    db.refresh(cycle)
    if str(cycle.status) in ("paused", "cancelled"):
        return
    # Cycle outcome is about finishing the run, not per-NE results.
    # Individual task failures stay in fail_count for retry/dashboard.
    cycle.status = "success"
    if cycle.error_message == "completed_with_failures":
        cycle.error_message = ""
    cycle.ended_at = _utcnow()
    db.commit()
    try:
        prune_config_sync_cycles(db, keep=_cycle_keep_value(ensure_policy(db)))
    except Exception:
        _log.exception("prune_config_sync_cycles after finish failed")

