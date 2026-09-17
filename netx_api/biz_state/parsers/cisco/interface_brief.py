"""Cisco: show ip interface brief / show interfaces status — TODO implement."""

from __future__ import annotations

from typing import Any


def normalize_interface_brief(
    *,
    raw_text: str,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    _ = (raw_text, vendor, device_type, command, params)
    raise NotImplementedError("cisco interface_brief parser not implemented yet")
