"""Cisco IOS / NX-OS / XR parsers.

LLDP already works via ``common.lldp_neighbors`` (profile ``parser_id=lldp_neighbors``).

Shared ``metric_id`` schemas live in ``profiles.py`` (ZTE is the reference
implementation). Stub modules keep normalize signatures aligned — do **not**
register in ``PARSERS`` until implemented::

    interface_brief.py  arp.py  isis_adjacency.py  nd6_cache.py  bgp_peer.py

When ready, import and add to ``PARSERS``. If ``parser_id`` already exists for
another vendor, prefer a vendor-dispatch in ``common/`` or a prefixed parser_id.
"""

from __future__ import annotations

from typing import Any, Callable

NormalizeFn = Callable[..., list[dict[str, Any]]]

# Do not register stubs yet — they raise NotImplementedError.
PARSERS: dict[str, NormalizeFn] = {}

__all__ = ["PARSERS"]
