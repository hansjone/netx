"""Shared helpers for port traffic device/sample services."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .models import (
    PortTrafficDevice,
    PortTrafficSeries,
    PortTrafficTarget,
)
from .port_traffic_commands import commands_for_vendor
from .port_traffic_migrate import default_series_title, unique_series_title
from .port_traffic_schemas import (
    PortTrafficDeviceOut,
    PortTrafficIfaceIn,
    PortTrafficTargetOut,
)
from .timeutil import utcnow_naive


def _utcnow() -> datetime:
    return utcnow_naive()


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


