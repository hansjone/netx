"""Shared CLI timeout runner — one pool, force-close Netmiko on timeout."""
from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FuturesTimeout
from contextlib import nullcontext
from typing import Any, Callable, TypeVar

from .cli_budget import acquire_cli_slot
from .config import settings
from .ne_session_factory import close_netmiko_connection

_log = logging.getLogger("netx.cli.timeout")
_pool_lock = threading.Lock()
_pool: ThreadPoolExecutor | None = None

T = TypeVar("T")


def _timeout_pool() -> ThreadPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            # Shared watchdog pool: enough for concurrent timed CLI jobs, not per-task.
            workers = max(4, int(getattr(settings, "cli_timeout_pool_workers", 16) or 16))
            _pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="cli-timeout")
        return _pool


def shutdown_cli_timeout_pool(*, wait: bool = False) -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.shutdown(wait=wait, cancel_futures=True)
            _pool = None


def run_cli_with_timeout(
    fn: Callable[[], T],
    *,
    timeout_sec: float,
    conn_holder: dict[str, Any] | None = None,
    label: str = "cli",
    acquire_budget: bool = True,
) -> T:
    """Run ``fn`` with a wall-clock timeout.

    If ``conn_holder`` is provided and ``fn`` stores the live connection under
    key ``\"conn\"``, a timeout will force-close that connection so the worker
    thread can exit instead of leaking SSH/FD until Netmiko finishes.

    CLI budget is acquired *outside* the timed window so queue wait is not
    counted against the SSH timeout.
    """
    budget = max(1.0, float(timeout_sec))
    slot_cm = acquire_cli_slot() if acquire_budget else nullcontext(True)

    with slot_cm as ok:
        if acquire_budget and not ok:
            raise TimeoutError(f"{label}_cli_budget_unavailable")
        fut: Future[T] = _timeout_pool().submit(fn)
        try:
            return fut.result(timeout=budget)
        except FuturesTimeout as exc:
            conn = None
            if conn_holder is not None:
                conn = conn_holder.get("conn")
                conn_holder["timed_out"] = True
            if conn is not None:
                _log.warning("%s timeout after %.0fs — force-closing netmiko connection", label, budget)
                try:
                    close_netmiko_connection(conn)
                except Exception:  # noqa: BLE001
                    _log.exception("%s force-close failed", label)
            else:
                _log.warning("%s timeout after %.0fs (no live connection to close yet)", label, budget)
            raise TimeoutError(f"{label}_timeout ({int(budget)}s)") from exc
