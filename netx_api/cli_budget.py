"""Global CLI concurrency budget — shared across discover / collect / config_sync / port_traffic."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

from .config import settings

_lock = threading.Lock()
_sem: threading.BoundedSemaphore | None = None
_limit = 0
_in_use = 0
_in_use_lock = threading.Lock()


def _ensure_sem() -> threading.BoundedSemaphore:
    global _sem, _limit
    with _lock:
        want = max(1, int(getattr(settings, "cli_max_concurrent", 20) or 20))
        if _sem is None or want != _limit:
            _sem = threading.BoundedSemaphore(want)
            _limit = want
        return _sem


def cli_budget_status() -> dict[str, int]:
    """Snapshot for health/metrics."""
    _ensure_sem()
    with _in_use_lock:
        used = _in_use
    return {"limit": _limit, "in_use": used, "available": max(0, _limit - used)}


def clamp_cli_workers(requested: int, *, hard_cap: int) -> int:
    """Clamp a feature concurrency against the global CLI budget and a hard cap."""
    budget = max(1, int(getattr(settings, "cli_max_concurrent", 20) or 20))
    return max(1, min(int(hard_cap), int(requested or 1), budget))


@contextmanager
def acquire_cli_slot(*, blocking: bool = True, timeout: float | None = None) -> Iterator[bool]:
    """Acquire one global CLI slot for an SSH/Netmiko session.

    Yields True when acquired. When ``blocking=False`` and the budget is full,
    yields False without waiting.
    """
    global _in_use
    sem = _ensure_sem()
    acquired = False
    if blocking:
        if timeout is None:
            sem.acquire()
            acquired = True
        else:
            acquired = bool(sem.acquire(timeout=float(timeout)))
    else:
        acquired = bool(sem.acquire(blocking=False))
    if acquired:
        with _in_use_lock:
            _in_use += 1
    try:
        yield acquired
    finally:
        if acquired:
            with _in_use_lock:
                _in_use = max(0, _in_use - 1)
            sem.release()
