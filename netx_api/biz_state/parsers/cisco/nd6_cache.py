"""Cisco: show ipv6 neighbors — TODO implement."""

from __future__ import annotations

from typing import Any

RULE_KEYS: tuple[str, ...] = ()


def normalize_nd6_cache(
    *,
    raw_text: str,
    fsm_tables=None,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    _ = (raw_text, vendor, device_type, command, params, fsm_tables)
    raise NotImplementedError("cisco nd6_cache parser not implemented yet")

normalize_nd6_cache.RULE_KEYS = RULE_KEYS
