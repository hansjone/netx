"""Cisco: show bgp * summary / show ip bgp summary — TODO implement."""

from __future__ import annotations

from typing import Any


def normalize_bgp_peer(
    *,
    raw_text: str,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    _ = (raw_text, vendor, device_type, command, params)
    raise NotImplementedError("cisco bgp_peer parser not implemented yet")
