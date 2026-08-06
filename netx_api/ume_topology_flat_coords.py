"""Compose non-overlapping flat-world coordinates by packing per-region ME blocks."""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from .models import TopoFabricNode, TopoFolder, UmeTopoNode
from .topology_common import _utcnow

_log = logging.getLogger("netx.ume.topo_flat")

# Gap between packed region blocks (same units as UME local coords).
SLOT_PAD = 600.0


def recompute_flat_world_coords(db: Session) -> dict[str, Any]:
    """Pack each ME-owning region cluster into a non-overlapping block.

    Block key = ME's owning region (ume_sbn_id / region_folder_id), typically the
    leaf SBN that directly parents the ME — not collapsed to the L2 World child.
    Preserves relative geometry inside a block; area-sorted strip packing prevents
    city-on-city stacking.

    Does not clear TopoViewNode drag overrides (display layer merges those).
    """
    from .ume_topology_world import get_world_drill_folder, world_map_should_exist

    now = _utcnow()
    stats: dict[str, Any] = {"slots": 0, "nodes": 0, "cleared": 0, "skipped": False}

    fabric_by_ume: dict[str, TopoFabricNode] = {
        str(n.ume_ne_id): n
        for n in db.query(TopoFabricNode).filter(TopoFabricNode.ume_ne_id.isnot(None)).all()
        if str(n.ume_ne_id or "").strip()
    }

    # Only clear when World tree exists but rule 2A says no world map.
    # Before seed (no drill), still pack so apply() can set world_* coords.
    drill = get_world_drill_folder(db)
    if drill is not None and not world_map_should_exist(db):
        for fn in fabric_by_ume.values():
            attrs = dict(fn.attrs or {})
            if "ume" not in set(attrs.get("sources") or []):
                continue
            if fn.world_x is not None or fn.world_y is not None:
                fn.world_x = None
                fn.world_y = None
                stats["cleared"] += 1
        db.commit()
        stats["skipped"] = True
        _log.info("flat world coords skipped (2A not met): %s", stats)
        return stats

    folders_by_ref: dict[str, TopoFolder] = {
        str(f.external_ref): f
        for f in db.query(TopoFolder)
        .filter(TopoFolder.external_ref.isnot(None), TopoFolder.external_ref != "")
        .all()
        if str(f.external_ref or "").strip() and not str(f.external_ref).startswith("ume:")
    }

    # Block key → list of (local_x, local_y, fabric_node)
    blocks: dict[str, list[tuple[float, float, TopoFabricNode]]] = defaultdict(list)

    me_rows = (
        db.query(UmeTopoNode)
        .filter(UmeTopoNode.node_type == "TOPO_NODE_ME")
        .all()
    )
    for tn in me_rows:
        uid = str(tn.ume_ne_id or tn.node_id or "").strip()
        if not uid:
            continue
        fn = fabric_by_ume.get(uid)
        if fn is None:
            continue
        lx = float(tn.x_pos) if tn.x_pos is not None else float((fn.attrs or {}).get("ume_local_x") or 0)
        ly = float(tn.y_pos) if tn.y_pos is not None else float((fn.attrs or {}).get("ume_local_y") or 0)
        parent = str(tn.parent_node or "").strip() or "_orphan"
        blocks[parent].append((lx, ly, fn))

    # Manual fabric NEs tagged to a region folder (non-UME).
    for fn in db.query(TopoFabricNode).filter(TopoFabricNode.region_folder_id.isnot(None)).all():
        if fn.ume_ne_id and str(fn.ume_ne_id) in fabric_by_ume:
            # Already handled via UME topo rows when present.
            already = False
            for members in blocks.values():
                if any(x[2].id == fn.id for x in members):
                    already = True
                    break
            if already:
                continue
        attrs = dict(fn.attrs or {})
        sid = str(attrs.get("ume_sbn_id") or "").strip()
        key = sid or f"folder:{fn.region_folder_id}"
        lx = float(attrs.get("ume_local_x") or 0)
        ly = float(attrs.get("ume_local_y") or 0)
        if any(x[2].id == fn.id for x in blocks[key]):
            continue
        blocks[key].append((lx, ly, fn))

    if not blocks:
        db.commit()
        _log.info("flat world coords: no blocks")
        return stats

    slot_meta: dict[str, dict[str, float]] = {}
    for key, members in blocks.items():
        xs = [m[0] for m in members]
        ys = [m[1] for m in members]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        w = max(max_x - min_x, 1.0) + SLOT_PAD
        h = max(max_y - min_y, 1.0) + SLOT_PAD
        slot_meta[key] = {
            "min_x": min_x,
            "min_y": min_y,
            "w": w,
            "h": h,
            "area": w * h,
        }

    # Area-descending strip pack into ~sqrt(n) columns (largest first).
    keys = sorted(blocks.keys(), key=lambda k: (-slot_meta[k]["area"], k))
    n = max(1, len(keys))
    cols = max(1, int(math.ceil(math.sqrt(n))))
    col_widths: list[float] = [0.0] * cols
    row_heights: dict[int, float] = defaultdict(float)
    for i, key in enumerate(keys):
        c, r = i % cols, i // cols
        meta = slot_meta[key]
        col_widths[c] = max(col_widths[c], meta["w"])
        row_heights[r] = max(row_heights[r], meta["h"])

    col_origin = [0.0]
    for c in range(cols - 1):
        col_origin.append(col_origin[-1] + col_widths[c])
    row_origin: dict[int, float] = {0: 0.0}
    for r in range(1, (n // cols) + 2):
        row_origin[r] = row_origin.get(r - 1, 0.0) + row_heights.get(r - 1, 0.0)

    origins: dict[str, tuple[float, float]] = {}
    for i, key in enumerate(keys):
        c, r = i % cols, i // cols
        origins[key] = (col_origin[c], row_origin[r])
        stats["slots"] += 1

    touched: set[str] = set()
    for key, members in blocks.items():
        ox, oy = origins[key]
        meta = slot_meta[key]
        folder = folders_by_ref.get(key)
        for lx, ly, fn in members:
            attrs = dict(fn.attrs or {})
            attrs["ume_local_x"] = lx
            attrs["ume_local_y"] = ly
            if key and not key.startswith("folder:") and not key.startswith("_"):
                attrs["ume_sbn_id"] = key[:128]
            if folder is not None:
                fn.region_folder_id = folder.id
                fn.region_source = fn.region_source or "ume"
            fn.attrs = attrs
            fn.world_x = ox + (lx - meta["min_x"])
            fn.world_y = oy + (ly - meta["min_y"])
            fn.updated_at = now
            touched.add(fn.id)
            stats["nodes"] += 1

    for fn in fabric_by_ume.values():
        if fn.id in touched:
            continue
        attrs = dict(fn.attrs or {})
        if "ume" not in set(attrs.get("sources") or []):
            continue
        fn.world_x = None
        fn.world_y = None
        stats["cleared"] += 1

    db.commit()
    _log.info("flat world coords recomputed: %s", stats)
    return stats
