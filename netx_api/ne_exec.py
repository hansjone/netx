"""Execute read-only CLI on netx managed network elements (for oclaw ops tools)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .cli_resolve import resolve_cli_target
from .config import settings
from .db import SessionLocal
from .ne_collect_runner import _collect_on_device
from .ne_crypto import credentials_configured
from .ne_exec_guard import _validate_command, validate_ne_exec_command

_EXEC_MAX_COMMANDS_CAP = 50
_EXEC_MAX_OUTPUT = 32_000
_EXEC_READ_TIMEOUT_DEFAULT = 60
_EXEC_READ_TIMEOUT_MAX = 120
_EXEC_BATCH_MAX_TARGETS = 20
_EXEC_BATCH_DEFAULT_CONCURRENCY = 4
_EXEC_BATCH_MAX_CONCURRENCY = 8

__all__ = [
    "_validate_command",
    "execute_managed_ne_commands",
    "execute_managed_ne_commands_batch",
    "validate_ne_exec_command",
]


def _exec_max_commands() -> int:
    raw = int(settings.ne_exec_max_commands or 5)
    return max(1, min(_EXEC_MAX_COMMANDS_CAP, raw))


def _normalize_read_timeout(sec: int | None) -> int:
    raw = int(sec if sec is not None else _EXEC_READ_TIMEOUT_DEFAULT)
    return max(10, min(_EXEC_READ_TIMEOUT_MAX, raw))


def execute_managed_ne_commands(
    db: Session,
    commands: list[str],
    *,
    ne_id: str | None = None,
    ume_ne_id: str | None = None,
    read_timeout_sec: int | None = None,
) -> dict[str, Any]:
    if not credentials_configured():
        raise HTTPException(status_code=503, detail="credential_secret_key_not_configured")
    mid = str(ne_id or "").strip()
    uid = str(ume_ne_id or "").strip()
    if bool(mid) == bool(uid):
        raise HTTPException(status_code=400, detail="exactly_one_of_ne_id_or_ume_ne_id_required")
    cmds = [str(c).strip() for c in commands if str(c).strip()]
    if not cmds:
        raise HTTPException(status_code=400, detail="commands_required")
    max_cmds = _exec_max_commands()
    if len(cmds) > max_cmds:
        raise HTTPException(status_code=400, detail=f"too_many_commands (max {max_cmds})")
    for c in cmds:
        validate_ne_exec_command(c)

    creds, device = resolve_cli_target(db, managed_ne_id=mid or None, ume_ne_id=uid or None)
    read_timeout = _normalize_read_timeout(read_timeout_sec)

    try:
        output = _collect_on_device(creds, cmds, read_timeout_sec=read_timeout)
    except Exception as exc:
        return {
            "ok": False,
            "device": device,
            "commands": cmds,
            "read_timeout_sec": read_timeout,
            "error": type(exc).__name__,
            "detail": str(exc)[:2000],
        }

    if len(output) > _EXEC_MAX_OUTPUT:
        output = output[:_EXEC_MAX_OUTPUT] + "\n...[truncated]"

    return {
        "ok": True,
        "device": device,
        "commands": cmds,
        "read_timeout_sec": read_timeout,
        "output": output,
    }


def _normalize_batch_targets(
    *,
    targets: list[dict[str, Any]] | None,
    ne_ids: list[str] | None,
    ume_ne_ids: list[str] | None,
    shared_commands: list[str] | None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in targets or []:
        if not isinstance(raw, dict):
            continue
        mid = str(raw.get("ne_id") or "").strip()
        uid = str(raw.get("ume_ne_id") or "").strip()
        cmds_raw = raw.get("commands")
        cmds = (
            [str(c).strip() for c in cmds_raw if str(c).strip()]
            if isinstance(cmds_raw, list)
            else list(shared_commands or [])
        )
        out.append({"ne_id": mid or None, "ume_ne_id": uid or None, "commands": cmds})
    for mid in ne_ids or []:
        s = str(mid or "").strip()
        if s:
            out.append({"ne_id": s, "ume_ne_id": None, "commands": list(shared_commands or [])})
    for uid in ume_ne_ids or []:
        s = str(uid or "").strip()
        if s:
            out.append({"ne_id": None, "ume_ne_id": s, "commands": list(shared_commands or [])})
    if not out:
        raise HTTPException(status_code=400, detail="targets_required")
    if len(out) > _EXEC_BATCH_MAX_TARGETS:
        raise HTTPException(
            status_code=400,
            detail=f"too_many_targets (max {_EXEC_BATCH_MAX_TARGETS})",
        )
    return out


def execute_managed_ne_commands_batch(
    *,
    targets: list[dict[str, Any]] | None = None,
    ne_ids: list[str] | None = None,
    ume_ne_ids: list[str] | None = None,
    commands: list[str] | None = None,
    read_timeout_sec: int | None = None,
    concurrency: int | None = None,
) -> dict[str, Any]:
    """Fan out read-only CLI across many NEs with bounded concurrency.

    Each worker opens its own DB session. Partial failures stay in ``results``;
    top-level ``ok`` is true when the batch request itself is valid.
    """
    shared = [str(c).strip() for c in (commands or []) if str(c).strip()]
    normalized = _normalize_batch_targets(
        targets=targets,
        ne_ids=ne_ids,
        ume_ne_ids=ume_ne_ids,
        shared_commands=shared,
    )
    workers = int(concurrency if concurrency is not None else _EXEC_BATCH_DEFAULT_CONCURRENCY)
    workers = max(1, min(_EXEC_BATCH_MAX_CONCURRENCY, workers, len(normalized)))

    def _run_one(idx: int, item: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        db = SessionLocal()
        try:
            try:
                row = execute_managed_ne_commands(
                    db,
                    list(item.get("commands") or []),
                    ne_id=item.get("ne_id"),
                    ume_ne_id=item.get("ume_ne_id"),
                    read_timeout_sec=read_timeout_sec,
                )
            except HTTPException as exc:
                row = {
                    "ok": False,
                    "ne_id": item.get("ne_id"),
                    "ume_ne_id": item.get("ume_ne_id"),
                    "commands": list(item.get("commands") or []),
                    "error": str(exc.detail),
                    "http_status": int(exc.status_code),
                }
            except Exception as exc:
                row = {
                    "ok": False,
                    "ne_id": item.get("ne_id"),
                    "ume_ne_id": item.get("ume_ne_id"),
                    "commands": list(item.get("commands") or []),
                    "error": type(exc).__name__,
                    "detail": str(exc)[:2000],
                }
            if isinstance(row, dict):
                row.setdefault("ne_id", item.get("ne_id"))
                row.setdefault("ume_ne_id", item.get("ume_ne_id"))
                row["target_index"] = idx
            return idx, row if isinstance(row, dict) else {"ok": False, "error": "invalid_result", "target_index": idx}
        finally:
            db.close()

    ordered: list[dict[str, Any] | None] = [None] * len(normalized)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_run_one, i, item) for i, item in enumerate(normalized)]
        for fut in as_completed(futs):
            idx, row = fut.result()
            ordered[idx] = row

    results = [r if isinstance(r, dict) else {"ok": False, "error": "missing_result"} for r in ordered]
    ok_n = sum(1 for r in results if r.get("ok") is True)
    fail_n = len(results) - ok_n
    return {
        "ok": True,
        "concurrency": workers,
        "summary": {"total": len(results), "ok": ok_n, "failed": fail_n},
        "results": results,
        "hint": (
            "Multi-NE CLI finished in one batch. Summarize ok/failed counts; "
            "do not re-loop execManagedNe per NE for the same commands."
        ),
    }
