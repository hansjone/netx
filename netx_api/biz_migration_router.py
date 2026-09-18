"""HTTP API for cutover migration monitor (/v1/biz-migration)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .biz_migration import service as svc
from .biz_migration import monitor_templates as mon_tpl
from .db import get_db

router = APIRouter(prefix="/v1/biz-migration", tags=["biz-migration"])


class ProjectIn(BaseModel):
    name: str
    old_task_id: str
    new_task_id: str
    old_baseline_batch_id: str = ""
    new_baseline_batch_id: str = ""
    mapping_id: str = ""
    status: str = "draft"
    note: str = ""


class ProjectPatchIn(BaseModel):
    name: str | None = None
    old_baseline_batch_id: str | None = None
    new_baseline_batch_id: str | None = None
    mapping_id: str | None = None
    status: str | None = None
    note: str | None = None


class BatchIn(BaseModel):
    batch_label: str = "batch"
    expect_set: dict[str, Any] = Field(default_factory=dict)
    status: str = "pending"
    note: str = ""


class MonitorTemplateIn(BaseModel):
    name: str = ""
    compare_template_id: str = ""
    collect_metric_ids: list[str] = Field(default_factory=list)
    defaults: dict[str, Any] = Field(default_factory=dict)
    sheet_overrides: list[dict[str, Any]] = Field(default_factory=list)
    note: str = ""


class MonitorTemplatePatchIn(BaseModel):
    name: str | None = None
    compare_template_id: str | None = None
    collect_metric_ids: list[str] | None = None
    defaults: dict[str, Any] | None = None
    sheet_overrides: list[dict[str, Any]] | None = None
    note: str | None = None


class BatchPatchIn(BaseModel):
    batch_label: str | None = None
    expect_set: dict[str, Any] | None = None
    status: str | None = None
    note: str | None = None


class EvaluateIn(BaseModel):
    old_batch_id: str = ""
    new_batch_id: str = ""


@router.get("/projects")
def api_list_projects(db: Session = Depends(get_db)):
    return {"items": svc.list_projects(db)}


@router.post("/projects")
def api_create_project(body: ProjectIn, db: Session = Depends(get_db)):
    return svc.create_project(db, body.model_dump())


@router.get("/projects/{project_id}")
def api_get_project(project_id: str, db: Session = Depends(get_db)):
    return svc.get_project(db, project_id)


@router.patch("/projects/{project_id}")
def api_patch_project(project_id: str, body: ProjectPatchIn, db: Session = Depends(get_db)):
    return svc.patch_project(db, project_id, body.model_dump(exclude_unset=True))


@router.delete("/projects/{project_id}")
def api_delete_project(project_id: str, db: Session = Depends(get_db)):
    return svc.delete_project(db, project_id)


@router.get("/projects/{project_id}/baseline-ports")
def api_baseline_ports(project_id: str, db: Session = Depends(get_db)):
    """List old-baseline interface_brief ports for expect-set selection."""
    return svc.list_baseline_ports(db, project_id)


class EnsureHighfreqIn(BaseModel):
    interval_sec: int = 60
    retention_days: int = 7
    collect_now: bool = True


@router.post("/projects/{project_id}/ensure-port-highfreq")
def api_ensure_port_highfreq(
    project_id: str,
    body: EnsureHighfreqIn | None = None,
    db: Session = Depends(get_db),
):
    """Create/bind biz_state high-freq interface_brief tasks for old/new NEs."""
    payload = body.model_dump() if body else {}
    return svc.ensure_port_highfreq(
        db,
        project_id,
        interval_sec=int(payload.get("interval_sec") or 60),
        retention_days=int(payload.get("retention_days") or 7),
        collect_now=bool(payload.get("collect_now", True)),
    )


@router.post("/projects/{project_id}/collect-now")
def api_collect_now(project_id: str, db: Session = Depends(get_db)):
    """Trigger immediate collect on project's bound biz_state tasks."""
    return svc.collect_project_now(db, project_id)


