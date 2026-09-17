"""Biz-state CLI parsers: vendor folders + one module per command/metric."""

from __future__ import annotations

from typing import Any, Callable

from .common.lldp_neighbors import normalize_lldp_neighbors
from .common.vrf_list import normalize_vrf_list
from .common.vrf_route_summary import normalize_vrf_route_summary
from .zte import PARSERS as _ZTE_PARSERS

NormalizeFn = Callable[..., list[dict[str, Any]]]

_REGISTRY: dict[str, NormalizeFn] = {
    "lldp_neighbors": normalize_lldp_neighbors,
    "vrf_list": normalize_vrf_list,
    "vrf_route_summary": normalize_vrf_route_summary,
    **_ZTE_PARSERS,
}


def get_parser(parser_id: str) -> NormalizeFn | None:
    return _REGISTRY.get(str(parser_id or "").strip())


def registered_parser_ids() -> list[str]:
    return sorted(_REGISTRY.keys())
