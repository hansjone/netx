from __future__ import annotations

import logging
import re
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime
from pathlib import Path
from typing import Any

from .collection_job_state import finalize_collection_job, sync_job_progress
from .config import settings
from .db import SessionLocal
from .models import ManagedNE, NeCollectionJob, NeCollectionRun
from .ne_collection_paths import clear_run_output_files, run_output_dir
from .ne_crypto import CredentialCryptoError
from .ne_netmiko import send_show_command
from .ne_service import get_device_credentials
from .ne_session_factory import close_netmiko_connection, open_netmiko_connection

_log = logging.getLogger("netx.ne.collect")
_executor: ThreadPoolExecutor | None = None


def _executor_pool() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        workers = max(1, int(settings.ne_collect_max_workers or 5))
        _executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ne-collect")
    return _executor


def _format_run_error(exc: BaseException) -> str:
    head = f"{type(exc).__name__}: {exc}"
    tb = traceback.format_exc().strip()
    text = f"{head}\n{tb}" if tb else head
    return text[:1020]


def _safe_filename_part(text: str) -> str:
    s = re.sub(r'[<>:"/\\|?*]', "_", str(text or "").strip())
    return s[:80] or "device"


def _collect_on_device(creds: dict[str, Any], commands: list[str]) -> str:
    per_cmd = int(settings.ne_collect_read_timeout_sec or 120)
    session_timeout = per_cmd * max(1, len(commands)) + 60
    conn = open_netmiko_connection(creds, session_timeout=session_timeout)
    try:
        prompt = str(conn.find_prompt() or "")
        chunks: list[str] = []
        for command in commands:
            ts = datetime.now().isoformat(timespec="seconds")
            chunks.append(f'>>> [{ts}] {{"String":"{command}", "Match":"{prompt}", "Timeout":0}}\n')
            out = send_show_command(conn, command, read_timeout=per_cmd)
            chunks.append(str(out or ""))
            chunks.append("\n")
        return "".join(chunks)
    finally:
        close_netmiko_connection(conn)


def _collect_with_timeout(creds: dict[str, Any], commands: list[str]) -> str:
    per_cmd = int(settings.ne_collect_read_timeout_sec or 120)
    cap = int(settings.ne_collect_run_timeout_cap_sec or 600)
    budget = min(cap, per_cmd * max(1, len(commands)) + 90)
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_collect_on_device, creds, commands)
        try:
            return fut.result(timeout=budget)
        except FuturesTimeout as exc:
            raise TimeoutError(f"collection_timeout ({budget}s)") from exc


def _update_run(run_id: str, **fields: Any) -> None:
    db = SessionLocal()
    try:
        row = db.get(NeCollectionRun, run_id)
        if not row:
            return
        for key, val in fields.items():
            setattr(row, key, val)
        db.commit()
    finally:
        db.close()


def _job_is_paused(job_id: str) -> bool:
    db = SessionLocal()
    try:
        job = db.get(NeCollectionJob, job_id)
        return not job or str(job.status or "") == "paused"
    finally:
        db.close()


def _claim_run(job_id: str, run_id: str) -> bool:
    """Atomically move a pending run to running; retry while DB rows become visible."""
    for attempt in range(10):
        db = SessionLocal()
        try:
            run = db.get(NeCollectionRun, run_id)
            job = db.get(NeCollectionJob, job_id)
            if not run or not job:
                time.sleep(0.05 * (attempt + 1))
                continue
            job_status = str(job.status or "")
            run_status = str(run.status or "")
            if job_status == "paused":
                if run_status == "pending":
                    run.status = "cancelled"
                    run.message = "paused"
                    run.ended_at = datetime.now()
                    db.commit()
                return False
            if job_status != "running":
                return False
            if run_status == "running":
                return False
            if run_status != "pending":
                return False
            run.status = "running"
            run.message = "collecting"
            run.started_at = datetime.now()
            db.commit()
            return True
        finally:
            db.close()
        time.sleep(0.05 * (attempt + 1))
    return False


