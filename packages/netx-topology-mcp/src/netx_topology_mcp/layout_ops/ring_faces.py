"""Minimal ring faces: detect chordless cycles and piercing by foreign edges.

Stage-1/2 use this to keep ring interiors hollow (metro face integrity).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from netx_topology_mcp.layout_metrics import segments_properly_intersect
from netx_topology_mcp.layout_ops.state import LayoutState
from netx_topology_mcp.layout_topology_quality import (
    RING_MAX_LEN,
    find_short_chordless_cycles,
)


@dataclass(frozen=True)
class RingFace:
    node_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"n": len(self.node_ids), "node_ids": list(self.node_ids)}


def extract_ring_faces(
    state: LayoutState,
    *,
    max_len: int = min(6, RING_MAX_LEN),
    max_cycles: int = 80,
) -> list[RingFace]:
    cycles = find_short_chordless_cycles(
        state.adj, max_len=max_len, max_cycles=max_cycles
    )
    faces = [RingFace(tuple(c)) for c in cycles]
    faces.sort(key=lambda f: (len(f.node_ids), f.node_ids[0] if f.node_ids else ""))
    return faces


def _point_in_poly(
    x: float, y: float, poly: list[tuple[float, float]]
) -> bool:
    """Ray casting; boundary counts as outside (conservative)."""
    n = len(poly)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / (yj - yi + 1e-15) + xi
        ):
            inside = not inside
        j = i
    return inside


def ring_polygon(
    face: RingFace, pos: dict[str, tuple[float, float]]
) -> list[tuple[float, float]] | None:
    pts = [pos[n] for n in face.node_ids if n in pos]
    if len(pts) < 3:
        return None
    return pts


def count_ring_pierces(
    pos: dict[str, tuple[float, float]],
    links: list[tuple[str, str]],
    faces: list[RingFace],
) -> dict[str, Any]:
    """Foreign edges that properly cross a ring boundary edge."""
    pierced = 0
    pierce_x = 0
    for face in faces:
        rset = set(face.node_ids)
        poly = ring_polygon(face, pos)
        if not poly:
            continue
        n = len(face.node_ids)
        ring_edges = []
        for i in range(n):
            a, b = face.node_ids[i], face.node_ids[(i + 1) % n]
            if a in pos and b in pos:
                ring_edges.append((a, b))
        hit = False
        for a, b in links:
            if a not in pos or b not in pos:
                continue
            # foreign if at least one endpoint outside ring
            if a in rset and b in rset:
                continue
            p1, p2 = pos[a], pos[b]
            for u, v in ring_edges:
                if len({a, b, u, v}) < 4:
                    continue
                if segments_properly_intersect(p1, p2, pos[u], pos[v]):
                    pierce_x += 1
                    hit = True
        if hit:
            pierced += 1
    return {
        "ring_count": len(faces),
        "rings_pierced": pierced,
        "pierce_crossings": pierce_x,
    }


def place_ring_rectangle(
    face: RingFace,
    *,
    center: tuple[float, float],
    width: float,
    height: float,
    start_angle: float = -math.pi / 2,
) -> dict[str, tuple[float, float]]:
    """Place ring nodes on an axis-aligned rectangle (hollow face)."""
    nodes = list(face.node_ids)
    n = len(nodes)
    if n < 3:
        return {}
    cx, cy = center
    hw, hh = width / 2, height / 2
    # Perimeter parametrization 0..1 around rectangle
    perim = 2 * (width + height)
    out: dict[str, tuple[float, float]] = {}
    for i, nid in enumerate(nodes):
        t = (i / n + (start_angle + math.pi / 2) / (2 * math.pi)) % 1.0
        d = t * perim
        if d <= width:
            x, y = cx - hw + d, cy - hh
        elif d <= width + height:
            x, y = cx + hw, cy - hh + (d - width)
        elif d <= 2 * width + height:
            x, y = cx + hw - (d - width - height), cy + hh
        else:
            x, y = cx - hw, cy + hh - (d - 2 * width - height)
        out[nid] = (x, y)
    return out


def eject_intruders(
    pos: dict[str, tuple[float, float]],
    faces: list[RingFace],
    *,
    push: float = 40.0,
    protected: set[str] | None = None,
) -> dict[str, tuple[float, float]]:
    """Push non-ring nodes out of ring polygons."""
    protected = protected or set()
    out = dict(pos)
    for face in faces:
        rset = set(face.node_ids)
        poly = ring_polygon(face, out)
        if not poly:
            continue
        cx = sum(p[0] for p in poly) / len(poly)
        cy = sum(p[1] for p in poly) / len(poly)
        for nid, (x, y) in list(out.items()):
            if nid in rset or nid in protected:
                continue
            if not _point_in_poly(x, y, poly):
                continue
            dx, dy = x - cx, y - cy
            dist = math.hypot(dx, dy) or 1.0
            out[nid] = (cx + dx / dist * (dist + push), cy + dy / dist * (dist + push))
    return out


def ring_faces_report(state: LayoutState) -> dict[str, Any]:
    faces = extract_ring_faces(state)
    pierces = count_ring_pierces(state.positions, state.links, faces)
    return {
        **pierces,
        "sample": [f.as_dict() for f in faces[:10]],
        "tip": "最小环应中空；channel_metro / protect_rings 优先降 pierced。",
    }
