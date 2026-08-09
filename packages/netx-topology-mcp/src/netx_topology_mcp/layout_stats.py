"""Topology layout statistics + composite score for parameter search.

Complements layout_metrics.analyze_positions with density / emptiness /
edge-stretch stats and a single comparable score in [0, 100].
"""

from __future__ import annotations

import math
from typing import Any

from netx_topology_mcp.layout_metrics import (
    REC_CENTER_DX,
    REC_CENTER_DY,
    analyze_positions,
    grade_layout,
    node_footprint,
)

# Sweet-spot nearest-neighbor band (px): readable but not empty.
NN_SWEET_LO = 140.0
NN_SWEET_HI = 220.0
# Ideal space_utilization band (n * rec_tile / bbox_area).
UTIL_SWEET_LO = 0.12
UTIL_SWEET_HI = 0.45
# Ideal median undirected edge length / recommended pitch.
EDGE_SWEET_LO = 0.7
EDGE_SWEET_HI = 2.2


def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Andrew monotone chain; returns hull CCW, or [] if < 3 unique points."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _polygon_area(poly: list[tuple[float, float]]) -> float:
    if len(poly) < 3:
        return 0.0
    a = 0.0
    for i in range(len(poly)):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % len(poly)]
        a += x0 * y1 - x1 * y0
    return abs(a) * 0.5


