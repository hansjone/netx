"""Periodic Fabric inventory reconcile: dangling detach + fully-orphaned GC."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .config import settings
from .db import SessionLocal

_log = logging.getLogger("netx.fabric_reconcile.scheduler")
_stop = threading.Event()
_thread: threading.Thread | None = None
_last_run_mono: float = 0.0
_last_stats: dict[str, Any] | None = None


def run_fabric_reconcile_once() -> dict[str, Any]:
    """Detach dangling managed/UME refs and purge fully orphaned fabric nodes."""
    global _last_run_mono, _last_stats
    from .topology_inventory_lifecycle import reconcile_dangling_fabric_links

    db = SessionLocal()
    try:
        stats = reconcile_dangling_fabric_links(db, sweep_orphans=True)
        db.commit()
        _last_run_mono = time.monotonic()
        _last_stats = dict(stats)
        purged = int(stats.get("purged_orphans") or 0)
        dangling = int(stats.get("dangling_managed_refs") or 0) + int(
            stats.get("dangling_ume_refs") or 0
        )
        if purged or dangling:
            _log.info(
                "fabric reconcile done dangling_m=%s dangling_u=%s purged_orphans=%s edges_deleted=%s",
                stats.get("dangling_managed_refs"),
                stats.get("dangling_ume_refs"),
                purged,
                stats.get("edges_deleted"),
            )
        else:
            _log.debug("fabric reconcile idle")
        return stats
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _loop() -> None:
    interval = max(300, int(getattr(settings, "fabric_reconcile_interval_sec", 21600) or 21600))
    # Short first delay so API/worker boot is not blocked by a full-table scan.
    startup_delay = min(120, interval)
    _log.info(
        "fabric reconcile scheduler started interval=%ss startup_delay=%ss",
        interval,
        startup_delay,
    )
    _stop.wait(startup_delay)
    while not _stop.is_set():
        try:
            if bool(getattr(settings, "fabric_reconcile_scheduler_enabled", True)):
                run_fabric_reconcile_once()
        except Exception:
            _log.exception("fabric reconcile tick failed")
        _stop.wait(interval)
    _log.info("fabric reconcile scheduler stopped")


def start_fabric_reconcile_scheduler() -> None:
    global _thread
    if not bool(getattr(settings, "fabric_reconcile_scheduler_enabled", True)):
        _log.info("fabric reconcile scheduler disabled by settings")
        return
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="fabric-reconcile-scheduler", daemon=True)
    _thread.start()


def stop_fabric_reconcile_scheduler() -> None:
    _stop.set()


def fabric_reconcile_scheduler_status() -> dict[str, Any]:
    now = time.monotonic()
    return {
        "running": bool(_thread is not None and _thread.is_alive()),
        "last_run_age_sec": (now - _last_run_mono) if _last_run_mono else None,
        "last_stats": dict(_last_stats) if _last_stats else None,
        "interval_sec": max(
            300, int(getattr(settings, "fabric_reconcile_interval_sec", 21600) or 21600)
        ),
        "enabled": bool(getattr(settings, "fabric_reconcile_scheduler_enabled", True)),
    }
