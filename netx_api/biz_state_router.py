"""HTTP API for business state monitoring (/v1/biz-state)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query
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
    purpose: str = ""
    status: str = "draft"
    interval_sec: int = 3600
    retention_days: int = 30
    daily_keep_enabled: bool = False
    daily_keep_count: int = 10
    items: list[TaskItemIn] = Field(default_factory=list)


class TaskPatchIn(BaseModel):
    note: str | None = None
    purpose: str | None = None
    interval_sec: int | None = None
    retention_days: int | None = None
    daily_keep_enabled: bool | None = None
    daily_keep_count: int | None = None
    status: str | None = None
    items: list[TaskItemIn] | None = None


class BatchBaselineIn(BaseModel):
    marked: bool = True


class BatchAliasIn(BaseModel):
    alias: str = ""


class BatchBulkDeleteIn(BaseModel):
    batch_ids: list[str] = Field(default_factory=list)


class PreviewIn(BaseModel):
    vendor: str = ""
    device_type: str = ""
    items: list[TaskItemIn] = Field(default_factory=list)


class DiscoverIn(BaseModel):
    source: str = "managed"
    ne_id: str = ""
    task_id: str = ""
    discover_profile_id: str = ""
    collect_profile_id: str = ""
    placeholder: str = ""
    force_refresh: bool = False


class BindingsIn(BaseModel):
    bindings: list[dict[str, str]] = Field(default_factory=list)


@router.get("/profiles")
def api_list_profiles(
    vendor: str = "",
    device_type: str = "",
    kind: str = "",
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    vkey = resolve_vendor_key(vendor, device_type) if (vendor or device_type) else ""
    items = svc.list_profiles_public(db, vendor_key=vkey)
    if kind:
        items = [p for p in items if str(p.get("kind") or "") == kind]
    return {"items": items}


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


@router.post("/discover")
def api_discover(body: DiscoverIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    from .biz_state.discover import discover_params

    return discover_params(
        db,
        source=body.source,
        ne_id=body.ne_id,
        task_id=body.task_id,
        discover_profile_id=body.discover_profile_id,
        collect_profile_id=body.collect_profile_id,
        placeholder=body.placeholder,
        force_refresh=bool(body.force_refresh),
    )


@router.put("/tasks/{task_id}/items/{item_id}/bindings")
def api_set_bindings(
    task_id: str,
    item_id: str,
    body: BindingsIn,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return svc.set_item_bindings(db, task_id, item_id, list(body.bindings or []))


@router.get("/tasks")
def api_list_tasks(purpose: str = "", db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"items": svc.list_tasks(db, purpose=purpose or None)}


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


@router.post("/tasks/{task_id}/pause")
def api_pause_task(task_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Pause periodic schedule; manual collect remains allowed."""
    return svc.set_task_status(db, task_id, "paused")


