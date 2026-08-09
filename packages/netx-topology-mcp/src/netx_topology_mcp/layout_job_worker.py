"""Subprocess entry for durable layout jobs (survives MCP parent restart)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="netx_topology_mcp.layout_job_worker")
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args(argv)
    job_id = str(args.job_id).strip()
    if not job_id:
        return 2

    from netx_topology_mcp.layout_jobs import (
        _JobCancelled,
        _args_path,
        _read_job_disk,
        _write_job_disk,
        bind_job,
        finish_job,
        is_cancelled,
        report_progress,
        touch_heartbeat,
        unbind_job,
    )

    args_file = _args_path(job_id)
    if not args_file.is_file():
        finish_job(job_id, error="job_args_missing")
        return 1

    try:
        tool_args = json.loads(args_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        finish_job(job_id, error=f"job_args_invalid:{exc}")
        return 1

    token = bind_job(job_id)
    import threading

    stop_beat = threading.Event()

    def _beat() -> None:
        while not stop_beat.wait(15.0):
            touch_heartbeat()

    threading.Thread(target=_beat, name=f"job-beat-{job_id}", daemon=True).start()
    try:
        job = _read_job_disk(job_id) or {"job_id": job_id, "status": "running"}
        job["pid"] = __import__("os").getpid()
        job["heartbeat_at"] = time.time()
        job["progress"] = {
            **(job.get("progress") or {}),
            "phase": "running",
            "message": "worker bound",
            "updated_at": time.time(),
            "pct": 1.0,
        }
        _write_job_disk(job)
        report_progress("running", pct=1.0, message="worker started")
        if is_cancelled():
            raise _JobCancelled()

        # Late import: pulls http_tools + layout stack.
        from netx_topology_mcp.http_tools import _layout_topology_view

        result = _layout_topology_view(tool_args)
        finish_job(job_id, result=result, cancelled=is_cancelled())
        return 0 if result.get("ok") else 1
    except _JobCancelled:
        finish_job(job_id, cancelled=True)
        return 0
    except Exception as exc:  # noqa: BLE001
        finish_job(job_id, error=f"job_exception:{exc}")
        return 1
    finally:
        stop_beat.set()
        unbind_job(token)
        try:
            args_file.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
