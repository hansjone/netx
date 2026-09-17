"""Biz-state CLI parsers: vendor folders + one module per command/metric.

Layout::

    parsers/
      common/          # cross-vendor (lldp, vrf_list, vrf_route_summary)
      zte/             # implemented status tables
      cisco/           # skeleton — add command modules here
      huawei/
      h3c/
      juniper/
      nokia/
      ericsson/

Each vendor package exports ``PARSERS: dict[parser_id, normalize_fn]``.
"""

from __future__ import annotations

from typing import Any, Callable

from .cisco import PARSERS as _CISCO_PARSERS
from .common.lldp_neighbors import normalize_lldp_neighbors
from .common.vrf_list import normalize_vrf_list
from .common.vrf_route_summary import normalize_vrf_route_summary
from .ericsson import PARSERS as _ERICSSON_PARSERS
from .h3c import PARSERS as _H3C_PARSERS
from .huawei import PARSERS as _HUAWEI_PARSERS
from .juniper import PARSERS as _JUNIPER_PARSERS
from .nokia import PARSERS as _NOKIA_PARSERS
from .zte import PARSERS as _ZTE_PARSERS

NormalizeFn = Callable[..., list[dict[str, Any]]]

# Later vendor packages may override earlier ones for the same parser_id.
# Prefer moving shared logic to common/ when two vendors implement the same metric.
_VENDOR_PARSERS: list[dict[str, NormalizeFn]] = [
    _CISCO_PARSERS,
    _HUAWEI_PARSERS,
    _H3C_PARSERS,
    _JUNIPER_PARSERS,
    _NOKIA_PARSERS,
    _ERICSSON_PARSERS,
    _ZTE_PARSERS,  # last so current ZTE status parsers win on overlaps
]

_REGISTRY: dict[str, NormalizeFn] = {
    "lldp_neighbors": normalize_lldp_neighbors,
    "vrf_list": normalize_vrf_list,
    "vrf_route_summary": normalize_vrf_route_summary,
}
for _pack in _VENDOR_PARSERS:
    _REGISTRY.update(_pack)


def get_parser(parser_id: str) -> NormalizeFn | None:
    return _REGISTRY.get(str(parser_id or "").strip())


def registered_parser_ids() -> list[str]:
    return sorted(_REGISTRY.keys())
