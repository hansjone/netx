"""WebCRT facade — re-exports channel helpers and session registry."""
from __future__ import annotations

from .webcrt_channel import (
    _decode_bytes,
    _encode_text,
    _normalize_encoding,
    channel_return,
    map_network_cli_enter,
    map_network_cli_keys,
    normalize_cli_transcript,
    prepare_bootstrap_output,
    read_session_log_tail,
    uses_network_cli_keymap,
    webcrt_data_root,
)
from .webcrt_session import (
    WebcrtSession,
    _webcrt_creds_ready,
    active_session_count,
    close_session,
    create_session,
    detach_session,
    find_ssh_session_for_ne,
    get_session,
    list_sessions,
    mark_attached,
    wait_session_ready,
)

__all__ = [
    "WebcrtSession",
    "_decode_bytes",
    "_encode_text",
    "_normalize_encoding",
    "_webcrt_creds_ready",
    "active_session_count",
    "channel_return",
    "close_session",
    "create_session",
    "detach_session",
    "find_ssh_session_for_ne",
    "get_session",
    "list_sessions",
    "map_network_cli_enter",
    "map_network_cli_keys",
    "mark_attached",
    "normalize_cli_transcript",
    "prepare_bootstrap_output",
    "read_session_log_tail",
    "uses_network_cli_keymap",
    "wait_session_ready",
    "webcrt_data_root",
]
