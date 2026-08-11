"""HTTP routes for network-management LLDP link collect."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .db import get_db
from .lldp_collect_schemas import LldpCollectPolicyUpdate, LldpCollectStartBody
from .lldp_collect_service import (
    get_dashboard,
    get_job_detail,
    get_policy,
    list_jobs,
    pause_collect,
    resume_collect,
    start_collect_from_body,
    stop_collect,
    update_policy,
)

router = APIRouter(prefix="/v1/topology/lldp-collect", tags=["topology-lldp-collect"])


@router.get("/policy")
def api_get_policy(db: Session = Depends(get_db)) -> dict[str, Any]:
    return get_policy(db).model_dump()


@router.put("/policy")
def api_put_policy(
    body: LldpCollectPolicyUpdate, db: Session = Depends(get_db)
) -> dict[str, Any]:
    return update_policy(db, body).model_dump()


@router.get("/dashboard")
def api_dashboard(db: Session = Depends(get_db)) -> dict[str, Any]:
    return get_dashboard(db).model_dump()


@router.post("/start")
def api_start(
    body: LldpCollectStartBody | None = None, db: Session = Depends(get_db)
) -> dict[str, Any]:
    return start_collect_from_body(db, body)


@router.post("/jobs/{job_id}/pause")
def api_pause_job(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return pause_collect(db, job_id)


@router.post("/jobs/{job_id}/resume")
def api_resume_job(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return resume_collect(db, job_id)


@router.post("/jobs/{job_id}/stop")
def api_stop_job(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return stop_collect(db, job_id)


@router.get("/jobs")
def api_list_jobs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return list_jobs(db, page=page, page_size=page_size)


@router.get("/jobs/{job_id}")
def api_get_job(
    job_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return get_job_detail(db, job_id, page=page, page_size=page_size)
