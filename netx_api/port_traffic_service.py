"""Port traffic monitoring service: device-centric CRUD, discover, samples, dashboard."""

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
from .models import PortTrafficDevice, PortTrafficEvent, PortTrafficSample, PortTrafficSeries, PortTrafficTarget
from .ne_session_factory import close_netmiko_connection, open_netmiko_connection
from .ne_netmiko import send_show_command
from .port_traffic_commands import commands_for_vendor
from .port_traffic_migrate import default_series_title, unique_series_title
from .port_traffic_parsers import brief_port_to_dict, parse_interface_brief, resolve_util_pct
from .port_traffic_schemas import (
    DiscoverPortItem,
    DiscoverPortsRequest,
    DiscoverPortsResponse,
    PortTrafficCompareMeta,
    PortTrafficCompareOut,
    PortTrafficDashboardOut,
    PortTrafficDeviceCreate,
    PortTrafficDeviceOut,
    PortTrafficDeviceUpdate,
    PortTrafficEventOut,
    PortTrafficEventsOut,
    PortTrafficIfaceIn,
    PortTrafficInterfacesPut,
    PortTrafficReplacePortRequest,
    PortTrafficSamplePoint,
    PortTrafficSamplesOut,
    PortTrafficSeriesOut,
    PortTrafficTargetOut,
)

_log = logging.getLogger("netx.port_traffic.service")


def _utcnow() -> datetime:
    return datetime.utcnow()


def _target_out(row: PortTrafficTarget) -> PortTrafficTargetOut:
    did = str(row.device_id or "")
    return PortTrafficTargetOut(
        id=str(row.id),
        device_id=did,
        task_id=did,
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


def _device_out(db: Session, device: PortTrafficDevice) -> PortTrafficDeviceOut:
    did = str(device.id)
    total = db.query(PortTrafficTarget).filter(PortTrafficTarget.device_id == did).count()
    active = (
        db.query(PortTrafficTarget)
        .filter(PortTrafficTarget.device_id == did, PortTrafficTarget.status == "active")
        .count()
    )
    return PortTrafficDeviceOut(
        id=did,
        source=str(device.source or ""),
        ne_id=str(device.ne_id or ""),
        ne_name=str(device.ne_name or ""),
        ne_ip=str(device.ne_ip or ""),
        vendor=str(device.vendor or ""),
        note=str(device.note or ""),
        status=str(device.status or ""),
        interval_sec=int(device.interval_sec or 60),
        retention_days=int(device.retention_days or 7),
        concurrency=int(device.concurrency or 1),
        collect_running=bool(device.collect_running),
        target_count=int(total),
        active_target_count=int(active),
        last_collect_started_at=device.last_collect_started_at,
        last_collect_ended_at=device.last_collect_ended_at,
        last_error=str(device.last_error or ""),
        created_at=device.created_at,
        updated_at=device.updated_at,
    )


def _assert_vendor(vendor: str, label: str) -> None:
    cmds = commands_for_vendor(vendor or "", "")
    if cmds is None:
        raise HTTPException(
            status_code=400,
            detail=f"vendor_not_supported_for_port_traffic: {vendor or 'unknown'} ({label})",
        )


def _assert_ifaces_free(
    db: Session,
    *,
    source: str,
    ne_id: str,
    ifnames: list[str],
    exclude_device_id: str | None = None,
) -> None:
    names = [str(x).strip() for x in ifnames if str(x).strip()]
    if not names:
        return
    q = db.query(PortTrafficTarget).filter(
        PortTrafficTarget.source == source,
        PortTrafficTarget.target_id == ne_id,
        PortTrafficTarget.ifname.in_(names),
        PortTrafficTarget.status == "active",
    )
    if exclude_device_id:
        q = q.filter(PortTrafficTarget.device_id != exclude_device_id)
    hit = q.first()
    if hit:
        raise HTTPException(
            status_code=409,
            detail=f"interface_already_monitored: {hit.ifname} on device {hit.device_id}",
        )


def _create_iface(
    db: Session,
    *,
    device: PortTrafficDevice,
    iface: PortTrafficIfaceIn,
    now: datetime,
) -> PortTrafficTarget:
    ifname = iface.ifname.strip()
    title = unique_series_title(
        db, str(device.id), default_series_title(device.ne_name or "", ifname)
    )
    sid = uuid4().hex
    db.add(
        PortTrafficSeries(
            id=sid,
            device_id=str(device.id),
            title=title,
            status="active",
            created_at=now,
        )
    )
    row = PortTrafficTarget(
        id=uuid4().hex,
        device_id=str(device.id),
        series_id=sid,
        source=str(device.source),
        target_id=str(device.ne_id),
        ne_name=str(device.ne_name or ""),
        ne_ip=str(device.ne_ip or ""),
        vendor=str(device.vendor or ""),
        ifname=ifname,
        if_description=iface.if_description or "",
        bw_bps=int(iface.bw_bps or 0),
        status="active",
        created_at=now,
    )
    db.add(row)
    return row


def list_devices(db: Session, *, page: int = 1, page_size: int = 20) -> dict[str, Any]:
    q = db.query(PortTrafficDevice).order_by(PortTrafficDevice.ne_name.asc(), PortTrafficDevice.ne_ip.asc())
    total = q.count()
    rows = q.offset((page - 1) * page_size).limit(page_size).all()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_device_out(db, r).model_dump() for r in rows],
    }


