"""Offline log import → same biz_state batch / parse / persist path (no SSH)."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from ..db import SessionLocal
from ..lldp_shared import resolve_vendor_key
from ..models import BizStateBatch, BizStateTask
from ..timeutil import utcnow_naive
from .collect_runner import (
    _finish_task,
    _finalize_batch_status,
    _format_error,
    _run_primary_parse_job,
)
from .collect_session import resolve_aux_command
from .command_match import match_command, normalize_command
from .log_split import LogSegment, unpack_upload
from .parse_pool import AuxRawCapture, PrimaryParseJob
from .parsers import get_parser
from .persist_pool import get_persist_pool
from .spool import (
    SpooledCommand,
    clear_batch_spool,
    count_text_lines,
    persist_every_cmds,
    write_meta,
    write_raw_text,
)

_log = logging.getLogger("netx.biz_state.import")


def _utcnow():
    return utcnow_naive()


def _vendor_fields_for_key(vendor_key: str) -> tuple[str, str]:
    """Map vendor_key → (vendor label, device_type) for offline import tasks."""
    key = str(vendor_key or "").strip().lower() or "zte"
    if key.startswith("huawei") or key in ("vrp", "ce", "ne"):
        return "Huawei", "huawei_vrp"
    if key.startswith("cisco") or key in ("ios", "nxos", "iosxe", "iosxr"):
        return "Cisco", "cisco_ios"
    if key.startswith("zte") or key in ("zxros", "zxr10"):
        return "ZTE", "zte_zxros"
    if key in ("generic", "any", "*"):
        return "generic", ""
    return key[:64] or "generic", ""


def create_standalone_import_task(
    *,
    vendor_key: str,
    ne_name: str = "",
    note: str = "",
    filename: str = "",
) -> dict[str, Any]:
    """Create a paused offline task (source=import) with no inventory NE."""
    vk = str(vendor_key or "").strip().lower() or "zte"
    vendor, device_type = _vendor_fields_for_key(vk)
    fname = Path(str(filename or "")).name[:120]
    label = str(ne_name or "").strip() or (fname and f"import:{fname}") or "offline-import"
    tid = uuid4().hex
    ne_id = f"import-{tid[:12]}"
    now = _utcnow()
    db = SessionLocal()
    try:
        task = BizStateTask(
            id=tid,
            source="import",
            ne_id=ne_id,
            ne_name=label[:256],
            ne_ip="",
            vendor=vendor,
            device_type=device_type,
            note=str(note or "")[:256],
            purpose="",
            status="paused",  # offline only — no schedule / SSH
            interval_sec=3600,
            retention_days=30,
            daily_keep_enabled=False,
            daily_keep_count=10,
            retention_batches=30,
            created_at=now,
            updated_at=now,
        )
        db.add(task)
        db.commit()
        return {
            "ok": True,
            "task_id": tid,
            "ne_id": ne_id,
            "ne_name": task.ne_name,
            "vendor": vendor,
            "device_type": device_type,
            "vendor_key": vk,
        }
    except Exception:
        _log.exception("create_standalone_import_task failed")
        try:
            db.rollback()
        except Exception:
            pass
        raise
    finally:
        db.close()


def enqueue_import(
    task_id: str,
    *,
    filename: str = "",
    vendor_key: str = "",
) -> dict[str, Any]:
    """Create a running import batch; mark task collect_running (mutex with SSH collect)."""
    tid = str(task_id or "").strip()
    if not tid:
        return {"ok": False, "queued": False, "reason": "missing_task_id", "task_id": ""}

    db = SessionLocal()
    try:
        task = db.get(BizStateTask, tid)
        if not task:
            return {"ok": False, "queued": False, "reason": "task_not_found", "task_id": tid}
        if bool(task.collect_running):
            return {
                "ok": True,
                "queued": False,
                "reason": "already_collecting",
                "task_id": tid,
            }
        st = str(task.status or "").strip()
        if st in ("", "deleted"):
            return {"ok": False, "queued": False, "reason": "bad_status", "task_id": tid}

        now = _utcnow()
        task.collect_running = True
        task.last_collect_started_at = now
        task.last_error = ""
        task.updated_at = now
        if hasattr(task, "collect_queued_at"):
            task.collect_queued_at = now

        fname = Path(str(filename or "import.log")).name[:120] or "import.log"
        vk = str(vendor_key or "").strip().lower()
        if not vk:
            vk = resolve_vendor_key(task.vendor or "", task.device_type or "")

        batch = BizStateBatch(
            id=uuid4().hex,
            task_id=task.id,
            source=task.source,
            ne_id=task.ne_id,
            ne_name=task.ne_name,
            vendor=task.vendor,
            status="running",
            started_at=now,
            message=f"importing:{fname}",
            alias=fname[:128],
        )
        db.add(batch)
        db.commit()
        return {
            "ok": True,
            "queued": True,
            "batch_id": batch.id,
            "task_id": tid,
            "vendor_key": vk,
            "filename": fname,
            "manual": True,
            "import": True,
        }
    except Exception:
        _log.exception("enqueue_import failed task=%s", tid)
        try:
            db.rollback()
        except Exception:
            pass
        return {"ok": False, "queued": False, "reason": "enqueue_failed", "task_id": tid}
    finally:
        db.close()


def _segment_index(segments: list[LogSegment]) -> dict[str, LogSegment]:
    """Map normalize_command(cmd) → first matching segment."""
    idx: dict[str, LogSegment] = {}
    for seg in segments:
        key = normalize_command(seg.command)
        if key and key not in idx:
            idx[key] = seg
    return idx


def _build_aux_captures(
    *,
    profile: Any,
    params: dict[str, str],
    batch_id: str,
    seg_index: dict[str, LogSegment],
) -> tuple[list[AuxRawCapture], list[str]]:
    """Try to satisfy aux_commands from segments already in the import log."""
    captures: list[AuxRawCapture] = []
    notes: list[str] = []
    for aux in list(getattr(profile, "aux_commands", None) or []):
        try:
            ra = resolve_aux_command(aux, params)
        except Exception as exc:
            notes.append(f"aux:{getattr(aux, 'key', '?')}:resolve:{exc}")
            continue
        hit = seg_index.get(normalize_command(ra.command))
        if hit is None:
            notes.append(f"enrich_skipped:missing_aux:{ra.key}")
            continue
        aux_id = uuid4().hex
        raw_rel = ""
        try:
            raw_rel = write_raw_text(batch_id, aux_id, hit.body)
        except Exception:
            _log.exception("import aux spool raw failed")
        captures.append(
            AuxRawCapture(
                key=ra.key,
                aux_id=aux_id,
                profile_id=ra.profile_id,
                parser_id=ra.parser_id,
                metric_id=str(getattr(ra.profile, "metric_id", "") or ""),
                command=ra.command,
                textfsm_command=ra.textfsm_command or ra.command,
                rule_keys=tuple(ra.rule_keys or ()),
                raw=hit.body,
                raw_rel_path=raw_rel,
                cache_hit=False,
                ok=True,
            )
        )
    return captures, notes


def run_import_batch(
    *,
    batch_id: str,
    task_id: str,
    segments: list[LogSegment],
    vendor_key: str,
    vendor: str = "",
    device_type: str = "",
    filename: str = "",
) -> dict[str, Any]:
    """Parse imported segments into the batch (sync parse + persist pool)."""
    bid = str(batch_id or "").strip()
    tid = str(task_id or "").strip()
    vk = str(vendor_key or "").strip().lower() or "zte"
    any_ok = False
    any_fail = False
    matched = 0
    unmatched = 0
    cmd_count = 0
    lane_errors: list[str] = []

    try:
        clear_batch_spool(bid)
    except Exception:
        _log.exception("import clear spool failed batch=%s", bid)

    persist = get_persist_pool()
    pending: list[SpooledCommand] = []
    flush_every = persist_every_cmds()
    seg_index = _segment_index(segments)
    persisted: set[tuple[str, str]] = set()
    cache_lock = threading.RLock()

    def _submit_pending() -> None:
        nonlocal pending
        if not pending:
            return
        chunk = list(pending)
        pending = []
        persist.submit(bid, chunk)

    def _queue(item: SpooledCommand) -> None:
        nonlocal cmd_count
        try:
            write_meta(bid, item.id, item.to_meta())
        except Exception:
            _log.exception("import write meta failed cmd=%s", item.id)
        pending.append(item)
        cmd_count += 1
        if len(pending) >= flush_every:
            _submit_pending()

    for seg in segments:
        cmd = normalize_command(seg.command)
        cmd_id = uuid4().hex
        src_note = ""
        if seg.source_file:
            src_note = f"file={seg.source_file}"
        hit = match_command(vendor_key=vk, command=cmd)
        raw_rel = ""
        try:
            raw_rel = write_raw_text(bid, cmd_id, seg.body)
        except Exception:
            _log.exception("import spool raw failed cmd=%s", cmd_id)

        if not hit:
            unmatched += 1
            any_fail = True
            msg = "no profile matched concrete command"
            if src_note:
                msg = f"{msg};{src_note}"
            _queue(
                SpooledCommand(
                    id=cmd_id,
                    batch_id=bid,
                    profile_id="",
                    raw_command=cmd[:512],
                    parse_status="unmatched",
                    message=msg[:1020],
                    raw_rel_path=raw_rel,
                    raw_line_count=count_text_lines(seg.body),
                )
            )
            continue

        if not get_parser(hit.profile.parser_id):
            unmatched += 1
            any_fail = True
            _queue(
                SpooledCommand(
                    id=cmd_id,
                    batch_id=bid,
                    profile_id=hit.profile.profile_id,
                    parser_id=hit.profile.parser_id,
                    metric_id=hit.profile.metric_id,
                    raw_command=cmd[:512],
                    params_json=dict(hit.params or {}),
                    parse_status="failed",
                    message=f"unknown parser {hit.profile.parser_id}"[:1020],
                    raw_rel_path=raw_rel,
                    raw_line_count=count_text_lines(seg.body),
                )
            )
            continue

        matched += 1
        aux_caps, aux_notes = _build_aux_captures(
            profile=hit.profile,
            params=dict(hit.params or {}),
            batch_id=bid,
            seg_index=seg_index,
        )
        enrich = list(hit.profile.enrich_joins or [])
        # Without in-log aux, skip enrich so we don't pretend peer intent joined.
        if any("enrich_skipped" in n for n in aux_notes):
            enrich = []
        job = PrimaryParseJob(
            batch_id=bid,
            cmd_id=cmd_id,
            task_item_id="",
            profile_id=hit.profile.profile_id,
            parser_id=hit.profile.parser_id,
            metric_id=hit.profile.metric_id,
            concrete=cmd,
            merged_params=dict(hit.params or {}),
            raw_text=seg.body,
            raw_rel_path=raw_rel,
            raw_line_count=count_text_lines(seg.body),
            textfsm_command=hit.profile.textfsm_command or cmd,
            vendor=vendor,
            device_type=device_type,
            enrich_joins=enrich,
            aux_captures=aux_caps,
            persisted=persisted,
            cache_lock=cache_lock,
            on_done=None,
        )
        try:
            ok, fail = _run_primary_parse_job(job)
            if ok:
                any_ok = True
            if fail:
                any_fail = True
            if aux_notes:
                _log.info(
                    "import enrich notes batch=%s cmd=%s %s",
                    bid,
                    cmd_id,
                    ";".join(aux_notes),
                )
            cmd_count += 1 + len(aux_caps)
        except Exception as exc:
            any_fail = True
            _queue(
                SpooledCommand(
                    id=cmd_id,
                    batch_id=bid,
                    profile_id=hit.profile.profile_id,
                    parser_id=hit.profile.parser_id,
                    metric_id=hit.profile.metric_id,
                    raw_command=cmd[:512],
                    params_json=dict(hit.params or {}),
                    parse_status="failed",
                    message=f"parse: {_format_error(exc)}"[:1020],
                    raw_rel_path=raw_rel,
                    raw_line_count=count_text_lines(seg.body),
                )
            )

    _submit_pending()
    # _run_primary_parse_job also submits to this same pool — wait once for all.
    if not persist.wait_idle(timeout=3600.0):
        any_fail = True
        lane_errors.append("import: persist_barrier_timeout")

    fname = Path(str(filename or "")).name
    summary = (
        f"imported:{fname or 'upload'}; segments={len(segments)}; "
        f"matched={matched}; unmatched={unmatched}"
    )
    if fname:
        lane_errors.insert(0, summary)

    status = ""
    try:
        status = _finalize_batch_status(
            batch_id=bid,
            task_id=tid,
            cmd_count=cmd_count,
            total_rows=0,
            any_fail=any_fail,
            any_ok=any_ok or matched > 0,
            lane_errors=lane_errors,
        )
    except Exception as exc:
        _log.exception("import finalize failed batch=%s", bid)
        lane_errors.append(_format_error(exc))
        try:
            from .collect_runner import _fail_batch_status

            _fail_batch_status(bid, "; ".join(lane_errors)[:1020])
        except Exception:
            pass
        status = "failed"

    # Full success clears message in finalize — keep import provenance visible.
    if status == "success":
        try:
            _stamp_import_message(bid, summary)
        except Exception:
            _log.exception("import stamp message failed batch=%s", bid)

    err = ""
    if status in ("failed",) and not any_ok:
        err = "; ".join(lane_errors)[:1020] or "import failed"
    try:
        _finish_task(tid, error=err)
    except Exception:
        _log.exception("import finish task failed task=%s", tid)

    try:
        clear_batch_spool(bid)
    except Exception:
        pass

    return {
        "ok": status in ("success", "partial"),
        "batch_id": bid,
        "task_id": tid,
        "status": status,
        "segments": len(segments),
        "matched": matched,
        "unmatched": unmatched,
        "command_count": cmd_count,
    }


def _stamp_import_message(batch_id: str, summary: str) -> None:
    db = SessionLocal()
    try:
        batch = db.get(BizStateBatch, batch_id)
        if not batch:
            return
        if not str(batch.message or "").strip():
            batch.message = str(summary or "")[:1020]
            db.commit()
    finally:
        db.close()


def start_standalone_import(
    *,
    filename: str,
    data: bytes,
    vendor_key: str = "",
    ne_name: str = "",
    note: str = "",
) -> dict[str, Any]:
    """Create offline task + unpack + enqueue import (no inventory NE)."""
    vk = str(vendor_key or "").strip().lower() or "zte"
    try:
        segments, stats = unpack_upload(
            filename=filename, data=data, vendor_key=vk
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not segments:
        raise HTTPException(
            status_code=400,
            detail="no show/display command segments found in upload",
        )

    created = create_standalone_import_task(
        vendor_key=vk,
        ne_name=ne_name,
        note=note,
        filename=filename,
    )
    tid = str(created["task_id"])
    vendor = str(created.get("vendor") or "")
    device_type = str(created.get("device_type") or "")

    result = enqueue_import(tid, filename=filename, vendor_key=vk)
    if not result.get("queued"):
        # Best-effort: leave the empty task for the user to retry / delete.
        return {
            "ok": bool(result.get("ok", False)),
            "started": False,
            "queued": False,
            "reason": result.get("reason") or "enqueue_failed",
            "task_id": tid,
            "created_task": True,
            **stats,
        }

    return {
        "ok": True,
        "started": True,
        "queued": True,
        "batch_id": result["batch_id"],
        "task_id": tid,
        "vendor_key": vk,
        "filename": result.get("filename") or filename,
        "vendor": vendor,
        "device_type": device_type,
        "ne_name": created.get("ne_name") or "",
        "created_task": True,
        "segments_preview": stats,
        "segments": segments,
        "collect_running": True,
    }


def start_import_from_upload(
    *,
    task_id: str,
    filename: str,
    data: bytes,
    vendor_key: str = "",
) -> dict[str, Any]:
    """Enqueue + unpack; caller should run ``execute_import`` in background."""
    tid = str(task_id or "").strip()
    db = SessionLocal()
    try:
        task = db.get(BizStateTask, tid)
        if not task:
            raise HTTPException(status_code=404, detail="task not found")
        vendor = str(task.vendor or "")
        device_type = str(task.device_type or "")
        vk = str(vendor_key or "").strip().lower()
        if not vk:
            vk = resolve_vendor_key(vendor, device_type)
    finally:
        db.close()

    try:
        segments, stats = unpack_upload(
            filename=filename, data=data, vendor_key=vk
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not segments:
        raise HTTPException(
            status_code=400,
            detail="no show/display command segments found in upload",
        )

    result = enqueue_import(tid, filename=filename, vendor_key=vk)
    if not result.get("queued"):
        return {
            "ok": bool(result.get("ok", False)),
            "started": False,
            "queued": False,
            "reason": result.get("reason") or "enqueue_failed",
            "task_id": tid,
            **stats,
        }

    return {
        "ok": True,
        "started": True,
        "queued": True,
        "batch_id": result["batch_id"],
        "task_id": tid,
        "vendor_key": vk,
        "filename": result.get("filename") or filename,
        "vendor": vendor,
        "device_type": device_type,
        "segments_preview": stats,
        "segments": segments,  # passed to background runner (in-memory)
        "collect_running": True,
    }


def execute_import(
    *,
    batch_id: str,
    task_id: str,
    segments: list[LogSegment],
    vendor_key: str,
    vendor: str = "",
    device_type: str = "",
    filename: str = "",
) -> dict[str, Any]:
    """Background entry: parse imported segments."""
    try:
        return run_import_batch(
            batch_id=batch_id,
            task_id=task_id,
            segments=segments,
            vendor_key=vendor_key,
            vendor=vendor,
            device_type=device_type,
            filename=filename,
        )
    except Exception as exc:
        _log.exception("execute_import failed batch=%s", batch_id)
        try:
            from .collect_runner import _fail_batch_status

            _fail_batch_status(batch_id, _format_error(exc))
        except Exception:
            pass
        try:
            _finish_task(task_id, error=_format_error(exc))
        except Exception:
            pass
        return {
            "ok": False,
            "batch_id": batch_id,
            "task_id": task_id,
            "status": "failed",
            "error": _format_error(exc),
        }
