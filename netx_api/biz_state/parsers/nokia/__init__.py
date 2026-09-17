"""Nokia / Alcatel (SROS / AOS) parsers.

LLDP already works via ``common.lldp_neighbors``
(``show system lldp neighbor`` / AOS ``show lldp remote-system``).

Add one module per show command, then register in ``PARSERS``.
"""

from __future__ import annotations

from typing import Any, Callable

NormalizeFn = Callable[..., list[dict[str, Any]]]

PARSERS: dict[str, NormalizeFn] = {}

__all__ = ["PARSERS"]
