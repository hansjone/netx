"""Config sync worker: claim tasks, collect vendor configs, store zlib snapshots."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime
from threading import Lock
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from .cli_resolve import resolve_cli_target
from .config import settings
from .config_sync_codec import compress_text
from .config_sync_commands import command_list, commands_for_vendor
from .config_sync_service import finalize_cycle, sync_cycle_progress
from .db import SessionLocal
from .models import ConfigSyncCycle, ConfigSyncPolicy, ConfigSyncTask, NeConfigHistory, NeConfigSnapshot
from .ne_session_factory import close_netmiko_connection, open_netmiko_connection

_log = logging.getLogger("netx.config_sync.runner")
_pools: dict[str, ThreadPoolExecutor] = {}
_pools_lock = Lock()


def _utcnow() -> datetime:
    return datetime.utcnow()


def _format_error(exc: BaseException) -> str:
    head = f"{type(exc).__name__}: {exc}"
    return head[:1020]


def _pool_for_cycle(cycle_id: str, concurrency: int) -> ThreadPoolExecutor:
    with _pools_lock:
        pool = _pools.get(cycle_id)
        if pool is None:
            workers = max(1, min(30, int(concurrency or 5)))
            pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"cfg-sync-{cycle_id[:8]}")
            _pools[cycle_id] = pool
        return pool


def _release_pool(cycle_id: str) -> None:
    with _pools_lock:
        pool = _pools.pop(cycle_id, None)
    if pool is not None:
        try:
            pool.shutdown(wait=False, cancel_futures=False)
        except TypeError:
            pool.shutdown(wait=False)
        except Exception:
            _log.exception("config_sync pool shutdown failed cycle=%s", cycle_id)


def _update_task(task_id: str, **fields: Any) -> None:
    db = SessionLocal()
    try:
        row = db.get(ConfigSyncTask, task_id)
        if not row:
            return
        for key, val in fields.items():
            setattr(row, key, val)
        db.commit()
    finally:
        db.close()


def _claim_task(cycle_id: str, task_id: str) -> bool:
    for attempt in range(10):
        db = SessionLocal()
        try:
            task = db.get(ConfigSyncTask, task_id)
            cycle = db.get(ConfigSyncCycle, cycle_id)
            if not task or not cycle:
                time.sleep(0.05 * (attempt + 1))
                continue
            cycle_status = str(cycle.status or "")
            task_status = str(task.status or "")
            if cycle_status == "paused":
                return False
            if cycle_status != "running":
                return False
            if task_status == "running":
                return False
            if task_status != "pending":
                return False
            task.status = "running"
            task.message = "collecting"
            task.started_at = _utcnow()
            db.commit()
            return True
        finally:
            db.close()
        time.sleep(0.05 * (attempt + 1))
    return False


def _collect_commands(creds: dict[str, Any], commands: list[str]) -> list[str]:
    per_cmd = int(settings.ne_collect_read_timeout_sec or 120)
    session_timeout = per_cmd * max(1, len(commands)) + 60
    conn = open_netmiko_connection(creds, session_timeout=session_timeout)
    try:
        outputs: list[str] = []
        for command in commands:
            out = conn.send_command(command_string=command, read_timeout=per_cmd)
            outputs.append(str(out or ""))
        return outputs
    finally:
        close_netmiko_connection(conn)


def _collect_with_timeout(creds: dict[str, Any], commands: list[str]) -> list[str]:
    per_cmd = int(settings.ne_collect_read_timeout_sec or 120)
    cap = int(settings.ne_collect_run_timeout_cap_sec or 600)
    budget = min(cap, per_cmd * max(1, len(commands)) + 90)
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_collect_commands, creds, commands)
        try:
            return fut.result(timeout=budget)
        except FuturesTimeout as exc:
            raise TimeoutError(f"config_sync_timeout ({budget}s)") from exc


def _history_keep(db) -> int:
    pol = db.get(ConfigSyncPolicy, 1)
    if not pol:
        return 3
    return max(0, min(30, int(pol.history_keep if pol.history_keep is not None else 3)))


def _save_success_snapshot(
    *,
    source: str,
    target_id: str,
    vendor: str,
    device_type: str,
    ne_name: str,
    ne_ip: str,
    primary_text: str,
    alt_text: str | None,
    commands: list[str],
    cycle_id: str,
    task_id: str,
) -> None:
    primary_blob, primary_sha, plain_size, zlib_size = compress_text(primary_text)
    alt_blob = None
    alt_sha = ""
    plain_alt = 0
    zlib_alt = 0
    if alt_text is not None:
        alt_blob, alt_sha, plain_alt, zlib_alt = compress_text(alt_text)

    db = SessionLocal()
    try:
        existing = db.get(NeConfigSnapshot, {"source": source, "target_id": target_id})
        changed = True
        if existing is not None:
            changed = (
                str(existing.config_sha256 or "") != primary_sha
                or str(existing.config_alt_sha256 or "") != alt_sha
            )
            if changed:
                keep = _history_keep(db)
                if keep > 0:
                    # Archive previous successful snapshot before overwrite.
                    db.add(
                        NeConfigHistory(
                            id=uuid4().hex,
                            source=existing.source,
                            target_id=existing.target_id,
                            vendor=str(existing.vendor or ""),
                            device_type=str(existing.device_type or ""),
                            ne_name=str(existing.ne_name or ""),
                            ne_ip=str(existing.ne_ip or ""),
                            config_zlib=existing.config_zlib or b"",
                            config_alt_zlib=existing.config_alt_zlib,
                            config_sha256=str(existing.config_sha256 or ""),
                            config_alt_sha256=str(existing.config_alt_sha256 or ""),
                            plain_size=int(existing.plain_size or 0),
                            plain_alt_size=int(existing.plain_alt_size or 0),
                            zlib_size=int(existing.zlib_size or 0),
                            zlib_alt_size=int(existing.zlib_alt_size or 0),
                            commands_json=existing.commands_json if isinstance(existing.commands_json, list) else [],
                            collected_at=existing.collected_at or _utcnow(),
                            cycle_id=str(existing.last_cycle_id or ""),
                            task_id=str(existing.last_task_id or ""),
                        )
                    )
                    db.flush()
                    old_rows = (
                        db.query(NeConfigHistory)
                        .filter(NeConfigHistory.source == source, NeConfigHistory.target_id == target_id)
                        .order_by(NeConfigHistory.collected_at.desc())
                        .all()
                    )
                    for stale in old_rows[keep:]:
                        db.delete(stale)

            existing.vendor = vendor
            existing.device_type = device_type
            existing.ne_name = ne_name
            existing.ne_ip = ne_ip
            existing.config_zlib = primary_blob
            existing.config_alt_zlib = alt_blob
            existing.config_sha256 = primary_sha
            existing.config_alt_sha256 = alt_sha
            existing.plain_size = plain_size
            existing.plain_alt_size = plain_alt
            existing.zlib_size = zlib_size
            existing.zlib_alt_size = zlib_alt
            existing.commands_json = list(commands)
            existing.collected_at = _utcnow()
            existing.last_cycle_id = cycle_id
            existing.last_task_id = task_id
        else:
            db.add(
                NeConfigSnapshot(
                    source=source,
                    target_id=target_id,
                    vendor=vendor,
                    device_type=device_type,
                    ne_name=ne_name,
                    ne_ip=ne_ip,
                    config_zlib=primary_blob,
                    config_alt_zlib=alt_blob,
                    config_sha256=primary_sha,
                    config_alt_sha256=alt_sha,
                    plain_size=plain_size,
                    plain_alt_size=plain_alt,
                    zlib_size=zlib_size,
                    zlib_alt_size=zlib_alt,
                    commands_json=list(commands),
                    collected_at=_utcnow(),
                    last_cycle_id=cycle_id,
                    last_task_id=task_id,
                )
            )
        db.commit()
    finally:
        db.close()


def _run_single(cycle_id: str, task_id: str) -> None:
    if not _claim_task(cycle_id, task_id):
        db = SessionLocal()
        try:
            sync_cycle_progress(db, cycle_id)
            finalize_cycle(db, cycle_id)
        finally:
            db.close()
        return

    db = SessionLocal()
    try:
        task = db.get(ConfigSyncTask, task_id)
        if not task:
            return
        source = str(task.source or "").strip().lower()
        target_id = str(task.target_id or "").strip()
        vendor_hint = str(task.vendor or "")
        try:
            if source == "managed":
                creds, device = resolve_cli_target(db, managed_ne_id=target_id)
            elif source == "ume":
                creds, device = resolve_cli_target(db, ume_ne_id=target_id)
            else:
                _update_task(task_id, status="fail", message="invalid_source", ended_at=_utcnow())
                return
        except HTTPException as exc:
            detail = str(exc.detail) if exc.detail else "resolve_failed"
            _update_task(task_id, status="fail", message=detail[:1020], ended_at=_utcnow())
            return
        except Exception as exc:
            _update_task(task_id, status="fail", message=_format_error(exc), ended_at=_utcnow())
            return

        vendor = str(device.get("vendor") or vendor_hint or "")
        device_type = str(device.get("device_type") or "")
        ne_name = str(device.get("name") or task.ne_name or "")
        ne_ip = str(device.get("ip_address") or task.ne_ip or "")
        cmds = commands_for_vendor(vendor, device_type)
        if cmds is None:
            _update_task(
                task_id,
                status="fail",
                message="unsupported_vendor",
                vendor=vendor,
                ne_name=ne_name,
                ne_ip=ne_ip,
                ended_at=_utcnow(),
            )
            return

        cmd_names = command_list(cmds)
        try:
            outputs = _collect_with_timeout(creds, cmd_names)
        except Exception as exc:
            _log.warning("config_sync collect failed task=%s: %s", task_id, _format_error(exc))
            _update_task(
                task_id,
                status="fail",
                message=_format_error(exc),
                vendor=vendor,
                ne_name=ne_name,
                ne_ip=ne_ip,
                ended_at=_utcnow(),
            )
            return

        if not outputs or not str(outputs[0] or "").strip():
            _update_task(
                task_id,
                status="fail",
                message="empty_config_output",
                vendor=vendor,
                ne_name=ne_name,
                ne_ip=ne_ip,
                ended_at=_utcnow(),
            )
            return

        primary = outputs[0]
        alt = outputs[1] if cmds.alt and len(outputs) > 1 else None
        try:
            _save_success_snapshot(
                source=source,
                target_id=target_id,
                vendor=vendor,
                device_type=device_type,
                ne_name=ne_name,
                ne_ip=ne_ip,
                primary_text=primary,
                alt_text=alt,
                commands=cmd_names,
                cycle_id=cycle_id,
                task_id=task_id,
            )
        except Exception as exc:
            _log.exception("config_sync snapshot save failed task=%s", task_id)
            _update_task(task_id, status="fail", message=_format_error(exc), ended_at=_utcnow())
            return

        _update_task(
            task_id,
            status="success",
            message="synced",
            vendor=vendor,
            ne_name=ne_name,
            ne_ip=ne_ip,
            ended_at=_utcnow(),
        )
    finally:
        db.close()
        db2 = SessionLocal()
        try:
            sync_cycle_progress(db2, cycle_id)
            finalize_cycle(db2, cycle_id)
            cycle = db2.get(ConfigSyncCycle, cycle_id)
            if cycle and str(cycle.status or "") in ("success", "fail", "cancelled"):
                _release_pool(cycle_id)
        finally:
            db2.close()


def _run_safe(cycle_id: str, task_id: str) -> None:
    try:
        _run_single(cycle_id, task_id)
    except Exception:
        _log.exception("config_sync worker crashed cycle=%s task=%s", cycle_id, task_id)
        _update_task(
            task_id,
            status="fail",
            message="config_sync_worker_crashed",
            ended_at=_utcnow(),
        )
        db = SessionLocal()
        try:
            sync_cycle_progress(db, cycle_id)
            finalize_cycle(db, cycle_id)
        finally:
            db.close()


def schedule_cycle_tasks(cycle_id: str, task_ids: list[str], concurrency: int) -> int:
    if not task_ids:
        db = SessionLocal()
        try:
            finalize_cycle(db, cycle_id)
        finally:
            db.close()
        return 0
    pool = _pool_for_cycle(cycle_id, concurrency)
    submitted = 0
    for tid in task_ids:
        pool.submit(_run_safe, cycle_id, str(tid))
        submitted += 1
    _log.info("scheduled config_sync cycle=%s tasks=%s concurrency=%s", cycle_id, submitted, concurrency)
    return submitted


def dispatch_cycle(cycle_id: str) -> int:
    """Load pending tasks for a cycle and schedule workers."""
    db = SessionLocal()
    try:
        cycle = db.get(ConfigSyncCycle, cycle_id)
        if not cycle:
            return 0
        if str(cycle.status or "") not in ("running", "pending"):
            return 0
        if str(cycle.status or "") == "pending":
            cycle.status = "running"
            if not cycle.started_at:
                cycle.started_at = _utcnow()
            db.commit()
        pending = (
            db.query(ConfigSyncTask)
            .filter(ConfigSyncTask.cycle_id == cycle_id, ConfigSyncTask.status == "pending")
            .all()
        )
        task_ids = [str(t.id) for t in pending]
        concurrency = max(1, min(30, int(cycle.concurrency or 5)))
    finally:
        db.close()
    return schedule_cycle_tasks(cycle_id, task_ids, concurrency)
