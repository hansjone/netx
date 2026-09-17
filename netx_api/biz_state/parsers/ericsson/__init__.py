"""Ericsson IPOS parsers (skeleton).

LLDP profile exists; TextFSM may still be stub — see topology_lldp.STUB_PARSER_KEYS.

Add one module per show command, then register in ``PARSERS``.
"""

from __future__ import annotations

from typing import Any, Callable

NormalizeFn = Callable[..., list[dict[str, Any]]]

PARSERS: dict[str, NormalizeFn] = {}

__all__ = ["PARSERS"]
