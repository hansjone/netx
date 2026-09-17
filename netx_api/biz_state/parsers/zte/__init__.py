"""ZTE ZXROS status parsers — one module per show command."""

from __future__ import annotations

from typing import Any, Callable

from .arp import is_valid_arp_age, normalize_arp
from .bgp_peer import normalize_bgp_peer
from .interface_brief import normalize_interface_brief
from .isis_adjacency import normalize_isis_adjacency
from .nd6_cache import normalize_nd6_cache

NormalizeFn = Callable[..., list[dict[str, Any]]]

PARSERS: dict[str, NormalizeFn] = {
    "isis_adjacency": normalize_isis_adjacency,
    "interface_brief": normalize_interface_brief,
    "arp": normalize_arp,
    "nd6_cache": normalize_nd6_cache,
    "bgp_peer": normalize_bgp_peer,
}

__all__ = [
    "PARSERS",
    "is_valid_arp_age",
    "normalize_arp",
    "normalize_bgp_peer",
    "normalize_interface_brief",
    "normalize_isis_adjacency",
    "normalize_nd6_cache",
]
