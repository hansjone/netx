"""HTTP API for port traffic monitoring."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from .auth_service import write_audit
from .db import get_db
from .port_traffic_schemas import (
    DiscoverPortsRequest,
    PortTrafficReplacePortRequest,
    PortTrafficTaskCreate,
    PortTrafficTaskUpdate,
    PortTrafficTargetsPut,
)
from .port_traffic_service import (
    compare_targets,
    create_task,
    dashboard,
    delete_task,
    discover_ports,
    get_samples,
    get_task,
    list_series,
    list_targets,
    list_tasks,
    put_targets,
    replace_series_port,
    set_task_status,
    update_task,
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


@router.get("/tasks")
def api_list_tasks(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return list_tasks(db, page=page, page_size=page_size)


@router.post("/tasks")
def api_create_task(
    body: PortTrafficTaskCreate,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
):
    out = create_task(db, body)
    if body.start_now and out.status == "running":
        from .port_traffic_runner import dispatch_collect

        background_tasks.add_task(dispatch_collect, out.id)
    uid, uname = _actor(request)
    write_audit(
        db,
        action="port_traffic.task.create",
        actor_user_id=uid,
        actor_username=uname,
        method="POST",
        path="/v1/port-traffic/tasks",
        status_code=200,
        detail={"id": out.id, "title": out.title, "start_now": body.start_now},
    )
    return out.model_dump()


@router.get("/tasks/{task_id}")
def api_get_task(task_id: str, db: Session = Depends(get_db)):
    return get_task(db, task_id).model_dump()


@router.patch("/tasks/{task_id}")
def api_patch_task(
    task_id: str,
    body: PortTrafficTaskUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    out = update_task(db, task_id, body)
    uid, uname = _actor(request)
    write_audit(
        db,
        action="port_traffic.task.update",
        actor_user_id=uid,
        actor_username=uname,
        method="PATCH",
        path=f"/v1/port-traffic/tasks/{task_id}",
        status_code=200,
        detail=body.model_dump(exclude_unset=True),
    )
    return out.model_dump()


@router.delete("/tasks/{task_id}")
def api_delete_task(task_id: str, request: Request, db: Session = Depends(get_db)):
    out = delete_task(db, task_id)
    uid, uname = _actor(request)
    write_audit(
        db,
        action="port_traffic.task.delete",
        actor_user_id=uid,
        actor_username=uname,
        method="DELETE",
        path=f"/v1/port-traffic/tasks/{task_id}",
        status_code=200,
        detail={"id": task_id},
    )
    return out


@router.post("/tasks/{task_id}/start")
def api_start_task(
    task_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
):
    out = set_task_status(db, task_id, "running")
    from .port_traffic_runner import dispatch_collect

    background_tasks.add_task(dispatch_collect, task_id)
    uid, uname = _actor(request)
    write_audit(
        db,
        action="port_traffic.task.start",
        actor_user_id=uid,
        actor_username=uname,
        method="POST",
        path=f"/v1/port-traffic/tasks/{task_id}/start",
        status_code=200,
        detail={"id": task_id},
    )
    return out.model_dump()


@router.post("/tasks/{task_id}/collect-now")
def api_collect_now(
    task_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
):
    task = get_task(db, task_id)
    if task.status not in ("running", "paused", "draft", "stopped"):
        raise HTTPException(status_code=400, detail="invalid_status")
    # Force due: clear last end so claim accepts, ensure running for this round.
    from .models import PortTrafficTask
    from .port_traffic_runner import dispatch_collect

    row = db.get(PortTrafficTask, task_id)
    if not row:
        raise HTTPException(status_code=404, detail="task_not_found")
    if bool(row.collect_running):
        return {"ok": True, "started": False, "reason": "already_collecting", **task.model_dump()}
    if str(row.status) != "running":
        row.status = "running"
    row.last_collect_ended_at = None
    row.updated_at = datetime.utcnow()
    db.commit()
    background_tasks.add_task(dispatch_collect, task_id)
    uid, uname = _actor(request)
    write_audit(
        db,
        action="port_traffic.task.collect_now",
        actor_user_id=uid,
        actor_username=uname,
        method="POST",
        path=f"/v1/port-traffic/tasks/{task_id}/collect-now",
        status_code=200,
        detail={"id": task_id},
    )
    out = get_task(db, task_id)
    return {"ok": True, "started": True, **out.model_dump()}


@router.post("/tasks/{task_id}/pause")
def api_pause_task(task_id: str, request: Request, db: Session = Depends(get_db)):
    out = set_task_status(db, task_id, "paused")
    uid, uname = _actor(request)
    write_audit(
        db,
        action="port_traffic.task.pause",
        actor_user_id=uid,
        actor_username=uname,
        method="POST",
        path=f"/v1/port-traffic/tasks/{task_id}/pause",
        status_code=200,
        detail={"id": task_id},
    )
    return out.model_dump()


@router.post("/tasks/{task_id}/stop")
def api_stop_task(task_id: str, request: Request, db: Session = Depends(get_db)):
    out = set_task_status(db, task_id, "stopped")
    uid, uname = _actor(request)
    write_audit(
        db,
        action="port_traffic.task.stop",
        actor_user_id=uid,
        actor_username=uname,
        method="POST",
        path=f"/v1/port-traffic/tasks/{task_id}/stop",
        status_code=200,
        detail={"id": task_id},
    )
    return out.model_dump()


@router.get("/tasks/{task_id}/targets")
def api_list_targets(task_id: str, db: Session = Depends(get_db)):
    return {"items": [t.model_dump() for t in list_targets(db, task_id)]}


@router.get("/tasks/{task_id}/series")
def api_list_series(task_id: str, db: Session = Depends(get_db)):
    return {"items": [s.model_dump() for s in list_series(db, task_id)]}


@router.post("/tasks/{task_id}/series/{series_id}/replace")
def api_replace_series_port(
    task_id: str,
    series_id: str,
    body: PortTrafficReplacePortRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    out = replace_series_port(db, task_id, series_id, body)
    uid, uname = _actor(request)
    write_audit(
        db,
        action="port_traffic.series.replace",
        actor_user_id=uid,
        actor_username=uname,
        method="POST",
        path=f"/v1/port-traffic/tasks/{task_id}/series/{series_id}/replace",
        status_code=200,
        detail={"series_id": series_id, "ifname": body.ifname, "target_id": body.target_id},
    )
    return out.model_dump()


@router.put("/tasks/{task_id}/targets")
def api_put_targets(
    task_id: str,
    body: PortTrafficTargetsPut,
    request: Request,
    db: Session = Depends(get_db),
):
    items = put_targets(db, task_id, body)
    uid, uname = _actor(request)
    write_audit(
        db,
        action="port_traffic.targets.put",
        actor_user_id=uid,
        actor_username=uname,
        method="PUT",
        path=f"/v1/port-traffic/tasks/{task_id}/targets",
        status_code=200,
        detail={"id": task_id, "count": len(items)},
    )
    return {"items": [t.model_dump() for t in items]}


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
        description="optional mapped interface for baseline overlay (any task)",
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
