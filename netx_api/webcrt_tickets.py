"""Short-lived WebCRT WebSocket tickets (avoid putting JWT in query strings)."""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass

_TTL_SEC = 90
_lock = threading.Lock()
_tickets: dict[str, tuple[float, str, frozenset[str]]] = {}


@dataclass(frozen=True)
class TicketInfo:
    user_id: str
    scopes: frozenset[str]


def issue_ws_ticket(*, user_id: str, scopes: frozenset[str], ttl_sec: int = _TTL_SEC) -> tuple[str, int]:
    tid = secrets.token_urlsafe(24)
    exp = time.time() + max(15, int(ttl_sec))
    with _lock:
        _purge_locked()
        _tickets[tid] = (exp, str(user_id), frozenset(scopes))
    return tid, max(15, int(ttl_sec))


def consume_ws_ticket(ticket: str) -> TicketInfo | None:
    raw = str(ticket or "").strip()
    if not raw:
        return None
    with _lock:
        _purge_locked()
        row = _tickets.pop(raw, None)
    if row is None:
        return None
    exp, user_id, scopes = row
    if exp < time.time():
        return None
    return TicketInfo(user_id=user_id, scopes=scopes)


def _purge_locked() -> None:
    now = time.time()
    dead = [k for k, (exp, _, _) in _tickets.items() if exp < now]
    for k in dead:
        _tickets.pop(k, None)
