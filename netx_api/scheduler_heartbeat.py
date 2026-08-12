"""Cross-process device-scheduler heartbeat for API /metrics when worker is split.

When ``NETX_RUN_INLINE_SCHEDULERS=false``, collectors live in ``python -m netx_api.worker``.
The API process cannot see their threads; the worker publishes a small JSON heartbeat
that ``/metrics`` and ``/health/ready`` can read.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from .config import settings

_log = logging.getLogger("netx.scheduler.heartbeat")

_HB_LOCK = threading.Lock()
_publisher_stop = threading.Event()
_publisher_thread: threading.Thread | None = None

# Consider worker gone if heartbeat older than this (worker publishes every ~5s).
DEFAULT_STALE_SEC = 45.0


def heartbeat_path() -> Path:
    raw = str(getattr(settings, "scheduler_heartbeat_path", "") or "").strip()
    if raw:
        return Path(raw)
    return Path("data") / "runtime" / "scheduler_heartbeat.json"


def local_device_scheduler_status(*, role: str = "unknown") -> dict[str, Any]:
    """In-process scheduler thread status (API inline or worker)."""
    out: dict[str, Any] = {
        "pid": os.getpid(),
        "role": role,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "updated_mono": time.monotonic(),
    }
    try:
        from .config_sync_scheduler import config_sync_scheduler_status

        out["config_sync"] = config_sync_scheduler_status()
    except Exception:  # noqa: BLE001
        out["config_sync"] = {"running": False, "error": "unavailable"}
    try:
        from .lldp_collect_scheduler import lldp_collect_scheduler_status

        out["lldp_collect"] = lldp_collect_scheduler_status()
    except Exception:  # noqa: BLE001
        out["lldp_collect"] = {"running": False, "error": "unavailable"}
    try:
        from .ne_collect_scheduler import ne_collect_scheduler_status

        out["ne_collect"] = ne_collect_scheduler_status()
    except Exception:  # noqa: BLE001
        out["ne_collect"] = {"running": False, "error": "unavailable"}
    try:
        from .port_traffic_scheduler import port_traffic_scheduler_status

        out["port_traffic"] = port_traffic_scheduler_status()
    except Exception:  # noqa: BLE001
        out["port_traffic"] = {"running": False, "error": "unavailable"}
    try:
        from .fabric_reconcile_scheduler import fabric_reconcile_scheduler_status

        out["fabric_reconcile"] = fabric_reconcile_scheduler_status()
    except Exception:  # noqa: BLE001
        out["fabric_reconcile"] = {"running": False, "error": "unavailable"}
    return out


def publish_scheduler_heartbeat(*, role: str = "worker") -> Path:
    """Atomically write local scheduler status for the API process to read."""
    path = heartbeat_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = local_device_scheduler_status(role=role)
    # Prefer wall-clock for cross-process age; drop mono (not comparable across processes).
    payload.pop("updated_mono", None)
    payload["updated_at_epoch"] = time.time()
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    fd, tmp_name = tempfile.mkstemp(prefix=".hb-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path


def read_scheduler_heartbeat(*, max_age_sec: float = DEFAULT_STALE_SEC) -> dict[str, Any] | None:
    path = heartbeat_path()
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except FileNotFoundError:
        return None
    except Exception:  # noqa: BLE001
        _log.debug("scheduler heartbeat read failed path=%s", path, exc_info=True)
        return None
    if not isinstance(data, dict):
        return None
    epoch = float(data.get("updated_at_epoch") or 0)
    age = (time.time() - epoch) if epoch > 0 else None
    data["age_sec"] = round(age, 1) if age is not None else None
    data["stale"] = bool(age is None or age > float(max_age_sec))
    return data


def resolve_device_scheduler_metrics() -> dict[str, Any]:
    """API-facing view: local threads when inline, else worker heartbeat file."""
    inline = bool(getattr(settings, "run_inline_schedulers", True))
    if inline:
        local = local_device_scheduler_status(role="api_inline")
        local.pop("updated_mono", None)
        return {
            "mode": "inline",
            "source": "local",
            "stale": False,
            "hint": None,
            **local,
        }

    hb = read_scheduler_heartbeat()
    if hb is None:
        return {
            "mode": "external_worker",
            "source": "missing",
            "stale": True,
            "hint": "run `python -m netx_api.worker` (start_netx scripts do this by default)",
            "pid": None,
            "role": None,
            "config_sync": {"running": False},
            "lldp_collect": {"running": False},
            "port_traffic": {"running": False},
        }

    return {
        "mode": "external_worker",
        "source": "heartbeat",
        "stale": bool(hb.get("stale")),
        "hint": "worker heartbeat stale — check worker.pid / restart start_netx"
        if hb.get("stale")
        else None,
        "pid": hb.get("pid"),
        "role": hb.get("role"),
        "updated_at": hb.get("updated_at"),
        "age_sec": hb.get("age_sec"),
        "config_sync": hb.get("config_sync") or {"running": False},
        "lldp_collect": hb.get("lldp_collect") or {"running": False},
        "port_traffic": hb.get("port_traffic") or {"running": False},
    }


def _publisher_loop(*, role: str, interval_sec: float) -> None:
    _log.info("scheduler heartbeat publisher started role=%s interval=%ss path=%s", role, interval_sec, heartbeat_path())
    while not _publisher_stop.is_set():
        try:
            publish_scheduler_heartbeat(role=role)
        except Exception:  # noqa: BLE001
            _log.exception("scheduler heartbeat publish failed")
        _publisher_stop.wait(max(1.0, float(interval_sec)))
    _log.info("scheduler heartbeat publisher stopped")


def start_scheduler_heartbeat_publisher(*, role: str = "worker", interval_sec: float = 5.0) -> None:
    """Daemon thread: keep heartbeat fresh while this process owns device schedulers."""
    global _publisher_thread
    with _HB_LOCK:
        if _publisher_thread and _publisher_thread.is_alive():
            return
        _publisher_stop.clear()
        _publisher_thread = threading.Thread(
            target=_publisher_loop,
            kwargs={"role": role, "interval_sec": interval_sec},
            name="scheduler-heartbeat",
            daemon=True,
        )
        _publisher_thread.start()
    try:
        publish_scheduler_heartbeat(role=role)
    except Exception:  # noqa: BLE001
        _log.exception("initial scheduler heartbeat publish failed")


def stop_scheduler_heartbeat_publisher() -> None:
    _publisher_stop.set()
