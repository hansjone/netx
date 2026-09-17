"""LLDP neighbor normalize (multi-vendor via lldp_shared / TextFSM)."""

from __future__ import annotations

from typing import Any

from ....lldp_shared import NeighborHit, parse_neighbor_output


def normalize_lldp_neighbors(
    *,
    raw_text: str,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    hits: list[NeighborHit] = parse_neighbor_output(
        raw_text,
        vendor=vendor,
        device_type=device_type,
        command=command,
    )
    _ = params
    rows: list[dict[str, Any]] = []
    for h in hits:
        rows.append(
            {
                "local_if": str(h.local_port or "").strip(),
                "remote_sys": str(h.remote_name or "").strip(),
                "remote_if": str(h.remote_port or "").strip(),
                "remote_ip": str(h.remote_ip or "").strip(),
                "protocol": str(h.protocol or "lldp").strip() or "lldp",
            }
        )
    return rows
