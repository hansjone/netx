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
from .models import PortTrafficSample, PortTrafficSeries, PortTrafficTarget, PortTrafficTask
from .ne_session_factory import close_netmiko_connection, open_netmiko_connection
from .ne_netmiko import send_show_command
from .port_traffic_commands import commands_for_vendor
from .port_traffic_migrate import default_series_title, unique_series_title
from .port_traffic_parsers import brief_port_to_dict, parse_interface_brief
from .port_traffic_schemas import (
    DiscoverPortItem,
    DiscoverPortsRequest,
    DiscoverPortsResponse,
    PortTrafficCompareMeta,
    PortTrafficCompareOut,
    PortTrafficDashboardOut,
    PortTrafficReplacePortRequest,
    PortTrafficSamplePoint,
    PortTrafficSamplesOut,
    PortTrafficSeriesOut,
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
        series_id=str(row.series_id or ""),
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


def _create_series_and_target(
    db: Session,
    *,
    task_id: str,
    t: PortTrafficTargetIn,
    now: datetime,
) -> PortTrafficTarget:
    title = unique_series_title(db, task_id, default_series_title(t.ne_name or "", t.ifname.strip()))
    sid = uuid4().hex
    db.add(
        PortTrafficSeries(
            id=sid,
            task_id=task_id,
            title=title,
            status="active",
            created_at=now,
        )
    )
    row = PortTrafficTarget(
        id=uuid4().hex,
        task_id=task_id,
        series_id=sid,
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
    return row


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
        _create_series_and_target(db, task_id=task.id, t=t, now=now)
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
    series_ids = [str(t.series_id) for t in targets if t.series_id]
    if ids:
        db.query(PortTrafficSample).filter(PortTrafficSample.target_row_id.in_(ids)).delete(
            synchronize_session=False
        )
        db.query(PortTrafficTarget).filter(PortTrafficTarget.task_id == task_id).delete(
            synchronize_session=False
        )
    if series_ids:
        db.query(PortTrafficSeries).filter(PortTrafficSeries.id.in_(series_ids)).delete(
            synchronize_session=False
        )
    else:
        db.query(PortTrafficSeries).filter(PortTrafficSeries.task_id == task_id).delete(
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
    old_series = list({str(t.series_id) for t in old if t.series_id})
    if old_ids:
        db.query(PortTrafficSample).filter(PortTrafficSample.target_row_id.in_(old_ids)).delete(
            synchronize_session=False
        )
        db.query(PortTrafficTarget).filter(PortTrafficTarget.task_id == task_id).delete(
            synchronize_session=False
        )
    if old_series:
        db.query(PortTrafficSeries).filter(PortTrafficSeries.id.in_(old_series)).delete(
            synchronize_session=False
        )
    now = _utcnow()
    rows: list[PortTrafficTarget] = []
    for t in body.targets:
        rows.append(_create_series_and_target(db, task_id=task_id, t=t, now=now))
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


def list_series(db: Session, task_id: str) -> list[PortTrafficSeriesOut]:
    task = db.get(PortTrafficTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task_not_found")
    rows = (
        db.query(PortTrafficSeries)
        .filter(PortTrafficSeries.task_id == task_id)
        .order_by(PortTrafficSeries.title.asc())
        .all()
    )
    out: list[PortTrafficSeriesOut] = []
    for s in rows:
        active = (
            db.query(PortTrafficTarget)
            .filter(PortTrafficTarget.series_id == s.id, PortTrafficTarget.status == "active")
            .order_by(PortTrafficTarget.created_at.desc())
            .first()
        )
        retired = (
            db.query(PortTrafficTarget)
            .filter(PortTrafficTarget.series_id == s.id, PortTrafficTarget.status == "retired")
            .count()
        )
        out.append(
            PortTrafficSeriesOut(
                id=str(s.id),
                task_id=str(s.task_id),
                title=str(s.title or ""),
                status=str(s.status or ""),
                active_target=_target_out(active) if active else None,
                retired_target_count=int(retired),
                created_at=s.created_at,
            )
        )
    return out


def replace_series_port(
    db: Session,
    task_id: str,
    series_id: str,
    body: PortTrafficReplacePortRequest,
) -> PortTrafficSeriesOut:
    task = db.get(PortTrafficTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task_not_found")
    if bool(task.collect_running):
        raise HTTPException(status_code=409, detail="collect_running")
    series = db.get(PortTrafficSeries, series_id)
    if not series or str(series.task_id) != task_id:
        raise HTTPException(status_code=404, detail="series_not_found")
    tmp = PortTrafficTargetIn(
        source=body.source,
        target_id=body.target_id,
        ne_name=body.ne_name,
        ne_ip=body.ne_ip,
        vendor=body.vendor,
        ifname=body.ifname,
        if_description=body.if_description,
        bw_bps=body.bw_bps,
    )
    _assert_supported_targets([tmp])
    now = _utcnow()
    actives = (
        db.query(PortTrafficTarget)
        .filter(PortTrafficTarget.series_id == series_id, PortTrafficTarget.status == "active")
        .all()
    )
    for old in actives:
        old.status = "retired"
    if body.series_title is not None and str(body.series_title).strip():
        wanted = str(body.series_title).strip()[:256]
        if wanted != str(series.title or ""):
            clash = (
                db.query(PortTrafficSeries.id)
                .filter(
                    PortTrafficSeries.task_id == task_id,
                    PortTrafficSeries.title == wanted,
                    PortTrafficSeries.id != series_id,
                )
                .first()
            )
            if clash:
                raise HTTPException(status_code=400, detail="series_title_exists")
            series.title = wanted
    row = PortTrafficTarget(
        id=uuid4().hex,
        task_id=task_id,
        series_id=series_id,
        source=body.source,
        target_id=body.target_id,
        ne_name=body.ne_name or "",
        ne_ip=body.ne_ip or "",
        vendor=body.vendor or "",
        ifname=body.ifname.strip(),
        if_description=body.if_description or "",
        bw_bps=int(body.bw_bps or 0),
        status="active",
        created_at=now,
    )
    db.add(row)
    task.updated_at = now
    db.commit()
    items = list_series(db, task_id)
    for item in items:
        if item.id == series_id:
            return item
    raise HTTPException(status_code=500, detail="series_replace_failed")


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


def _sample_points(
    rows: list[PortTrafficSample],
    *,
    align_offset: timedelta | None = None,
) -> list[PortTrafficSamplePoint]:
    points: list[PortTrafficSamplePoint] = []
    for r in rows:
        ts_raw = r.ts
        ts = ts_raw
        if align_offset is not None and ts_raw is not None:
            ts = ts_raw + align_offset
        points.append(
            PortTrafficSamplePoint(
                ts=ts,
                ts_raw=ts_raw if align_offset is not None else None,
                in_bps=float(r.in_bps or 0),
                out_bps=float(r.out_bps or 0),
                in_util_pct=float(r.in_util_pct or 0),
                out_util_pct=float(r.out_util_pct or 0),
                bw_bps=int(r.bw_bps or 0),
                rate_period_sec=int(r.rate_period_sec or 0),
            )
        )
    return points


def _query_target_samples(
    db: Session,
    *,
    target_row_id: str,
    from_ts: datetime,
    to_ts: datetime,
) -> list[PortTrafficSample]:
    return (
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


def baseline_offset_hours(baseline: str, range_hours: float, offset_hours: float | None) -> float | None:
    key = str(baseline or "off").strip().lower()
    if key in ("", "off", "none"):
        return None
    if key == "shift":
        return float(range_hours)
    if key == "day":
        return 24.0
    if key == "week":
        return 24.0 * 7
    if key == "custom":
        if offset_hours is None or float(offset_hours) <= 0:
            raise HTTPException(status_code=400, detail="offset_hours_required")
        return float(offset_hours)
    raise HTTPException(status_code=400, detail=f"invalid_baseline: {baseline}")


def compare_targets(
    db: Session,
    *,
    target_row_id: str,
    range_hours: float = 24,
    baseline: str = "off",
    offset_hours: float | None = None,
    baseline_target_id: str | None = None,
    to_ts: datetime | None = None,
) -> PortTrafficCompareOut:
    """Compare current interface samples vs period and/or manually mapped interface."""
    target = db.get(PortTrafficTarget, target_row_id)
    if not target:
        raise HTTPException(status_code=404, detail="target_not_found")

    mapped: PortTrafficTarget | None = None
    mapped_id = str(baseline_target_id or "").strip()
    if mapped_id:
        if mapped_id == str(target.id):
            raise HTTPException(status_code=400, detail="baseline_target_same_as_current")
        mapped = db.get(PortTrafficTarget, mapped_id)
        if not mapped:
            raise HTTPException(status_code=404, detail="baseline_target_not_found")

    now = _utcnow()
    to_ts = _as_naive_utc(to_ts) or now
    range_h = max(0.25, float(range_hours or 24))
    from_ts = to_ts - timedelta(hours=range_h)
    to_q = to_ts + timedelta(seconds=5)
    current_rows = _query_target_samples(
        db, target_row_id=str(target.id), from_ts=from_ts, to_ts=to_q
    )
    current = _sample_points(current_rows)

    off_h = baseline_offset_hours(baseline, range_h, offset_hours)
    baseline_points: list[PortTrafficSamplePoint] = []
    # Baseline source: mapped interface if set, else same interface (period compare only).
    base_src = mapped if mapped is not None else target
    want_baseline = off_h is not None or mapped is not None
    if want_baseline:
        if off_h is not None:
            delta = timedelta(hours=off_h)
            b_from = from_ts - delta
            b_to = to_q - delta
            base_rows = _query_target_samples(
                db, target_row_id=str(base_src.id), from_ts=b_from, to_ts=b_to
            )
            baseline_points = _sample_points(base_rows, align_offset=delta)
        else:
            # Mapped port, same window (no time shift) — cross-device overlay.
            base_rows = _query_target_samples(
                db, target_row_id=str(base_src.id), from_ts=from_ts, to_ts=to_q
            )
            baseline_points = _sample_points(base_rows)

    return PortTrafficCompareOut(
        meta=PortTrafficCompareMeta(
            target_id=str(target.id),
            baseline=str(baseline or "off"),
            offset_hours=float(off_h or 0),
            range_hours=range_h,
            current_target=_target_out(target),
            baseline_target=_target_out(mapped) if mapped is not None else None,
            baseline_target_id=str(mapped.id) if mapped is not None else "",
        ),
        current=current,
        baseline=baseline_points,
    )


# Back-compat alias for older imports/tests.
def compare_series(db: Session, **kwargs: Any) -> PortTrafficCompareOut:
    target_row_id = kwargs.pop("target_row_id", None) or kwargs.pop("target_id", None)
    series_id = kwargs.pop("series_id", None)
    if not target_row_id and series_id:
        active = (
            db.query(PortTrafficTarget)
            .filter(PortTrafficTarget.series_id == series_id, PortTrafficTarget.status == "active")
            .order_by(PortTrafficTarget.created_at.desc())
            .first()
        )
        if not active:
            raise HTTPException(status_code=404, detail="series_active_target_not_found")
        target_row_id = str(active.id)
    if not target_row_id:
        raise HTTPException(status_code=400, detail="target_id_required")
    return compare_targets(db, target_row_id=str(target_row_id), **kwargs)


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
    rows = _query_target_samples(db, target_row_id=target_row_id, from_ts=from_ts, to_ts=to_ts)
    return PortTrafficSamplesOut(target=_target_out(target), points=_sample_points(rows))


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
