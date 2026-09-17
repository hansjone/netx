"""Compat shim — prefer ``parsers.common.vrf_list`` / ``vrf_route_summary``."""

from __future__ import annotations

from .common.vrf_list import normalize_vrf_list
from .common.vrf_route_summary import normalize_vrf_route_summary

__all__ = ["normalize_vrf_list", "normalize_vrf_route_summary"]
