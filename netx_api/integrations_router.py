"""Thin integrations / health routes (liveness vs readiness)."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, WebSocket
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .dsh_alarm_hub import dsh_alarm_ws_loop, hub_status

router = APIRouter(tags=["health"])


@router.websocket("/v1/integrations/dsh-alarm/ws")
async def dsh_alarm_subscribe(websocket: WebSocket) -> None:
    """netxops dials out here to receive matched key-alert pushes."""
    await dsh_alarm_ws_loop(websocket)


@router.get("/health/live", status_code=200)
def health_live() -> dict[str, str]:
    """Process liveness — no DB or upstream checks."""
    return {"status": "ok", "probe": "live"}


@router.get("/health/ready", status_code=200)
def health_ready(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Readiness — DB plus scheduler deployment hint."""
    from .cli_budget import cli_budget_status
    from .db import db_pool_status

    out: dict[str, Any] = {"status": "ok", "probe": "ready"}
    try:
        db.execute(sql_text("select 1"))
        out["db"] = "up"
    except Exception as exc:
        return {
            "status": "down",
            "probe": "ready",
            "db": "down",
            "error": str(exc)[:240],
        }
    out["db_pool"] = db_pool_status()
    out["cli_budget"] = cli_budget_status()
    inline = bool(getattr(settings, "run_inline_schedulers", True))
    sched_block: dict[str, Any] = {
        "inline": inline,
        "mode": "inline" if inline else "external_worker",
        "hint": None
        if inline
        else "run `python -m netx_api.worker` for config_sync / lldp_collect / port_traffic",
    }
    try:
        from .scheduler_heartbeat import resolve_device_scheduler_metrics

        resolved = resolve_device_scheduler_metrics()
        sched_block["source"] = resolved.get("source")
        sched_block["stale"] = bool(resolved.get("stale"))
        sched_block["age_sec"] = resolved.get("age_sec")
        sched_block["worker_pid"] = resolved.get("pid")
        for key in ("config_sync", "lldp_collect", "port_traffic"):
            block = resolved.get(key) or {}
            sched_block[key] = {"running": bool(block.get("running"))}
        if resolved.get("hint"):
            sched_block["hint"] = resolved["hint"]
        if not inline and resolved.get("stale"):
            out["status"] = "degraded"
    except Exception:  # noqa: BLE001
        pass
    out["schedulers"] = sched_block
    return out


@router.get("/v1/integrations/status")
def integrations_status(db: Session = Depends(get_db)) -> dict:
    """netx API + DB + DSH alarm hub status."""
    netx_api = {"status": "up"}

    db_status: dict = {"status": "unknown"}
    try:
        t0 = time.monotonic()
        db.execute(sql_text("select 1"))
        db_status = {"status": "up", "latency_ms": int((time.monotonic() - t0) * 1000)}
    except Exception as exc:
        db_status = {"status": "down", "error": str(exc)[:240]}

    return {
        "netx_api": netx_api,
        "db": db_status,
        "dsh_alarm_hub": hub_status(),
    }