@router.get("/projects/{project_id}/batches")
def api_list_batches(project_id: str, db: Session = Depends(get_db)):
    return {"items": svc.list_batches(db, project_id)}


@router.post("/projects/{project_id}/batches")
def api_create_batch(project_id: str, body: BatchIn, db: Session = Depends(get_db)):
    return svc.create_batch(db, project_id, body.model_dump())


@router.post("/batches/{batch_id}/finish")
def api_finish_batch(
    batch_id: str,
    mark_done: bool = False,
    db: Session = Depends(get_db),
):
    """本批完成：终验 + 小结 + 红单（允许带红继续）."""
    return svc.finish_batch(db, batch_id, mark_done=mark_done)


@router.get("/projects/{project_id}/red-tickets")
def api_list_red_tickets(
    project_id: str,
    status: str = "",
    limit: int = 200,
    db: Session = Depends(get_db),
):
    return svc.list_red_tickets(db, project_id, status=status, limit=limit)


class RedTicketPatchIn(BaseModel):
    note: str = ""


@router.post("/red-tickets/{ticket_id}/resolve")
def api_resolve_red_ticket(
    ticket_id: str,
    body: RedTicketPatchIn | None = None,
    db: Session = Depends(get_db),
):
    note = body.note if body else ""
    return svc.resolve_red_ticket(db, ticket_id, note=note)


@router.patch("/batches/{batch_id}")
def api_patch_batch(batch_id: str, body: BatchPatchIn, db: Session = Depends(get_db)):
    return svc.patch_batch(db, batch_id, body.model_dump(exclude_unset=True))


@router.post("/batches/{batch_id}/evaluate")
def api_evaluate(batch_id: str, body: EvaluateIn | None = None, db: Session = Depends(get_db)):
    payload = body.model_dump() if body else {}
    return svc.run_evaluate(
        db,
        batch_id=batch_id,
        old_batch_id=str(payload.get("old_batch_id") or ""),
        new_batch_id=str(payload.get("new_batch_id") or ""),
    )


@router.get("/batches/{batch_id}/runs")
def api_list_runs(batch_id: str, limit: int = 20, db: Session = Depends(get_db)):
    return {"items": svc.list_runs(db, batch_id, limit=limit)}


@router.get("/batches/{batch_id}/board")
def api_board(batch_id: str, run_id: str = "", db: Session = Depends(get_db)):
    return svc.board(db, batch_id, run_id=run_id)


@router.get("/runs/{run_id}")
def api_get_run(run_id: str, db: Session = Depends(get_db)):
    return svc.get_run(db, run_id)


@router.get("/runs/{run_id}/diffs")
def api_list_diffs(
    run_id: str,
    metric_id: str = "",
    verdict: str = "",
    color: str = "",
    kw: str = "",
    offset: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    return svc.list_run_diffs(
        db,
        run_id,
        metric_id=metric_id,
        verdict=verdict,
        color=color,
        kw=kw,
        offset=offset,
        limit=limit,
    )


@router.get("/monitor-templates")
def api_list_monitor_templates(db: Session = Depends(get_db)):
    return {"items": mon_tpl.list_monitor_templates(db)}


@router.post("/monitor-templates")
def api_create_monitor_template(body: MonitorTemplateIn, db: Session = Depends(get_db)):
    return mon_tpl.create_monitor_template(db, body.model_dump())


@router.get("/monitor-templates/{template_id}")
def api_get_monitor_template(template_id: str, db: Session = Depends(get_db)):
    return mon_tpl.get_monitor_template(db, template_id)


@router.patch("/monitor-templates/{template_id}")
def api_patch_monitor_template(
    template_id: str,
    body: MonitorTemplatePatchIn,
    db: Session = Depends(get_db),
):
    return mon_tpl.update_monitor_template(
        db, template_id, body.model_dump(exclude_unset=True)
    )


@router.delete("/monitor-templates/{template_id}")
def api_delete_monitor_template(template_id: str, db: Session = Depends(get_db)):
    mon_tpl.delete_monitor_template(db, template_id)
    return {"ok": True}
