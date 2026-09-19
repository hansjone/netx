"""Huawei VRP parsers.

LLDP already works via ``common.lldp_neighbors`` (``display lldp neighbor``).

Shared ``metric_id`` / FieldDef schemas (cross-vendor compare) are defined in
``profiles.py`` for ZTE first. Stub modules below keep the same normalize
signatures for future Huawei implementations — do **not** register in
``PARSERS`` until TextFSM + prefer_fsm paths exist (avoids NotImplementedError
at collect time)::

    interface_brief.py  arp.py  isis_adjacency.py  nd6_cache.py  bgp_peer.py

When ready, import and add to ``PARSERS``. Prefer same field names as ZTE
(``bgp_peer``, ``ospf_neighbor``, ``vrrp``, ``ip_route``, …).
"""

from __future__ import annotations

from typing import Any, Callable

NormalizeFn = Callable[..., list[dict[str, Any]]]

# Do not register stubs yet — they raise NotImplementedError.
PARSERS: dict[str, NormalizeFn] = {}

__all__ = ["PARSERS"]
