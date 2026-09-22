"""Dedicated biz_state collect worker process.

When ``NETX_BIZ_STATE_DEDICATED_WORKERS=true`` (default) and API uses external
schedulers (``NETX_RUN_INLINE_SCHEDULERS=false``), run one or more replicas:

    python -m netx_api.biz_state_worker

Each process enqueues due tasks, claims queued batches (SKIP LOCKED + NE mutex),
runs SSH collect → spool, and drains the persist pool. Scale by starting N
processes on the same host (start_netx.ps1 / .sh do this via replicas).
"""

from __future__ import annotations

import logging
import signal
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
_log = logging.getLogger("netx.biz_state.worker")


def main() -> None:
    from .biz_state_scheduler import start_biz_state_scheduler, stop_biz_state_scheduler
    from .config import settings
    from .scheduler_heartbeat import start_scheduler_heartbeat_publisher

    stop = False

    def _handle(_sig: int, _frame: object) -> None:
        nonlocal stop
        _log.info("shutdown signal received")
        stop = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle)
        except Exception:
            pass

    start_biz_state_scheduler()
    try:
        start_scheduler_heartbeat_publisher(role="biz_state_worker")
    except Exception:
        _log.exception("biz_state worker heartbeat failed to start")

    _log.info(
        "biz_state worker started dedicated=%s max_concurrent=%s",
        bool(getattr(settings, "biz_state_dedicated_workers", True)),
        int(getattr(settings, "biz_state_max_concurrent_tasks", 16) or 16),
    )

    while not stop:
        time.sleep(1.0)

    stop_biz_state_scheduler()
    _log.info("biz_state worker exiting")


if __name__ == "__main__":
    main()
