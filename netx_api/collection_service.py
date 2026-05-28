from __future__ import annotations

import io
import logging
import re
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .models import ManagedNE, NeCollectionJob, NeCollectionRun
from .collection_job_state import (
    finalize_collection_job,
    reconcile_stale_collection_job,
    sync_job_progress,
    _sync_job_counts,
)
from .collection_schemas import CollectionJobCreate, CollectionJobOut, CollectionRunOut
from .ne_collection_paths import clear_run_output_files, collection_data_root

_log = logging.getLogger("netx.collection")


class CollectionSchedulePayload(TypedDict):
    job_id: str
    run_ids: list[str]
    commands: list[str]


def _now() -> datetime:
    return datetime.now()


def _parse_commands(text: str) -> list[str]:
    lines = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return lines


def _output_counts_for_jobs(db: Session, job_ids: list[str]) -> dict[str, int]:
    if not job_ids:
        return {}
    rows = (
        db.query(NeCollectionRun.job_id, func.count())
        .filter(
            NeCollectionRun.job_id.in_(job_ids),
            NeCollectionRun.output_rel_path != "",
            NeCollectionRun.output_rel_path.isnot(None),
        )
        .group_by(NeCollectionRun.job_id)
        .all()
    )
    return {str(job_id): int(count) for job_id, count in rows}


def _output_count_for_job(db: Session, job_id: str) -> int:
    return int(
        db.query(func.count())
        .select_from(NeCollectionRun)
        .filter(
            NeCollectionRun.job_id == job_id,
            NeCollectionRun.output_rel_path != "",
            NeCollectionRun.output_rel_path.isnot(None),
        )
        .scalar()
        or 0
    )


def job_to_out(row: NeCollectionJob, *, output_count: int | None = None) -> CollectionJobOut:
    return CollectionJobOut(
        id=str(row.id),
        title=str(row.title or ""),
        commands=str(row.commands or ""),
        status=str(row.status or "pending"),
        ne_count=int(row.ne_count or 0),
        success_count=int(row.success_count or 0),
        fail_count=int(row.fail_count or 0),
        output_count=int(output_count if output_count is not None else 0),
        error_message=str(row.error_message or "")[:1000],
        created_at=row.created_at,
        started_at=row.started_at,
        ended_at=row.ended_at,
        last_run_at=row.last_run_at,
    )


def run_to_out(row: NeCollectionRun) -> CollectionRunOut:
    rel = str(row.output_rel_path or "").strip()
    return CollectionRunOut(
        id=str(row.id),
        job_id=str(row.job_id),
        ne_id=str(row.ne_id),
        ne_name=str(row.ne_name or ""),
        ne_ip=str(row.ne_ip or ""),
        status=str(row.status or "pending"),
        message=str(row.message or ""),
        output_rel_path=rel,
        has_output=bool(rel),
        started_at=row.started_at,
        ended_at=row.ended_at,
    )


