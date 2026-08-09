"""Multi-layer orthogonal metro layout (H/V edges, parallel tracks).

Design rules (canvas has no Steiner bends):
1. Core beam stays on track 0 (horizontal).
2. Deg≤2 corridors grow **horizontally** on their own parallel track.
3. Vertical edges are short stubs (beam→track or track↔track at one column).
4. Non-triangle edges are forced to share x or y; triangle hyps may stay diagonal.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from netx_topology_mcp.layout_metrics import (
    REC_CENTER_DX,
    REC_CENTER_DY,
    count_edge_crossings,
    node_footprint,
)
from netx_topology_mcp.layout_ops.channels import extract_channels
from netx_topology_mcp.layout_ops.hotspots import overlapping_nodes
from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState, OpResult
from netx_topology_mcp.layout_ops.transforms import normalize_origin

_TOL_PX = 8.0


def _axis_ok(a: tuple[float, float], b: tuple[float, float], tol: float = _TOL_PX) -> bool:
    return abs(a[0] - b[0]) <= tol or abs(a[1] - b[1]) <= tol


def _segment_clear(
    pos: dict[str, tuple[float, float]],
    a: str,
    b: str,
    *,
    thr: float = 40.0,
    skip: set[str] | None = None,
) -> bool:
    """True if no other node sits on the open segment a—b (edge occlusion)."""
    from netx_topology_mcp.layout_metrics import point_segment_dist

    if a not in pos or b not in pos:
        return True
    pa, pb = pos[a], pos[b]
    skip = skip or set()
    for n, p in pos.items():
        if n == a or n == b or n in skip:
            continue
        d, t = point_segment_dist(p, pa, pb)
        if d < thr and 0.05 < t < 0.95:
            return False
    return True


def _pick_beam(state: LayoutState) -> list[str]:
    """Core layer hubs, else top-degree pair that are neighbors, else max hub."""
    cores = [n for n, ly in state.layers.items() if ly == "core" and n in state.positions]
    if len(cores) >= 2:
        best: tuple[int, str, str] | None = None
        for i, a in enumerate(cores):
            for b in cores[i + 1 :]:
                if b in (state.adj.get(a) or set()):
                    score = len(state.adj.get(a) or ()) + len(state.adj.get(b) or ())
                    if best is None or score > best[0]:
                        best = (score, a, b)
        if best:
            return [best[1], best[2]]
        cores.sort(key=lambda n: -len(state.adj.get(n) or ()))
        return cores[:2]
    if len(cores) == 1:
        return [cores[0]]
    ranked = sorted(
        state.positions.keys(),
        key=lambda n: (-len(state.adj.get(n) or ()), state.names.get(n) or n),
    )
    if not ranked:
        return []
    hub = ranked[0]
    nbrs = sorted(
        state.adj.get(hub) or (),
        key=lambda n: (-len(state.adj.get(n) or ()), state.names.get(n) or n),
    )
    if nbrs and len(state.adj.get(hub) or ()) >= 2:
        return [hub, nbrs[0]]
    return [hub]


def _edge_key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def _dist2(a: tuple[float, float], b: tuple[float, float]) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def _triangles(adj: dict[str, set[str]]) -> list[tuple[str, str, str]]:
    nodes = sorted(adj)
    idx = {n: i for i, n in enumerate(nodes)}
    seen: set[tuple[str, str, str]] = set()
    out: list[tuple[str, str, str]] = []
    for u in nodes:
        nbrs = sorted(adj.get(u) or (), key=lambda n: idx.get(n, 0))
        for i, v in enumerate(nbrs):
            if idx[v] <= idx[u]:
                continue
            for w in nbrs[i + 1 :]:
                if w in (adj.get(v) or ()):
                    t = tuple(sorted((u, v, w)))
                    if t not in seen:
                        seen.add(t)  # type: ignore[arg-type]
                        out.append(t)  # type: ignore[arg-type]
    return out


def _triangle_hyps(
    pos: dict[str, tuple[float, float]],
    adj: dict[str, set[str]],
) -> set[tuple[str, str]]:
    """One skippable diagonal per K3: prefer edge between two highest-degree nodes."""
    hyps: set[tuple[str, str]] = set()
    for a, b, c in _triangles(adj):
        deg = {n: len(adj.get(n) or ()) for n in (a, b, c)}
        edges = [
            (_edge_key(a, b), deg[a] + deg[b], _dist2(pos[a], pos[b])),
            (_edge_key(a, c), deg[a] + deg[c], _dist2(pos[a], pos[c])),
            (_edge_key(b, c), deg[b] + deg[c], _dist2(pos[b], pos[c])),
        ]
        # highest combined degree, then longest
        edges.sort(key=lambda t: (-t[1], -t[2]))
        hyps.add(edges[0][0])
    return hyps


def _components(state: LayoutState) -> list[list[str]]:
    seen: set[str] = set()
    out: list[list[str]] = []
    for n in sorted(state.positions):
        if n in seen:
            continue
        block: list[str] = []
        q = deque([n])
        seen.add(n)
        while q:
            u = q.popleft()
            block.append(u)
            for v in state.adj.get(u) or ():
                if v in state.positions and v not in seen:
                    seen.add(v)
                    q.append(v)
        out.append(block)
    out.sort(key=lambda b: (-len(b), min(b)))
    return out


def _cell_free(
    pos: dict[str, tuple[float, float]],
    xy: tuple[float, float],
    *,
    skip: str | None,
    min_dx: float,
    min_dy: float,
) -> bool:
    x, y = xy
    for nid, (px, py) in pos.items():
        if nid == skip:
            continue
        if abs(px - x) < min_dx * 0.92 and abs(py - y) < min_dy * 0.92:
            return False
    return True


def _snap_grid(
    x: float, y: float, *, ox: float, oy: float, pitch: float, side: float
) -> tuple[float, float]:
    col = round((x - ox) / pitch)
    row = round((y - oy) / side)
    return (ox + col * pitch, oy + row * side)


def _axis_score(
    pos: dict[str, tuple[float, float]],
    links: list[tuple[str, str]],
    *,
    skip: set[tuple[str, str]],
) -> int:
    s = 0
    for a, b in links:
        if a not in pos or b not in pos:
            continue
        if _edge_key(a, b) in skip:
            continue
        if _axis_ok(pos[a], pos[b]):
            s += 1
    return s


def _find_free(
    pos: dict[str, tuple[float, float]],
    base: tuple[float, float],
    *,
    skip: str | None,
    pitch: float,
    side: float,
    ox: float,
    oy: float,
    prefer_h: bool = True,
) -> tuple[float, float]:
    """Nearest free grid cell near base, preferring H then V offsets."""
    bx, by = _snap_grid(base[0], base[1], ox=ox, oy=oy, pitch=pitch, side=side)
    if _cell_free(pos, (bx, by), skip=skip, min_dx=pitch, min_dy=side):
        return (bx, by)
    order = (
        [(1, 0), (-1, 0), (0, 1), (0, -1)]
        if prefer_h
        else [(0, 1), (0, -1), (1, 0), (-1, 0)]
    )
    for s in range(1, 28):
        for dx, dy in order:
            cand = _snap_grid(
                bx + dx * s * pitch, by + dy * s * side, ox=ox, oy=oy, pitch=pitch, side=side
            )
            if _cell_free(pos, cand, skip=skip, min_dx=pitch, min_dy=side):
                return cand
        # diagonals last (only for parking, not for edge endpoints ideally)
        for dx, dy in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
            cand = _snap_grid(
                bx + dx * s * pitch, by + dy * s * side, ox=ox, oy=oy, pitch=pitch, side=side
            )
            if _cell_free(pos, cand, skip=skip, min_dx=pitch, min_dy=side):
                return cand
    return (bx, by)


def _fp_overlap(
    pos: dict[str, tuple[float, float]],
    names: dict[str, str],
    a: str,
    b: str,
) -> bool:
    ax, ay = pos[a]
    bx, by = pos[b]
    af = node_footprint(names.get(a, a))
    bf = node_footprint(names.get(b, b))
    aa = (ax + af[0], ay + af[1], ax + af[2], ay + af[3])
    bb = (bx + bf[0], by + bf[1], bx + bf[2], by + bf[3])
    return aa[0] < bb[2] and aa[2] > bb[0] and aa[1] < bb[3] and aa[3] > bb[1]


def separate_overlaps_ortho(
    state: LayoutState,
    params: LayoutParams | None = None,
) -> OpResult:
    """Slide nodes on H/V only until footprint overlaps are gone (axis-preserving)."""
    params = params or LayoutParams()
    st = state.copy()
    pitch = max(float(params.pitch), REC_CENTER_DX)
    side = max(float(params.side), REC_CENTER_DY)
    pos = dict(st.positions)
    names = st.names
    deg = {n: len(st.adj.get(n) or ()) for n in pos}
    cores = {n for n, ly in st.layers.items() if ly == "core" and n in pos}
    links = list(st.links)
    skip: set[tuple[str, str]] = set()
    moved: set[str] = set()

    for _ in range(24):
        hits = overlapping_nodes(
            LayoutState(
                positions=pos,
                names=names,
                layers=st.layers,
                links=links,
                adj=st.adj,
            )
        )
        if not hits:
            break
        # pair-wise: move lower-degree non-core
        ids = sorted(hits)
        progressed = False
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                if a not in pos or b not in pos:
                    continue
                if not _fp_overlap(pos, names, a, b):
                    continue
                if a in cores and b not in cores:
                    mov = b
                elif b in cores and a not in cores:
                    mov = a
                elif deg.get(a, 0) <= deg.get(b, 0):
                    mov = a
                else:
                    mov = b
                mx, my = pos[mov]
                base = _axis_score(pos, links, skip=skip)
                placed = False
                for s in range(1, 20):
                    for cand in (
                        (mx + s * pitch, my),
                        (mx - s * pitch, my),
                        (mx, my + s * side),
                        (mx, my - s * side),
                    ):
                        trial = dict(pos)
                        trial[mov] = cand
                        if _axis_score(trial, links, skip=skip) < base - 1:
                            continue
                        # no footprint hit with anyone
                        ok = True
                        for other in trial:
                            if other == mov:
                                continue
                            if _fp_overlap(trial, names, mov, other):
                                ok = False
                                break
                        if ok:
                            pos[mov] = cand
                            moved.add(mov)
                            placed = True
                            progressed = True
                            break
                    if placed:
                        break
                if not placed:
                    # accept axis-preserving slide even if still tight — next round
                    for s in range(1, 8):
                        cand = (mx + s * pitch, my)
                        trial = dict(pos)
                        trial[mov] = cand
                        if _axis_score(trial, links, skip=skip) >= base - 1:
                            pos[mov] = cand
                            moved.add(mov)
                            progressed = True
                            break
        if not progressed:
            break

    st.positions = pos
    return OpResult(
        state=st,
        moved=moved,
        op="separate_overlaps_ortho",
        params={"moved_n": len(moved)},
        note=f"ortho_sep moved={len(moved)} ov_left={len(overlapping_nodes(st))}",
    )


def _place_component(
    state: LayoutState,
    nodes: list[str],
    params: LayoutParams,
    *,
    skip_triangles: bool = True,
    origin: tuple[float, float] = (0.0, 0.0),
) -> dict[str, tuple[float, float]]:
    pitch = max(float(params.pitch), REC_CENTER_DX)
    side = max(float(params.side), REC_CENTER_DY)
    sub_ids = set(nodes)
    adj: dict[str, set[str]] = {
        n: {m for m in (state.adj.get(n) or ()) if m in sub_ids} for n in sub_ids
    }
    deg = {n: len(adj[n]) for n in sub_ids}
    links = [(a, b) for a, b in state.links if a in sub_ids and b in sub_ids]

    mini = LayoutState(
        positions={n: state.positions[n] for n in sub_ids},
        names={n: state.names.get(n, n) for n in sub_ids},
        layers={n: state.layers.get(n, "other") for n in sub_ids},
        links=links,
        adj=adj,
    )
    beam = [n for n in _pick_beam(mini) if n in sub_ids]
    if not beam:
        beam = [min(sub_ids)]
    beam_set = set(beam)
    ox, oy = origin

    pos: dict[str, tuple[float, float]] = {}

    # --- 1) beam on track 0 ---
    if len(beam) >= 2:
        gap = max(2.0, 1.0 + 0.2 * max(deg[beam[0]], deg[beam[1]]))
        pos[beam[0]] = (ox, oy)
        pos[beam[1]] = (ox + pitch * gap, oy)
        for i, n in enumerate(beam[2:]):
            pos[n] = (pos[beam[1]][0] + pitch * (i + 1), oy)
    else:
        pos[beam[0]] = (ox, oy)

    # --- 2) fan beam neighbors onto parallel tracks (short V stubs) ---
    fan_i = 0
    track_of: dict[str, int] = {b: 0 for b in beam}
    attach: dict[str, str] = {}  # node -> beam attach

    def _next_track() -> int:
        nonlocal fan_i
        i = fan_i
        fan_i += 1
        k = (i // 2) + 1
        return k if i % 2 == 0 else -k

    beam_nbrs: list[tuple[str, str]] = []
    for b in beam:
        for n in sorted(adj[b], key=lambda x: (-deg[x], state.names.get(x) or x)):
            if n in beam_set or n in pos:
                continue
            beam_nbrs.append((b, n))

    for b, n in beam_nbrs:
        if n in pos:
            continue
        tr = _next_track()
        bx, _by = pos[b]
        cand = (bx, oy + tr * side)
        cand = _find_free(pos, cand, skip=None, pitch=pitch, side=side, ox=ox, oy=oy, prefer_h=True)
        # Prefer same x as beam (true stub); if shifted, keep track y
        if abs(cand[1] - (oy + tr * side)) > _TOL_PX:
            cand = _find_free(
                pos,
                (bx + pitch, oy + tr * side),
                skip=None,
                pitch=pitch,
                side=side,
                ox=ox,
                oy=oy,
            )
        # Force onto track y
        cand = (cand[0], oy + tr * side)
        if not _cell_free(pos, cand, skip=None, min_dx=pitch, min_dy=side):
            cand = _find_free(
                pos, (bx, oy + tr * side), skip=None, pitch=pitch, side=side, ox=ox, oy=oy
            )
            cand = (cand[0], oy + tr * side)
            if not _cell_free(pos, cand, skip=None, min_dx=pitch, min_dy=side):
                cand = _find_free(pos, cand, skip=None, pitch=pitch, side=side, ox=ox, oy=oy)
        pos[n] = cand
        track_of[n] = tr
        attach[n] = b

    # --- 3) grow deg≤2 corridors horizontally on their track ---
    # BFS from placed nodes; children of corridor nodes continue east/west on same y.
    q = deque(sorted(pos.keys(), key=lambda n: (abs(track_of.get(n, 0)), pos[n][0])))
    parent: dict[str, str | None] = {b: None for b in beam}
    for n, b in attach.items():
        parent[n] = b

    while q:
        u = q.popleft()
        ux, uy = pos[u]
        tr = track_of.get(u, 0)
        kids = sorted(
            (v for v in adj[u] if v not in pos),
            key=lambda n: (-deg[n], state.names.get(n) or n),
        )
        for ki, v in enumerate(kids):
            # Corridor continuation: deg(u)<=2 or u is non-beam leaf-ish → stay on track, H grow
            corridor = (deg[u] <= 2 and u not in beam_set) or (
                deg[v] <= 2 and deg[u] <= 3 and u not in beam_set
            )
            if corridor or ki == 0 and deg[v] <= 2:
                p = parent.get(u)
                if p and p in pos:
                    direction = 1.0 if ux >= pos[p][0] - 1e-9 else -1.0
                else:
                    direction = 1.0 if ux >= (pos[beam[0]][0] + pos[beam[-1]][0]) / 2 else -1.0
                cand = (ux + direction * pitch, uy)  # same track
                if not _cell_free(pos, cand, skip=None, min_dx=pitch, min_dy=side):
                    # try opposite then further
                    for s in range(1, 16):
                        for d in (direction, -direction):
                            c2 = (ux + d * s * pitch, uy)
                            if _cell_free(pos, c2, skip=None, min_dx=pitch, min_dy=side):
                                cand = c2
                                break
                        else:
                            continue
                        break
                pos[v] = cand
                track_of[v] = tr
            else:
                # branch: new parallel track, short stub from u (prefer same x)
                sign = 1 if ki % 2 == 0 else -1
                # pick unused track near u
                new_tr = tr + sign
                while any(
                    abs(track_of.get(n, 999) - new_tr) < 1 and n in pos for n in pos
                ) and abs(new_tr) < 20:
                    # allow reuse if cells free
                    break
                # find free track index
                tried = 0
                while tried < 12:
                    cy = oy + new_tr * side
                    cand = (ux, cy)
                    if _cell_free(pos, cand, skip=None, min_dx=pitch, min_dy=side):
                        pos[v] = cand
                        track_of[v] = new_tr
                        break
                    cand = (ux + pitch, cy)
                    if _cell_free(pos, cand, skip=None, min_dx=pitch, min_dy=side):
                        pos[v] = cand
                        track_of[v] = new_tr
                        break
                    new_tr += sign
                    tried += 1
                else:
                    pos[v] = _find_free(
                        pos, (ux + pitch, uy), skip=None, pitch=pitch, side=side, ox=ox, oy=oy
                    )
                    track_of[v] = int(round((pos[v][1] - oy) / side))
            parent[v] = u
            q.append(v)

    # Also lay extracted long channels that may have been fragmented
    channels = extract_channels(mini)
    for ch in channels:
        path = [n for n in ch.node_ids if n in sub_ids]
        if len(path) < 3:
            continue
        # If most already placed on same track, snap missing; else place whole path H
        placed_p = [n for n in path if n in pos]
        if len(placed_p) >= 2:
            # snap all to the median track of placed
            ys = sorted(pos[n][1] for n in placed_p)
            ty = ys[len(ys) // 2]
            tr = int(round((ty - oy) / side))
            # order by current x or path order
            xs = [pos[n][0] for n in placed_p]
            x0 = min(xs)
            # lay path left-to-right
            # find leftmost path index among placed
            for i, n in enumerate(path):
                if n in pos:
                    x0 = pos[n][0] - i * pitch
                    break
            for i, n in enumerate(path):
                cand = (x0 + i * pitch, oy + tr * side)
                if n in beam_set:
                    continue
                if n in pos and abs(pos[n][1] - cand[1]) <= _TOL_PX:
                    continue
                if n not in pos or deg[n] <= 2:
                    if _cell_free(pos, cand, skip=n, min_dx=pitch, min_dy=side) or n not in pos:
                        if n in pos and not _cell_free(pos, cand, skip=n, min_dx=pitch, min_dy=side):
                            continue
                        pos[n] = cand
                        track_of[n] = tr

    for n in sub_ids:
        if n not in pos:
            pos[n] = _find_free(
                pos,
                (ox + pitch * 3, oy),
                skip=None,
                pitch=pitch,
                side=side,
                ox=ox,
                oy=oy,
            )
            track_of[n] = int(round((pos[n][1] - oy) / side))

    for n in list(pos):
        pos[n] = _snap_grid(pos[n][0], pos[n][1], ox=ox, oy=oy, pitch=pitch, side=side)

    # --- 4) triangle right-angles + hyp skip ---
    hyps = _triangle_hyps(pos, adj) if skip_triangles else set()
    skip_edges = set(hyps) if skip_triangles else set()

    if skip_triangles:
        for a, b, c in _triangles(adj):
            edges = [
                (_edge_key(a, b), a, b, _dist2(pos[a], pos[b])),
                (_edge_key(a, c), a, c, _dist2(pos[a], pos[c])),
                (_edge_key(b, c), b, c, _dist2(pos[b], pos[c])),
            ]
            edges.sort(key=lambda t: -t[3])
            hyp_key = edges[0][0]
            leg1, leg2 = edges[1], edges[2]
            s1, s2 = {leg1[1], leg1[2]}, {leg2[1], leg2[2]}
            corner_set = s1 & s2
            if len(corner_set) != 1:
                continue
            corner = next(iter(corner_set))
            if corner in beam_set:
                continue
            e1 = next(iter(s1 - {corner}))
            e2 = next(iter(s2 - {corner}))
            for cand in ((pos[e1][0], pos[e2][1]), (pos[e2][0], pos[e1][1])):
                cand = _snap_grid(cand[0], cand[1], ox=ox, oy=oy, pitch=pitch, side=side)
                if not (_axis_ok(cand, pos[e1]) and _axis_ok(cand, pos[e2])):
                    continue
                if _cell_free(pos, cand, skip=corner, min_dx=pitch * 0.5, min_dy=side * 0.5):
                    pos[corner] = cand
                    skip_edges.add(hyp_key)
                    break

    # --- 5) force non-hyp edges via subtree rigid slide (preserves tree H/V) ---
    kids: dict[str, list[str]] = {n: [] for n in sub_ids}
    for c, p in parent.items():
        if p is not None and c in sub_ids and p in sub_ids:
            kids[p].append(c)

    def _subtree(root: str) -> list[str]:
        out: list[str] = []
        stack = [root]
        seen_s = {root}
        while stack:
            u = stack.pop()
            out.append(u)
            for v in kids.get(u) or ():
                if v not in seen_s:
                    seen_s.add(v)
                    stack.append(v)
        return out

    def _free_axis_with_parent(n: str) -> str | None:
        """Return 'x' if can slide in x (parent edge is H), 'y' if can slide in y (V)."""
        p = parent.get(n)
        if p is None or p not in pos:
            return "xy"  # roots / beam kids treated carefully below
        if abs(pos[n][1] - pos[p][1]) <= _TOL_PX:
            return "x"  # horizontal parent edge → free in x
        if abs(pos[n][0] - pos[p][0]) <= _TOL_PX:
            return "y"  # vertical parent edge → free in y
        return None

    def _translate_subtree(root: str, dx: float, dy: float) -> dict[str, tuple[float, float]]:
        trial = dict(pos)
        for n in _subtree(root):
            if n in beam_set:
                continue
            x, y = trial[n]
            trial[n] = (x + dx, y + dy)
        return trial

    def _collides(trial: dict[str, tuple[float, float]], moved: set[str]) -> bool:
        for n in moved:
            if n not in trial:
                continue
            if not _cell_free(trial, trial[n], skip=n, min_dx=pitch, min_dy=side):
                return True
        return False

    def _try_subtree_align(mov: str, fix: str) -> bool:
        if mov in beam_set or mov not in pos or fix not in pos:
            return False
        if _axis_ok(pos[mov], pos[fix]):
            return True
        base = _axis_score(pos, links, skip=skip_edges)
        free = _free_axis_with_parent(mov)
        if free is None:
            return False
        mx, my = pos[mov]
        fx, fy = pos[fix]
        attempts: list[tuple[float, float]] = []
        # Align column (V edge) by sliding in x
        if free in ("x", "xy") and abs(mx - fx) > _TOL_PX:
            attempts.append((fx - mx, 0.0))
        # Align row (H edge) by sliding in y
        if free in ("y", "xy") and abs(my - fy) > _TOL_PX:
            attempts.append((0.0, fy - my))
        # Beam direct neighbors: parent is beam (H free if stub was V from beam)
        if parent.get(mov) in beam_set:
            attempts = [(fx - mx, 0.0), (0.0, fy - my)]

        for dx, dy in attempts:
            if abs(dx) < 1e-9 and abs(dy) < 1e-9:
                continue
            # snap delta to grid
            dx = round(dx / pitch) * pitch
            dy = round(dy / side) * side
            trial = _translate_subtree(mov, dx, dy)
            moved = set(_subtree(mov)) - beam_set
            if _collides(trial, moved):
                # nudge further along same axis to clear
                cleared = False
                for s in range(1, 10):
                    for sign in (1, -1):
                        ndx = dx + (sign * s * pitch if abs(dx) >= abs(dy) else 0.0)
                        ndy = dy + (sign * s * side if abs(dy) > abs(dx) else 0.0)
                        if abs(dx) < 1e-9:
                            ndx = 0.0
                            ndy = dy + sign * s * side
                        if abs(dy) < 1e-9:
                            ndy = 0.0
                            ndx = dx + sign * s * pitch
                        t2 = _translate_subtree(mov, ndx, ndy)
                        if not _collides(t2, moved) and _axis_ok(t2[mov], t2[fix]):
                            trial = t2
                            cleared = True
                            break
                    if cleared:
                        break
                if not cleared:
                    continue
            if not _axis_ok(trial[mov], trial[fix]):
                continue
            if not _segment_clear(trial, mov, fix, thr=40.0):
                continue
            sc = _axis_score(trial, links, skip=skip_edges)
            if sc >= base:
                pos.clear()
                pos.update(trial)
                return True
        return False

    for _ in range(24):
        dirty = False
        diags = [
            (a, b)
            for a, b in links
            if not _axis_ok(pos[a], pos[b]) and _edge_key(a, b) not in skip_edges
        ]
        if not diags:
            break
        diags.sort(key=lambda e: (min(deg[e[0]], deg[e[1]]), -max(deg[e[0]], deg[e[1]])))
        for a, b in diags:
            if _axis_ok(pos[a], pos[b]):
                continue
            # Prefer moving the deeper / lower-degree endpoint's subtree
            def _depth(n: str) -> int:
                d = 0
                cur: str | None = n
                seen_d = set()
                while cur and cur in parent and cur not in seen_d:
                    seen_d.add(cur)
                    cur = parent.get(cur)
                    d += 1
                    if d > 64:
                        break
                return d

            if a in beam_set:
                order = [(b, a)]
            elif b in beam_set:
                order = [(a, b)]
            else:
                order = [(a, b), (b, a)]
                order.sort(key=lambda t: (-_depth(t[0]), deg[t[0]]))
            for mov, fix in order:
                if _try_subtree_align(mov, fix):
                    dirty = True
                    break
        if skip_triangles:
            skip_edges |= _triangle_hyps(pos, adj)
        if not dirty:
            break

    # --- 5b) hard force non-hyp diags onto H/V (beam edges first; allow sc-1) ---
    def _force_one(mov: str, fix: str, *, min_sc_delta: int = -1) -> bool:
        if mov in beam_set or mov not in pos or fix not in pos:
            return False
        mx, my = pos[mov]
        fx, fy = pos[fix]
        base = _axis_score(pos, links, skip=skip_edges)
        # Prefer V stub to beam (share x), else H
        ordered = (
            [(fx, my), (mx, fy)]
            if fix in beam_set
            else [(mx, fy), (fx, my)]  # prefer stay on track
        )
        for cand in ordered:
            cand = _snap_grid(cand[0], cand[1], ox=ox, oy=oy, pitch=pitch, side=side)
            if not _axis_ok(cand, (fx, fy)):
                continue
            options = [cand]
            for s in range(1, 18):
                for sign in (1, -1):
                    if abs(cand[0] - fx) <= _TOL_PX:
                        options.append((cand[0], cand[1] + sign * s * side))
                    else:
                        options.append((cand[0] + sign * s * pitch, cand[1]))
            for c2 in options:
                c2 = _snap_grid(c2[0], c2[1], ox=ox, oy=oy, pitch=pitch, side=side)
                if not _axis_ok(c2, (fx, fy)):
                    continue
                trial = dict(pos)
                trial[mov] = c2
                # Never create an H/V edge that passes through another node.
                if not _segment_clear(trial, mov, fix, thr=40.0):
                    continue
                sc = _axis_score(trial, links, skip=skip_edges)
                if sc < base + min_sc_delta:
                    continue
                free = _cell_free(trial, c2, skip=mov, min_dx=pitch, min_dy=side)
                if free or sc > base:
                    pos[mov] = c2
                    return True
        return False

    for _ in range(40):
        dirty = False
        diags = [
            (a, b)
            for a, b in links
            if not _axis_ok(pos[a], pos[b]) and _edge_key(a, b) not in skip_edges
        ]
        if not diags:
            break
        # Beam-touching edges first (metro stubs), then low-degree
        def _prio(e: tuple[str, str]) -> tuple:
            a, b = e
            beam_touch = 0 if (a in beam_set or b in beam_set) else 1
            return (beam_touch, min(deg[a], deg[b]), -max(deg[a], deg[b]))

        diags.sort(key=_prio)
        for a, b in diags:
            if _axis_ok(pos[a], pos[b]):
                continue
            ends: list[tuple[str, str]] = []
            if a in beam_set and b not in beam_set:
                ends = [(b, a)]
            elif b in beam_set and a not in beam_set:
                ends = [(a, b)]
            else:
                ends = [(a, b), (b, a)]
                ends.sort(key=lambda t: deg[t[0]])
            for mov, fix in ends:
                if _force_one(mov, fix, min_sc_delta=-1):
                    dirty = True
                    break
            if dirty:
                break
        if skip_triangles:
            skip_edges |= _triangle_hyps(pos, adj)
        if not dirty:
            # final desperation: allow larger score drop to kill residual diags
            for a, b in diags:
                if _axis_ok(pos[a], pos[b]) or _edge_key(a, b) in skip_edges:
                    continue
                mov, fix = (a, b) if deg[a] <= deg[b] else (b, a)
                if mov in beam_set:
                    mov, fix = fix, mov
                if _force_one(mov, fix, min_sc_delta=-3):
                    dirty = True
                    break
            if not dirty:
                break

    # --- 6) reduce crossings by sliding on free axis ---
    for _ in range(12):
        x0 = count_edge_crossings(pos, links)
        if x0 == 0:
            break
        improved = False
        # candidate movers: endpoints of crossing-heavy edges — try all low-deg
        movers = sorted(sub_ids, key=lambda n: (deg[n], n))
        for mov in movers:
            if mov in beam_set:
                continue
            mx, my = pos[mov]
            base_x = count_edge_crossings(pos, links)
            base_ax = _axis_score(pos, links, skip=skip_edges)
            best = None
            best_x = base_x
            for s in range(1, 10):
                for cand in (
                    (mx + s * pitch, my),
                    (mx - s * pitch, my),
                    (mx, my + s * side),
                    (mx, my - s * side),
                ):
                    cand = _snap_grid(cand[0], cand[1], ox=ox, oy=oy, pitch=pitch, side=side)
                    if not _cell_free(pos, cand, skip=mov, min_dx=pitch, min_dy=side):
                        continue
                    trial = dict(pos)
                    trial[mov] = cand
                    # must not drop axis score
                    if _axis_score(trial, links, skip=skip_edges) < base_ax:
                        continue
                    xc = count_edge_crossings(trial, links)
                    if xc < best_x:
                        best_x = xc
                        best = cand
            if best is not None and best_x < base_x:
                pos[mov] = best
                improved = True
        if not improved:
            break

    # --- 7) ortho separation ---
    for _ in range(8):
        moved_any = False
        ids = sorted(pos, key=lambda n: (pos[n][1], pos[n][0]))
        for i, a in enumerate(ids):
            ax, ay = pos[a]
            for b in ids[i + 1 :]:
                bx, by = pos[b]
                if abs(ax - bx) >= pitch * 0.92 or abs(ay - by) >= side * 0.92:
                    continue
                mov = b if a in beam_set else a if b in beam_set else (a if deg[a] <= deg[b] else b)
                if mov in beam_set:
                    continue
                mx, my = pos[mov]
                base = _axis_score(pos, links, skip=skip_edges)
                for s in range(1, 14):
                    done = False
                    for cand in (
                        (mx + s * pitch, my),
                        (mx - s * pitch, my),
                        (mx, my + s * side),
                        (mx, my - s * side),
                    ):
                        cand = _snap_grid(cand[0], cand[1], ox=ox, oy=oy, pitch=pitch, side=side)
                        if not _cell_free(pos, cand, skip=mov, min_dx=pitch, min_dy=side):
                            continue
                        trial = dict(pos)
                        trial[mov] = cand
                        if _axis_score(trial, links, skip=skip_edges) >= base:
                            pos[mov] = cand
                            moved_any = True
                            done = True
                            break
                    if done:
                        break
                ax, ay = pos[a]
        if not moved_any:
            break

    # --- 8) occluding H/V: nudge low-deg obstacle 1 track off; else leave chord ---
    # Prefer readable metro over forcing a long chord through other nodes.
    for _ in range(8):
        dirty = False
        for a, b in links:
            if a not in pos or b not in pos or not _axis_ok(pos[a], pos[b]):
                continue
            if _segment_clear(pos, a, b, thr=40.0):
                continue
            # Collect obstacles on open segment
            from netx_topology_mcp.layout_metrics import point_segment_dist

            pa, pb = pos[a], pos[b]
            obstacles = []
            for n, p in pos.items():
                if n in (a, b):
                    continue
                d, t = point_segment_dist(p, pa, pb)
                if d < 40.0 and 0.05 < t < 0.95:
                    obstacles.append(n)
            # Nudge lowest-degree obstacle one grid step off the trunk
            obstacles.sort(key=lambda n: (deg.get(n, 0), n))
            for nid in obstacles:
                if nid in beam_set or deg.get(nid, 0) >= 4:
                    continue
                nx, ny = pos[nid]
                horiz = abs(pa[1] - pb[1]) <= _TOL_PX
                for dx, dy in (
                    [(0.0, side), (0.0, -side)]
                    if horiz
                    else [(pitch, 0.0), (-pitch, 0.0)]
                ):
                    trial = dict(pos)
                    trial[nid] = _snap_grid(
                        nx + dx, ny + dy, ox=ox, oy=oy, pitch=pitch, side=side
                    )
                    if not _cell_free(
                        trial, trial[nid], skip=nid, min_dx=pitch, min_dy=side
                    ):
                        continue
                    d2, t2 = point_segment_dist(trial[nid], pa, pb)
                    if 0.05 < t2 < 0.95 and d2 < 40.0:
                        continue
                    # Keep incident axis count if possible
                    ok_axis = True
                    for nb in adj.get(nid) or ():
                        if nb not in trial:
                            continue
                        if _axis_ok(pos[nid], pos[nb]) and not _axis_ok(
                            trial[nid], trial[nb]
                        ):
                            ok_axis = False
                            break
                    if not ok_axis:
                        continue
                    pos[nid] = trial[nid]
                    dirty = True
                    break
                if dirty:
                    break
            if dirty:
                break
            # Cannot clear without wrecking hubs: break chord by 1 step on low-deg end
            ends = sorted((a, b), key=lambda n: (deg.get(n, 0), n))
            for end in ends:
                if end in beam_set or deg.get(end, 0) >= 4:
                    continue
                ex, ey = pos[end]
                horiz = abs(pa[1] - pb[1]) <= _TOL_PX
                for dx, dy in (
                    [(0.0, side), (0.0, -side)]
                    if horiz
                    else [(pitch, 0.0), (-pitch, 0.0)]
                ):
                    trial = dict(pos)
                    trial[end] = _snap_grid(
                        ex + dx, ey + dy, ox=ox, oy=oy, pitch=pitch, side=side
                    )
                    if not _cell_free(
                        trial, trial[end], skip=end, min_dx=pitch, min_dy=side
                    ):
                        continue
                    if not _segment_clear(trial, a, b, thr=40.0):
                        continue
                    pos[end] = trial[end]
                    dirty = True
                    break
                if dirty:
                    break
            if dirty:
                break
        if not dirty:
            break

    return pos


def build_ortho_metro_skeleton(
    state: LayoutState,
    params: LayoutParams | None = None,
    *,
    skip_triangles: bool = True,
) -> OpResult:
    """Multi-layer H/V metro layout for the whole canvas (per-component)."""
    params = params or LayoutParams()
    st = state.copy()
    if not st.positions:
        return OpResult(state=st, moved=set(), op="build_ortho_metro_skeleton", note="empty")

    comps = _components(st)
    pitch = max(float(params.pitch), REC_CENTER_DX)
    pad_x = max(float(params.island_pad_x), pitch)
    placed: dict[str, tuple[float, float]] = {}
    cursor_x = 0.0
    comp_meta: list[dict[str, Any]] = []

    for bi, block in enumerate(comps):
        local = _place_component(
            st,
            block,
            params,
            skip_triangles=skip_triangles,
            origin=(0.0, 0.0),
        )
        if not local:
            continue
        min_x = min(x for x, _y in local.values())
        min_y = min(y for _x, y in local.values())
        max_x = max(x for x, _y in local.values())
        for nid, (x, y) in local.items():
            placed[nid] = (x - min_x + cursor_x, y - min_y)
        width = max_x - min_x
        cursor_x += width + pad_x
        links = [(a, b) for a, b in st.links if a in local and b in local]
        adj = {n: {m for m in (st.adj.get(n) or ()) if m in local} for n in local}
        hyps = _triangle_hyps(local, adj) if skip_triangles else set()
        diag = sum(1 for a, b in links if not _axis_ok(local[a], local[b]))
        diag_skip = sum(
            1
            for a, b in links
            if not _axis_ok(local[a], local[b]) and _edge_key(a, b) in hyps
        )
        comp_meta.append(
            {
                "block_id": bi,
                "n": len(block),
                "diag": diag,
                "diag_triangle_skip": diag_skip,
                "beam": _pick_beam(
                    LayoutState(
                        positions=local,
                        names=st.names,
                        layers=st.layers,
                        links=links,
                        adj=adj,
                    )
                ),
            }
        )

    st.positions = placed
    st = separate_overlaps_ortho(st, params).state
    st = normalize_origin(st, params).state
    st = separate_overlaps_ortho(st, params).state

    # Nodes must not sit on non-incident H/V trunks (edge occlusion).
    from netx_topology_mcp.layout_ops.clear_edge_hits import clear_edge_hits

    side = max(float(params.side), REC_CENTER_DY)
    clr = clear_edge_hits(
        st,
        params,
        top_n=60,
        thr=40.0,
        margin=20.0,
        max_moves=60,
        preserve_axis=True,
        pitch=pitch,
        side=side,
        rounds=4,
    )
    st = clr.state
    st = separate_overlaps_ortho(st, params).state

    axis_n = 0
    diag_n = 0
    for a, b in st.links:
        if a not in st.positions or b not in st.positions:
            continue
        if _axis_ok(st.positions[a], st.positions[b]):
            axis_n += 1
        else:
            diag_n += 1
    cross = count_edge_crossings(st.positions, st.links)
    from netx_topology_mcp.layout_metrics import compute_edge_clearance

    clr_m = compute_edge_clearance(st.positions, st.links, names=st.names, thr=40.0)
    st.meta = dict(st.meta or {})
    st.meta["rings_mode"] = "ortho_metro"
    st.meta["ortho_metro"] = {
        "components": len(comps),
        "axis_edges": axis_n,
        "diag_edges": diag_n,
        "skip_triangles": bool(skip_triangles),
        "crossings": cross,
        "blocks": comp_meta,
        "pitch": pitch,
        "side": side,
        "overlaps": len(overlapping_nodes(st)),
        "edge_clearance_hits": int(clr_m.get("edge_clearance_hits") or 0),
        "clear_edge": clr.params,
    }
    return OpResult(
        state=st,
        moved=set(st.positions.keys()),
        op="build_ortho_metro_skeleton",
        params={
            "axis_edges": axis_n,
            "diag_edges": diag_n,
            "components": len(comps),
            "skip_triangles": bool(skip_triangles),
            "crossings": cross,
        },
        note=(
            f"ortho_metro comps={len(comps)} axis={axis_n} diag={diag_n} "
            f"skip_tri={int(bool(skip_triangles))} x={cross}"
        ),
    )


def ortho_metro_params_from_overrides(overrides: dict[str, Any] | None) -> dict[str, Any]:
    o = overrides or {}
    out: dict[str, Any] = {}
    if "skip_triangles" in o:
        out["skip_triangles"] = bool(o.get("skip_triangles"))
    return out
