"""HTTP API for business state monitoring (/v1/biz-state)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .db import get_db
from .biz_state import service as svc
from .biz_state.collect_runner import dispatch_collect
from .lldp_shared import resolve_vendor_key
from .models import BizStateTask

router = APIRouter(prefix="/v1/biz-state", tags=["biz-state"])


class ProfileOverrideIn(BaseModel):
    title: str | None = None
    command_template: str | None = None
    description: str | None = None
    sample_output: str | None = None
    enabled: bool | None = None


class TaskItemIn(BaseModel):
    source_profile_id: str = ""
    kind: str = "catalog"
    enabled: bool = True
    title: str = ""
    command_override: str = ""
    sort_order: int | None = None
    bindings: list[dict[str, str]] = Field(default_factory=list)


class TaskCreateIn(BaseModel):
    source: str = "managed"
    ne_id: str
    ne_name: str = ""
    ne_ip: str = ""
    vendor: str = ""
    device_type: str = ""
    note: str = ""
    interval_sec: int = 300
    retention_batches: int = 30
    items: list[TaskItemIn] = Field(default_factory=list)


class TaskPatchIn(BaseModel):
    note: str | None = None
    interval_sec: int | None = None
    retention_batches: int | None = None
    status: str | None = None
    items: list[TaskItemIn] | None = None


class PreviewIn(BaseModel):
    vendor: str = ""
    device_type: str = ""
    items: list[TaskItemIn] = Field(default_factory=list)


@router.get("/profiles")
def api_list_profiles(
    vendor: str = "",
    device_type: str = "",
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    vkey = resolve_vendor_key(vendor, device_type) if (vendor or device_type) else ""
    return {"items": svc.list_profiles_public(db, vendor_key=vkey)}


@router.patch("/profiles/{profile_id}")
def api_patch_profile(
    profile_id: str,
    body: ProfileOverrideIn,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return svc.upsert_profile_override(db, profile_id, body.model_dump(exclude_unset=True))


@router.post("/tasks/preview-items")
def api_preview_items(
    body: PreviewIn,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    items = [i.model_dump() for i in body.items]
    return {
        "items": svc.preview_items(
            db, vendor=body.vendor, device_type=body.device_type, items=items
        )
    }


@router.get("/tasks")
def api_list_tasks(db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"items": svc.list_tasks(db)}


@router.post("/tasks")
def api_create_task(body: TaskCreateIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    return svc.create_task(db, body.model_dump())


@router.get("/tasks/{task_id}")
def api_get_task(task_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return svc.get_task(db, task_id)


@router.patch("/tasks/{task_id}")
def api_patch_task(
    task_id: str, body: TaskPatchIn, db: Session = Depends(get_db)
) -> dict[str, Any]:
    return svc.update_task(db, task_id, body.model_dump(exclude_unset=True))


@router.delete("/tasks/{task_id}")
def api_delete_task(task_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    svc.delete_task(db, task_id)
    return {"ok": True}


@router.post("/tasks/{task_id}/collect")
def api_collect_now(
    task_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    task = db.get(BizStateTask, task_id)
    if not task:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="task not found")
    if bool(task.collect_running):
        return {"ok": True, "started": False, "reason": "already_collecting", "task_id": task_id}
    # Allow one-shot from draft/paused
    task.last_collect_ended_at = None
    db.commit()
    background_tasks.add_task(dispatch_collect, task_id)
    return {"ok": True, "started": True, "task_id": task_id}


@router.get("/tasks/{task_id}/batches")
def api_list_batches(
    task_id: str, limit: int = 50, db: Session = Depends(get_db)
) -> dict[str, Any]:
    return {"items": svc.list_batches(db, task_id, limit=limit)}


@router.get("/batches/{batch_id}")
def api_get_batch(batch_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return svc.get_batch(db, batch_id)


@router.get("/batches/{batch_id}/export")
def api_export_batch(batch_id: str, db: Session = Depends(get_db)) -> StreamingResponse:
    data = svc.export_batch_zip(db, batch_id)
    return StreamingResponse(
        iter([data]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="biz_state_{batch_id}.zip"'},
    )
