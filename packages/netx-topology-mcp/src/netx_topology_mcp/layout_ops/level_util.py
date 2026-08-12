"""Map fabric level / role / name → layout layer key; level y-bands."""

from __future__ import annotations

import math
import re
from typing import Any

from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState, OpResult


_ROLE_TO_LAYER = {
    "external": "external",
    "core": "core",
    "cn": "core",
    "aggregation": "agg",
    "aggregate": "agg",
    "agg": "agg",
    "an": "agg",
    "access": "access",
    "en": "access",
    "edge": "access",
    "cpe": "access",
}

# Top → bottom on canvas (smaller fabric level sits higher).
_BAND_ORDER = ("external", "core", "agg", "access", "other")


def _parse_level(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            value = float(s)
        except ValueError:
            return None
    try:
        lv = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(lv):
        return None
    return lv


def infer_layer(
    name: str,
    role: str | None = None,
    level: float | None = None,
) -> str:
    """Map level / role / name token → external|core|agg|access|other."""
    lv = _parse_level(level)
    if lv is not None:
        maj = int(math.floor(lv))
        if maj <= 0:
            return "external"
        if maj == 1:
            return "core"
        if maj == 2:
            return "agg"
        return "access"
    r = str(role or "").strip().lower()
    if r in _ROLE_TO_LAYER:
        return _ROLE_TO_LAYER[r]
    m = re.search(r"-(CN|AN|EN)(\d*)-", name or "", re.I)
    if not m:
        return "other"
    return {"CN": "core", "AN": "agg", "EN": "access"}[m.group(1).upper()]


def apply_level_bands(
    state: LayoutState,
    params: LayoutParams | None = None,
    *,
    y0: float = 120.0,
    band_gap: float = 320.0,
    pitch: float | None = None,
    preserve_x: bool = True,
    layers: tuple[str, ...] | None = None,
    max_per_row: int = 24,
    row_gap: float | None = None,
) -> OpResult:
    """Snap nodes into horizontal bands by layer (external→…→access).

    Large bands wrap into multiple rows (``max_per_row``) so hundreds of
    access nodes are not crushed onto one y-line. Within a row, x is either
    kept (preserve_x) then gently de-overlapped to ``pitch``, or re-spaced
    by name order.
    """
    params = params or LayoutParams()
    step = float(pitch if pitch is not None else max(params.pitch, 200.0))
    rgap = float(row_gap if row_gap is not None else max(step * 0.85, 170.0))
    per_row = max(4, int(max_per_row or 24))
    order = tuple(layers) if layers else _BAND_ORDER
    by: dict[str, list[str]] = {ly: [] for ly in order}
    for nid in state.positions:
        ly = state.layers.get(nid) or "other"
        if ly not in by:
            ly = "other"
            by.setdefault(ly, [])
        by[ly].append(nid)

    pos = dict(state.positions)
    moved: set[str] = set()
    band_notes: list[dict[str, Any]] = []
    y_cursor = float(y0)

    def _place_row(ids: list[str], y: float) -> None:
        nonlocal moved
        if not ids:
            return
        if preserve_x:
            ids = sorted(ids, key=lambda n: (pos[n][0], state.names.get(n, n)))
            xs = [float(pos[n][0]) for n in ids]
            # Enforce min pitch left→right without reversing order.
            fixed: list[float] = []
            for i, x in enumerate(xs):
                if i == 0:
                    fixed.append(x)
                else:
                    fixed.append(max(x, fixed[-1] + step))
            for n, x in zip(ids, fixed):
                nxt = (x, y)
                if nxt != pos[n]:
                    moved.add(n)
                pos[n] = nxt
        else:
            ids = sorted(ids, key=lambda n: state.names.get(n, n))
            for i, n in enumerate(ids):
                nxt = (40.0 + i * step, y)
                if nxt != pos.get(n):
                    moved.add(n)
                pos[n] = nxt

    for ly in order:
        ids = by.get(ly) or []
        if not ids:
            continue
        if preserve_x:
            ids = sorted(ids, key=lambda n: (pos[n][0], state.names.get(n, n)))
        else:
            ids = sorted(ids, key=lambda n: state.names.get(n, n))
        rows = [ids[i : i + per_row] for i in range(0, len(ids), per_row)]
        y_band0 = y_cursor
        for ri, row in enumerate(rows):
            _place_row(row, y_cursor + ri * rgap)
        band_h = max(0, len(rows) - 1) * rgap
        band_notes.append(
            {
                "layer": ly,
                "count": len(ids),
                "y": y_band0,
                "rows": len(rows),
                "y_max": y_band0 + band_h,
            }
        )
        y_cursor = y_band0 + band_h + float(band_gap)

    out = state.copy()
    out.positions = pos
    out.meta = dict(out.meta or {})
    out.meta["level_bands"] = {
        "bands": band_notes,
        "preserve_x": preserve_x,
        "max_per_row": per_row,
    }
    return OpResult(
        state=out,
        moved=moved,
        op="level_bands",
        params={
            "bands": band_notes,
            "preserve_x": preserve_x,
            "y0": y0,
            "band_gap": band_gap,
            "pitch": step,
            "max_per_row": per_row,
            "moved_n": len(moved),
        },
        note=f"level_bands:{len(band_notes)} bands moved={len(moved)}",
    )


def level_bands_params_from_overrides(overrides: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not overrides:
        return out
    for key, cast in (
        ("y0", float),
        ("band_gap", float),
        ("pitch", float),
        ("row_gap", float),
        ("max_per_row", int),
    ):
        if overrides.get(key) is not None:
            try:
                out[key] = cast(overrides[key])
            except (TypeError, ValueError):
                pass
    if "preserve_x" in overrides:
        out["preserve_x"] = bool(overrides.get("preserve_x"))
    raw = overrides.get("layers")
    if isinstance(raw, (list, tuple)) and raw:
        out["layers"] = tuple(str(x) for x in raw if str(x).strip())
    return out
