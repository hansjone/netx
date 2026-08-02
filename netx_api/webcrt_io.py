"""Dedicated thread pool for WebCRT blocking I/O (stdout take / stdin / resize).

Keeps session pumps off the default asyncio executor so HTTP handlers and other
``run_in_executor(None, ...)`` callers are not starved under many CRT sessions.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from .config import settings

_log = logging.getLogger("netx.webcrt.io")
_lock = threading.Lock()
_executor: ThreadPoolExecutor | None = None


def webcrt_io_executor() -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            # Cap workers: each attached WS holds one blocking take_stdout wait.
            n = max(4, min(48, int(getattr(settings, "webcrt_max_sessions", 40) or 40)))
            _executor = ThreadPoolExecutor(max_workers=n, thread_name_prefix="webcrt-io")
            _log.info("webcrt io executor started workers=%s", n)
        return _executor


def shutdown_webcrt_io_executor(*, wait: bool = False) -> None:
    global _executor
    with _lock:
        if _executor is None:
            return
        try:
            _executor.shutdown(wait=wait, cancel_futures=True)
        except TypeError:
            _executor.shutdown(wait=wait)
        except Exception:  # noqa: BLE001
            _log.exception("webcrt io executor shutdown failed")
        _executor = None
