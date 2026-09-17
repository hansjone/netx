"""Huawei: display arp all / display arp — TODO implement."""

from __future__ import annotations

from typing import Any


def normalize_arp(
    *,
    raw_text: str,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    _ = (raw_text, vendor, device_type, command, params)
    raise NotImplementedError("huawei arp parser not implemented yet")
