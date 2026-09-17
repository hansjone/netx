"""Juniper Junos parsers.

LLDP already works via ``common.lldp_neighbors`` (``show lldp neighbors``).

Add one module per show command, then register in ``PARSERS``.
"""

from __future__ import annotations

from typing import Any, Callable

NormalizeFn = Callable[..., list[dict[str, Any]]]

PARSERS: dict[str, NormalizeFn] = {}

__all__ = ["PARSERS"]