def get_device(db: Session, device_id: str) -> PortTrafficDeviceOut:
    device = db.get(PortTrafficDevice, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="device_not_found")
    return _device_out(db, device)


def create_device(db: Session, body: PortTrafficDeviceCreate) -> PortTrafficDeviceOut:
    source = body.source
    ne_id = body.ne_id.strip()
    _assert_vendor(body.vendor, body.ne_name or ne_id)
    clash = (
        db.query(PortTrafficDevice)
        .filter(PortTrafficDevice.source == source, PortTrafficDevice.ne_id == ne_id)
        .first()
    )
    if clash:
        raise HTTPException(status_code=409, detail="device_already_monitored")
    ifnames = [i.ifname for i in body.interfaces]
    _assert_ifaces_free(db, source=source, ne_id=ne_id, ifnames=ifnames)

    now = _utcnow()
    status = "running" if body.start_now and body.interfaces else "draft"
    device = PortTrafficDevice(
        id=uuid4().hex,
        source=source,
        ne_id=ne_id,
        ne_name=(body.ne_name or "").strip(),
        ne_ip=(body.ne_ip or "").strip(),
        vendor=(body.vendor or "").strip(),
        note=(body.note or "").strip(),
        status=status,
        interval_sec=int(body.interval_sec),
        retention_days=int(body.retention_days),
        concurrency=int(body.concurrency),
        created_at=now,
        updated_at=now,
    )
    db.add(device)
    db.flush()
    for iface in body.interfaces:
        if not iface.ifname.strip():
            continue
        _create_iface(db, device=device, iface=iface, now=now)
    db.commit()
    db.refresh(device)
    return _device_out(db, device)


def update_device(db: Session, device_id: str, body: PortTrafficDeviceUpdate) -> PortTrafficDeviceOut:
    device = db.get(PortTrafficDevice, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="device_not_found")
    data = body.model_dump(exclude_unset=True)
    for key in ("note", "ne_name", "ne_ip", "vendor"):
        if key in data and data[key] is not None:
            setattr(device, key, str(data[key]).strip())
    if "interval_sec" in data and data["interval_sec"] is not None:
        device.interval_sec = int(data["interval_sec"])
    if "retention_days" in data and data["retention_days"] is not None:
        device.retention_days = int(data["retention_days"])
    if "concurrency" in data and data["concurrency"] is not None:
        device.concurrency = int(data["concurrency"])
    device.updated_at = _utcnow()
    db.commit()
    db.refresh(device)
    return _device_out(db, device)


def delete_device(db: Session, device_id: str) -> dict[str, Any]:
    device = db.get(PortTrafficDevice, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="device_not_found")
    if bool(device.collect_running):
        raise HTTPException(status_code=409, detail="collect_running")
    targets = db.query(PortTrafficTarget).filter(PortTrafficTarget.device_id == device_id).all()
    ids = [str(t.id) for t in targets]
    series_ids = [str(t.series_id) for t in targets if t.series_id]
    if ids:
        db.query(PortTrafficSample).filter(PortTrafficSample.target_row_id.in_(ids)).delete(
            synchronize_session=False
        )
        db.query(PortTrafficTarget).filter(PortTrafficTarget.device_id == device_id).delete(
            synchronize_session=False
        )
    db.query(PortTrafficEvent).filter(PortTrafficEvent.device_id == device_id).delete(
        synchronize_session=False
    )
    if series_ids:
        db.query(PortTrafficSeries).filter(PortTrafficSeries.id.in_(series_ids)).delete(
            synchronize_session=False
        )
    else:
        db.query(PortTrafficSeries).filter(PortTrafficSeries.device_id == device_id).delete(
            synchronize_session=False
        )
    db.delete(device)
    db.commit()
    return {"ok": True, "id": device_id}


