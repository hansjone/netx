"""Parse pool: TextFSM / normalize off the collect (SSH) threads."""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..config import settings

_log = logging.getLogger("netx.biz_state.parse")

_SENTINEL = object()


@dataclass
class AuxRawCapture:
    """CLI-only aux capture passed to the parse worker."""

    key: str
    aux_id: str
    profile_id: str
    parser_id: str
    metric_id: str
    command: str
    textfsm_command: str
    rule_keys: tuple[str, ...] = ()
    raw: str = ""
    raw_rel_path: str = ""
    cache_hit: bool = False
    # Pre-parsed records when cache already had a full parse hit.
    records: list[dict[str, Any]] = field(default_factory=list)
    fsm_tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    ok: bool = True
    error: str = ""


@dataclass
class PrimaryParseJob:
    """One primary command (with aux raws) ready for CPU parse + spool."""

    batch_id: str
    cmd_id: str
    task_item_id: str
    profile_id: str
    parser_id: str
    metric_id: str
    concrete: str
    merged_params: dict[str, Any]
    raw_text: str
    raw_rel_path: str
    textfsm_command: str
    vendor: str
    device_type: str
    enrich_joins: list[Any] = field(default_factory=list)
    aux_captures: list[AuxRawCapture] = field(default_factory=list)
    # Shared across lanes for aux metric de-dupe (may be None).
    persisted: set[tuple[str, str]] | None = None
    cache_lock: Any | None = None
    on_done: Callable[[bool, bool], None] | None = None


class ParsePool:
    """Background workers that parse + enqueue persist for primary jobs."""

    def __init__(self, *, workers: int | None = None) -> None:
        n = max(
            1,
            int(
                workers
                if workers is not None
                else (getattr(settings, "biz_state_parse_workers", 8) or 8)
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
                name=f"biz-parse-{i}",
                daemon=True,
            )
            t.start()
            self._workers.append(t)
        _log.info("biz_state parse pool started workers=%s", n)

    def submit(self, job: PrimaryParseJob) -> None:
        if self._stopped or job is None:
            return
        with self._cv:
            self._inflight += 1
        self._q.put(job)

    def wait_idle(self, *, timeout: float | None = None) -> bool:
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
        from .collect_runner import _run_primary_parse_job

        while True:
            job = self._q.get()
            if job is _SENTINEL:
                self._q.task_done()
                break
            ok = False
            fail = False
            try:
                ok, fail = _run_primary_parse_job(job)
            except Exception:
                fail = True
                _log.exception(
                    "biz_state parse job failed batch=%s cmd=%s",
                    getattr(job, "batch_id", ""),
                    getattr(job, "cmd_id", ""),
                )
                try:
                    if job.on_done:
                        job.on_done(False, True)
                except Exception:
                    pass
            else:
                try:
                    if job.on_done:
                        job.on_done(ok, fail)
                except Exception:
                    _log.exception("biz_state parse on_done failed")
            finally:
                with self._cv:
                    self._inflight = max(0, self._inflight - 1)
                    self._cv.notify_all()
                self._q.task_done()


_pool_lock = threading.Lock()
_pool: ParsePool | None = None


def parse_async_enabled() -> bool:
    return bool(getattr(settings, "biz_state_parse_async", True))


def get_parse_pool() -> ParsePool:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ParsePool()
        return _pool


def shutdown_parse_pool(*, wait: bool = False) -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.shutdown(wait=wait)
            _pool = None
