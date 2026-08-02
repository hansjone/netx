"""Port traffic samples, compare, dashboard, events, and retention."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .config import settings
from .models import (
    PortTrafficDevice,
    PortTrafficEvent,
    PortTrafficSample,
    PortTrafficSeries,
    PortTrafficTarget,
)
from .port_traffic_common import _device_out, _target_out, _utcnow
from .port_traffic_parsers import resolve_util_pct
from .port_traffic_schemas import (
    PortTrafficCompareMeta,
    PortTrafficCompareOut,
    PortTrafficDashboardOut,
    PortTrafficEventOut,
    PortTrafficEventsOut,
    PortTrafficSamplePoint,
    PortTrafficSamplesOut,
)

def _as_naive_utc(value: datetime | None) -> datetime | None:
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
        in_bps = float(r.in_bps or 0)
        out_bps = float(r.out_bps or 0)
        bw = int(r.bw_bps or 0)
        points.append(
            PortTrafficSamplePoint(
                ts=ts,
                ts_raw=ts_raw if align_offset is not None else None,
                in_bps=in_bps,
                out_bps=out_bps,
                in_util_pct=resolve_util_pct(float(r.in_util_pct or 0), in_bps, bw),
                out_util_pct=resolve_util_pct(float(r.out_util_pct or 0), out_bps, bw),
                bw_bps=bw,
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
    ahead_hours: float = 0,
    to_ts: datetime | None = None,
) -> PortTrafficCompareOut:
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
    # Anchor = "now" (or explicit to). Lookback is from anchor; ahead extends past it
    # so period compare can show baseline trend after the current clock time.
    anchor = _as_naive_utc(to_ts) or now
    range_h = max(0.25, float(range_hours or 24))
    ahead_h = max(0.0, min(24.0, float(ahead_hours or 0)))
    from_ts = anchor - timedelta(hours=range_h)
    to_end = anchor + timedelta(hours=ahead_h)
    to_q = to_end + timedelta(seconds=5)
    current = _sample_points(
        _query_target_samples(db, target_row_id=str(target.id), from_ts=from_ts, to_ts=to_q)
    )

    off_h = baseline_offset_hours(baseline, range_h, offset_hours)
    baseline_points: list[PortTrafficSamplePoint] = []
    base_src = mapped if mapped is not None else target
    want_baseline = off_h is not None or mapped is not None
    if want_baseline:
        if off_h is not None:
            delta = timedelta(hours=off_h)
            base_rows = _query_target_samples(
                db,
                target_row_id=str(base_src.id),
                from_ts=from_ts - delta,
                to_ts=to_q - delta,
            )
            baseline_points = _sample_points(base_rows, align_offset=delta)
        else:
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
            ahead_hours=ahead_h,
            current_target=_target_out(target),
            baseline_target=_target_out(mapped) if mapped is not None else None,
            baseline_target_id=str(mapped.id) if mapped is not None else "",
        ),
        current=current,
        baseline=baseline_points,
    )


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
    to_ts = to_ts + timedelta(seconds=5)
    rows = _query_target_samples(db, target_row_id=target_row_id, from_ts=from_ts, to_ts=to_ts)
    return PortTrafficSamplesOut(target=_target_out(target), points=_sample_points(rows))


def dashboard(db: Session) -> PortTrafficDashboardOut:
    device_count = db.query(PortTrafficDevice).count()
    running = db.query(PortTrafficDevice).filter(PortTrafficDevice.status == "running").count()
    active_targets = db.query(PortTrafficTarget).filter(PortTrafficTarget.status == "active").count()
    since = _utcnow() - timedelta(hours=24)
    sample_count = (
        db.query(PortTrafficSample)
        .filter(PortTrafficSample.ts >= since, PortTrafficSample.raw_ok.is_(True))
        .count()
    )
    last = db.query(func.max(PortTrafficSample.ts)).scalar()
    return PortTrafficDashboardOut(
        device_count=int(device_count),
        running_device_count=int(running),
        active_target_count=int(active_targets),
        sample_count_24h=int(sample_count),
        last_sample_at=last,
        task_count=int(device_count),
        running_task_count=int(running),
    )


def list_device_events(
    db: Session,
    device_id: str,
    *,
    limit: int = 100,
) -> PortTrafficEventsOut:
    device = db.get(PortTrafficDevice, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="device_not_found")
    lim = max(1, min(500, int(limit or 100)))
    q = db.query(PortTrafficEvent).filter(PortTrafficEvent.device_id == device_id)
    total = q.count()
    # Seed once from current last_error snapshot so older failures still show in log UI.
    if total == 0:
        seeded = False
        if str(device.last_error or "").strip():
            append_device_event(
                db,
                device_id=device_id,
                message=str(device.last_error),
                level="error",
            )
            seeded = True
        for t in (
            db.query(PortTrafficTarget)
            .filter(PortTrafficTarget.device_id == device_id)
            .order_by(PortTrafficTarget.ifname)
            .all()
        ):
            err = str(t.last_error or "").strip()
            if not err:
                continue
            append_device_event(
                db,
                device_id=device_id,
                target_row_id=str(t.id),
                ifname=str(t.ifname or ""),
                message=err,
                level="error",
            )
            seeded = True
        if seeded:
            db.commit()
            total = db.query(PortTrafficEvent).filter(PortTrafficEvent.device_id == device_id).count()
            q = db.query(PortTrafficEvent).filter(PortTrafficEvent.device_id == device_id)
    rows = q.order_by(PortTrafficEvent.created_at.desc()).limit(lim).all()
    items = [
        PortTrafficEventOut(
            id=str(r.id),
            device_id=str(r.device_id or ""),
            target_row_id=str(r.target_row_id or ""),
            ifname=str(r.ifname or ""),
            level=str(r.level or "error"),
            message=str(r.message or ""),
            created_at=r.created_at,
        )
        for r in rows
    ]
    return PortTrafficEventsOut(items=items, total=int(total))


def append_device_event(
    db: Session,
    *,
    device_id: str,
    message: str,
    level: str = "error",
    target_row_id: str = "",
    ifname: str = "",
) -> None:
    msg = str(message or "").strip()
    if not msg or not device_id:
        return
    db.add(
        PortTrafficEvent(
            id=uuid4().hex,
            device_id=device_id,
            target_row_id=str(target_row_id or ""),
            ifname=str(ifname or ""),
            level=str(level or "error")[:16],
            message=msg[:4000],
            created_at=_utcnow(),
        )
    )


def purge_expired_samples(db: Session) -> int:
    devices = db.query(PortTrafficDevice).all()
    deleted = 0
    now = _utcnow()
    for device in devices:
        days = max(1, int(device.retention_days or 7))
        cutoff = now - timedelta(days=days)
        target_ids = [
            str(t.id)
            for t in db.query(PortTrafficTarget.id)
            .filter(PortTrafficTarget.device_id == device.id)
            .all()
        ]
        if target_ids:
            n = (
                db.query(PortTrafficSample)
                .filter(
                    PortTrafficSample.target_row_id.in_(target_ids),
                    PortTrafficSample.ts < cutoff,
                )
                .delete(synchronize_session=False)
            )
            deleted += int(n or 0)
        db.query(PortTrafficEvent).filter(
            PortTrafficEvent.device_id == device.id,
            PortTrafficEvent.created_at < cutoff,
        ).delete(synchronize_session=False)
    db.commit()
    if deleted:
        _log.info("port_traffic retention purged samples=%s", deleted)
    return deleted
