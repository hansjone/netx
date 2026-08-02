"""Unified live task overview across NetX runners."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from .models import (
    AuditLog,
    ConfigSyncCycle,
    ConfigSyncTask,
    ManagedNE,
    NeCollectionJob,
    NeCollectionRun,
    PortTrafficDevice,
    TopoDiscoverJob,
    UmeCliOverride,
    UmeSyncJob,
)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    try:
        return dt.isoformat()
    except Exception:
        return str(dt)


def _item(
    *,
    kind: str,
    id: str,
    title: str,
    status: str,
    trigger: str = "",
    actor: str = "",
    started_at: datetime | None = None,
    updated_at: datetime | None = None,
    progress: str = "",
    detail: str = "",
    href: str = "",
) -> dict[str, Any]:
    return {
        "kind": kind,
        "id": id,
        "title": title,
        "status": status,
        "trigger": trigger,
        "actor": actor or "—",
        "started_at": _iso(started_at),
        "updated_at": _iso(updated_at),
        "progress": progress,
        "detail": detail,
        "href": href,
    }


def _audit_actor_map(db: Session, *, hours: int = 72) -> dict[str, str]:
    """Best-effort map of resource id → latest actor username from audit detail."""
    since = datetime.utcnow() - timedelta(hours=max(1, hours))
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.ts >= since, AuditLog.status_code < 400)
        .order_by(AuditLog.ts.desc())
        .limit(800)
        .all()
    )
    out: dict[str, str] = {}
    for row in rows:
        user = str(row.actor_username or "").strip()
        if not user:
            continue
        detail = row.detail if isinstance(row.detail, dict) else {}
        for key in ("id", "cycle_id", "job_id", "device_id", "session_id", "board_id"):
            rid = str(detail.get(key) or "").strip()
            if rid and rid not in out:
                out[rid] = user
    return out


def _port_traffic_items(db: Session, actors: dict[str, str]) -> list[dict[str, Any]]:
    rows = (
        db.query(PortTrafficDevice)
        .filter(
            (PortTrafficDevice.collect_running.is_(True))
            | (PortTrafficDevice.status == "running")
        )
        .order_by(PortTrafficDevice.updated_at.desc())
        .limit(200)
        .all()
    )
    items: list[dict[str, Any]] = []
    for row in rows:
        did = str(row.id)
        name = str(row.ne_name or row.ne_ip or did)
        if bool(row.collect_running):
            status = "collecting"
        else:
            status = str(row.status or "running")
        actor = actors.get(did) or "scheduler"
        items.append(
            _item(
                kind="port_traffic",
                id=did,
                title=f"端口流量 · {name}",
                status=status,
                trigger="schedule" if actor == "scheduler" else "manual",
                actor=actor,
                started_at=row.last_collect_started_at or row.updated_at,
                updated_at=row.last_collect_ended_at or row.updated_at,
                progress="",
                detail=str(row.last_error or "")[:240],
                href="/network/tasks/port-traffic",
            )
        )
    return items


def _config_sync_items(db: Session, actors: dict[str, str]) -> list[dict[str, Any]]:
    rows = (
        db.query(ConfigSyncCycle)
        .filter(ConfigSyncCycle.status.in_(("pending", "running", "paused")))
        .order_by(ConfigSyncCycle.created_at.desc())
        .limit(50)
        .all()
    )
    items: list[dict[str, Any]] = []
    for row in rows:
        cid = str(row.id)
        running = (
            db.query(ConfigSyncTask)
            .filter(ConfigSyncTask.cycle_id == cid, ConfigSyncTask.status == "running")
            .count()
        )
        done = int(row.success_count or 0) + int(row.fail_count or 0) + int(row.skip_count or 0)
        planned = int(row.planned_count or 0)
        trigger = str(row.trigger_mode or "schedule")
        actor = actors.get(cid) or ("scheduler" if trigger == "schedule" else "—")
        items.append(
            _item(
                kind="config_sync",
                id=cid,
                title=f"配置同步 · {cid[:8]}",
                status=str(row.status or "pending"),
                trigger=trigger,
                actor=actor,
                started_at=row.started_at or row.created_at,
                updated_at=row.ended_at or row.started_at or row.created_at,
                progress=f"{done}/{planned}" + (f" · running {running}" if running else ""),
                detail=str(row.error_message or "")[:240],
                href="/network/tasks/config-sync",
            )
        )
    return items


def _collection_items(db: Session, actors: dict[str, str]) -> list[dict[str, Any]]:
    rows = (
        db.query(NeCollectionJob)
        .filter(NeCollectionJob.status.in_(("pending", "running", "paused")))
        .order_by(NeCollectionJob.created_at.desc())
        .limit(50)
        .all()
    )
    items: list[dict[str, Any]] = []
    for row in rows:
        jid = str(row.id)
        running = (
            db.query(NeCollectionRun)
            .filter(NeCollectionRun.job_id == jid, NeCollectionRun.status == "running")
            .count()
        )
        title = str(row.title or "").strip() or f"采集任务 · {jid[:8]}"
        actor = actors.get(jid) or "—"
        items.append(
            _item(
                kind="ne_collect",
                id=jid,
                title=title,
                status=str(row.status or "pending"),
                trigger="manual",
                actor=actor,
                started_at=row.started_at or row.created_at,
                updated_at=row.last_run_at or row.ended_at or row.started_at or row.created_at,
                progress=(
                    f"ok {int(row.success_count or 0)} / fail {int(row.fail_count or 0)}"
                    f" / total {int(row.ne_count or 0)}"
                    + (f" · running {running}" if running else "")
                ),
                detail=str(row.error_message or "")[:240],
                href="/network/tasks/collect",
            )
        )
    return items


def _lldp_items(db: Session, actors: dict[str, str]) -> list[dict[str, Any]]:
    rows = (
        db.query(TopoDiscoverJob)
        .filter(TopoDiscoverJob.status.in_(("pending", "running")))
        .order_by(TopoDiscoverJob.created_at.desc())
        .limit(20)
        .all()
    )
    items: list[dict[str, Any]] = []
    for row in rows:
        jid = str(row.id)
        trigger = str(row.trigger_mode or "manual")
        actor = actors.get(jid) or (
            "scheduler" if trigger == "schedule" else ("topology" if trigger == "topology" else "—")
        )
        done = int(row.done or 0)
        total = int(row.total or 0)
        items.append(
            _item(
                kind="lldp_discover",
                id=jid,
                title=f"LLDP 发现 · {trigger}",
                status=str(row.status or "pending"),
                trigger=trigger,
                actor=actor,
                started_at=row.started_at or row.created_at,
                updated_at=row.updated_at or row.ended_at or row.started_at,
                progress=f"{done}/{total}",
                detail=str(row.error or "")[:240],
                href="/network/topology/lldp",
            )
        )
    return items


def _ume_sync_items(db: Session, actors: dict[str, str]) -> list[dict[str, Any]]:
    """In-flight UME inventory/alarm sync jobs (manual or scheduled)."""
    rows = (
        db.query(UmeSyncJob)
        .filter(UmeSyncJob.status == "running", UmeSyncJob.ended_at.is_(None))
        .order_by(UmeSyncJob.started_at.desc())
        .limit(20)
        .all()
    )
    items: list[dict[str, Any]] = []
    for row in rows:
        jid = str(row.id)
        domain = str(row.domain or "sync")
        trigger = str(row.trigger_mode or "manual")
        actor = actors.get(jid) or ("scheduler" if trigger == "schedule" else "—")
        pulled = int(row.pulled_count or 0)
        inserted = int(row.inserted_count or 0)
        updated = int(row.updated_count or 0)
        items.append(
            _item(
                kind="ume_sync",
                id=jid,
                title=f"UME 同步 · {domain}",
                status="running",
                trigger=trigger,
                actor=actor,
                started_at=row.started_at,
                updated_at=row.started_at,
                progress=f"pull {pulled} · +{inserted} ~{updated}",
                detail=str(row.error_message or "")[:240],
                href="/ume",
            )
        )
    return items


def _ne_connect_items(db: Session, actors: dict[str, str]) -> list[dict[str, Any]]:
    """CLI connect-test pool work marked as testing on NE / UME override rows."""
    items: list[dict[str, Any]] = []
    managed = (
        db.query(ManagedNE)
        .filter(ManagedNE.connect_status == "testing")
        .order_by(ManagedNE.updated_at.desc())
        .limit(100)
        .all()
    )
    for row in managed:
        nid = str(row.id)
        name = str(row.name or row.ip_address or nid[:8])
        items.append(
            _item(
                kind="ne_connect",
                id=f"managed:{nid}",
                title=f"连通性测试 · {name}",
                status="testing",
                trigger="manual",
                actor=actors.get(nid) or "—",
                started_at=row.connect_tested_at or row.updated_at,
                updated_at=row.updated_at,
                progress=str(row.ip_address or ""),
                detail=str(row.connect_message or "")[:240],
                href="/network/devices",
            )
        )
    overrides = (
        db.query(UmeCliOverride)
        .filter(UmeCliOverride.connect_status == "testing")
        .order_by(UmeCliOverride.updated_at.desc())
        .limit(100)
        .all()
    )
    for row in overrides:
        uid = str(row.ume_ne_id)
        items.append(
            _item(
                kind="ne_connect",
                id=f"ume:{uid}",
                title=f"连通性测试 · UME {uid[:12]}",
                status="testing",
                trigger="manual",
                actor=actors.get(uid) or "—",
                started_at=row.connect_tested_at or row.updated_at,
                updated_at=row.updated_at,
                progress="",
                detail=str(row.connect_message or "")[:240],
                href="/ume",
            )
        )
    return items


def _ume_runtime_items() -> list[dict[str, Any]]:
    try:
        from .main import _list_runtime_tasks
    except Exception:
        return []
    items: list[dict[str, Any]] = []
    for row in _list_runtime_tasks():
        task = str(row.get("task") or "")
        status = str(row.get("status") or "unknown")
        # Always show UME background loops so ops can see paused/idle too.
        items.append(
            _item(
                kind="ume_runtime",
                id=task,
                title=f"UME · {task}",
                status=status,
                trigger="system",
                actor="system",
                started_at=None,
                updated_at=None,
                progress=str(row.get("interval_label") or ""),
                detail=str(row.get("last_error") or "")[:240],
                href="/ume",
            )
        )
        # Fix updated_at from last_run_at string if present
        if row.get("last_run_at") and items:
            items[-1]["updated_at"] = row.get("last_run_at")
    return items


def _webcrt_items(actors: dict[str, str]) -> list[dict[str, Any]]:
    try:
        from .webcrt_service import list_sessions
    except Exception:
        return []
    data = list_sessions()
    items: list[dict[str, Any]] = []
    now = datetime.utcnow().timestamp()
    for row in data.get("items") or []:
        sid = str(row.get("session_id") or "")
        name = str(row.get("ne_name") or row.get("ne_ip") or sid[:8])
        lifecycle = str(row.get("lifecycle") or row.get("state") or "unknown")
        actor = actors.get(sid) or "—"
        detail = ""
        progress = ""
        if lifecycle == "connecting":
            elapsed_ms = row.get("elapsed_ms")
            progress = f"{int(elapsed_ms)} ms" if isinstance(elapsed_ms, int) else "logging in"
            detail = "authenticating"
        elif lifecycle == "ready":
            progress = "attached"
            if row.get("connect_ms") is not None:
                detail = f"connect {int(row['connect_ms'])} ms"
        elif lifecycle == "detached":
            progress = "detached"
            deadline = row.get("detach_deadline")
            if isinstance(deadline, (int, float)) and deadline > 0:
                left = max(0, int(deadline - now))
                detail = f"grace {left}s"
            else:
                detail = "awaiting reconnect / close"
        elif lifecycle == "error":
            progress = "error"
            detail = str(row.get("connect_error") or "")[:240]
        items.append(
            _item(
                kind="webcrt",
                id=sid,
                title=f"WebCRT · {name}",
                status=lifecycle,
                trigger="manual",
                actor=actor,
                started_at=None,
                updated_at=None,
                progress=progress,
                detail=detail,
                href="/webcrt",
            )
        )
        if row.get("created_at"):
            items[-1]["started_at"] = row.get("created_at")
        if row.get("last_activity"):
            items[-1]["updated_at"] = row.get("last_activity")
    return items


def list_ops_tasks(db: Session) -> dict[str, Any]:
    actors = _audit_actor_map(db)
    items: list[dict[str, Any]] = []
    items.extend(_port_traffic_items(db, actors))
    items.extend(_config_sync_items(db, actors))
    items.extend(_collection_items(db, actors))
    items.extend(_lldp_items(db, actors))
    items.extend(_ume_sync_items(db, actors))
    items.extend(_ne_connect_items(db, actors))
    items.extend(_ume_runtime_items())
    items.extend(_webcrt_items(actors))

    by_kind: dict[str, int] = {}
    by_status: dict[str, int] = {}
    active = 0
    for it in items:
        kind = str(it.get("kind") or "")
        status = str(it.get("status") or "")
        by_kind[kind] = int(by_kind.get(kind, 0)) + 1
        by_status[status] = int(by_status.get(status, 0)) + 1
        if status in (
            "running",
            "collecting",
            "pending",
            "paused",
            "connecting",
            "ready",
            "testing",
        ):
            active += 1

    # Sort: active-ish first, then kind/title
    rank = {
        "collecting": 0,
        "running": 1,
        "testing": 2,
        "connecting": 3,
        "pending": 4,
        "paused": 5,
        "ready": 6,
        "detached": 7,
        "error": 8,
    }
    items.sort(
        key=lambda x: (
            rank.get(str(x.get("status") or ""), 50),
            str(x.get("kind") or ""),
            str(x.get("title") or ""),
        )
    )

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "total": len(items),
        "active": active,
        "by_kind": by_kind,
        "by_status": by_status,
        "items": items,
    }