@router.post("/tasks/{task_id}/start")
def api_start_task(task_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Enable periodic schedule (requires bindings for non–cutover-HF tasks)."""
    return svc.set_task_status(db, task_id, "running")


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
    # Allow one-shot even when schedule is enabled (idle only).
    task.last_collect_ended_at = None
    db.commit()
    tid = task_id
    background_tasks.add_task(lambda: dispatch_collect(tid, manual=True))
    return {"ok": True, "started": True, "task_id": task_id}


@router.get("/tasks/{task_id}/batches")
def api_list_batches(
    task_id: str, limit: int = 50, db: Session = Depends(get_db)
) -> dict[str, Any]:
    return {"items": svc.list_batches(db, task_id, limit=limit)}


@router.post("/tasks/{task_id}/purge")
def api_purge_task(task_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Apply retention policy now (age + optional daily keep). Protected batches skipped."""
    return svc.run_purge_for_task(db, task_id)


@router.post("/batches/bulk-delete")
def api_bulk_delete_batches(
    body: BatchBulkDeleteIn, db: Session = Depends(get_db)
) -> dict[str, Any]:
    return svc.delete_batches_bulk(db, list(body.batch_ids or []))


@router.post("/batches/{batch_id}/baseline")
def api_set_batch_baseline(
    batch_id: str, body: BatchBaselineIn, db: Session = Depends(get_db)
) -> dict[str, Any]:
    return svc.set_batch_baseline(db, batch_id, marked=bool(body.marked))


@router.patch("/batches/{batch_id}/alias")
def api_set_batch_alias(
    batch_id: str, body: BatchAliasIn, db: Session = Depends(get_db)
) -> dict[str, Any]:
    return svc.set_batch_alias(db, batch_id, alias=str(body.alias or ""))


@router.delete("/batches/{batch_id}")
def api_delete_batch(batch_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return svc.delete_batch(db, batch_id)


@router.get("/batches/{batch_id}")
def api_get_batch(batch_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return svc.get_batch(db, batch_id)


@router.get("/batches/{batch_id}/metrics/{metric_id}")
def api_list_batch_metric_rows(
    batch_id: str,
    metric_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    kw: str = Query(""),
    column: str = Query(""),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return svc.list_batch_metric_rows(
        db,
        batch_id,
        metric_id,
        page=page,
        page_size=page_size,
        kw=kw,
        column=column,
    )


@router.get("/batches/{batch_id}/commands/{command_id}")
def api_get_batch_command(
    batch_id: str, command_id: str, db: Session = Depends(get_db)
) -> dict[str, Any]:
    return svc.get_batch_command(db, batch_id, command_id)


@router.get("/batches/{batch_id}/export")
def api_export_batch(batch_id: str, db: Session = Depends(get_db)) -> StreamingResponse:
    data = svc.export_batch_zip(db, batch_id)
    return StreamingResponse(
        iter([data]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="biz_state_{batch_id}.zip"'},
    )


# ---- Phase2: templates / port maps / compare jobs ----

from .biz_state import compare_service as cmp_svc  # noqa: E402


class FieldRuleIn(BaseModel):
    field: str
    compare: str = ""
    normalize: str = ""
    ignore: bool = False
    tolerance: float | None = None


class RowFilterIn(BaseModel):
    """Leaf or nested (any/all) row filter — kept open for nested dicts."""

    model_config = {"extra": "allow"}

    field: str | None = None
    op: str | None = None
    value: Any = None
    any: list[dict[str, Any]] | None = None
    all: list[dict[str, Any]] | None = None


class TemplateMetricIn(BaseModel):
    metric_id: str
    # Split sheets share metric_id; identity is sheet_id (export/import must keep both).
    sheet_id: str = ""
    title: str = ""
    key_fields: list[str] = Field(default_factory=list)
    iface_fields: list[str] = Field(default_factory=list)
    compare_fields: list[str] = Field(default_factory=list)
    display_fields: list[str] = Field(default_factory=list)
    row_filters: list[dict[str, Any]] = Field(default_factory=list)
    field_rules: list[FieldRuleIn] = Field(default_factory=list)


class TemplateIn(BaseModel):
    name: str = ""
    note: str = ""
    # Preferred: multi-metric sheets
    metrics: list[TemplateMetricIn] | None = None
    # Template-owned interface type aliases (GE→gei, …)
    iface_normalize_rules: list[dict[str, str]] | None = None
    # Legacy single-metric fields (still accepted)
    metric_id: str = "lldp_neighbor"
    key_fields: list[str] = Field(default_factory=list)
    iface_fields: list[str] = Field(default_factory=list)
    compare_fields: list[str] = Field(default_factory=list)
    ignore_fields: list[str] = Field(default_factory=list)
    display_fields: list[str] = Field(default_factory=list)
    row_filters: list[dict[str, Any]] = Field(default_factory=list)
    field_rules: list[FieldRuleIn] = Field(default_factory=list)


class TemplatePatchIn(BaseModel):
    name: str | None = None
    note: str | None = None
    metrics: list[TemplateMetricIn] | None = None
    iface_normalize_rules: list[dict[str, str]] | None = None
    metric_id: str | None = None
    key_fields: list[str] | None = None
    iface_fields: list[str] | None = None
    compare_fields: list[str] | None = None
    ignore_fields: list[str] | None = None
    display_fields: list[str] | None = None
    row_filters: list[dict[str, Any]] | None = None
    field_rules: list[FieldRuleIn] | None = None


class MappingRowIn(BaseModel):
    before_if: str
    after_if: str


class MappingIn(BaseModel):
    name: str = ""
    note: str = ""
    rows: list[MappingRowIn] = Field(default_factory=list)


class ValidateMappingIn(BaseModel):
    mapping_id: str
    before_batch_id: str
    after_batch_id: str
    template_id: str = ""


class CompareJobIn(BaseModel):
    name: str = ""
    template_id: str = ""
    mapping_id: str = ""
    before_task_id: str = ""
    after_task_id: str = ""
    before_batch_id: str = ""
    after_batch_id: str = ""
    mode: str = "manual"
    # Empty = all template sheets; non-empty = only these sheet_id values
    enabled_sheet_ids: list[str] = Field(default_factory=list)
    note: str = ""


@router.get("/compare/metrics")
def api_list_compare_metrics() -> dict[str, Any]:
    return {
        "items": cmp_svc.list_metric_schemas(),
        "row_filter_presets": cmp_svc.list_row_filter_presets(),
    }


@router.get("/compare/templates")
def api_list_templates(db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"items": cmp_svc.list_templates(db)}


@router.post("/compare/templates")
def api_create_template(body: TemplateIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    return cmp_svc.create_template(db, body.model_dump())


@router.patch("/compare/templates/{template_id}")
def api_patch_template(
    template_id: str, body: TemplatePatchIn, db: Session = Depends(get_db)
) -> dict[str, Any]:
    return cmp_svc.update_template(db, template_id, body.model_dump(exclude_unset=True))


@router.delete("/compare/templates/{template_id}")
def api_delete_template(template_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    cmp_svc.delete_template(db, template_id)
    return {"ok": True}


@router.get("/compare/mappings")
def api_list_mappings(db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"items": cmp_svc.list_mappings(db)}


@router.post("/compare/mappings")
def api_create_mapping(body: MappingIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    return cmp_svc.create_mapping(db, body.model_dump())


@router.patch("/compare/mappings/{mapping_id}")
def api_patch_mapping(
    mapping_id: str, body: MappingIn, db: Session = Depends(get_db)
) -> dict[str, Any]:
    return cmp_svc.update_mapping(db, mapping_id, body.model_dump(exclude_unset=True))


@router.delete("/compare/mappings/{mapping_id}")
def api_delete_mapping(mapping_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    cmp_svc.delete_mapping(db, mapping_id)
    return {"ok": True}


@router.post("/compare/mappings/validate")
def api_validate_mapping(body: ValidateMappingIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    return cmp_svc.validate_mapping(
        db,
        mapping_id=body.mapping_id,
        before_batch_id=body.before_batch_id,
        after_batch_id=body.after_batch_id,
        template_id=body.template_id,
    )


@router.get("/compare/jobs")
def api_list_jobs(db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"items": cmp_svc.list_jobs(db)}


@router.post("/compare/jobs")
def api_create_job(body: CompareJobIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    return cmp_svc.create_job(db, body.model_dump())


@router.patch("/compare/jobs/{job_id}")
def api_patch_job(job_id: str, body: CompareJobIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    return cmp_svc.update_job(db, job_id, body.model_dump(exclude_unset=True))


@router.delete("/compare/jobs/{job_id}")
def api_delete_job(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    cmp_svc.delete_job(db, job_id)
    return {"ok": True}


@router.post("/compare/jobs/{job_id}/run")
def api_run_job(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return cmp_svc.run_compare(db, job_id)


@router.get("/compare/jobs/{job_id}/runs")
def api_list_runs(job_id: str, limit: int = 20, db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"items": cmp_svc.list_runs(db, job_id, limit=limit)}


@router.get("/compare/runs/{run_id}")
def api_get_run(run_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return cmp_svc.get_run(db, run_id)


@router.delete("/compare/runs/{run_id}")
def api_delete_run(run_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return cmp_svc.delete_run(db, run_id)


@router.get("/compare/runs/{run_id}/diffs")
def api_list_run_diffs(
    run_id: str,
    metric_id: str = "",
    kind: str = "diff",
    kw: str = "",
    page: int = 1,
    page_size: int = 100,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return cmp_svc.list_run_diffs(
        db,
        run_id,
        metric_id=metric_id,
        kind=kind,
        kw=kw,
        page=page,
        page_size=page_size,
    )


@router.get("/compare/runs/{run_id}/export")
def api_export_run(run_id: str, db: Session = Depends(get_db)) -> StreamingResponse:
    data = cmp_svc.export_run_zip(db, run_id)
    return StreamingResponse(
        iter([data]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="biz_compare_{run_id}.zip"'},
    )
