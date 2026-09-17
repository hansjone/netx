"""Parser callbacks keyed by parser_id."""

from __future__ import annotations

from typing import Any, Callable

from ...lldp_shared import NeighborHit, parse_neighbor_output
from .vrf import normalize_vrf_list, normalize_vrf_route_summary
from .zte_status import (
    normalize_arp,
    normalize_bgp_peer,
    normalize_interface_brief,
    normalize_isis_adjacency,
    normalize_nd6_cache,
)

NormalizeFn = Callable[..., list[dict[str, Any]]]


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


_REGISTRY: dict[str, NormalizeFn] = {
    "lldp_neighbors": normalize_lldp_neighbors,
    "vrf_list": normalize_vrf_list,
    "vrf_route_summary": normalize_vrf_route_summary,
    "isis_adjacency": normalize_isis_adjacency,
    "interface_brief": normalize_interface_brief,
    "arp": normalize_arp,
    "nd6_cache": normalize_nd6_cache,
    "bgp_peer": normalize_bgp_peer,
}


def get_parser(parser_id: str) -> NormalizeFn | None:
    return _REGISTRY.get(str(parser_id or "").strip())
