"""H3C / HP Comware parsers.

LLDP already works via ``common.lldp_neighbors``
(``display lldp neighbor-information list``).

Add one module per display command, then register in ``PARSERS``.
"""

from __future__ import annotations

from typing import Any, Callable

NormalizeFn = Callable[..., list[dict[str, Any]]]

PARSERS: dict[str, NormalizeFn] = {}

__all__ = ["PARSERS"]
