"""Thin integrations / health routes (liveness vs readiness)."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .oclaw_alarm_forwarder import forwarder_status

router = APIRouter(tags=["health"])


@router.get("/health/live", status_code=200)
def health_live() -> dict[str, str]:
    """Process liveness — no DB or upstream checks."""
    return {"status": "ok", "probe": "live"}


@router.get("/health/ready", status_code=200)
def health_ready(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Readiness — DB plus scheduler deployment hint."""
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
    inline = bool(getattr(settings, "run_inline_schedulers", False))
    out["schedulers"] = {
        "inline": inline,
        "mode": "inline" if inline else "external_worker",
        "hint": None
        if inline
        else "run `python -m netx_api.worker` for config_sync / lldp_collect / port_traffic",
    }
    return out


@router.get("/v1/integrations/status")
def integrations_status(db: Session = Depends(get_db)) -> dict:
    """netx API + DB + oclaw bridge status."""
    netx_api = {"status": "up"}

    db_status: dict = {"status": "unknown"}
    try:
        t0 = time.monotonic()
        db.execute(sql_text("select 1"))
        db_status = {"status": "up", "latency_ms": int((time.monotonic() - t0) * 1000)}
    except Exception as exc:
        db_status = {"status": "down", "error": str(exc)[:240]}

    oclaw_status: dict = {"status": "unknown"}
    fwd = forwarder_status()
    if not bool(fwd.get("enabled")):
        oclaw_status = {
            "status": "unknown",
            "mode": "ws",
            "enabled": False,
            "connected": False,
            "error_kind": "disabled",
            "error": "NETX_OCLAW_ALARM_WS_ENABLED=false or missing token/url",
            "forwarder": fwd,
        }
    elif bool(fwd.get("paused")):
        oclaw_status = {
            "status": "unknown",
            "mode": "ws",
            "enabled": True,
            "connected": False,
            "error_kind": "paused",
            "error": "oclaw_alarm_forwarder runtime task paused",
            "forwarder": fwd,
        }
    elif bool(fwd.get("connected")):
        oclaw_status = {
            "status": "up",
            "mode": "ws",
            "enabled": True,
            "connected": True,
            "queue_size": int(fwd.get("queue_size") or 0),
            "published_ok": int(fwd.get("published_ok") or 0),
            "published_fail": int(fwd.get("published_fail") or 0),
            "url": str(fwd.get("url") or ""),
            "forwarder": fwd,
        }
    else:
        oclaw_status = {
            "status": "down",
            "mode": "ws",
            "enabled": True,
            "connected": False,
            "error_kind": "ws_disconnected",
            "error": "oclaw netx-bridge WebSocket not connected",
            "queue_size": int(fwd.get("queue_size") or 0),
            "url": str(fwd.get("url") or ""),
            "forwarder": fwd,
        }

    return {"netx_api": netx_api, "db": db_status, "oclaw_bridge": oclaw_status}
