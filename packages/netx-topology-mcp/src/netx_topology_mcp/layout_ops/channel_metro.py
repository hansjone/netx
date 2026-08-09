"""channel_metro: corridor skeleton → core beam pin → channel soft petals.

Composes proven atoms for giant star / core_bar canvases where ~90% of
nodes sit on deg≤2 corridors.
"""

from __future__ import annotations

from netx_topology_mcp.layout_metrics import count_edge_crossings
from netx_topology_mcp.layout_ops.channels import extract_channels
from netx_topology_mcp.layout_ops.hotspots import fix_overlaps_local
from netx_topology_mcp.layout_ops.pin_beam import pin_beam_rigid
from netx_topology_mcp.layout_ops.ring_faces import eject_intruders, extract_ring_faces
from netx_topology_mcp.layout_ops.skeleton import build_skeleton
from netx_topology_mcp.layout_ops.soft_petals import soft_petals_greedy
from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState, OpResult
from netx_topology_mcp.layout_ops.transforms import normalize_origin


def build_channel_metro_skeleton(
    state: LayoutState, params: LayoutParams | None = None
) -> OpResult:
    """Compose: Tutte skeleton → pin_beam → soft_petals → ring eject → fix ov."""
    params = params or LayoutParams()
    channels = extract_channels(state)
    rings = extract_ring_faces(state, max_len=5, max_cycles=40)

    sk = build_skeleton(state, params)
    st = sk.state

    beam = pin_beam_rigid(st, params)
    st = beam.state

    petals = soft_petals_greedy(st, params)
    st = petals.state

    cores = {n for n, ly in st.layers.items() if ly == "core"}
    aggs = {n for n, ly in st.layers.items() if ly == "agg"}
    st.positions = eject_intruders(
        dict(st.positions),
        rings,
        push=max(params.side * 0.25, 36.0),
        protected=cores | aggs,
    )

    st = fix_overlaps_local(st, params).state
    st = normalize_origin(st, params).state

    cross = count_edge_crossings(st.positions, st.links)
    st.meta = dict(st.meta or {})
    st.meta["rings_mode"] = "channel_metro"
    st.meta["channel_metro"] = {
        "channels": len(channels),
        "rings": len(rings),
        "pin_beam": beam.note,
        "soft_petals": petals.note,
        "crossings": cross,
        "skeleton": sk.note,
    }
    return OpResult(
        state=st,
        moved=set(st.positions.keys()),
        op="build_channel_metro_skeleton",
        params={
            "channel_count": len(channels),
            "ring_count": len(rings),
            "crossings": cross,
            "pin_beam": beam.params,
            "soft_petals": petals.params,
        },
        note=f"channel_metro ch={len(channels)} {beam.note}; {petals.note} x={cross}",
    )
