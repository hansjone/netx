"""Durable background jobs for long layoutTopologyView calls.

Cursor MCP tool calls often die around ~60s, and Cursor may also **respawn**
the MCP process between tool calls — in-memory threads then vanish.

Design:
- Job records live as JSON under ``NETX_LAYOUT_JOB_DIR`` (default
  ``<netx>/data/runtime/layout_jobs``).
- Workers run in a **subprocess** so they survive MCP parent restarts.
- Poll via ``job_status`` (same contract); cancel is cooperative via the
  JSON ``cancel_requested`` flag.

Remote: any sticky MCP worker that shares the job dir can poll; true
multi-host still wants API-hosted jobs later.
"""

from __future__ import annotations

import contextvars
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable


_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}  # hot cache; disk is source of truth
_MAX_JOBS = 48
_STALE_AFTER_S = 90.0
_CURRENT_JOB_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "layout_job_id", default=None
)


def _job_dir() -> Path:
    env = (os.getenv("NETX_LAYOUT_JOB_DIR") or "").strip()
    if env:
        p = Path(env)
    else:
        # packages/netx-topology-mcp/src/netx_topology_mcp → repo data/
        here = Path(__file__).resolve()
        repo = here.parents[4]  # .../netx
        p = repo / "data" / "runtime" / "layout_jobs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _job_path(job_id: str) -> Path:
    return _job_dir() / f"{job_id}.json"