def _run_single(job_id: str, run_id: str, commands: list[str]) -> None:
    if not _claim_run(job_id, run_id):
        db = SessionLocal()
        try:
            run = db.get(NeCollectionRun, run_id)
            st = str(run.status or "") if run else ""
            if st in ("cancelled", "success", "fail", "running"):
                sync_job_progress(db, job_id)
                finalize_collection_job(db, job_id)
                return
            if st == "pending":
                _log.warning("collection claim failed job=%s run=%s", job_id, run_id)
                _update_run(
                    run_id,
                    status="fail",
                    message="collection_claim_failed",
                    ended_at=datetime.now(),
                )
            sync_job_progress(db, job_id)
            finalize_collection_job(db, job_id)
        finally:
            db.close()
        return
    db = SessionLocal()
    try:
        run = db.get(NeCollectionRun, run_id)
        if not run:
            return
        ne = db.get(ManagedNE, str(run.ne_id))
        if not ne:
            _update_run(run_id, status="fail", message="managed_ne_not_found", ended_at=datetime.now())
            return
        if _job_is_paused(job_id):
            _update_run(run_id, status="cancelled", message="paused", ended_at=datetime.now())
            return
        try:
            creds = get_device_credentials(ne)
            output = _collect_with_timeout(creds, commands)
            finished_at = datetime.now()
            name_part = _safe_filename_part(str(run.ne_name or creds.get("name") or "ne"))
            ip_part = _safe_filename_part(str(run.ne_ip or creds.get("ip_address") or "ip"))
            ts = finished_at.strftime("%Y%m%d%H%M%S")
            rel_dir = Path(job_id) / run_id
            out_dir = run_output_dir(job_id, run_id)
            clear_run_output_files(job_id, run_id)
            out_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{name_part}-{ip_part}-{ts}.txt"
            full_path = out_dir / filename
            full_path.write_text(output, encoding="utf-8", errors="replace")
            rel_path = str(rel_dir / filename).replace("\\", "/")
            _update_run(
                run_id,
                status="success",
                message="collected",
                output_rel_path=rel_path,
                ended_at=finished_at,
            )
        except CredentialCryptoError as exc:
            _update_run(run_id, status="fail", message=str(exc)[:1020], ended_at=datetime.now())
        except Exception as exc:
            _log.exception("collection failed run=%s", run_id)
            _update_run(run_id, status="fail", message=_format_run_error(exc), ended_at=datetime.now())
    finally:
        db.close()
        db2 = SessionLocal()
        try:
            sync_job_progress(db2, job_id)
            finalize_collection_job(db2, job_id)
        finally:
            db2.close()


def _run_collect_safe(job_id: str, run_id: str, commands: list[str]) -> None:
    try:
        _run_single(job_id, run_id, commands)
    except Exception:
        _log.exception("collection task crashed job=%s run=%s", job_id, run_id)
        _update_run(
            run_id,
            status="fail",
            message="collection_worker_crashed",
            ended_at=datetime.now(),
        )
        db = SessionLocal()
        try:
            sync_job_progress(db, job_id)
            finalize_collection_job(db, job_id)
        finally:
            db.close()


def schedule_collection_runs(job_id: str, run_ids: list[str], commands: list[str]) -> int:
    pool = _executor_pool()
    cmd_list = list(commands)
    submitted = 0
    for run_id in run_ids:
        pool.submit(_run_collect_safe, job_id, str(run_id), cmd_list)
        submitted += 1
    if submitted:
        _log.info("scheduled collection job=%s runs=%s", job_id, submitted)
    return submitted


def dispatch_collection_runs(job_id: str, run_ids: list[str], commands: list[str]) -> int:
    """Entry point for FastAPI BackgroundTasks after the request transaction commits."""
    return schedule_collection_runs(job_id, run_ids, commands)
