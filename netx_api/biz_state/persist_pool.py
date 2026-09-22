"""Persist pool: flush spooled commands off the collect/SSH threads."""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any

from ..config import settings

_log = logging.getLogger("netx.biz_state.persist")

_SENTINEL = object()


class PersistPool:
    """Background workers that call ``_flush_spooled_commands``."""

    def __init__(self, *, workers: int | None = None) -> None:
        n = max(
            1,
            int(
                workers
                if workers is not None
                else (getattr(settings, "biz_state_persist_workers", 4) or 4)
            ),
        )
        self._q: queue.Queue[Any] = queue.Queue()
        self._inflight = 0
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._workers: list[threading.Thread] = []
        self._stopped = False
        for i in range(n):
            t = threading.Thread(
                target=self._loop,
                name=f"biz-persist-{i}",
                daemon=True,
            )
            t.start()
            self._workers.append(t)

    def submit(self, batch_id: str, items: list[Any]) -> None:
        if not items or self._stopped:
            return
        payload = (str(batch_id), list(items))
        with self._cv:
            self._inflight += 1
        self._q.put(payload)

    def wait_idle(self, *, timeout: float | None = None) -> bool:
        """Block until queue empty and no in-flight flush. Returns False on timeout."""
        end = None
        if timeout is not None:
            end = time.monotonic() + max(0.0, float(timeout))
        with self._cv:
            while self._inflight > 0 or not self._q.empty():
                remaining = None
                if end is not None:
                    remaining = end - time.monotonic()
                    if remaining <= 0:
                        return False
                self._cv.wait(timeout=remaining)
            return True

    def shutdown(self, *, wait: bool = True) -> None:
        self._stopped = True
        for _ in self._workers:
            self._q.put(_SENTINEL)
        if wait:
            for t in self._workers:
                t.join(timeout=5.0)

    def _loop(self) -> None:
        from .collect_runner import _flush_spooled_commands

        while True:
            job = self._q.get()
            if job is _SENTINEL:
                self._q.task_done()
                break
            batch_id, items = job
            try:
                pending = list(items)
                _flush_spooled_commands(batch_id, pending)
                if pending:
                    _log.warning(
                        "biz_state persist retry leftover=%s batch=%s",
                        len(pending),
                        batch_id,
                    )
                    try:
                        _flush_spooled_commands(batch_id, pending)
                    except Exception:
                        _log.exception(
                            "biz_state persist retry failed batch=%s", batch_id
                        )
            except Exception:
                _log.exception("biz_state persist flush failed batch=%s", batch_id)
            finally:
                with self._cv:
                    self._inflight = max(0, self._inflight - 1)
                    self._cv.notify_all()
                self._q.task_done()


_pool_lock = threading.Lock()
_pool: PersistPool | None = None


def get_persist_pool() -> PersistPool:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = PersistPool()
        return _pool


def shutdown_persist_pool(*, wait: bool = False) -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.shutdown(wait=wait)
            _pool = None