def _pct(sorted_vals: list[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, max(0, int(round((len(sorted_vals) - 1) * p))))
    return round(sorted_vals[idx], 2)


def _band_score(x: float, lo: float, hi: float, *, hard_lo: float = 0.0, hard_hi: float | None = None) -> float:
    """1.0 inside [lo,hi], linear falloff outside toward hard bounds → 0."""
    if lo <= x <= hi:
        return 1.0
    if x < lo:
        floor = hard_lo
        if x <= floor:
            return 0.0
        return max(0.0, (x - floor) / max(lo - floor, 1e-9))
    ceiling = hard_hi if hard_hi is not None else hi * 3.0
    if x >= ceiling:
        return 0.0
    return max(0.0, (ceiling - x) / max(ceiling - hi, 1e-9))


def compute_density_stats(
    pos: dict[str, tuple[float, float]],
    names: dict[str, str],
    links: list[tuple[str, str]],
) -> dict[str, Any]:
    """Emptiness / compactness / edge-stretch stats (no crossing count)."""
    n = len(pos)
    if n == 0:
        return {
            "bbox_area": 0.0,
            "hull_area": 0.0,
            "space_utilization": 0.0,
            "hull_utilization": 0.0,
            "footprint_fill": 0.0,
            "grid_occupancy": 0.0,
            "empty_grid_ratio": 1.0,
            "aspect_ratio": 1.0,
            "edge_len_p50": None,
            "edge_len_p90": None,
            "edge_stretch_p50": None,
            "whitespace_index": 1.0,
        }

    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    bw = max(xs) - min(xs)
    bh = max(ys) - min(ys)
    bbox_area = max(bw * bh, 1.0)
    tile = REC_CENTER_DX * REC_CENTER_DY
    space_utilization = n * tile / bbox_area

    hull = _convex_hull(list(pos.values()))
    hull_area = max(_polygon_area(hull), 1.0) if len(hull) >= 3 else bbox_area
    hull_utilization = n * tile / hull_area

    # Sum of icon+label AABBs / bbox (can exceed 1 if overlaps)
    fp_area = 0.0
    for nid, (x, y) in pos.items():
        fx0, fy0, fx1, fy1 = node_footprint(names.get(nid, nid))
        fp_area += max(fx1 - fx0, 1.0) * max(fy1 - fy0, 1.0)
    footprint_fill = fp_area / bbox_area

    # Grid occupancy: cells of size pitch×side covering bbox
    cell_w, cell_h = REC_CENTER_DX, REC_CENTER_DY
    x0, y0 = min(xs), min(ys)
    cols = max(1, int(math.ceil(bw / cell_w)) + 1)
    rows = max(1, int(math.ceil(bh / cell_h)) + 1)
    occupied: set[tuple[int, int]] = set()
    for x, y in pos.values():
        occupied.add((int((x - x0) // cell_w), int((y - y0) // cell_h)))
    total_cells = cols * rows
    grid_occupancy = len(occupied) / total_cells
    empty_grid_ratio = 1.0 - grid_occupancy

    aspect = (bw / bh) if bh > 1e-9 else 999.0

    pitch = math.hypot(REC_CENTER_DX, REC_CENTER_DY) / math.sqrt(2.0)  # ~185
    edge_lens: list[float] = []
    for a, b in links:
        if a not in pos or b not in pos:
            continue
        edge_lens.append(math.hypot(pos[a][0] - pos[b][0], pos[a][1] - pos[b][1]))
    edge_lens.sort()
    el_p50 = _pct(edge_lens, 0.5)
    el_p90 = _pct(edge_lens, 0.9)
    stretch_p50 = round(el_p50 / pitch, 3) if el_p50 else None

    # whitespace_index: 0 compact, 1 desolate (based on util + empty grid)
    whitespace_index = round(
        0.55 * (1.0 - min(space_utilization / UTIL_SWEET_LO, 1.0))
        + 0.45 * empty_grid_ratio,
        4,
    )

    return {
        "bbox_area": round(bbox_area, 1),
        "hull_area": round(hull_area, 1),
        "space_utilization": round(space_utilization, 4),
        "hull_utilization": round(hull_utilization, 4),
        "footprint_fill": round(footprint_fill, 4),
        "grid_occupancy": round(grid_occupancy, 4),
        "empty_grid_ratio": round(empty_grid_ratio, 4),
        "aspect_ratio": round(aspect, 3),
        "edge_len_p50": el_p50,
        "edge_len_p90": el_p90,
        "edge_stretch_p50": stretch_p50,
        "whitespace_index": whitespace_index,
        "grid_cells": [cols, rows],
        "occupied_cells": len(occupied),
    }


def score_layout_components(metrics: dict[str, Any]) -> dict[str, Any]:
    """Weighted sub-scores in [0,1] + total in [0,100].

    Hard gate: any footprint/label overlap → total capped near 0 (still report parts).
    Mid-tier: chain（直链成一体）+ rings（最小环不被穿）, each weight 0.10.
    """
    n = int(metrics.get("node_count") or 0)
    overlaps = int(metrics.get("footprint_overlap_pairs") or 0)
    label_ov = int(metrics.get("label_overlap_pairs") or 0)
    cpl = float(metrics.get("crossings_per_link") or 0.0)
    nn = float(metrics.get("nn_p50") or 0.0)
    util = float(metrics.get("space_utilization") or 0.0)
    hull_u = float(metrics.get("hull_utilization") or util)
    grid_occ = float(metrics.get("grid_occupancy") or 0.0)
    stretch = metrics.get("edge_stretch_p50")
    stretch_f = float(stretch) if stretch is not None else 1.0
    white = float(metrics.get("whitespace_index") or 0.0)
    chain_s = float(metrics.get("chain_score") if metrics.get("chain_score") is not None else 1.0)
    rings_s = float(metrics.get("rings_score") if metrics.get("rings_score") is not None else 1.0)
    edge_clr_s = float(
        metrics.get("edge_clearance_score")
        if metrics.get("edge_clearance_score") is not None
        else 1.0
    )
    edge_axis_s = float(
        metrics.get("edge_axis_score")
        if metrics.get("edge_axis_score") is not None
        else 1.0
    )

    # Crossing: UME mid-size ~0.16; 0 → 1.0, 0.30 → 0
    if n <= 50:
        cpl_ok, cpl_bad = 0.05, 0.20
    elif n <= 200:
        cpl_ok, cpl_bad = 0.10, 0.25
    else:
        cpl_ok, cpl_bad = 0.16, 0.30
    if cpl <= cpl_ok:
        cross_s = 1.0
    elif cpl >= cpl_bad:
        cross_s = 0.0
    else:
        cross_s = 1.0 - (cpl - cpl_ok) / max(cpl_bad - cpl_ok, 1e-9)

    overlap_s = 1.0 if overlaps == 0 and label_ov == 0 else 0.0
    nn_s = _band_score(nn, NN_SWEET_LO, NN_SWEET_HI, hard_lo=40.0, hard_hi=500.0)
    util_s = _band_score(util, UTIL_SWEET_LO, UTIL_SWEET_HI, hard_lo=0.01, hard_hi=1.2)
    hull_s = _band_score(hull_u, UTIL_SWEET_LO, UTIL_SWEET_HI + 0.1, hard_lo=0.02, hard_hi=1.5)
    grid_s = _band_score(grid_occ, 0.25, 0.75, hard_lo=0.02, hard_hi=1.0)
    stretch_s = _band_score(stretch_f, EDGE_SWEET_LO, EDGE_SWEET_HI, hard_lo=0.2, hard_hi=8.0)
    white_s = max(0.0, 1.0 - white)  # less whitespace → better
    chain_s = max(0.0, min(1.0, chain_s))
    rings_s = max(0.0, min(1.0, rings_s))
    edge_clr_s = max(0.0, min(1.0, edge_clr_s))
    edge_axis_s = max(0.0, min(1.0, edge_axis_s))

    weights = {
        "overlap": 0.24,
        "crossing": 0.18,
        "utilization": 0.12,
        "chain": 0.10,
        "rings": 0.10,
        "edge_clearance": 0.08,
        "edge_axis": 0.06,
        "grid": 0.04,
        "nn": 0.04,
        "hull": 0.02,
        "stretch": 0.02,
    }
    parts = {
        "overlap": round(overlap_s, 4),
        "crossing": round(cross_s, 4),
        "utilization": round(util_s, 4),
        "chain": round(chain_s, 4),
        "rings": round(rings_s, 4),
        "edge_clearance": round(edge_clr_s, 4),
        "edge_axis": round(edge_axis_s, 4),
        "grid": round(grid_s, 4),
        "nn": round(nn_s, 4),
        "hull": round(hull_s, 4),
        "stretch": round(stretch_s, 4),
        "compactness": round(white_s, 4),
    }
    # compactness folded into util/grid already; keep as diagnostic
    total = sum(parts[k] * weights[k] for k in weights)
    if overlap_s < 1.0:
        total *= 0.15  # hard gate: overlaps wreck the score
    total_100 = round(100.0 * total, 2)

    return {
        "total": total_100,
        "parts": parts,
        "weights": weights,
        "targets": {
            "nn_sweet": [NN_SWEET_LO, NN_SWEET_HI],
            "util_sweet": [UTIL_SWEET_LO, UTIL_SWEET_HI],
            "edge_stretch_sweet": [EDGE_SWEET_LO, EDGE_SWEET_HI],
            "cpl_ok": cpl_ok,
            "cpl_bad": cpl_bad,
            "chain_ok": 0.75,
            "chain_warn": 0.45,
            "rings_ok": 0.75,
            "rings_warn": 0.45,
            "edge_clearance_ok": 0.85,
            "edge_clearance_warn": 0.45,
            "edge_axis_ok": 0.75,
            "edge_axis_warn": 0.45,
        },
        "rank_key": [
            0 if overlap_s >= 1.0 else 1,
            -total_100,
            int(metrics.get("edge_crossings") or 0),
            -util,
            -chain_s,
            -rings_s,
            -edge_clr_s,
            -edge_axis_s,
        ],
        "hint": (
            "total∈[0,100]. Overlaps hard-gate the score. "
            "Mid-tier: chain=直链成一体、rings=最小环不被穿（各权 0.10）；"
            "edge_clearance=网元勿贴非关联边（权 0.08）；"
            "edge_axis=边宜水平/垂直且水平优先（权 0.06）. "
            "Raise utilization/grid_occupancy without overlaps; "
            "keep crossings_per_link near ~0.16 (mid-size reference) and nn_p50 in 140–220."
        ),
    }


def _status_from_score(part: float, *, fail_below: float = 0.01, warn_below: float = 0.55) -> str:
    if part <= fail_below:
        return "fail"
    if part < warn_below:
        return "warn"
    return "ok"


def _sparsity_status(util: float, white: float, grid_occ: float) -> str:
    if util < 0.03 or white > 0.85 or grid_occ < 0.05:
        return "fail"
    if util < 0.08 or white > 0.65 or grid_occ < 0.15:
        return "warn"
    return "ok"


def _headline(verdict_overall: str, dims: dict[str, Any]) -> str:
    bad = [k for k, v in dims.items() if v.get("status") == "fail"]
    warn = [k for k, v in dims.items() if v.get("status") == "warn"]
    labels = {
        "overlap": "重叠",
        "crossing": "交叉",
        "spacing": "间距",
        "sparsity": "稀疏/空旷",
        "edges": "边长",
        "chains": "直链不成一体",
        "rings": "最小环被穿",
        "edge_clearance": "网元贴边",
        "edge_axis": "斜边过多",
    }
    if bad:
        return "问题：" + "、".join(labels.get(k, k) for k in bad)
    if warn:
        return "可改进：" + "、".join(labels.get(k, k) for k in warn)
    if verdict_overall == "ok":
        return "布图验收通过"
    return f"overall={verdict_overall}"


def build_layout_report(metrics: dict[str, Any]) -> dict[str, Any]:
    """One structured report for the single analyze tool (agent-facing)."""
    score = metrics.get("score") or score_layout_components(metrics)
    grade = metrics.get("grade") or grade_layout(metrics)
    parts = score.get("parts") or {}
    targets = score.get("targets") or {}

    fp = int(metrics.get("footprint_overlap_pairs") or 0)
    lbl = int(metrics.get("label_overlap_pairs") or 0)
    util = float(metrics.get("space_utilization") or 0.0)
    white = float(metrics.get("whitespace_index") or 0.0)
    grid_occ = float(metrics.get("grid_occupancy") or 0.0)
    nn_p50 = metrics.get("nn_p50")
    stretch = metrics.get("edge_stretch_p50")

    overlap = {
        "status": "ok" if fp == 0 and lbl == 0 else "fail",
        "score": parts.get("overlap"),
        "footprint_pairs": fp,
        "label_pairs": lbl,
        "hard_zero": True,
        "tip": "图标+名称 AABB 不得互挡；有重叠则总分硬门控。",
    }
    top_x = metrics.get("top_crossing_nodes") or []
    top_e = metrics.get("top_crossing_edges") or []
    crossing = {
        "status": grade.get("crossing_grade") or _status_from_score(float(parts.get("crossing") or 0)),
        "score": parts.get("crossing"),
        "edge_crossings": metrics.get("edge_crossings"),
        "crossings_per_link": metrics.get("crossings_per_link"),
        "crossings_per_node": metrics.get("crossings_per_node"),
        "top_nodes": top_x,
        "top_edges": top_e,
        "budget": {
            "cpl_ok": targets.get("cpl_ok"),
            "cpl_bad": targets.get("cpl_bad"),
            "cross_warn": (grade.get("budgets") or {}).get("cross_warn"),
            "cross_fail": (grade.get("budgets") or {}).get("cross_fail"),
        },
        "tip": (
            "无向 NE↔NE 真交叉（共端点不算）；优先看 crossings_per_link。"
            " top_nodes=交叉最重前5网元；top_edges=交叉点最多的前5条边；"
            "阶段2优先处理它们的端点/邻边。"
        ),
    }
    spacing = {
        "status": (
            "fail"
            if nn_p50 is not None and float(nn_p50) < 80
            else "warn"
            if nn_p50 is not None and float(nn_p50) < 150
            else "ok"
            if nn_p50 is not None
            else "warn"
        ),
        "score": parts.get("nn"),
        "nn_min": metrics.get("nn_min"),
        "nn_p10": metrics.get("nn_p10"),
        "nn_p50": nn_p50,
        "pairs_closer_than_min_dist": metrics.get("pairs_closer_than_min_dist"),
        "sweet": targets.get("nn_sweet") or [NN_SWEET_LO, NN_SWEET_HI],
        "tip": "中位最近邻宜在 140–220；过小挤、过大浪费。",
    }
    sparsity = {
        "status": _sparsity_status(util, white, grid_occ),
        "score": parts.get("utilization"),
        "space_utilization": util,
        "hull_utilization": metrics.get("hull_utilization"),
        "grid_occupancy": grid_occ,
        "empty_grid_ratio": metrics.get("empty_grid_ratio"),
        "whitespace_index": white,
        "footprint_fill": metrics.get("footprint_fill"),
        "aspect_ratio": metrics.get("aspect_ratio"),
        "sweet_util": targets.get("util_sweet") or [UTIL_SWEET_LO, UTIL_SWEET_HI],
        "tip": (
            "util=推荐瓦片×n/bbox；grid_occupancy=有点格子占比；"
            "whitespace_index→1 表示太空旷。目标 util≈0.12–0.45。"
        ),
    }
    edges = {
        "status": _status_from_score(float(parts.get("stretch") or 0), fail_below=0.15, warn_below=0.5),
        "score": parts.get("stretch"),
        "edge_len_p50": metrics.get("edge_len_p50"),
        "edge_len_p90": metrics.get("edge_len_p90"),
        "edge_stretch_p50": stretch,
        "sweet": targets.get("edge_stretch_sweet") or [EDGE_SWEET_LO, EDGE_SWEET_HI],
        "tip": "中位边长/推荐步长；过大=走廊被拉爆，过小=叠在一起。",
    }

    chain_ok = float(targets.get("chain_ok") or 0.75)
    chain_warn = float(targets.get("chain_warn") or 0.45)
    rings_ok = float(targets.get("rings_ok") or 0.75)
    rings_warn = float(targets.get("rings_warn") or 0.45)
    chain_part = float(parts.get("chain") if parts.get("chain") is not None else 1.0)
    rings_part = float(parts.get("rings") if parts.get("rings") is not None else 1.0)

    def _mid_status(part: float, ok: float, warn: float) -> str:
        if part >= ok:
            return "ok"
        if part >= warn:
            return "warn"
        return "fail"

    chains = {
        "status": _mid_status(chain_part, chain_ok, chain_warn),
        "score": parts.get("chain"),
        "chain_count": metrics.get("chain_count"),
        "chain_nodes": metrics.get("chain_nodes"),
        "straightness_p50": metrics.get("chain_straightness_p50"),
        "straightness_mean": metrics.get("chain_straightness_mean"),
        "kink_count": metrics.get("chain_kink_count"),
        "kink_frac": metrics.get("chain_kink_frac"),
        "budget": {"ok": chain_ok, "warn": chain_warn},
        "tip": metrics.get("chain_tip")
        or "deg≤2 走廊应近似共线成一体；折角少、chord/path≈1。",
    }
    rings = {
        "status": _mid_status(rings_part, rings_ok, rings_warn),
        "score": parts.get("rings"),
        "ring_count": metrics.get("ring_count"),
        "rings_pierced": metrics.get("rings_pierced"),
        "pierce_crossings": metrics.get("ring_pierce_crossings"),
        "budget": {"ok": rings_ok, "warn": rings_warn},
        "tip": metrics.get("rings_tip")
        or "弦无关短环（3–8）边界不应被环外边穿越。",
    }
    clr_ok = float(targets.get("edge_clearance_ok") or 0.85)
    clr_warn = float(targets.get("edge_clearance_warn") or 0.45)
    clr_part = float(
        parts.get("edge_clearance") if parts.get("edge_clearance") is not None else 1.0
    )
    edge_clearance = {
        "status": _mid_status(clr_part, clr_ok, clr_warn),
        "score": parts.get("edge_clearance"),
        "hits": metrics.get("edge_clearance_hits"),
        "nodes_hit": metrics.get("nodes_hit"),
        "min_clearance_p50": metrics.get("min_clearance_p50"),
        "thr": metrics.get("edge_clearance_thr"),
        "top": metrics.get("top_edge_hits") or [],
        "budget": {"ok": clr_ok, "warn": clr_warn},
        "tip": metrics.get("edge_clearance_tip")
        or "非关联边不得擦过网元；阶段2 clear_edge_hits。",
    }
    axis_ok = float(targets.get("edge_axis_ok") or 0.75)
    axis_warn = float(targets.get("edge_axis_warn") or 0.45)
    axis_part = float(
        parts.get("edge_axis") if parts.get("edge_axis") is not None else 1.0
    )
    edge_axis = {
        "status": _mid_status(axis_part, axis_ok, axis_warn),
        "score": parts.get("edge_axis"),
        "axis_frac": metrics.get("axis_frac"),
        "horiz_frac": metrics.get("horiz_frac"),
        "vert_frac": metrics.get("vert_frac"),
        "diag_frac": metrics.get("diag_frac"),
        "horiz_n": metrics.get("horiz_n"),
        "vert_n": metrics.get("vert_n"),
        "diag_n": metrics.get("diag_n"),
        "tol_deg": metrics.get("edge_axis_tol_deg"),
        "top_skew": metrics.get("top_skew_edges") or [],
        "budget": {"ok": axis_ok, "warn": axis_warn},
        "tip": metrics.get("edge_axis_tip")
        or "边宜水平/垂直（水平优先）；阶段2 straighten_channels / polish。",
    }

    dims = {
        "overlap": overlap,
        "crossing": crossing,
        "spacing": spacing,
        "sparsity": sparsity,
        "edges": edges,
        "chains": chains,
        "rings": rings,
        "edge_clearance": edge_clearance,
        "edge_axis": edge_axis,
    }
    overall = grade.get("overall") or "fail"
    # sparsity / mid-tier fails should surface in overall
    order = {"ok": 0, "warn": 1, "fail": 2}
    for d in dims.values():
        st = str(d.get("status") or "ok")
        if order.get(st, 0) > order.get(overall, 0):
            overall = st

    issues = list(grade.get("issues") or [])
    if sparsity["status"] == "fail" and not any("space_utilization" in i for i in issues):
        issues.append(f"sparsity: util={util} whitespace={white} grid={grid_occ}")
    if sparsity["status"] == "warn" and not any("space_utilization" in i for i in issues):
        issues.append(f"sparsity_warn: util={util} whitespace={white}")
    if chains["status"] != "ok":
        issues.append(
            f"chain_{chains['status']}: straight_p50={chains.get('straightness_p50')} "
            f"kinks={chains.get('kink_count')} score={chains.get('score')}"
        )
    if rings["status"] != "ok":
        issues.append(
            f"rings_{rings['status']}: pierced={rings.get('rings_pierced')}/"
            f"{rings.get('ring_count')} pierce_x={rings.get('pierce_crossings')} "
            f"score={rings.get('score')}"
        )
    if edge_clearance["status"] != "ok":
        issues.append(
            f"edge_clearance_{edge_clearance['status']}: "
            f"hits={edge_clearance.get('hits')} nodes={edge_clearance.get('nodes_hit')} "
            f"score={edge_clearance.get('score')}"
        )
    if edge_axis["status"] != "ok":
        issues.append(
            f"edge_axis_{edge_axis['status']}: "
            f"H={edge_axis.get('horiz_n')} V={edge_axis.get('vert_n')} "
            f"D={edge_axis.get('diag_n')} score={edge_axis.get('score')}"
        )

    return {
        "verdict": {
            "overall": overall,
            "total": score.get("total"),
            "headline": _headline(overall, dims),
            "issues": issues,
        },
        "size": {
            "nodes": metrics.get("node_count"),
            "links": metrics.get("link_count"),
            "bbox": metrics.get("bbox"),
            "bbox_area": metrics.get("bbox_area"),
            "hull_area": metrics.get("hull_area"),
        },
        "overlap": overlap,
        "crossing": crossing,
        "spacing": spacing,
        "sparsity": sparsity,
        "edges": edges,
        "chains": chains,
        "rings": rings,
        "edge_clearance": edge_clearance,
        "edge_axis": edge_axis,
        "score": {
            "total": score.get("total"),
            "parts": parts,
            "weights": score.get("weights"),
            "rank_key": score.get("rank_key"),
            "targets": targets,
            "hint": score.get("hint"),
        },
        "guide": {
            "spacing": metrics.get("spacing_guide"),
            "crossing_definition": metrics.get("crossing_definition"),
            "how_to_read": (
                "只看本工具即可验收：verdict.total∈[0,100]；"
                "overlap/crossing/spacing/sparsity/edges/chains/rings/"
                "edge_clearance/edge_axis 各有 status。"
                "中档：chains/rings 各权 0.10；edge_clearance=网元贴边（0.08）；"
                "edge_axis=水平/垂直边且水平优先（0.06）。"
                "扫参用 score.rank_key（先零重叠，再高 total）。"
            ),
        },
    }


def analyze_layout_stats(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    with_meta: bool = False,
    ume_reference: bool = False,
    fast: bool = False,
) -> dict[str, Any]:
    """Full stats: flat metrics + composite score + unified report.

    ``fast=True`` skips ring-pierce (expensive on giant metro canvases) and
    still scores overlap/crossing/util/chains for apply gates + agent QA.
    """
    base = analyze_positions(nodes, edges, with_meta=with_meta)
    pos: dict[str, tuple[float, float]] = {}
    names: dict[str, str] = {}
    for n in nodes:
        if not isinstance(n, dict):
            continue
        fid = str(n.get("fabric_node_id") or n.get("id") or "").strip()
        if not fid:
            continue
        try:
            x = float(n.get("x") if n.get("x") is not None else 0.0)
            y = float(n.get("y") if n.get("y") is not None else 0.0)
        except (TypeError, ValueError):
            x, y = 0.0, 0.0
        pos[fid] = (x, y)
        names[fid] = str(n.get("name") or n.get("label") or fid)

    from netx_topology_mcp.layout_metrics import (
        collapse_links,
        compute_edge_axis,
        compute_edge_clearance,
    )
    from netx_topology_mcp.layout_topology_quality import (
        compute_chain_cohesion,
        compute_topology_quality,
    )

    links = collapse_links(edges)
    density = compute_density_stats(pos, names, links)
    if fast:
        chain_q = compute_chain_cohesion(pos, links)
        rings_q = {
            "ring_count": None,
            "rings_pierced": None,
            "pierce_crossings": None,
            "score": 1.0,
            "tip": "fast score: ring pierce skipped (n/links large).",
        }
    else:
        quality = compute_topology_quality(pos, links)
        chain_q = quality.get("chains") or {}
        rings_q = quality.get("rings") or {}
    clr_q = compute_edge_clearance(pos, links, names=names)
    axis_q = compute_edge_axis(pos, links, names=names)
    merged = {**base, **density}
    merged["space_utilization"] = density["space_utilization"]
    merged["chain_score"] = chain_q.get("score", 1.0)
    merged["chain_count"] = chain_q.get("chain_count")
    merged["chain_nodes"] = chain_q.get("chain_nodes")
    merged["chain_straightness_p50"] = chain_q.get("straightness_p50")
    merged["chain_straightness_mean"] = chain_q.get("straightness_mean")
    merged["chain_kink_count"] = chain_q.get("kink_count")
    merged["chain_kink_frac"] = chain_q.get("kink_frac")
    merged["chain_tip"] = chain_q.get("tip")
    merged["rings_score"] = rings_q.get("score", 1.0)
    merged["ring_count"] = rings_q.get("ring_count")
    merged["rings_pierced"] = rings_q.get("rings_pierced")
    merged["ring_pierce_crossings"] = rings_q.get("pierce_crossings")
    merged["rings_tip"] = rings_q.get("tip")
    merged["edge_clearance_score"] = clr_q.get("edge_clearance_score", 1.0)
    merged["edge_clearance_hits"] = clr_q.get("edge_clearance_hits")
    merged["nodes_hit"] = clr_q.get("nodes_hit")
    merged["min_clearance_p50"] = clr_q.get("min_clearance_p50")
    merged["top_edge_hits"] = clr_q.get("top_edge_hits") or []
    merged["edge_clearance_tip"] = clr_q.get("edge_clearance_tip")
    merged["edge_clearance_thr"] = clr_q.get("edge_clearance_thr")
    merged["edge_clearance_skipped"] = clr_q.get("edge_clearance_skipped")
    merged["edge_axis_score"] = axis_q.get("edge_axis_score", 1.0)
    merged["axis_frac"] = axis_q.get("axis_frac")
    merged["horiz_frac"] = axis_q.get("horiz_frac")
    merged["vert_frac"] = axis_q.get("vert_frac")
    merged["diag_frac"] = axis_q.get("diag_frac")
    merged["horiz_n"] = axis_q.get("horiz_n")
    merged["vert_n"] = axis_q.get("vert_n")
    merged["diag_n"] = axis_q.get("diag_n")
    merged["top_skew_edges"] = axis_q.get("top_skew_edges") or []
    merged["edge_axis_tip"] = axis_q.get("edge_axis_tip")
    merged["edge_axis_tol_deg"] = axis_q.get("edge_axis_tol_deg")
    merged["edge_axis_tol_px"] = axis_q.get("edge_axis_tol_px")
    score = score_layout_components(merged)
    grade = grade_layout(merged, ume_reference=ume_reference)
    packed = {
        **merged,
        "score": score,
        "grade": grade,
        "summary": {
            "total": score["total"],
            "overall": grade.get("overall"),
            "crossings": merged.get("edge_crossings"),
            "cpl": merged.get("crossings_per_link"),
            "overlaps": merged.get("footprint_overlap_pairs"),
            "nn_p50": merged.get("nn_p50"),
            "util": merged.get("space_utilization"),
            "grid_occ": merged.get("grid_occupancy"),
            "whitespace": merged.get("whitespace_index"),
            "edge_stretch": merged.get("edge_stretch_p50"),
            "chain": merged.get("chain_score"),
            "rings": merged.get("rings_score"),
            "rings_pierced": merged.get("rings_pierced"),
            "edge_clearance": merged.get("edge_clearance_score"),
            "edge_clearance_hits": merged.get("edge_clearance_hits"),
            "edge_axis": merged.get("edge_axis_score"),
            "axis_frac": merged.get("axis_frac"),
            "horiz_frac": merged.get("horiz_frac"),
            "diag_n": merged.get("diag_n"),
            "top_crossing": [
                {
                    "name": r.get("name"),
                    "hits": r.get("crossing_hits"),
                    "id": r.get("fabric_node_id"),
                }
                for r in (merged.get("top_crossing_nodes") or [])[:5]
            ],
            "top_crossing_edges": [
                {
                    "label": r.get("label"),
                    "hits": r.get("crossing_hits"),
                    "a": r.get("a_name"),
                    "b": r.get("b_name"),
                    "a_id": r.get("a_node_id"),
                    "b_id": r.get("b_node_id"),
                }
                for r in (merged.get("top_crossing_edges") or [])[:5]
            ],
        },
    }
    packed["report"] = build_layout_report(packed)
    # Align summary.overall with report (includes sparsity)
    packed["summary"]["overall"] = packed["report"]["verdict"]["overall"]
    packed["summary"]["headline"] = packed["report"]["verdict"]["headline"]
    return packed