def _args_path(job_id: str) -> Path:
    return _job_dir() / f"{job_id}.args.json"


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _read_job_disk(job_id: str) -> dict[str, Any] | None:
    path = _job_path(job_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_job_disk(job: dict[str, Any]) -> None:
    jid = str(job.get("job_id") or "").strip()
    if not jid:
        return
    _atomic_write(_job_path(jid), job)


def _prune_disk() -> None:
    files = sorted(_job_dir().glob("*.json"), key=lambda p: p.stat().st_mtime)
    # ignore *.args.json
    files = [p for p in files if not p.name.endswith(".args.json")]
    if len(files) <= _MAX_JOBS:
        return
    for path in files[: max(0, len(files) - _MAX_JOBS)]:
        try:
            path.unlink(missing_ok=True)
            path.with_name(path.stem + ".args.json").unlink(missing_ok=True)
        except OSError:
            pass


def _public_snapshot(job: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    started = float(job.get("started_at") or now)
    finished = job.get("finished_at")
    status = str(job.get("status") or "unknown")
    heartbeat = float(job.get("heartbeat_at") or started)
    elapsed_ms = job.get("elapsed_ms")
    if elapsed_ms is None:
        end = float(finished) if finished is not None else now
        elapsed_ms = int(max(0.0, end - started) * 1000)
    heartbeat_age_ms = int(max(0.0, now - heartbeat) * 1000)
    stale = status in {"running", "cancelling"} and (now - heartbeat) >= _STALE_AFTER_S
    progress = dict(job.get("progress") or {})
    out = {
        "job_id": job.get("job_id"),
        "status": status,
        "action": job.get("action"),
        "view_id": job.get("view_id"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "elapsed_ms": elapsed_ms,
        "heartbeat_at": job.get("heartbeat_at"),
        "heartbeat_age_ms": heartbeat_age_ms,
        "stale": stale,
        "cancel_requested": bool(job.get("cancel_requested")),
        "progress": progress,
        "meta": dict(job.get("meta") or {}),
        "error": job.get("error"),
        "pid": job.get("pid"),
    }
    if status in {"done", "error", "cancelled"}:
        out["result"] = job.get("result")
    return out


def current_job_id() -> str | None:
    return _CURRENT_JOB_ID.get()


def report_progress(
    phase: str,
    *,
    pct: float | None = None,
    message: str = "",
    step: int | None = None,
    total_steps: int | None = None,
    **extra: Any,
) -> None:
    jid = _CURRENT_JOB_ID.get()
    if not jid:
        return
    now = time.time()
    phase_s = str(phase or "").strip() or "running"
    prog: dict[str, Any] = {
        "phase": phase_s,
        "message": str(message or "")[:240],
        "updated_at": now,
    }
    if pct is not None:
        try:
            prog["pct"] = max(0.0, min(100.0, float(pct)))
        except (TypeError, ValueError):
            pass
    if step is not None:
        try:
            prog["step"] = int(step)
        except (TypeError, ValueError):
            pass
    if total_steps is not None:
        try:
            prog["total_steps"] = int(total_steps)
        except (TypeError, ValueError):
            pass
    for k, v in extra.items():
        if v is None:
            continue
        if isinstance(v, (str, int, float, bool)):
            prog[k] = v
        elif isinstance(v, (list, tuple)) and len(v) <= 12:
            prog[k] = list(v)
    with _LOCK:
        job = _read_job_disk(jid) or _JOBS.get(jid)
        if not job or job.get("status") not in {"running", "cancelling"}:
            return
        prev = dict(job.get("progress") or {})
        prev.update(prog)
        job["progress"] = prev
        job["heartbeat_at"] = now
        _JOBS[jid] = job
        _write_job_disk(job)


def touch_heartbeat() -> None:
    jid = _CURRENT_JOB_ID.get()
    if not jid:
        return
    with _LOCK:
        job = _read_job_disk(jid) or _JOBS.get(jid)
        if job and job.get("status") in {"running", "cancelling"}:
            job["heartbeat_at"] = time.time()
            _JOBS[jid] = job
            _write_job_disk(job)


def is_cancelled() -> bool:
    jid = _CURRENT_JOB_ID.get()
    if not jid:
        return False
    with _LOCK:
        job = _read_job_disk(jid) or _JOBS.get(jid)
        if not job:
            return False
        return bool(job.get("cancel_requested"))


def cancel_job(job_id: str) -> dict[str, Any]:
    jid = str(job_id or "").strip()
    if not jid:
        return {"ok": False, "error": "job_id_required"}
    with _LOCK:
        job = _read_job_disk(jid) or _JOBS.get(jid)
        if not job:
            return {"ok": False, "error": "job_not_found", "job_id": jid}
        status = str(job.get("status") or "")
        if status in {"done", "error", "cancelled"}:
            return {
                "ok": True,
                "job_id": jid,
                "status": status,
                "cancel_requested": bool(job.get("cancel_requested")),
                "hint": "Job already finished; cancel is a no-op.",
            }
        job["cancel_requested"] = True
        job["status"] = "cancelling"
        job["heartbeat_at"] = time.time()
        job["progress"] = {
            **(job.get("progress") or {}),
            "phase": "cancelling",
            "message": "cancel requested",
            "updated_at": time.time(),
        }
        _JOBS[jid] = job
        _write_job_disk(job)
        return {
            "ok": True,
            "job_id": jid,
            "status": "cancelling",
            "cancel_requested": True,
            "hint": "Cooperative cancel armed; worker exits at next checkpoint.",
        }


class _JobCancelled(Exception):
    """Internal: cooperative cancel before/during runner."""


def raise_if_cancelled() -> None:
    if is_cancelled():
        raise _JobCancelled()


def bind_job(job_id: str) -> contextvars.Token:
    """Worker entry: bind ContextVar for report_progress / is_cancelled."""
    return _CURRENT_JOB_ID.set(str(job_id))


def unbind_job(token: contextvars.Token) -> None:
    _CURRENT_JOB_ID.reset(token)


def finish_job(
    job_id: str,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    cancelled: bool = False,
) -> None:
    """Worker finalizer — write terminal status to disk."""
    jid = str(job_id or "").strip()
    with _LOCK:
        job = _read_job_disk(jid) or _JOBS.get(jid) or {"job_id": jid}
        started = float(job.get("started_at") or time.time())
        now = time.time()
        applied = bool(isinstance(result, dict) and result.get("applied"))
        if cancelled and not applied:
            job["status"] = "cancelled"
            job["error"] = "cancelled"
            job["result"] = result or {
                "ok": False,
                "error": "cancelled",
                "job_id": jid,
                "applied": False,
            }
        elif error and not (isinstance(result, dict) and result.get("ok")):
            job["status"] = "error"
            job["error"] = str(error)[:500]
            job["result"] = result or {
                "ok": False,
                "error": job["error"],
                "job_id": jid,
            }
        else:
            job["status"] = "done" if (result or {}).get("ok") else "error"
            job["result"] = result
            if cancelled and applied and isinstance(job["result"], dict):
                job["result"] = {
                    **job["result"],
                    "cancel_requested": True,
                    "hint": (
                        str(job["result"].get("hint") or "")
                        + " (cancel requested after apply; write kept)"
                    ).strip(),
                }
            if not (result or {}).get("ok"):
                job["error"] = (result or {}).get("error") or error or "job_failed"
        job["finished_at"] = now
        job["elapsed_ms"] = int((now - started) * 1000)
        job["heartbeat_at"] = now
        job["progress"] = {
            **(job.get("progress") or {}),
            "phase": job["status"],
            "pct": 100.0 if job["status"] == "done" else (job.get("progress") or {}).get("pct"),
            "message": str(job.get("error") or job["status"]),
            "updated_at": now,
        }
        _JOBS[jid] = job
        _write_job_disk(job)


def start_job(
    *,
    action: str,
    view_id: str,
    runner: Callable[[], dict[str, Any]] | None = None,
    meta: dict[str, Any] | None = None,
    tool_args: dict[str, Any] | None = None,
) -> str:
    """Start a durable subprocess job.

    ``tool_args`` should be the full layoutTopologyView args with
    ``params._force_sync=True`` so the worker runs the heavy path inline.
    ``runner`` is only used in unit tests (in-process fallback).
    """
    job_id = uuid.uuid4().hex[:16]
    now = time.time()
    job = {
        "job_id": job_id,
        "status": "running",
        "action": action,
        "view_id": view_id,
        "started_at": now,
        "finished_at": None,
        "elapsed_ms": None,
        "heartbeat_at": now,
        "cancel_requested": False,
        "progress": {
            "phase": "queued",
            "pct": 0.0,
            "message": "starting worker",
            "updated_at": now,
        },
        "result": None,
        "error": None,
        "meta": dict(meta or {}),
        "pid": None,
    }
    with _LOCK:
        _prune_disk()
        _JOBS[job_id] = job
        _write_job_disk(job)

    # Unit-test / sync path: in-process thread when no tool_args.
    if tool_args is None and runner is not None:
        return _start_thread_job(job_id, action, view_id, runner)

    if tool_args is None:
        raise ValueError("tool_args_or_runner_required")

    args_payload = dict(tool_args)
    params = dict(args_payload.get("params") or {})
    params["_force_sync"] = True
    params.pop("background", None)
    args_payload["params"] = params
    args_payload["mode"] = "apply"
    args_payload["action"] = action
    args_payload["view_id"] = view_id
    _atomic_write(_args_path(job_id), args_payload)

    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    # Ensure worker can import the same package tree.
    src = str(Path(__file__).resolve().parents[1])
    prev = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src + (os.pathsep + prev if prev else "")

    creationflags = 0
    if sys.platform == "win32":
        # Detach from MCP console; survive parent exit.
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "DETACHED_PROCESS", 0x00000008
        )

    proc = subprocess.Popen(  # noqa: S603 — controlled argv
        [sys.executable, "-m", "netx_topology_mcp.layout_job_worker", "--job-id", job_id],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
        close_fds=True,
    )
    with _LOCK:
        job = _read_job_disk(job_id) or job
        job["pid"] = proc.pid
        job["progress"] = {
            **(job.get("progress") or {}),
            "phase": "running",
            "message": f"worker pid={proc.pid}",
            "updated_at": time.time(),
        }
        job["heartbeat_at"] = time.time()
        _JOBS[job_id] = job
        _write_job_disk(job)
    return job_id


def _start_thread_job(
    job_id: str,
    action: str,
    view_id: str,
    runner: Callable[[], dict[str, Any]],
) -> str:
    stop_beat = threading.Event()

    def _heartbeat_ticker() -> None:
        while not stop_beat.wait(15.0):
            touch_heartbeat()
            with _LOCK:
                job = _read_job_disk(job_id)
                if not job or job.get("status") not in {"running", "cancelling"}:
                    return

    def _run() -> None:
        token = bind_job(job_id)
        try:
            report_progress("running", pct=1.0, message="runner started")
            raise_if_cancelled()
            result = runner()
            finish_job(
                job_id,
                result=result,
                cancelled=is_cancelled(),
            )
        except _JobCancelled:
            finish_job(job_id, cancelled=True)
        except Exception as exc:  # noqa: BLE001
            finish_job(job_id, error=f"job_exception:{exc}")
        finally:
            stop_beat.set()
            unbind_job(token)

    threading.Thread(
        target=_heartbeat_ticker, name=f"layout-job-beat-{job_id}", daemon=True
    ).start()
    threading.Thread(target=_run, name=f"layout-job-{job_id}", daemon=True).start()
    return job_id


def get_job(job_id: str) -> dict[str, Any] | None:
    with _LOCK:
        job = _read_job_disk(job_id)
        if job:
            _JOBS[str(job_id)] = job
        return dict(job) if job else None


def job_public(job_id: str) -> dict[str, Any] | None:
    with _LOCK:
        job = _read_job_disk(job_id)
        if not job:
            return None
        _JOBS[str(job_id)] = job
        return _public_snapshot(job)
