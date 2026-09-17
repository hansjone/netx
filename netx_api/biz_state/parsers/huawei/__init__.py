"""Huawei VRP parsers.

LLDP already works via ``common.lldp_neighbors`` (``display lldp neighbor``).

Stub modules (not registered until implemented)::

    interface_brief.py  arp.py  isis_adjacency.py  nd6_cache.py  bgp_peer.py

When ready, import and add to ``PARSERS``.
"""

from __future__ import annotations

from typing import Any, Callable

NormalizeFn = Callable[..., list[dict[str, Any]]]

# Do not register stubs yet — they raise NotImplementedError.
PARSERS: dict[str, NormalizeFn] = {}

__all__ = ["PARSERS"]