def set_device_status(db: Session, device_id: str, status: str) -> PortTrafficDeviceOut:
    device = db.get(PortTrafficDevice, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="device_not_found")
    if status == "running":
        active = (
            db.query(PortTrafficTarget)
            .filter(PortTrafficTarget.device_id == device_id, PortTrafficTarget.status == "active")
            .count()
        )
        if active <= 0:
            raise HTTPException(status_code=400, detail="no_active_targets")
        device.last_collect_ended_at = None
    device.status = status
    device.updated_at = _utcnow()
    db.commit()
    db.refresh(device)
    return _device_out(db, device)


def put_interfaces(db: Session, device_id: str, body: PortTrafficInterfacesPut) -> list[PortTrafficTargetOut]:
    device = db.get(PortTrafficDevice, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="device_not_found")
    if bool(device.collect_running):
        raise HTTPException(status_code=409, detail="collect_running")

    wanted = {i.ifname.strip(): i for i in body.interfaces if i.ifname.strip()}
    _assert_ifaces_free(
        db,
        source=str(device.source),
        ne_id=str(device.ne_id),
        ifnames=list(wanted.keys()),
        exclude_device_id=device_id,
    )

    now = _utcnow()
    existing = (
        db.query(PortTrafficTarget)
        .filter(PortTrafficTarget.device_id == device_id)
        .all()
    )
    by_if = {str(t.ifname): t for t in existing if str(t.status) == "active"}

    for ifname, row in list(by_if.items()):
        if ifname not in wanted:
            row.status = "retired"

    for ifname, iface in wanted.items():
        if ifname in by_if:
            row = by_if[ifname]
            row.if_description = iface.if_description or row.if_description
            if iface.bw_bps:
                row.bw_bps = int(iface.bw_bps)
            continue
        # Reactivate retired same ifname if present
        retired = next(
            (
                t
                for t in existing
                if str(t.ifname) == ifname and str(t.status) in ("retired", "disabled")
            ),
            None,
        )
        if retired:
            retired.status = "active"
            retired.if_description = iface.if_description or retired.if_description
            if iface.bw_bps:
                retired.bw_bps = int(iface.bw_bps)
            continue
        _create_iface(db, device=device, iface=iface, now=now)

    device.updated_at = now
    db.commit()
    return list_targets(db, device_id)


def list_targets(db: Session, device_id: str) -> list[PortTrafficTargetOut]:
    device = db.get(PortTrafficDevice, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="device_not_found")
    rows = (
        db.query(PortTrafficTarget)
        .filter(PortTrafficTarget.device_id == device_id)
        .order_by(PortTrafficTarget.ifname)
        .all()
    )
    return [_target_out(r) for r in rows]


def list_series(db: Session, device_id: str) -> list[PortTrafficSeriesOut]:
    device = db.get(PortTrafficDevice, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="device_not_found")
    rows = (
        db.query(PortTrafficSeries)
        .filter(PortTrafficSeries.device_id == device_id)
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
        did = str(s.device_id or "")
        out.append(
            PortTrafficSeriesOut(
                id=str(s.id),
                device_id=did,
                task_id=did,
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
    device_id: str,
    series_id: str,
    body: PortTrafficReplacePortRequest,
) -> PortTrafficSeriesOut:
    device = db.get(PortTrafficDevice, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="device_not_found")
    if bool(device.collect_running):
        raise HTTPException(status_code=409, detail="collect_running")
    series = db.get(PortTrafficSeries, series_id)
    if not series or str(series.device_id) != device_id:
        raise HTTPException(status_code=404, detail="series_not_found")
    ifname = body.ifname.strip()
    _assert_ifaces_free(
        db,
        source=str(device.source),
        ne_id=str(device.ne_id),
        ifnames=[ifname],
        exclude_device_id=device_id,
    )
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
                    PortTrafficSeries.device_id == device_id,
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
        device_id=device_id,
        series_id=series_id,
        source=str(device.source),
        target_id=str(device.ne_id),
        ne_name=str(device.ne_name or ""),
        ne_ip=str(device.ne_ip or ""),
        vendor=str(device.vendor or ""),
        ifname=ifname,
        if_description=body.if_description or "",
        bw_bps=int(body.bw_bps or 0),
        status="active",
        created_at=now,
    )
    db.add(row)
    device.updated_at = now
    db.commit()
    for item in list_series(db, device_id):
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
    to_ts = _as_naive_utc(to_ts) or now
    range_h = max(0.25, float(range_hours or 24))
    from_ts = to_ts - timedelta(hours=range_h)
    to_q = to_ts + timedelta(seconds=5)
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