def list_eligible_ne(db: Session, *, page: int = 1, page_size: int = 200) -> dict[str, Any]:
    stmt = db.query(ManagedNE).filter(ManagedNE.connect_status == "pass")
    total = int(stmt.count())
    rows = (
        stmt.order_by(ManagedNE.name.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = [
        {
            "id": str(x.id),
            "name": str(x.name or ""),
            "vendor": str(x.vendor or ""),
            "device_type": str(x.device_type or ""),
            "ip_address": str(x.ip_address or ""),
            "connect_status": str(x.connect_status or ""),
            "connect_tested_at": x.connect_tested_at.isoformat() if x.connect_tested_at else None,
        }
        for x in rows
    ]
    return {"total": total, "page": page, "page_size": page_size, "items": items}


def create_and_start_collection(
    db: Session, body: CollectionJobCreate
) -> tuple[CollectionJobOut, CollectionSchedulePayload]:
    commands = _parse_commands(body.commands)
    if not commands:
        raise HTTPException(status_code=400, detail="commands_empty")
    ne_ids = [str(x).strip() for x in body.ne_ids if str(x).strip()]
    if not ne_ids:
        raise HTTPException(status_code=400, detail="ne_ids_required")

    ne_rows: list[ManagedNE] = []
    missing: list[str] = []
    not_pass: list[str] = []
    for ne_id in ne_ids:
        row = db.get(ManagedNE, ne_id)
        if not row:
            missing.append(ne_id)
            continue
        if str(row.connect_status or "") != "pass":
            not_pass.append(ne_id)
            continue
        ne_rows.append(row)
    if missing:
        raise HTTPException(status_code=404, detail=f"managed_ne_not_found: {','.join(missing[:5])}")
    if not_pass:
        raise HTTPException(status_code=400, detail=f"ne_connect_not_pass: {','.join(not_pass[:5])}")
    if not ne_rows:
        raise HTTPException(status_code=400, detail="no_eligible_ne")

    now = _now()
    job = NeCollectionJob(
        title=str(body.title or "").strip() or f"collect-{now.strftime('%Y%m%d-%H%M%S')}",
        commands="\n".join(commands),
        status="running",
        ne_count=len(ne_rows),
        created_at=now,
        started_at=now,
        last_run_at=now,
    )
    db.add(job)
    db.flush()

    run_ids: list[str] = []
    for ne in ne_rows:
        run = NeCollectionRun(
            job_id=str(job.id),
            ne_id=str(ne.id),
            ne_name=str(ne.name or ne.ip_address or ""),
            ne_ip=str(ne.ip_address or ""),
            status="pending",
        )
        db.add(run)
        run_ids.append(str(run.id))
    db.commit()
    db.refresh(job)

    payload: CollectionSchedulePayload = {
        "job_id": str(job.id),
        "run_ids": run_ids,
        "commands": commands,
    }
    return job_to_out(job, output_count=0), payload


def list_collection_jobs(db: Session, *, page: int = 1, page_size: int = 20) -> dict[str, Any]:
    stmt = db.query(NeCollectionJob)
    total = int(stmt.count())
    rows = stmt.order_by(NeCollectionJob.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    for row in rows:
        job_id = str(row.id)
        if str(row.status or "") not in ("done", "failed", "paused"):
            reconcile_stale_collection_job(db, job_id)
            db.refresh(row)
            if str(row.status or "") == "running":
                sync_job_progress(db, job_id)
                db.refresh(row)
    job_ids = [str(x.id) for x in rows]
    output_counts = _output_counts_for_jobs(db, job_ids)
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [job_to_out(x, output_count=output_counts.get(str(x.id), 0)).model_dump() for x in rows],
    }


def get_collection_job(db: Session, job_id: str) -> dict[str, Any]:
    job = db.get(NeCollectionJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="collection_job_not_found")
    if str(job.status or "") not in ("done", "failed", "paused"):
        reconcile_stale_collection_job(db, job_id)
        db.refresh(job)
        if str(job.status or "") == "running":
            sync_job_progress(db, job_id)
            db.refresh(job)
    return {
        "job": job_to_out(job, output_count=_output_count_for_job(db, job_id)).model_dump(),
    }


def list_collection_runs(
    db: Session,
    job_id: str,
    *,
    page: int = 1,
    page_size: int = 20,
    status: str = "",
    keyword: str = "",
) -> dict[str, Any]:
    job = db.get(NeCollectionJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="collection_job_not_found")
    stmt = db.query(NeCollectionRun).filter(NeCollectionRun.job_id == job_id)
    st = str(status or "").strip()
    if st:
        stmt = stmt.filter(NeCollectionRun.status == st)
    kw = str(keyword or "").strip()
    if kw:
        like = f"%{kw}%"
        stmt = stmt.filter(or_(NeCollectionRun.ne_name.ilike(like), NeCollectionRun.ne_ip.ilike(like)))
    total = int(stmt.count())
    rows = (
        stmt.order_by(NeCollectionRun.ne_name.asc(), NeCollectionRun.ne_ip.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [run_to_out(x).model_dump() for x in rows],
    }


def _active_runs(runs: list[NeCollectionRun]) -> bool:
    return any(str(r.status or "") in ("pending", "running") for r in runs)


def pause_collection_job(db: Session, job_id: str) -> CollectionJobOut:
    job = db.get(NeCollectionJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="collection_job_not_found")
    if str(job.status or "") != "running":
        raise HTTPException(status_code=400, detail="collection_job_not_running")
    now = _now()
    runs = db.query(NeCollectionRun).filter(NeCollectionRun.job_id == job_id).all()
    for run in runs:
        if str(run.status or "") != "pending":
            continue
        run.status = "cancelled"
        run.message = "paused"
        run.ended_at = now
    job.status = "paused"
    _sync_job_counts(job, runs)
    if not any(str(r.status or "") not in {"success", "fail", "cancelled"} for r in runs):
        job.ended_at = now
    db.commit()
    db.refresh(job)
    finalize_collection_job(db, job_id)
    db.refresh(job)
    return job_to_out(job, output_count=_output_count_for_job(db, job_id))


def _reset_runs_for_retry(
    db: Session,
    job_id: str,
    runs: list[NeCollectionRun],
    *,
    only_statuses: frozenset[str],
) -> list[str]:
    retry_ids: list[str] = []
    for run in runs:
        st = str(run.status or "")
        if st in ("pending", "running") or st not in only_statuses:
            continue
        clear_run_output_files(job_id, str(run.id))
        run.status = "pending"
        run.message = ""
        run.output_rel_path = ""
        run.started_at = None
        run.ended_at = None
        retry_ids.append(str(run.id))
    return retry_ids


def _start_job_retry(
    db: Session,
    job: NeCollectionJob,
    job_id: str,
    retry_ids: list[str],
    commands: list[str],
    *,
    reset_all_counts: bool,
) -> tuple[CollectionJobOut, CollectionSchedulePayload]:
    if not retry_ids:
        raise HTTPException(status_code=400, detail="collection_nothing_to_retry")
    now = _now()
    job.status = "running"
    job.ended_at = None
    job.error_message = ""
    if reset_all_counts:
        job.success_count = 0
        job.fail_count = 0
    else:
        runs = db.query(NeCollectionRun).filter(NeCollectionRun.job_id == job_id).all()
        _sync_job_counts(job, runs)
    job.started_at = now
    job.last_run_at = now
    db.commit()
    db.refresh(job)
    payload: CollectionSchedulePayload = {
        "job_id": job_id,
        "run_ids": retry_ids,
        "commands": commands,
    }
    return job_to_out(job, output_count=_output_count_for_job(db, job_id)), payload


def restart_collection_job(db: Session, job_id: str) -> tuple[CollectionJobOut, CollectionSchedulePayload]:
    job = db.get(NeCollectionJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="collection_job_not_found")
    runs = db.query(NeCollectionRun).filter(NeCollectionRun.job_id == job_id).all()
    if not runs:
        raise HTTPException(status_code=400, detail="collection_no_runs")
    if str(job.status or "") == "running" or _active_runs(runs):
        raise HTTPException(status_code=400, detail="collection_job_running")
    commands = _parse_commands(str(job.commands or ""))
    if not commands:
        raise HTTPException(status_code=400, detail="commands_empty")
    retry_ids = _reset_runs_for_retry(
        db,
        job_id,
        runs,
        only_statuses=frozenset({"success", "fail", "cancelled"}),
    )
    return _start_job_retry(db, job, job_id, retry_ids, commands, reset_all_counts=True)


def retry_failed_collection_job(
    db: Session, job_id: str
) -> tuple[CollectionJobOut, CollectionSchedulePayload]:
    job = db.get(NeCollectionJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="collection_job_not_found")
    runs = db.query(NeCollectionRun).filter(NeCollectionRun.job_id == job_id).all()
    if not runs:
        raise HTTPException(status_code=400, detail="collection_no_runs")
    if str(job.status or "") == "running" or _active_runs(runs):
        raise HTTPException(status_code=400, detail="collection_job_running")
    commands = _parse_commands(str(job.commands or ""))
    if not commands:
        raise HTTPException(status_code=400, detail="commands_empty")
    retry_ids = _reset_runs_for_retry(db, job_id, runs, only_statuses=frozenset({"fail"}))
    return _start_job_retry(db, job, job_id, retry_ids, commands, reset_all_counts=False)


def delete_collection_job(db: Session, job_id: str) -> dict[str, bool]:
    job = db.get(NeCollectionJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="collection_job_not_found")
    runs = db.query(NeCollectionRun).filter(NeCollectionRun.job_id == job_id).all()
    if str(job.status or "") == "running" or _active_runs(runs):
        raise HTTPException(status_code=400, detail="collection_job_running")
    db.query(NeCollectionRun).filter(NeCollectionRun.job_id == job_id).delete()
    db.delete(job)
    db.commit()
    job_dir = (collection_data_root() / job_id).resolve()
    root = collection_data_root().resolve()
    if str(job_dir).startswith(str(root)) and job_dir.is_dir():
        shutil.rmtree(job_dir, ignore_errors=True)
    return {"ok": True}


def _safe_archive_part(text: str, fallback: str = "device") -> str:
    s = re.sub(r'[<>:"/\\|?*]', "_", str(text or "").strip())
    return (s[:80] or fallback).strip("._") or fallback


def build_collection_job_zip(db: Session, job_id: str) -> tuple[str, bytes]:
    job = db.get(NeCollectionJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="collection_job_not_found")
    runs = (
        db.query(NeCollectionRun)
        .filter(NeCollectionRun.job_id == job_id)
        .order_by(NeCollectionRun.ne_name.asc())
        .all()
    )
    files: list[tuple[str, Path]] = []
    used_names: set[str] = set()
    for run in runs:
        rel = str(run.output_rel_path or "").strip()
        if not rel:
            continue
        path = resolve_run_output_path(rel)
        arcname = path.name
        if arcname in used_names:
            arcname = f"{str(run.id)[:8]}_{path.name}"
        used_names.add(arcname)
        files.append((arcname, path))
    if not files:
        raise HTTPException(status_code=404, detail="collection_outputs_not_found")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for arcname, path in files:
            zf.write(path, arcname=arcname)
    title = _safe_archive_part(str(job.title or "collect"), "collect")
    zip_name = f"{title}_{job_id[:8]}.zip"
    return zip_name, buf.getvalue()


def resolve_run_output_path(rel_path: str) -> Path:
    rel = str(rel_path or "").strip().replace("\\", "/")
    if not rel or ".." in rel.split("/"):
        raise HTTPException(status_code=400, detail="invalid_output_path")
    full = (collection_data_root() / rel).resolve()
    root = collection_data_root()
    if not str(full).startswith(str(root)):
        raise HTTPException(status_code=400, detail="invalid_output_path")
    if not full.is_file():
        raise HTTPException(status_code=404, detail="output_file_not_found")
    return full
