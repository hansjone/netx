"""Minimal process metrics for ops (Prometheus text + JSON)."""
from __future__ import annotations

import os
import threading
import time
from typing import Any

from fastapi import APIRouter, Response

from .audit_async import audit_queue_status
from .cli_budget import cli_budget_status
from .config import settings
from .db import db_pool_status
from .oclaw_alarm_forwarder import forwarder_status

router = APIRouter(tags=["metrics"])

_BOOT_MONO = time.monotonic()


def collect_runtime_metrics() -> dict[str, Any]:
    out: dict[str, Any] = {
        "uptime_sec": round(time.monotonic() - _BOOT_MONO, 1),
        "pid": os.getpid(),
        "thread_count": threading.active_count(),
        "db_pool": db_pool_status(),
        "cli_budget": cli_budget_status(),
        "audit_queue": audit_queue_status(),
        "oclaw_forwarder": forwarder_status(),
        "schedulers_inline": bool(getattr(settings, "run_inline_schedulers", True)),
    }
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
        # ru_maxrss is KB on Linux, bytes on macOS; report raw + note.
        out["ru_maxrss"] = int(usage.ru_maxrss)
    except Exception:  # noqa: BLE001
        pass
    try:
        from .port_traffic_scheduler import port_traffic_scheduler_status

        out["port_traffic"] = port_traffic_scheduler_status()
    except Exception:  # noqa: BLE001
        pass
    try:
        from .webcrt_session_registry import active_session_count, list_sessions

        out["webcrt"] = {
            "active_sessions": active_session_count(),
            "max_sessions": int(getattr(settings, "webcrt_max_sessions", 20) or 20),
        }
        # Avoid dumping full session list into metrics; depth aggregate only.
        sess = list_sessions()
        dropped = 0
        for item in sess.get("items") or []:
            dropped += int(item.get("queue_dropped") or 0)
        out["webcrt"]["queue_dropped_total"] = dropped
    except Exception:  # noqa: BLE001
        pass
    return out


def _prom_lines(metrics: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f'netx_uptime_seconds {metrics.get("uptime_sec") or 0}')
    lines.append(f'netx_thread_count {metrics.get("thread_count") or 0}')
    pool = metrics.get("db_pool") or {}
    for key in ("checked_out", "checked_in", "overflow", "size"):
        if key in pool:
            lines.append(f"netx_db_pool_{key} {pool[key]}")
    budget = metrics.get("cli_budget") or {}
    for key in ("limit", "in_use", "available"):
        if key in budget:
            lines.append(f"netx_cli_budget_{key} {budget[key]}")
    audit = metrics.get("audit_queue") or {}
    for key in ("depth", "dropped", "maxsize"):
        if key in audit:
            lines.append(f"netx_audit_queue_{key} {audit[key]}")
    fwd = metrics.get("oclaw_forwarder") or {}
    for key, prom in (
        ("queue_size", "netx_oclaw_forwarder_queue_size"),
        ("published_ok", "netx_oclaw_forwarder_published_ok"),
        ("published_fail", "netx_oclaw_forwarder_published_fail"),
        ("dropped", "netx_oclaw_forwarder_dropped"),
        ("requeued", "netx_oclaw_forwarder_requeued"),
        ("retry_exhausted", "netx_oclaw_forwarder_retry_exhausted"),
    ):
        if key in fwd:
            lines.append(f"{prom} {int(fwd.get(key) or 0)}")
    pt = metrics.get("port_traffic") or {}
    if pt.get("last_tick_age_sec") is not None:
        lines.append(f'netx_port_traffic_tick_age_seconds {pt["last_tick_age_sec"]}')
    if pt.get("last_purge_age_sec") is not None:
        lines.append(f'netx_port_traffic_purge_age_seconds {pt["last_purge_age_sec"]}')
    web = metrics.get("webcrt") or {}
    if "active_sessions" in web:
        lines.append(f'netx_webcrt_active_sessions {web["active_sessions"]}')
    if "queue_dropped_total" in web:
        lines.append(f'netx_webcrt_queue_dropped_total {web["queue_dropped_total"]}')
    if "ru_maxrss" in metrics:
        lines.append(f'netx_ru_maxrss {metrics["ru_maxrss"]}')
    lines.append("")
    return "\n".join(lines)


@router.get("/metrics")
def metrics_prometheus() -> Response:
    body = _prom_lines(collect_runtime_metrics())
    return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")


@router.get("/metrics/json")
def metrics_json() -> dict[str, Any]:
    return collect_runtime_metrics()
