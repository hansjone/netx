"""Atomic op: place side-branch NEs on both sides of spine."""

from __future__ import annotations

import math

from netx_topology_mcp.layout_ops.graph_util import spine_backbone
from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState, OpResult


def place_side_branches(
    state: LayoutState, params: LayoutParams | None = None
) -> OpResult:
    """X-gain + lane push for non-pinned / off-spine access nodes."""
    params = params or LayoutParams()
    st = state.copy()
    pos = dict(st.positions)
    if not pos:
        return OpResult(state=st, moved=set(), op="place_side_branches", note="empty")

    # Amplify X about global center (borrow left/right).
    xs = [pos[n][0] for n in pos]
    cx = sum(xs) / len(xs)
    pos = {n: (cx + (x - cx) * params.x_gain, y) for n, (x, y) in pos.items()}
    moved = set(pos.keys())

    ens = [n for n in pos if st.layers.get(n) == "access"]
    if ens:
        if st.spine:
            spine = spine_backbone([n for n in ens if n in st.spine], st.adj, st.names)
            if len(spine) < 2:
                spine = spine_backbone(ens, st.adj, st.names)
        else:
            spine = spine_backbone(ens, st.adj, st.names)
        spine_set = set(spine)
        st.spine = spine_set
        tang: dict[str, tuple[float, float]] = {}
        for i, n in enumerate(spine):
            if i + 1 < len(spine):
                x0, y0 = pos[n]
                x1, y1 = pos[spine[i + 1]]
            elif i > 0:
                x1, y1 = pos[n]
                x0, y0 = pos[spine[i - 1]]
            else:
                tang[n] = (1.0, 0.0)
                continue
            dx, dy = x1 - x0, y1 - y0
            L = math.hypot(dx, dy) or 1.0
            tang[n] = (dx / L, dy / L)

        for n in ens:
            if n in spine_set or n in st.pinned:
                continue
            sx, sy = pos[n]
            nearest = min(
                spine, key=lambda s: math.hypot(pos[s][0] - sx, pos[s][1] - sy)
            )
            tx, ty = tang.get(nearest, (1.0, 0.0))
            nx, ny = -ty, tx
            px, py = pos[nearest]
            cross = tx * (sy - py) - ty * (sx - px)
            sign = 1.0 if cross >= 0 else -1.0
            cur = math.hypot(sx - px, sy - py)
            target = max(params.lane, cur)
            if cur < 1e-6:
                pos[n] = (px + nx * sign * target, py + ny * sign * target)
            elif cur < target:
                sc = target / cur
                pos[n] = (px + (sx - px) * sc, py + (sy - py) * sc)

    # Pin skeleton after sides placed
    pin = {
        n
        for n in pos
        if st.layers.get(n) in ("agg", "core") or n in st.spine
    }
    st.positions = pos
    st.pinned = pin
    st.last_moved = moved - pin
    return OpResult(
        state=st,
        moved=st.last_moved,
        op="place_side_branches",
        params={"x_gain": params.x_gain, "lane": params.lane, "pinned_n": len(pin)},
        note="side lanes from spine; pinned=agg+core+spine",
    )
