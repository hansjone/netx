"""Suggest next non-dual sink batches: rank hub territories for move_nodes(park).

After the one-shot dual_unit eye is fixed, remaining access should migrate by
hub territory (not another dual sink). This module turns structure hubs +
soft_blocks into ordered batches of fabric_node_ids.
"""

from __future__ import annotations

from typing import Any


_LAYER_RANK = {"agg": 0, "core": 1, "access": 2, "external": 3, "other": 4}


def _as_id_set(raw: Any) -> set[str]:
    out: set[str] = set()
    if raw is None:
        return out
    if isinstance(raw, (str, bytes)):
        s = str(raw).strip()
        if s:
            out.add(s)
        return out
    if isinstance(raw, (list, tuple, set)):
        for x in raw:
            s = str(x or "").strip()
            if s and not s.startswith("region:"):
                out.add(s)
    return out


def _portal_ids_from_dual(dual_units: dict[str, Any] | None) -> set[str]:
    out: set[str] = set()
    if not isinstance(dual_units, dict):
        return out
    for u in dual_units.get("units") or []:
        if not isinstance(u, dict):
            continue
        for k in ("portal_a", "portal_b"):
            pid = str(u.get(k) or "").strip()
            if pid:
                out.add(pid)
    return out


def _hub_index(hubs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for h in hubs or []:
        if not isinstance(h, dict):
            continue
        hid = str(h.get("fabric_node_id") or "").strip()
        if hid:
            out[hid] = h
    return out


def _blocks_by_hub(soft_blocks: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(soft_blocks, dict):
        return out
    for b in soft_blocks.get("blocks") or []:
        if not isinstance(b, dict):
            continue
        hid = str(b.get("hub_id") or "").strip()
        if not hid:
            continue
        # Prefer larger territory if duplicate hub rows
        prev = out.get(hid)
        n = len([x for x in (b.get("node_ids") or []) if x])
        if prev is None or n > len([x for x in (prev.get("node_ids") or []) if x]):
            out[hid] = b
    return out


def suggest_sink_hub_batches(
    *,
    hubs: list[dict[str, Any]] | None,
    soft_blocks: dict[str, Any] | None,
    source_ids: set[str],
    sink_ids: set[str] | None = None,
    exclude_ids: set[str] | None = None,
    dual_units: dict[str, Any] | None = None,
    min_territory: int = 1,
    min_move_n: int = 1,
    top_n: int = 12,
    include_hub: bool = True,
    only_layers: list[str] | None = None,
) -> dict[str, Any]:
    """Rank hub territories still on ``source_ids`` and not already on sink.

    Ranking (desc): remaining move count → remaining access stubs → prefer agg
    over core → structure hub score. Eye portals / ``exclude_ids`` never lead a
    batch (and are dropped from id lists).
    """
    src = {str(x) for x in (source_ids or ()) if str(x) and not str(x).startswith("region:")}
    on_sink = {str(x) for x in (sink_ids or ()) if str(x)}
    excluded = set(exclude_ids or ())
    excluded |= _portal_ids_from_dual(dual_units)

    layer_allow: set[str] | None = None
    if only_layers:
        layer_allow = {str(x).strip().lower() for x in only_layers if str(x).strip()}

    by_hub = _hub_index(list(hubs or []))
    blocks = _blocks_by_hub(soft_blocks)

    # Ensure every structure hub has a block (fallback: hub + stub_ids).
    for hid, h in by_hub.items():
        if hid in blocks:
            continue
        stubs = [str(x) for x in (h.get("stub_ids") or []) if str(x)]
        blocks[hid] = {
            "hub_id": hid,
            "method": "hub_stubs",
            "node_ids": [hid, *stubs],
            "node_count": 1 + len(stubs),
        }

    batches: list[dict[str, Any]] = []
    for hid, block in blocks.items():
        hmeta = by_hub.get(hid) or {}
        layer = str(hmeta.get("layer") or "other").lower()
        if layer_allow is not None and layer not in layer_allow:
            continue

        raw_ids = [str(x) for x in (block.get("node_ids") or []) if str(x)]
        # Still on source, not yet on sink, not excluded portals
        move_ids = [
            nid
            for nid in raw_ids
            if nid in src and nid not in on_sink and nid not in excluded
        ]
        if not include_hub:
            move_ids = [nid for nid in move_ids if nid != hid]
        # Hub may already be on sink — still migrate remaining territory
        if hid in on_sink and include_hub:
            move_ids = [nid for nid in move_ids if nid != hid]

        # Dedup preserve order
        seen: set[str] = set()
        ordered: list[str] = []
        # Eye portals / exclude_ids never lead a batch, but leftover stubs under
        # an already-sunk or portal hub may still migrate.
        if include_hub and hid in src and hid not in on_sink and hid not in excluded:
            ordered.append(hid)
            seen.add(hid)
        for nid in move_ids:
            if nid in seen:
                continue
            seen.add(nid)
            ordered.append(nid)
        # Portal hub with nothing left to move (except itself) → skip
        if hid in excluded and not ordered:
            continue

        if len(ordered) < max(1, int(min_move_n)):
            continue

        # Territory signal: prefer structure territory, else remaining stubs
        struct_terr = int(hmeta.get("territory") or 0)
        remaining_n = len(ordered)
        # Count non-hub members as remaining territory proxy
        remaining_terr = max(0, remaining_n - (1 if hid in ordered else 0))
        if remaining_terr < max(0, int(min_territory)):
            continue

        access_n = int(hmeta.get("access_neighbors") or 0)
        score = float(hmeta.get("score") or 0.0)
        batches.append(
            {
                "hub_id": hid,
                "hub_name": str(hmeta.get("name") or hid),
                "layer": layer,
                "degree": int(hmeta.get("degree") or 0),
                "structure_territory": struct_terr,
                "structure_access_neighbors": access_n,
                "structure_score": score,
                "remaining_n": remaining_n,
                "remaining_territory": remaining_terr,
                "block_method": str(block.get("method") or ""),
                "fabric_node_ids": ordered,
                "already_on_sink": hid in on_sink,
                "excluded_from_batch": sorted(
                    nid for nid in raw_ids if nid in excluded or nid in on_sink
                )[:40],
            }
        )

    batches.sort(
        key=lambda b: (
            -int(b["remaining_territory"]),
            -int(b["remaining_n"]),
            _LAYER_RANK.get(str(b["layer"]), 9),
            -float(b["structure_score"]),
            str(b["hub_name"]),
        )
    )

    # Orphan leftovers: source ids with no hub territory still need park batches.
    covered: set[str] = set()
    for b in batches:
        covered |= set(b.get("fabric_node_ids") or [])
    orphans = sorted(
        nid
        for nid in src
        if nid not in on_sink and nid not in excluded and nid not in covered
    )
    orphan_batches: list[dict[str, Any]] = []
    if orphans and len(batches) < max(1, min(40, int(top_n))):
        # Chunk orphans so park stays stable (~8 per batch).
        chunk = 8
        for i in range(0, len(orphans), chunk):
            ids = orphans[i : i + chunk]
            orphan_batches.append(
                {
                    "hub_id": ids[0],
                    "hub_name": f"orphan_batch_{i // chunk + 1}",
                    "layer": "other",
                    "degree": 0,
                    "structure_territory": 0,
                    "structure_access_neighbors": 0,
                    "structure_score": 0.0,
                    "remaining_n": len(ids),
                    "remaining_territory": len(ids),
                    "block_method": "orphan_leftovers",
                    "fabric_node_ids": ids,
                    "already_on_sink": False,
                    "excluded_from_batch": [],
                    "orphan": True,
                }
            )
        batches.extend(orphan_batches)

    top = batches[: max(1, min(40, int(top_n)))]
    for i, b in enumerate(top, start=1):
        b["rank"] = i

    return {
        "ok": True,
        "batch_count": len(top),
        "excluded_n": len(excluded),
        "excluded_ids": sorted(excluded)[:40],
        "source_n": len(src),
        "sink_n": len(on_sink),
        "orphan_n": len(orphans),
        "batches": top,
        "hint": (
            "Pick batches[0] (or pick=N) → layoutTopologyView move_nodes "
            "source→sink with park=true. Do NOT sinkTopologyDualUnits again. "
            "Eye portals are excluded from leading a batch. "
            "orphan_batch_* = disconnected leftovers (park in chunks)."
        ),
    }


def pick_batch(report: dict[str, Any], pick: int = 1) -> dict[str, Any] | None:
    batches = list(report.get("batches") or [])
    if not batches:
        return None
    idx = max(1, min(len(batches), int(pick or 1))) - 1
    return dict(batches[idx])
