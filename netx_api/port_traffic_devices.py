"""Port traffic device CRUD, interfaces, series, and discover."""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .cli_creds import require_cli_creds_ready
from .cli_resolve import resolve_cli_target
from .config import settings
from .models import (
    ManagedNE,
    PortTrafficDevice,
    PortTrafficEvent,
    PortTrafficSample,
    PortTrafficSeries,
    PortTrafficTarget,
    UmeInventoryNE,
)
from .ne_netmiko import send_show_command
from .ne_session_factory import close_netmiko_connection, open_netmiko_connection
from .port_traffic_commands import commands_for_vendor
from .port_traffic_common import (
    _assert_ifaces_free,
    _assert_vendor,
    _create_iface,
    _device_out,
    _target_out,
    _utcnow,
)
from .port_traffic_migrate import default_series_title, unique_series_title
from .port_traffic_parsers import brief_port_to_dict, parse_interface_brief
from .port_traffic_schemas import (
    DiscoverPortItem,
    DiscoverPortsRequest,
    DiscoverPortsResponse,
    PortTrafficDeviceCreate,
    PortTrafficDeviceOut,
    PortTrafficDeviceRebind,
    PortTrafficDeviceUpdate,
    PortTrafficInterfacesPut,
    PortTrafficReplacePortRequest,
    PortTrafficSeriesOut,
    PortTrafficTargetOut,
)

_log = logging.getLogger("netx.port_traffic.service")


def list_devices(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 20,
    status: str = "",
    keyword: str = "",
) -> dict[str, Any]:
    q = db.query(PortTrafficDevice)
    st = str(status or "").strip()
    if st:
        q = q.filter(PortTrafficDevice.status == st)
    kw = str(keyword or "").strip()
    if kw:
        like = f"%{kw}%"
        q = q.filter(
            or_(
                PortTrafficDevice.ne_name.ilike(like),
                PortTrafficDevice.ne_ip.ilike(like),
                PortTrafficDevice.ne_id.ilike(like),
                PortTrafficDevice.vendor.ilike(like),
            )
        )
    q = q.order_by(PortTrafficDevice.ne_name.asc(), PortTrafficDevice.ne_ip.asc())
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


def rebind_device(
    db: Session,
    device_id: str,
    body: PortTrafficDeviceRebind,
) -> PortTrafficDeviceOut:
    """Point a monitor device at an explicitly chosen inventory NE; keeps samples."""
    device = db.get(PortTrafficDevice, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="device_not_found")
    if bool(device.collect_running):
        raise HTTPException(status_code=409, detail="collect_running")

    source = str(device.source or "").strip().lower() or "managed"
    want_id = str(body.ne_id or "").strip()
    if not want_id:
        raise HTTPException(status_code=400, detail="ne_id_required")

    new_id = ""
    new_name = ""
    new_ip = ""
    new_vendor = ""

    if source == "managed":
        row = db.get(ManagedNE, want_id)
        if not row:
            raise HTTPException(status_code=404, detail="managed_ne_not_found")
        new_id = str(row.id)
        new_name = str(row.name or "")
        new_ip = str(row.ip_address or "")
        new_vendor = str(row.vendor or "")
    elif source == "ume":
        inv = db.get(UmeInventoryNE, want_id)
        if not inv:
            raise HTTPException(status_code=404, detail="ume_ne_not_found")
        new_id = str(inv.ne_id)
        new_ip = str(inv.ip_address or "")
        new_name = str(inv.user_label or inv.ne_name or inv.host_name or new_ip or "").strip()
        new_vendor = str(inv.vendor or "")
    else:
        raise HTTPException(status_code=400, detail="invalid_source")

    if new_id == str(device.ne_id or ""):
        # Already bound; clear stale errors so collect can resume.
        device.last_error = ""
        device.updated_at = _utcnow()
        for tgt in db.query(PortTrafficTarget).filter(PortTrafficTarget.device_id == device_id).all():
            if "managed_ne_not_found" in str(tgt.last_error or "") or "ume_ne_not_found" in str(
                tgt.last_error or ""
            ):
                tgt.last_error = ""
        db.commit()
        db.refresh(device)
        return _device_out(db, device)

    clash = (
        db.query(PortTrafficDevice)
        .filter(
            PortTrafficDevice.source == source,
            PortTrafficDevice.ne_id == new_id,
            PortTrafficDevice.id != device_id,
        )
        .first()
    )
    if clash:
        raise HTTPException(status_code=409, detail="device_already_monitored")

    device.ne_id = new_id
    if new_name:
        device.ne_name = new_name
    if new_ip:
        device.ne_ip = new_ip
    if new_vendor:
        device.vendor = new_vendor
    device.last_error = ""
    device.updated_at = _utcnow()

    for tgt in db.query(PortTrafficTarget).filter(PortTrafficTarget.device_id == device_id).all():
        tgt.target_id = new_id
        if new_name:
            tgt.ne_name = new_name
        if new_ip:
            tgt.ne_ip = new_ip
        if new_vendor:
            tgt.vendor = new_vendor
        if "managed_ne_not_found" in str(tgt.last_error or "") or "ume_ne_not_found" in str(
            tgt.last_error or ""
        ):
            tgt.last_error = ""

    db.commit()
    db.refresh(device)
    _log.info("port_traffic rebind device=%s -> ne_id=%s ip=%s", device_id, new_id, new_ip)
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
        from .port_traffic_board_service import delete_panels_for_targets

        delete_panels_for_targets(db, ids)
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

    require_cli_creds_ready(creds, interactive=False)

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

    command = str(cmds.brief or "")
    base = DiscoverPortsResponse(
        source=body.source,
        id=body.id,
        ne_name=ne_name,
        ne_ip=ne_ip,
        vendor=vendor,
        vendor_key=cmds.vendor_key,
        command=command,
    )

    per_cmd = int(settings.ne_collect_read_timeout_sec or 120)
    raw = ""
    try:
        conn = open_netmiko_connection(creds, session_timeout=per_cmd + 60)
        try:
            raw = send_show_command(conn, command, read_timeout=per_cmd)
        finally:
            close_netmiko_connection(conn)
    except Exception as exc:
        preview = str(raw or "")
        if len(preview) > 12_000:
            preview = f"{preview[:12_000]}\n...[truncated preview 12000/{len(raw)} chars]"
        base.ok = False
        base.error = f"cli_failed: {exc}"
        base.raw_preview = preview
        return base

    preview = str(raw or "")
    if len(preview) > 12_000:
        preview = f"{preview[:12_000]}\n...[truncated preview 12000/{len(raw)} chars]"
    base.raw_preview = preview

    try:
        ports = [
            DiscoverPortItem(**brief_port_to_dict(p))
            for p in parse_interface_brief(
                raw,
                cmds.vendor_key,
                command=command,
                device_type=device_type,
            )
        ]
    except Exception as exc:
        base.ok = False
        base.error = f"parse_failed: {exc}"
        return base

    base.ports = ports
    if not ports:
        base.ok = False
        base.error = "no_ports_parsed"
    return base


