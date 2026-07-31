"""HTTP API for config sync."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from .auth_service import write_audit
from .config_sync_runner import dispatch_cycle
from .config_sync_schemas import ConfigSyncCycleCreate, ConfigSyncPolicyUpdate
from .config_sync_service import (
    build_snapshot_export,
    create_cycle,
    dashboard,
    get_cycle,
    get_policy,
    get_snapshot_detail,
    list_cycle_tasks,
    list_cycles,
    list_snapshot_history,
    list_snapshots,
    pause_cycle,
    resume_cycle,
    update_policy,
)
from .db import get_db

router = APIRouter(prefix="/v1/config-sync", tags=["config-sync"])


def _actor(request: Request) -> tuple[str, str]:
    user = getattr(request.state, "auth_user", None)
    if not user:
        return "", ""
    return str(getattr(user, "id", "") or ""), str(getattr(user, "username", "") or "")


@router.get("/policy")
def api_get_policy(db: Session = Depends(get_db)):
    return get_policy(db).model_dump()


@router.put("/policy")
def api_put_policy(
    body: ConfigSyncPolicyUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    out = update_policy(db, body)
    uid, uname = _actor(request)
    write_audit(
        db,
        action="config_sync.policy.update",
        actor_user_id=uid,
        actor_username=uname,
        method="PUT",
        path="/v1/config-sync/policy",
        status_code=200,
        detail=body.model_dump(exclude_unset=True),
    )
    return out.model_dump()


@router.get("/dashboard")
def api_dashboard(db: Session = Depends(get_db)):
    return dashboard(db).model_dump()


@router.get("/cycles")
def api_list_cycles(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return list_cycles(db, page=page, page_size=page_size)


@router.post("/cycles")
def api_create_cycle(
    body: ConfigSyncCycleCreate,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
):
    out = create_cycle(db, body)
    background_tasks.add_task(dispatch_cycle, out.id)
    uid, uname = _actor(request)
    action = "config_sync.retry_failed" if body.mode == "retry_failed" else "config_sync.start"
    write_audit(
        db,
        action=action,
        actor_user_id=uid,
        actor_username=uname,
        method="POST",
        path="/v1/config-sync/cycles",
        status_code=200,
        detail={"mode": body.mode, "cycle_id": out.id},
    )
    return out.model_dump()


@router.get("/cycles/{cycle_id}")
def api_get_cycle(cycle_id: str, db: Session = Depends(get_db)):
    return get_cycle(db, cycle_id).model_dump()


@router.get("/cycles/{cycle_id}/tasks")
def api_list_cycle_tasks(
    cycle_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    status: str = Query(default=""),
    keyword: str = Query(default=""),
    db: Session = Depends(get_db),
):
    return list_cycle_tasks(
        db, cycle_id, page=page, page_size=page_size, status=status, keyword=keyword
    )


@router.post("/cycles/{cycle_id}/pause")
def api_pause_cycle(cycle_id: str, db: Session = Depends(get_db)):
    return pause_cycle(db, cycle_id).model_dump()


@router.post("/cycles/{cycle_id}/resume")
def api_resume_cycle(
    cycle_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    out = resume_cycle(db, cycle_id)
    background_tasks.add_task(dispatch_cycle, out.id)
    return out.model_dump()


@router.get("/snapshots")
def api_list_snapshots(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    keyword: str = Query(default=""),
    source: str = Query(default=""),
    vendor: str = Query(default=""),
    db: Session = Depends(get_db),
):
    return list_snapshots(
        db, page=page, page_size=page_size, keyword=keyword, source=source, vendor=vendor
    )


@router.get("/snapshots/{source}/{target_id}/download")
def api_download_snapshot(
    source: str,
    target_id: str,
    field: str = Query(default="primary"),
    db: Session = Depends(get_db),
):
    filename, payload, media_type = build_snapshot_export(db, source, target_id, field=field)
    # ASCII fallback + UTF-8 filename for CJK device names
    safe_ascii = filename.encode("ascii", errors="replace").decode("ascii").replace("?", "_")
    return Response(
        content=payload,
        media_type=media_type,
        headers={
            "content-disposition": (
                f'attachment; filename="{safe_ascii}"; '
                f"filename*=UTF-8''{quote(filename)}"
            )
        },
    )


@router.get("/snapshots/{source}/{target_id}")
def api_get_snapshot(
    source: str,
    target_id: str,
    field: str = Query(default="both"),
    db: Session = Depends(get_db),
):
    return get_snapshot_detail(db, source, target_id, field=field).model_dump()


@router.get("/snapshots/{source}/{target_id}/history")
def api_snapshot_history(
    source: str,
    target_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return list_snapshot_history(db, source, target_id, page=page, page_size=page_size)
