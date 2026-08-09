"""Path-based channels: maximal deg≤2 corridors as atomic layout units.

Inspired by Path-Based Framework (PBF): treat corridors as first-class
geometry (straight spines), not independent force-directed points.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from netx_topology_mcp.layout_ops.state import LayoutState
from netx_topology_mcp.layout_topology_quality import extract_chain_paths


def order_stubs_crossing_aware(
    hub: str,
    stubs: list[str],
    adj: dict[str, set[str]],
    member_set: set[str],
    owner: dict[str, str],
) -> list[str]:
    """Stable stub order by foreign-neighbor hash angle (crossing-aware proxy)."""
    if len(stubs) <= 2:
        return sorted(stubs)

    def score(s: str) -> tuple[float, str]:
        terr = {n for n, o in owner.items() if o == s} | {s}
        foreign: list[str] = []
        for n in terr:
            for v in adj.get(n, ()):
                if v == hub or v in terr or v in member_set:
                    continue
                foreign.append(v)
        ang = (hash(s) % 1000) / 1000.0 * 2 * math.pi
        if foreign:
            ang = (hash(min(foreign)) % 1000) / 1000.0 * 2 * math.pi
        return (ang, s)

    return [s for _, s in sorted((score(s) for s in stubs), key=lambda t: t[0])]


@dataclass(frozen=True)
class Channel:
    """Ordered path hub…access… (portals may be high-degree)."""

    node_ids: tuple[str, ...]

    @property
    def length(self) -> int:
        return len(self.node_ids)

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.length,
            "ends": [self.node_ids[0], self.node_ids[-1]] if self.node_ids else [],
            "node_ids": list(self.node_ids)[:40],
        }


def extract_channels(state: LayoutState) -> list[Channel]:
    """All maximal deg≤2 corridors (with portal hubs when unique)."""
    paths = extract_chain_paths(state.adj)
    out = [Channel(tuple(p)) for p in paths if len(p) >= 3]
    out.sort(key=lambda c: (-c.length, c.node_ids[0] if c.node_ids else ""))
    return out


def channels_touching(
    channels: list[Channel], node_set: set[str]
) -> list[Channel]:
    """Channels with ≥2 nodes inside ``node_set`` (block-local)."""
    return [c for c in channels if sum(1 for n in c.node_ids if n in node_set) >= 2]


def place_channel_ray(
    path: list[str],
    *,
    origin: tuple[float, float],
    ux: float,
    uy: float,
    step: float,
    pinned: set[str] | None = None,
) -> dict[str, tuple[float, float]]:
    """Lay path along a unit ray from origin; skip pinned nodes (keep them).

    Distance index: if path[0] is pinned (hub at origin), node at path[i]
    sits at i*step; otherwise path[0] is at 1*step.
    """
    pinned = pinned or set()
    ox, oy = origin
    out: dict[str, tuple[float, float]] = {}
    hub_first = bool(path and path[0] in pinned)
    for i, n in enumerate(path):
        if n in pinned:
            continue
        k = i if hub_first else i + 1
        out[n] = (ox + ux * step * k, oy + uy * step * k)
    return out


def channels_report(state: LayoutState) -> dict[str, Any]:
    ch = extract_channels(state)
    nodes = {n for c in ch for n in c.node_ids}
    return {
        "channel_count": len(ch),
        "channel_nodes": len(nodes),
        "longest": ch[0].length if ch else 0,
        "sample": [c.as_dict() for c in ch[:12]],
        "tip": "通道是 deg≤2 走廊原子；channel_metro 按射线拉直，勿逐点力导。",
    }


def _straighten_path_positions(
    path: list[str],
    pos: dict[str, tuple[float, float]],
    *,
    mode: str = "chord",
    step: float | None = None,
    bend_frac: float = 0.5,
) -> dict[str, tuple[float, float]] | None:
    """Reposition interior (and optionally ends) of path. Ends stay fixed for chord/L."""
    pts = [pos[n] for n in path if n in pos]
    if len(pts) < 3 or len(pts) != len(path):
        return None
    x0, y0 = pts[0]
    x1, y1 = pts[-1]
    n = len(path) - 1
    out: dict[str, tuple[float, float]] = {}
    if mode == "chord" or mode.startswith("chord_shift"):
        dx, dy = x1 - x0, y1 - y0
        plen = math.hypot(dx, dy) or 1.0
        # lateral offset for chord_shift± (keeps ends fixed via taper)
        off = 0.0
        if mode.startswith("chord_shift"):
            try:
                off = float(mode.split(":", 1)[1])
            except (IndexError, ValueError):
                off = 0.0
        px, py = (-dy / plen) * off, (dx / plen) * off
        for i, nid in enumerate(path):
            if i == 0 or i == len(path) - 1:
                continue
            t = i / n
            # taper offset to 0 at ends (already skipping ends)
            taper = math.sin(math.pi * t)
            out[nid] = (
                x0 + dx * t + px * taper,
                y0 + dy * t + py * taper,
            )
        return out
    if mode in {"L_hv", "L_vh"}:
        # Orthogonal elbow; both ends fixed. bend_frac ∈ (0,1) picks knee along path.
        bf = min(0.85, max(0.15, float(bend_frac)))
        bend_i = max(1, min(n - 1, int(round(n * bf))))
        for i, nid in enumerate(path):
            if i == 0 or i == len(path) - 1:
                continue
            if mode == "L_hv":
                # (x0,y0) → (x1,y0) → (x1,y1)
                if i <= bend_i:
                    t = i / bend_i
                    out[nid] = (x0 + (x1 - x0) * t, y0)
                else:
                    t = (i - bend_i) / (n - bend_i)
                    out[nid] = (x1, y0 + (y1 - y0) * t)
            else:
                # (x0,y0) → (x0,y1) → (x1,y1)
                if i <= bend_i:
                    t = i / bend_i
                    out[nid] = (x0, y0 + (y1 - y0) * t)
                else:
                    t = (i - bend_i) / (n - bend_i)
                    out[nid] = (x0 + (x1 - x0) * t, y1)
        return out
    if mode in {"horizontal", "vertical"}:
        # Keep first endpoint; lay along axis with equal step
        st = step or (
            math.hypot(x1 - x0, y1 - y0) / max(n, 1)
            if math.hypot(x1 - x0, y1 - y0) > 1
            else 180.0
        )
        st = max(120.0, min(st, 280.0))
        for i, nid in enumerate(path):
            if i == 0:
                continue
            if mode == "horizontal":
                out[nid] = (x0 + st * i, y0)
            else:
                out[nid] = (x0, y0 + st * i)
        return out
    return None


def _path_axis_credit(
    path: list[str],
    pos: dict[str, tuple[float, float]],
    *,
    tol_deg: float = 8.0,
    tol_px: float = 4.0,
) -> float:
    """Mean H/V credit along path edges (H=1, V=0.75, diagonal=0)."""
    import math

    if len(path) < 2:
        return 1.0
    tol_rad = math.radians(max(0.1, float(tol_deg)))
    tol_p = max(0.0, float(tol_px))
    credits: list[float] = []
    for i in range(len(path) - 1):
        a, b = path[i], path[i + 1]
        if a not in pos or b not in pos:
            continue
        ax, ay = pos[a]
        bx, by = pos[b]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length < 1e-9:
            credits.append(1.0)
            continue
        adx, ady = abs(dx), abs(dy)
        ang_h = math.atan2(ady, adx)
        ang_v = abs(math.pi / 2 - ang_h)
        if ady <= tol_p or ang_h <= tol_rad:
            credits.append(1.0)
        elif adx <= tol_p or ang_v <= tol_rad:
            credits.append(0.75)
        else:
            credits.append(0.0)
    return sum(credits) / max(len(credits), 1)


def straighten_channels_greedy(
    state: LayoutState,
    params: Any | None = None,
    *,
    step: float | None = None,
    min_len: int = 3,
    max_passes: int = 3,
):
    """Straighten deg≤2 channels.

    Prefer fewer global crossings; when crossings stay equal (incl. already 0),
    accept H/V / L modes that raise path axis credit (metro look).
    """
    from netx_topology_mcp.layout_metrics import count_edge_crossings
    from netx_topology_mcp.layout_ops.hotspots import fix_overlaps_local
    from netx_topology_mcp.layout_ops.state import LayoutParams, OpResult

    params = params or LayoutParams()
    step_px = float(step if step is not None else max(getattr(params, "pitch", 180.0), 160.0))
    pos = dict(state.positions)
    links = list(state.links)
    cross = count_edge_crossings(pos, links)
    accepted: list[dict[str, Any]] = []
    base_modes = (
        "chord",
        "horizontal",
        "vertical",
        "L_hv",
        "L_vh",
    )
    bend_fracs = (0.35, 0.5, 0.65)
    shift_offs = sorted(
        {
            -2.0 * step_px,
            -1.5 * step_px,
            -step_px,
            -0.5 * step_px,
            0.5 * step_px,
            step_px,
            1.5 * step_px,
            2.0 * step_px,
            -400.0,
            -280.0,
            280.0,
            400.0,
        }
    )

    from netx_topology_mcp.layout_jobs import raise_if_cancelled, report_progress

    for _pass in range(max(1, int(max_passes))):
        raise_if_cancelled()
        channels = extract_channels(state)
        # Prefer longer kinked channels first
        accepted_pass = 0
        n_ch = max(1, len(channels))
        for ci, ch in enumerate(channels):
            if ci % 8 == 0:
                raise_if_cancelled()
                report_progress(
                    "straighten_channels",
                    pct=48.0 + 6.0 * ((_pass + ci / n_ch) / max(1, int(max_passes))),
                    message=f"pass {_pass + 1} ch {ci + 1}/{n_ch} x={cross}",
                    step=ci + 1,
                    total_steps=n_ch,
                    crossings=cross,
                )
            path = list(ch.node_ids)
            if len(path) < min_len:
                continue
            if any(n not in pos for n in path):
                continue
            axis0 = _path_axis_credit(path, pos)
            # Lexicographic key: fewer crossings, then higher axis credit.
            best_key = (cross, -axis0)
            best_pos = None
            best_mode = None
            modes = list(base_modes) + [f"chord_shift:{o:.1f}" for o in shift_offs]
            for mode in modes:
                fracs = bend_fracs if mode.startswith("L_") else (0.5,)
                for bf in fracs:
                    delta = _straighten_path_positions(
                        path, pos, mode=mode, step=step_px, bend_frac=bf
                    )
                    if not delta:
                        continue
                    trial = dict(pos)
                    trial.update(delta)
                    c1 = count_edge_crossings(trial, links)
                    if c1 > cross:
                        continue
                    a1 = _path_axis_credit(path, trial)
                    key = (c1, -a1)
                    if key < best_key:
                        best_key = key
                        best_pos = trial
                        best_mode = f"{mode}@{bf:.2f}" if mode.startswith("L_") else mode
            if best_pos is None:
                continue
            # light overlap fix; keep if still improved
            st_try = state.copy()
            st_try.positions = best_pos
            stf = fix_overlaps_local(st_try, params).state
            # restore pinned hubs (core/agg endpoints)
            for end in (path[0], path[-1]):
                if state.layers.get(end) in ("core", "agg") and end in pos:
                    stf.positions[end] = pos[end]
            trial2 = {k: (float(v[0]), float(v[1])) for k, v in stf.positions.items()}
            c2 = count_edge_crossings(trial2, links)
            a2 = _path_axis_credit(path, trial2)
            if (c2, -a2) >= (cross, -axis0):
                continue
            pos = trial2
            cross = c2
            accepted_pass += 1
            accepted.append(
                {
                    "ends": [path[0], path[-1]],
                    "n": len(path),
                    "mode": best_mode,
                    "crossings": cross,
                    "axis": round(a2, 3),
                }
            )
        if accepted_pass == 0:
            break

    if not accepted:
        return OpResult(
            state=state,
            moved=set(),
            op="straighten_channels",
            params={"accepted_n": 0},
            note="no_channel_improved",
        )
    out = state.copy()
    out.positions = pos
    moved = {
        n for n, p in pos.items() if n in state.positions and p != state.positions[n]
    }
    out.meta = dict(out.meta or {})
    out.meta["straighten_channels"] = {"accepted": accepted, "crossings": cross}
    return OpResult(
        state=out,
        moved=moved,
        op="straighten_channels",
        params={"accepted_n": len(accepted), "accepted": accepted[:20], "crossings": cross},
        note=f"straighten_channels:{len(accepted)} x->{cross}",
    )


def straighten_params_from_overrides(overrides: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not overrides:
        return out
    if overrides.get("step") is not None:
        try:
            out["step"] = float(overrides["step"])
        except (TypeError, ValueError):
            pass
    if overrides.get("min_len") is not None:
        try:
            out["min_len"] = int(overrides["min_len"])
        except (TypeError, ValueError):
            pass
    if overrides.get("max_passes") is not None:
        try:
            out["max_passes"] = int(overrides["max_passes"])
        except (TypeError, ValueError):
            pass
    return out
