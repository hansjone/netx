from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from .collection_service import (
    build_collection_job_zip,
    create_collection,
    delete_collection_job,
    get_collection_dashboard,
    get_collection_job,
    list_collection_jobs,
    list_collection_runs,
    list_eligible_ne,
    pause_collection_job,
    resolve_run_output_path,
    restart_collection_job,
    start_collection_job,
    retry_failed_collection_job,
)
from .collection_schemas import CollectionJobCreate
from .db import get_db
from .models import NeCollectionRun
from .ne_collect_runner import dispatch_collection_runs

router = APIRouter(prefix="/v1/ne-collections", tags=["ne-collections"])


@router.get("/eligible-ne")
def api_eligible_ne(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=200, ge=1, le=500),
    keyword: str = Query(default=""),
    db: Session = Depends(get_db),
):
    return list_eligible_ne(db, page=page, page_size=page_size, keyword=keyword)


@router.get("/dashboard")
def api_collection_dashboard(db: Session = Depends(get_db)):
    return get_collection_dashboard(db).model_dump()


@router.post("")
def api_create_collection(body: CollectionJobCreate, db: Session = Depends(get_db)):
    return create_collection(db, body).model_dump()


@router.get("")
def api_list_collections(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return list_collection_jobs(db, page=page, page_size=page_size)


@router.get("/runs/{run_id}/download")
def api_download_run_output(run_id: str, db: Session = Depends(get_db)):
    run = db.get(NeCollectionRun, run_id)
    if not run:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="collection_run_not_found")
    path = resolve_run_output_path(str(run.output_rel_path or ""))
    filename = path.name
    return FileResponse(path, media_type="text/plain; charset=utf-8", filename=filename)


@router.get("/{job_id}/download")
def api_download_collection_job(job_id: str, db: Session = Depends(get_db)):
    filename, payload = build_collection_job_zip(db, job_id)
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"content-disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{job_id}/runs")
def api_list_collection_runs(
    job_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    status: str = Query(default=""),
    keyword: str = Query(default=""),
    db: Session = Depends(get_db),
):
    return list_collection_runs(db, job_id, page=page, page_size=page_size, status=status, keyword=keyword)


@router.get("/{job_id}")
def api_get_collection(job_id: str, db: Session = Depends(get_db)):
    return get_collection_job(db, job_id)


@router.post("/{job_id}/pause")
def api_pause_collection(job_id: str, db: Session = Depends(get_db)):
    return pause_collection_job(db, job_id).model_dump()


@router.post("/{job_id}/start")
def api_start_collection(
    job_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    out, payload = start_collection_job(db, job_id)
    background_tasks.add_task(
        dispatch_collection_runs,
        payload["job_id"],
        payload["run_ids"],
        payload["commands"],
    )
    return out.model_dump()


@router.post("/{job_id}/restart")
def api_restart_collection(
    job_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Alias of /start for backward compatibility."""
    out, payload = restart_collection_job(db, job_id)
    background_tasks.add_task(
        dispatch_collection_runs,
        payload["job_id"],
        payload["run_ids"],
        payload["commands"],
    )
    return out.model_dump()


@router.post("/{job_id}/retry-failed")
def api_retry_failed_collection(
    job_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    out, payload = retry_failed_collection_job(db, job_id)
    background_tasks.add_task(
        dispatch_collection_runs,
        payload["job_id"],
        payload["run_ids"],
        payload["commands"],
    )
    return out.model_dump()


@router.delete("/{job_id}")
def api_delete_collection(job_id: str, db: Session = Depends(get_db)):
    return delete_collection_job(db, job_id)
