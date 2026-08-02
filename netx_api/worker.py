"""Background worker process for long-running schedulers.

Run separately from the API when NETX_RUN_INLINE_SCHEDULERS=false:

    python -m netx_api.worker

Starts: config_sync, lldp_collect, port_traffic tick loops.
UME WS / keepalive remain in the API process (token + alarm coordination).
"""

from __future__ import annotations

import logging
import signal
import threading
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
_log = logging.getLogger("netx.worker")


def main() -> None:
    stop = threading.Event()

    def _handle(_sig: int, _frame: object) -> None:
        _log.info("shutdown signal received")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle)
        except Exception:
            pass

    from .ume_runtime import start_device_schedulers

    start_device_schedulers()
    _log.info("netx worker schedulers started (config_sync, lldp_collect, port_traffic)")

    while not stop.is_set():
        time.sleep(1.0)
    _log.info("netx worker exiting")


if __name__ == "__main__":
    main()
