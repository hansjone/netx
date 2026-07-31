"""Port traffic monitoring service: CRUD, discover, samples, dashboard."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .cli_resolve import resolve_cli_target
from .config import settings
from .models import PortTrafficSample, PortTrafficTarget, PortTrafficTask
from .ne_session_factory import close_netmiko_connection, open_netmiko_connection
from .ne_netmiko import send_show_command
from .port_traffic_commands import commands_for_vendor
from .port_traffic_parsers import brief_port_to_dict, parse_interface_brief
from .port_traffic_schemas import (
    DiscoverPortItem,
    DiscoverPortsRequest,
    DiscoverPortsResponse,
    PortTrafficDashboardOut,
    PortTrafficSamplePoint,
    PortTrafficSamplesOut,
    PortTrafficTargetIn,
    PortTrafficTargetOut,
    PortTrafficTargetsPut,
    PortTrafficTaskCreate,
    PortTrafficTaskOut,
    PortTrafficTaskUpdate,
)

_log = logging.getLogger("netx.port_traffic.service")


def _utcnow() -> datetime:
    return datetime.utcnow()


def _task_out(db: Session, task: PortTrafficTask) -> PortTrafficTaskOut:
    tid = str(task.id)
    total = db.query(PortTrafficTarget).filter(PortTrafficTarget.task_id == tid).count()
    active = (
        db.query(PortTrafficTarget)
        .filter(PortTrafficTarget.task_id == tid, PortTrafficTarget.status == "active")
        .count()
    )
    return PortTrafficTaskOut(
        id=tid,
        title=str(task.title or ""),
        status=str(task.status or ""),
        interval_sec=int(task.interval_sec or 60),
        retention_days=int(task.retention_days or 7),
        concurrency=int(task.concurrency or 5),
        collect_running=bool(task.collect_running),
        target_count=int(total),
        active_target_count=int(active),
        last_collect_started_at=task.last_collect_started_at,
        last_collect_ended_at=task.last_collect_ended_at,
        last_error=str(task.last_error or ""),
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def _target_out(row: PortTrafficTarget) -> PortTrafficTargetOut:
    return PortTrafficTargetOut(
        id=str(row.id),
        task_id=str(row.task_id),
        source=str(row.source or ""),
        target_id=str(row.target_id or ""),
        ne_name=str(row.ne_name or ""),
        ne_ip=str(row.ne_ip or ""),
        vendor=str(row.vendor or ""),
        ifname=str(row.ifname or ""),
        if_description=str(row.if_description or ""),
        bw_bps=int(row.bw_bps or 0),
        status=str(row.status or ""),
        last_error=str(row.last_error or ""),
        last_sample_at=row.last_sample_at,
        created_at=row.created_at,
    )


def _assert_supported_targets(targets: list[PortTrafficTargetIn]) -> None:
    for t in targets:
        cmds = commands_for_vendor(t.vendor or "", "")
        if cmds is None:
            raise HTTPException(
                status_code=400,
                detail=f"vendor_not_supported_for_port_traffic: {t.vendor or 'unknown'} ({t.ne_name or t.target_id})",
            )


def list_tasks(db: Session, *, page: int = 1, page_size: int = 20) -> dict[str, Any]:
    q = db.query(PortTrafficTask).order_by(PortTrafficTask.created_at.desc())
    total = q.count()
    rows = q.offset((page - 1) * page_size).limit(page_size).all()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_task_out(db, r).model_dump() for r in rows],
    }


def get_task(db: Session, task_id: str) -> PortTrafficTaskOut:
    task = db.get(PortTrafficTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task_not_found")
    return _task_out(db, task)


def create_task(db: Session, body: PortTrafficTaskCreate) -> PortTrafficTaskOut:
    _assert_supported_targets(body.targets)
    now = _utcnow()
    status = "running" if body.start_now and body.targets else "draft"
    if body.start_now and not body.targets:
        status = "draft"
    task = PortTrafficTask(
        id=uuid4().hex,
        title=body.title.strip(),
        status=status,
        interval_sec=int(body.interval_sec),
        retention_days=int(body.retention_days),
        concurrency=int(body.concurrency),
        created_at=now,
        updated_at=now,
    )
    db.add(task)
    db.flush()
    for t in body.targets:
        db.add(
            PortTrafficTarget(
                id=uuid4().hex,
                task_id=task.id,
                source=t.source,
                target_id=t.target_id,
                ne_name=t.ne_name or "",
                ne_ip=t.ne_ip or "",
                vendor=t.vendor or "",
                ifname=t.ifname.strip(),
                if_description=t.if_description or "",
                bw_bps=int(t.bw_bps or 0),
                status="active",
                created_at=now,
            )
        )
    db.commit()
    db.refresh(task)
    return _task_out(db, task)


def update_task(db: Session, task_id: str, body: PortTrafficTaskUpdate) -> PortTrafficTaskOut:
    task = db.get(PortTrafficTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task_not_found")
    data = body.model_dump(exclude_unset=True)
    if "title" in data and data["title"] is not None:
        task.title = str(data["title"]).strip()
    if "interval_sec" in data and data["interval_sec"] is not None:
        task.interval_sec = int(data["interval_sec"])
    if "retention_days" in data and data["retention_days"] is not None:
        task.retention_days = int(data["retention_days"])
    if "concurrency" in data and data["concurrency"] is not None:
        task.concurrency = int(data["concurrency"])
    task.updated_at = _utcnow()
    db.commit()
    db.refresh(task)
    return _task_out(db, task)


def delete_task(db: Session, task_id: str) -> dict[str, Any]:
    task = db.get(PortTrafficTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task_not_found")
    if bool(task.collect_running):
        raise HTTPException(status_code=409, detail="collect_running")
    targets = db.query(PortTrafficTarget).filter(PortTrafficTarget.task_id == task_id).all()
    ids = [str(t.id) for t in targets]
    if ids:
        db.query(PortTrafficSample).filter(PortTrafficSample.target_row_id.in_(ids)).delete(
            synchronize_session=False
        )
        db.query(PortTrafficTarget).filter(PortTrafficTarget.task_id == task_id).delete(
            synchronize_session=False
        )
    db.delete(task)
    db.commit()
    return {"ok": True, "id": task_id}


def set_task_status(db: Session, task_id: str, status: str) -> PortTrafficTaskOut:
    task = db.get(PortTrafficTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task_not_found")
    if status == "running":
        active = (
            db.query(PortTrafficTarget)
            .filter(PortTrafficTarget.task_id == task_id, PortTrafficTarget.status == "active")
            .count()
        )
        if active <= 0:
            raise HTTPException(status_code=400, detail="no_active_targets")
        # Allow scheduler/collect-now to fire immediately after start/resume.
        task.last_collect_ended_at = None
    task.status = status
    task.updated_at = _utcnow()
    db.commit()
    db.refresh(task)
    return _task_out(db, task)


def put_targets(db: Session, task_id: str, body: PortTrafficTargetsPut) -> list[PortTrafficTargetOut]:
    task = db.get(PortTrafficTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task_not_found")
    if bool(task.collect_running):
        raise HTTPException(status_code=409, detail="collect_running")
    _assert_supported_targets(body.targets)
    old = db.query(PortTrafficTarget).filter(PortTrafficTarget.task_id == task_id).all()
    old_ids = [str(t.id) for t in old]
    if old_ids:
        db.query(PortTrafficSample).filter(PortTrafficSample.target_row_id.in_(old_ids)).delete(
            synchronize_session=False
        )
        db.query(PortTrafficTarget).filter(PortTrafficTarget.task_id == task_id).delete(
            synchronize_session=False
        )
    now = _utcnow()
    rows: list[PortTrafficTarget] = []
    for t in body.targets:
        row = PortTrafficTarget(
            id=uuid4().hex,
            task_id=task_id,
            source=t.source,
            target_id=t.target_id,
            ne_name=t.ne_name or "",
            ne_ip=t.ne_ip or "",
            vendor=t.vendor or "",
            ifname=t.ifname.strip(),
            if_description=t.if_description or "",
            bw_bps=int(t.bw_bps or 0),
            status="active",
            created_at=now,
        )
        db.add(row)
        rows.append(row)
    task.updated_at = now
    db.commit()
    return [_target_out(r) for r in rows]


def list_targets(db: Session, task_id: str) -> list[PortTrafficTargetOut]:
    task = db.get(PortTrafficTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task_not_found")
    rows = (
        db.query(PortTrafficTarget)
        .filter(PortTrafficTarget.task_id == task_id)
        .order_by(PortTrafficTarget.ne_name, PortTrafficTarget.ifname)
        .all()
    )
    return [_target_out(r) for r in rows]


def discover_ports(db: Session, body: DiscoverPortsRequest) -> DiscoverPortsResponse:
    try:
        if body.source == "managed":
            creds, device = resolve_cli_target(db, managed_ne_id=body.id)
        else:
            creds, device = resolve_cli_target(db, ume_ne_id=body.id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"resolve_failed: {exc}") from exc

    vendor = str(device.get("vendor") or creds.get("vendor") or "")
    device_type = str(device.get("device_type") or creds.get("device_type") or "")
    ne_name = str(device.get("name") or creds.get("host") or "")
    ne_ip = str(device.get("ip_address") or creds.get("host") or "")
    cmds = commands_for_vendor(vendor, device_type)
    if cmds is None:
        raise HTTPException(
            status_code=400,
            detail=f"vendor_not_supported_for_port_traffic: {vendor or 'unknown'}",
        )

    per_cmd = int(settings.ne_collect_read_timeout_sec or 120)
    conn = open_netmiko_connection(creds, session_timeout=per_cmd + 60)
    try:
        raw = send_show_command(conn, cmds.brief, read_timeout=per_cmd)
    finally:
        close_netmiko_connection(conn)

    ports = [
        DiscoverPortItem(**brief_port_to_dict(p))
        for p in parse_interface_brief(raw, cmds.vendor_key)
    ]
    return DiscoverPortsResponse(
        source=body.source,
        id=body.id,
        ne_name=ne_name,
        ne_ip=ne_ip,
        vendor=vendor,
        vendor_key=cmds.vendor_key,
        ports=ports,
    )


def _as_naive_utc(value: datetime | None) -> datetime | None:
    """Normalize query bounds to naive UTC (DB columns are naive utcnow)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def get_samples(
    db: Session,
    *,
    target_row_id: str,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
) -> PortTrafficSamplesOut:
    target = db.get(PortTrafficTarget, target_row_id)
    if not target:
        raise HTTPException(status_code=404, detail="target_not_found")
    now = _utcnow()
    to_ts = _as_naive_utc(to_ts) or now
    from_ts = _as_naive_utc(from_ts) or (to_ts - timedelta(hours=1))
    # Slight skew so just-written samples are not clipped by client clock.
    to_ts = to_ts + timedelta(seconds=5)
    rows = (
        db.query(PortTrafficSample)
        .filter(
            PortTrafficSample.target_row_id == target_row_id,
            PortTrafficSample.ts >= from_ts,
            PortTrafficSample.ts <= to_ts,
            PortTrafficSample.raw_ok.is_(True),
        )
        .order_by(PortTrafficSample.ts.asc())
        .all()
    )
    points = [
        PortTrafficSamplePoint(
            ts=r.ts,
            in_bps=float(r.in_bps or 0),
            out_bps=float(r.out_bps or 0),
            in_util_pct=float(r.in_util_pct or 0),
            out_util_pct=float(r.out_util_pct or 0),
            bw_bps=int(r.bw_bps or 0),
            rate_period_sec=int(r.rate_period_sec or 0),
        )
        for r in rows
    ]
    return PortTrafficSamplesOut(target=_target_out(target), points=points)


