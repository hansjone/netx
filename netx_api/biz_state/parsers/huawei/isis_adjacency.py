"""Huawei: display isis peer / display isis interface — TODO implement."""

from __future__ import annotations

from typing import Any

RULE_KEYS: tuple[str, ...] = ()


def normalize_isis_adjacency(
    *,
    raw_text: str,
    fsm_tables=None,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    _ = (raw_text, vendor, device_type, command, params, fsm_tables)
    raise NotImplementedError("huawei isis_adjacency parser not implemented yet")

normalize_isis_adjacency.RULE_KEYS = RULE_KEYS
