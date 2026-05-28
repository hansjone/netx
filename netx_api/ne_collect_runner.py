from __future__ import annotations

import logging
import re
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime
from pathlib import Path
from typing import Any

from netmiko import ConnectHandler

from .collection_job_state import finalize_collection_job, sync_job_progress
from .config import settings
from .db import SessionLocal
from .models import ManagedNE, NeCollectionJob, NeCollectionRun
from .ne_collection_paths import clear_run_output_files, run_output_dir
from .ne_crypto import CredentialCryptoError
from .ne_netmiko import normalize_netmiko_device_type
from .ne_service import get_device_credentials

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
    device_type = normalize_netmiko_device_type(creds["device_type"], creds["protocol"])
    per_cmd = int(settings.ne_collect_read_timeout_sec or 120)
    dev: dict[str, Any] = {
        "device_type": device_type,
        "host": creds["ip_address"],
        "username": creds["username"],
        "password": creds["password"],
        "port": int(creds["port"] or 22),
        "conn_timeout": int(settings.ne_connect_timeout_sec or 30),
        "auth_timeout": int(settings.ne_connect_timeout_sec or 30),
        "banner_timeout": int(settings.ne_connect_timeout_sec or 30),
        "session_timeout": per_cmd * max(1, len(commands)) + 60,
    }
    secret = str(creds.get("enable_secret") or "").strip()
    if secret:
        dev["secret"] = secret
    chunks: list[str] = []
    with ConnectHandler(**dev) as conn:
        prompt = str(conn.find_prompt() or "")
        for command in commands:
            ts = datetime.now().isoformat(timespec="seconds")
            chunks.append(f'>>> [{ts}] {{"String":"{command}", "Match":"{prompt}", "Timeout":0}}\n')
            out = conn.send_command(command_string=command, read_timeout=per_cmd)
            chunks.append(str(out or ""))
            chunks.append("\n")
    return "".join(chunks)


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


def _collection_aborted(job_id: str, run_id: str) -> bool:
    db = SessionLocal()
    try:
        job = db.get(NeCollectionJob, job_id)
        run = db.get(NeCollectionRun, run_id)
        if not job or not run:
            return True
        if str(job.status or "") == "paused":
            if str(run.status or "") == "pending":
                run.status = "cancelled"
                run.message = "paused"
                run.ended_at = datetime.now()
                db.commit()
            return True
        if str(run.status or "") in ("cancelled", "success", "fail"):
            return True
        return False
    finally:
        db.close()


def _run_single(job_id: str, run_id: str, commands: list[str]) -> None:
    if _collection_aborted(job_id, run_id):
        db = SessionLocal()
        try:
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
        started_at = datetime.now()
        _update_run(run_id, status="running", message="collecting", started_at=started_at)
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


def schedule_collection_runs(job_id: str, run_ids: list[str], commands: list[str]) -> int:
    pool = _executor_pool()
    submitted = 0
    for run_id in run_ids:
        pool.submit(_run_single, job_id, run_id, list(commands))
        submitted += 1
    return submitted
