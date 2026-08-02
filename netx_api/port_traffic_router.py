"""HTTP API for port traffic monitoring (device-centric)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from .auth_service import write_audit
from .db import get_db
from .models import PortTrafficDevice
from .port_traffic_schemas import (
    DiscoverPortsRequest,
    PortTrafficBoardCreate,
    PortTrafficBoardPanelsPut,
    PortTrafficBoardUpdate,
    PortTrafficDeviceCreate,
    PortTrafficDeviceRebind,
    PortTrafficDeviceUpdate,
    PortTrafficInterfacesPut,
    PortTrafficReplacePortRequest,
)
from .port_traffic_board_service import (
    create_board,
    delete_board,
    get_board,
    list_boards,
    put_board_panels,
    update_board,
)
from .port_traffic_service import (
    compare_targets,
    create_device,
    dashboard,
    delete_device,
    discover_ports,
    get_device,
    get_samples,
    list_device_events,
    list_devices,
    list_series,
    list_targets,
    put_interfaces,
    rebind_device,
    replace_series_port,
    set_device_status,
    update_device,
)

router = APIRouter(prefix="/v1/port-traffic", tags=["port-traffic"])


def _actor(request: Request) -> tuple[str, str]:
    user = getattr(request.state, "auth_user", None)
    if not user:
        return "", ""
    return str(getattr(user, "id", "") or ""), str(getattr(user, "username", "") or "")


@router.get("/dashboard")
def api_dashboard(db: Session = Depends(get_db)):
    return dashboard(db).model_dump()


@router.get("/boards")
def api_list_boards(db: Session = Depends(get_db)):
    return {"items": [b.model_dump() for b in list_boards(db)]}


@router.post("/boards")
def api_create_board(
    body: PortTrafficBoardCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    uid, uname = _actor(request)
    out = create_board(db, body, actor_user_id=uid)
    write_audit(
        db,
        action="port_traffic.board.create",
        actor_user_id=uid,
        actor_username=uname,
        method="POST",
        path="/v1/port-traffic/boards",
        status_code=200,
        detail={"id": out.id, "name": out.name},
    )
    return out.model_dump()


@router.get("/boards/{board_id}")
def api_get_board(board_id: str, db: Session = Depends(get_db)):
    return get_board(db, board_id).model_dump()


@router.patch("/boards/{board_id}")
def api_update_board(
    board_id: str,
    body: PortTrafficBoardUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    uid, uname = _actor(request)
    out = update_board(db, board_id, body, actor_user_id=uid)
    write_audit(
        db,
        action="port_traffic.board.update",
        actor_user_id=uid,
        actor_username=uname,
        method="PATCH",
        path=f"/v1/port-traffic/boards/{board_id}",
        status_code=200,
        detail={"id": board_id},
    )
    return out.model_dump()


@router.put("/boards/{board_id}/panels")
def api_put_board_panels(
    board_id: str,
    body: PortTrafficBoardPanelsPut,
    request: Request,
    db: Session = Depends(get_db),
):
    uid, uname = _actor(request)
    out = put_board_panels(db, board_id, body, actor_user_id=uid)
    write_audit(
        db,
        action="port_traffic.board.panels_put",
        actor_user_id=uid,
        actor_username=uname,
        method="PUT",
        path=f"/v1/port-traffic/boards/{board_id}/panels",
        status_code=200,
        detail={"id": board_id, "count": len(out.panels)},
    )
    return out.model_dump()


@router.delete("/boards/{board_id}")
def api_delete_board(
    board_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    uid, uname = _actor(request)
    out = delete_board(db, board_id)
    write_audit(
        db,
        action="port_traffic.board.delete",
        actor_user_id=uid,
        actor_username=uname,
        method="DELETE",
        path=f"/v1/port-traffic/boards/{board_id}",
        status_code=200,
        detail={"id": board_id},
    )
    return out


@router.get("/devices")
def api_list_devices(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return list_devices(db, page=page, page_size=page_size)


@router.post("/devices")
def api_create_device(
    body: PortTrafficDeviceCreate,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
):
    out = create_device(db, body)
    if body.start_now and out.status == "running":
        from .port_traffic_runner import dispatch_collect

        background_tasks.add_task(dispatch_collect, out.id)
    uid, uname = _actor(request)
    write_audit(
        db,
        action="port_traffic.device.create",
        actor_user_id=uid,
        actor_username=uname,
        method="POST",
        path="/v1/port-traffic/devices",
        status_code=200,
        detail={"id": out.id, "ne_id": out.ne_id, "start_now": body.start_now},
    )
    return out.model_dump()


@router.get("/devices/{device_id}")
def api_get_device(device_id: str, db: Session = Depends(get_db)):
    return get_device(db, device_id).model_dump()


@router.patch("/devices/{device_id}")
def api_patch_device(
    device_id: str,
    body: PortTrafficDeviceUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    out = update_device(db, device_id, body)
    uid, uname = _actor(request)
    write_audit(
        db,
        action="port_traffic.device.update",
        actor_user_id=uid,
        actor_username=uname,
        method="PATCH",
        path=f"/v1/port-traffic/devices/{device_id}",
        status_code=200,
        detail=body.model_dump(exclude_unset=True),
    )
    return out.model_dump()


@router.delete("/devices/{device_id}")
def api_delete_device(device_id: str, request: Request, db: Session = Depends(get_db)):
    out = delete_device(db, device_id)
    uid, uname = _actor(request)
    write_audit(
        db,
        action="port_traffic.device.delete",
        actor_user_id=uid,
        actor_username=uname,
        method="DELETE",
        path=f"/v1/port-traffic/devices/{device_id}",
        status_code=200,
        detail={"id": device_id},
    )
    return out


@router.post("/devices/{device_id}/rebind")
def api_rebind_device(
    device_id: str,
    body: PortTrafficDeviceRebind,
    request: Request,
    db: Session = Depends(get_db),
):
    out = rebind_device(db, device_id, body)
    uid, uname = _actor(request)
    write_audit(
        db,
        action="port_traffic.device.rebind",
        actor_user_id=uid,
        actor_username=uname,
        method="POST",
        path=f"/v1/port-traffic/devices/{device_id}/rebind",
        status_code=200,
        detail={"id": device_id, "ne_id": out.ne_id},
    )
    return out.model_dump()


@router.post("/devices/{device_id}/start")
def api_start_device(
    device_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
):
    out = set_device_status(db, device_id, "running")
    from .port_traffic_runner import dispatch_collect

    background_tasks.add_task(dispatch_collect, device_id)
    uid, uname = _actor(request)
    write_audit(
        db,
        action="port_traffic.device.start",
        actor_user_id=uid,
        actor_username=uname,
        method="POST",
        path=f"/v1/port-traffic/devices/{device_id}/start",
        status_code=200,
        detail={"id": device_id},
    )
    return out.model_dump()


@router.post("/devices/{device_id}/collect-now")
def api_collect_now(
    device_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
):
    device = get_device(db, device_id)
    from .port_traffic_runner import dispatch_collect

    row = db.get(PortTrafficDevice, device_id)
    if not row:
        raise HTTPException(status_code=404, detail="device_not_found")
    if bool(row.collect_running):
        return {"ok": True, "started": False, "reason": "already_collecting", **device.model_dump()}
    if str(row.status) != "running":
        row.status = "running"
    row.last_collect_ended_at = None
    row.updated_at = datetime.utcnow()
    db.commit()
    background_tasks.add_task(dispatch_collect, device_id)
    uid, uname = _actor(request)
    write_audit(
        db,
        action="port_traffic.device.collect_now",
        actor_user_id=uid,
        actor_username=uname,
        method="POST",
        path=f"/v1/port-traffic/devices/{device_id}/collect-now",
        status_code=200,
        detail={"id": device_id},
    )
    out = get_device(db, device_id)
    return {"ok": True, "started": True, **out.model_dump()}


@router.post("/devices/{device_id}/pause")
def api_pause_device(device_id: str, request: Request, db: Session = Depends(get_db)):
    out = set_device_status(db, device_id, "paused")
    uid, uname = _actor(request)
    write_audit(
        db,
        action="port_traffic.device.pause",
        actor_user_id=uid,
        actor_username=uname,
        method="POST",
        path=f"/v1/port-traffic/devices/{device_id}/pause",
        status_code=200,
        detail={"id": device_id},
    )
    return out.model_dump()


@router.post("/devices/{device_id}/stop")
def api_stop_device(device_id: str, request: Request, db: Session = Depends(get_db)):
    out = set_device_status(db, device_id, "stopped")
    uid, uname = _actor(request)
    write_audit(
        db,
        action="port_traffic.device.stop",
        actor_user_id=uid,
        actor_username=uname,
        method="POST",
        path=f"/v1/port-traffic/devices/{device_id}/stop",
        status_code=200,
        detail={"id": device_id},
    )
    return out.model_dump()


@router.get("/devices/{device_id}/targets")
def api_list_targets(device_id: str, db: Session = Depends(get_db)):
    return {"items": [t.model_dump() for t in list_targets(db, device_id)]}


@router.get("/devices/{device_id}/events")
def api_list_device_events(
    device_id: str,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    return list_device_events(db, device_id, limit=limit).model_dump()


@router.put("/devices/{device_id}/interfaces")
def api_put_interfaces(
    device_id: str,
    body: PortTrafficInterfacesPut,
    request: Request,
    db: Session = Depends(get_db),
):
    items = put_interfaces(db, device_id, body)
    uid, uname = _actor(request)
    write_audit(
        db,
        action="port_traffic.device.interfaces_put",
        actor_user_id=uid,
        actor_username=uname,
        method="PUT",
        path=f"/v1/port-traffic/devices/{device_id}/interfaces",
        status_code=200,
        detail={"id": device_id, "count": len(items)},
    )
    return {"items": [t.model_dump() for t in items]}


@router.get("/devices/{device_id}/series")
def api_list_series(device_id: str, db: Session = Depends(get_db)):
    return {"items": [s.model_dump() for s in list_series(db, device_id)]}


@router.post("/devices/{device_id}/series/{series_id}/replace")
def api_replace_series_port(
    device_id: str,
    series_id: str,
    body: PortTrafficReplacePortRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    out = replace_series_port(db, device_id, series_id, body)
    uid, uname = _actor(request)
    write_audit(
        db,
        action="port_traffic.series.replace",
        actor_user_id=uid,
        actor_username=uname,
        method="POST",
        path=f"/v1/port-traffic/devices/{device_id}/series/{series_id}/replace",
        status_code=200,
        detail={"series_id": series_id, "ifname": body.ifname},
    )
    return out.model_dump()


@router.post("/discover/ports")
def api_discover_ports(body: DiscoverPortsRequest, db: Session = Depends(get_db)):
    return discover_ports(db, body).model_dump()


@router.get("/samples")
def api_samples(
    target_id: str = Query(..., description="port_traffic_target row id"),
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
):
    return get_samples(db, target_row_id=target_id, from_ts=from_ts, to_ts=to_ts).model_dump()


@router.get("/compare")
def api_compare(
    target_id: str = Query(..., description="port_traffic_target row id (interface)"),
    range_hours: float = Query(default=24, ge=0.25, le=24 * 90),
    baseline: str = Query(default="off", description="off|shift|day|week|custom"),
    offset_hours: float | None = Query(default=None, ge=0.25, le=24 * 90),
    baseline_target_id: str | None = Query(
        default=None,
        description="optional mapped interface for baseline overlay",
    ),
    to_ts: datetime | None = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
):
    return compare_targets(
        db,
        target_row_id=target_id,
        range_hours=range_hours,
        baseline=baseline,
        offset_hours=offset_hours,
        baseline_target_id=baseline_target_id,
        to_ts=to_ts,
    ).model_dump()
