"""Ring/chain layout entry: dual-hub min-rings first, else UME petals.

- ``min_rings``: two agg hubs + ≥2 parallel corridors → nested ellipse bands
  (A1-ULU / PLAU↔ATP style). Prefer when structure says ``agg_bar``.
- ``ume_petals``: rect-perimeter petals around AN anchors (legacy Sugiyama path).
"""

from __future__ import annotations

from netx_topology_mcp.layout_ops.min_rings import build_min_ring_skeleton
from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState, OpResult
from netx_topology_mcp.layout_ops.sugiyama import build_sugiyama_layout


def build_ring_skeleton(state: LayoutState, params: LayoutParams | None = None) -> OpResult:
    params = params or LayoutParams()
    dual = build_min_ring_skeleton(state, params)
    if dual is not None:
        return OpResult(
            state=dual.state,
            moved=dual.moved,
            op="build_ring_skeleton",
            params={**(dual.params or {}), "mode": "min_rings"},
            note=dual.note,
        )
    out = build_sugiyama_layout(state, params)
    st = out.state
    st.meta["rings_mode"] = st.meta.get("rings_mode") or "ume_petals"
    return OpResult(
        state=st,
        moved=out.moved,
        op="build_ring_skeleton",
        params={**(out.params or {}), "mode": st.meta.get("rings_mode", "ume_petals")},
        note=out.note or "UME petal rect-perimeter skeleton",
    )