def dashboard(db: Session) -> PortTrafficDashboardOut:
    task_count = db.query(PortTrafficTask).count()
    running = db.query(PortTrafficTask).filter(PortTrafficTask.status == "running").count()
    active_targets = db.query(PortTrafficTarget).filter(PortTrafficTarget.status == "active").count()
    since = _utcnow() - timedelta(hours=24)
    sample_count = (
        db.query(PortTrafficSample)
        .filter(PortTrafficSample.ts >= since, PortTrafficSample.raw_ok.is_(True))
        .count()
    )
    last = db.query(func.max(PortTrafficSample.ts)).scalar()
    return PortTrafficDashboardOut(
        task_count=int(task_count),
        running_task_count=int(running),
        active_target_count=int(active_targets),
        sample_count_24h=int(sample_count),
        last_sample_at=last,
    )


def purge_expired_samples(db: Session) -> int:
    """Delete samples older than each task's retention_days."""
    tasks = db.query(PortTrafficTask).all()
    deleted = 0
    now = _utcnow()
    for task in tasks:
        days = max(1, int(task.retention_days or 7))
        cutoff = now - timedelta(days=days)
        target_ids = [
            str(t.id)
            for t in db.query(PortTrafficTarget.id).filter(PortTrafficTarget.task_id == task.id).all()
        ]
        if not target_ids:
            continue
        n = (
            db.query(PortTrafficSample)
            .filter(
                PortTrafficSample.target_row_id.in_(target_ids),
                PortTrafficSample.ts < cutoff,
            )
            .delete(synchronize_session=False)
        )
        deleted += int(n or 0)
    if deleted:
        db.commit()
        _log.info("port_traffic retention purged samples=%s", deleted)
    return deleted
