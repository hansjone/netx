"""Fabric topology level (layout rank): major.minor, smaller = closer to external/WAN."""

from __future__ import annotations

import math
from typing import Any

# Preset alias → default level (major.0). Sub-tiers use 1.1, 2.1, …
LEVEL_PRESETS: dict[str, float] = {
    "external": 0.0,
    "core": 1.0,
    "aggregation": 2.0,
    "aggregate": 2.0,
    "agg": 2.0,
    "access": 3.0,
    "edge": 4.0,
    "cpe": 4.0,
}

# floor(level) → synced role alias (filters / UI chips)
_MAJOR_TO_ROLE: dict[int, str] = {
    0: "external",
    1: "core",
    2: "aggregation",
    3: "access",
}

_ROLE_VALUES = frozenset(LEVEL_PRESETS) | {"unknown", ""}


def normalize_level(value: Any) -> float | None:
    """Parse level; empty/None → None. Snap to one decimal in [0, 99.9]."""
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip().lower()
        if not s or s in {"unknown", "null", "none"}:
            return None
        if s in LEVEL_PRESETS:
            return LEVEL_PRESETS[s]
        try:
            value = float(s)
        except ValueError as exc:
            raise ValueError("level_invalid") from exc
    try:
        lv = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("level_invalid") from exc
    if not math.isfinite(lv):
        raise ValueError("level_invalid")
    if lv < 0 or lv > 99.9:
        raise ValueError("level_out_of_range")
    return round(lv + 1e-9, 1)


def level_major(level: float | None) -> int | None:
    if level is None:
        return None
    return int(math.floor(float(level)))


def level_to_role(level: float | None) -> str:
    """Denormalized role alias for filters; empty when unclassified."""
    maj = level_major(level)
    if maj is None:
        return ""
    if maj in _MAJOR_TO_ROLE:
        return _MAJOR_TO_ROLE[maj]
    if maj >= 4:
        return "edge"
    return ""


def role_to_level(role: str | None) -> float | None:
    r = str(role or "").strip().lower()
    if not r or r == "unknown":
        return None
    if r in LEVEL_PRESETS:
        return LEVEL_PRESETS[r]
    raise ValueError("role_invalid")


def coerce_level_input(
    *,
    level: Any = None,
    role: Any = None,
    level_provided: bool = False,
    role_provided: bool = False,
) -> float | None | object:
    """Resolve patch/bulk input.

    Returns:
      - float | None: concrete level (None clears)
      - Ellipsis: neither field provided
    """
    if level_provided:
        return normalize_level(level)
    if role_provided:
        return role_to_level(None if role is None else str(role))
    return Ellipsis


def format_level(level: float | None) -> str:
    if level is None:
        return ""
    lv = float(level)
    if abs(lv - round(lv)) < 1e-9:
        return str(int(round(lv)))
    return f"{lv:.1f}".rstrip("0").rstrip(".") if "." in f"{lv:.1f}" else f"{lv:.1f}"


def infer_layer_from_level(
    level: float | None,
    *,
    name: str = "",
    role: str | None = None,
) -> str:
    """Map to layout layer key: external|core|agg|access|other."""
    maj = level_major(level)
    if maj is not None:
        if maj <= 0:
            return "external"
        if maj == 1:
            return "core"
        if maj == 2:
            return "agg"
        if maj >= 3:
            return "access"
    # Fallbacks when unclassified
    r = str(role or "").strip().lower()
    if r in LEVEL_PRESETS:
        return infer_layer_from_level(LEVEL_PRESETS[r])
    import re

    m = re.search(r"-(CN|AN|EN)(\d*)-", name or "", re.I)
    if not m:
        return "other"
    return {"CN": "core", "AN": "agg", "EN": "access"}[m.group(1).upper()]
