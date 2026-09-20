"""Compat shim — prefer ``parsers.common.vrf_list``."""

from __future__ import annotations

from .common.vrf_list import normalize_vrf_list

__all__ = ["normalize_vrf_list"]
