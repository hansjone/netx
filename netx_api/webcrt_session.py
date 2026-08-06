"""WebCRT interactive sessions and process-local registry (facade)."""
from __future__ import annotations

from .webcrt_session_model import WebcrtSession
from .webcrt_session_registry import (
    _webcrt_creds_ready,
    active_session_count,
    close_session,
    close_sessions_for_user,
    create_session,
    detach_session,
    find_ssh_session_for_ne,
    get_session,
    list_sessions,
    mark_attached,
    session_access_allowed,
    wait_session_ready,
)

__all__ = [
    "WebcrtSession",
    "_webcrt_creds_ready",
    "active_session_count",
    "close_session",
    "close_sessions_for_user",
    "create_session",
    "detach_session",
    "find_ssh_session_for_ne",
    "get_session",
    "list_sessions",
    "mark_attached",
    "session_access_allowed",
    "wait_session_ready",
]
