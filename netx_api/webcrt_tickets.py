"""Short-lived WebCRT WebSocket tickets (avoid putting JWT in query strings).

Tickets allow a small number of uses so React StrictMode remounts (dev) can
open a second WebSocket with the same URL without racing a one-shot consume.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass

_TTL_SEC = 90
# React 18 StrictMode mounts effects twice in DEV; keep a spare use.
_MAX_USES = 3
_lock = threading.Lock()
# ticket -> (expires_at, user_id, scopes, uses_remaining)
_tickets: dict[str, tuple[float, str, frozenset[str], int]] = {}


@dataclass(frozen=True)
class TicketInfo:
    user_id: str
    scopes: frozenset[str]


def issue_ws_ticket(*, user_id: str, scopes: frozenset[str], ttl_sec: int = _TTL_SEC) -> tuple[str, int]:
    tid = secrets.token_urlsafe(24)
    exp = time.time() + max(15, int(ttl_sec))
    with _lock:
        _purge_locked()
        _tickets[tid] = (exp, str(user_id), frozenset(scopes), int(_MAX_USES))
    return tid, max(15, int(ttl_sec))


def consume_ws_ticket(ticket: str) -> TicketInfo | None:
    raw = str(ticket or "").strip()
    if not raw:
        return None
    with _lock:
        _purge_locked()
        row = _tickets.get(raw)
        if row is None:
            return None
        exp, user_id, scopes, uses_left = row
        if exp < time.time() or uses_left <= 0:
            _tickets.pop(raw, None)
            return None
        uses_left -= 1
        if uses_left <= 0:
            _tickets.pop(raw, None)
        else:
            _tickets[raw] = (exp, user_id, scopes, uses_left)
    return TicketInfo(user_id=user_id, scopes=scopes)


def _purge_locked() -> None:
    now = time.time()
    dead = [k for k, (exp, _, _, _) in _tickets.items() if exp < now]
    for k in dead:
        _tickets.pop(k, None)
