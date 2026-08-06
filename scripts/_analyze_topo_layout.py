"""Analyze imported UME topo + fabric for layout strategy."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import text
from netx_api.db import engine

out: dict = {}

with engine.connect() as c:
    def q(sql: str, **kw):
        return c.execute(text(sql), kw).fetchall()

    def scalar(sql: str, **kw):
        return c.execute(text(sql), kw).scalar()

    out["counts"] = {
        "topo_nodes": scalar("select count(*) from ume_topo_node"),
        "topo_me": scalar("select count(*) from ume_topo_node where node_type='TOPO_NODE_ME'"),
        "topo_sbn": scalar("select count(*) from ume_topo_node where node_type='TOPO_NODE_SBN'"),
        "topo_links": scalar("select count(*) from ume_topo_link"),
        "fabric_nodes": scalar("select count(*) from topo_fabric_node"),
        "fabric_edges": scalar("select count(*) from topo_fabric_edge"),
        "inventory": scalar("select count(*) from ume_inventory_ne"),
    }

    # coordinate extent for ME only
    row = q(
        """
        select
          min(x_pos), max(x_pos), min(y_pos), max(y_pos),
          avg(x_pos::float), avg(y_pos::float),
          count(*) filter (where x_pos is null or y_pos is null),
          count(*) filter (where x_pos=0 and y_pos=0)
        from ume_topo_node where node_type='TOPO_NODE_ME'
        """
    )[0]
    out["me_bbox"] = {
        "min_x": row[0], "max_x": row[1], "min_y": row[2], "max_y": row[3],
        "avg_x": round(float(row[4] or 0), 1), "avg_y": round(float(row[5] or 0), 1),
        "null_xy": row[6], "zero_zero": row[7],
        "width": (row[1] or 0) - (row[0] or 0),
        "height": (row[3] or 0) - (row[2] or 0),
    }

    # SBN hierarchy depth + children
    sbns = q(
        """
        select node_id, user_label, parent_node, x_pos, y_pos
        from ume_topo_node where node_type='TOPO_NODE_SBN'
        """
    )
    me_by_parent = q(
        """
        select parent_node, count(*)
        from ume_topo_node
        where node_type='TOPO_NODE_ME'
        group by parent_node
        order by count(*) desc
        """
    )
    out["sbn_count"] = len(sbns)
    out["me_per_parent_top20"] = [
        {"parent": r[0], "me_count": r[1]} for r in me_by_parent[:20]
    ]
    out["me_parent_buckets"] = {
        "parents_gt_2000": sum(1 for r in me_by_parent if r[1] > 2000),
        "parents_gt_500": sum(1 for r in me_by_parent if r[1] > 500),
        "parents_gt_100": sum(1 for r in me_by_parent if r[1] > 100),
        "parents_le_100": sum(1 for r in me_by_parent if r[1] <= 100),
        "max_me_in_one_parent": me_by_parent[0][1] if me_by_parent else 0,
    }

    # resolve parent labels
    sbn_label = {r[0]: r[1] for r in sbns}
    labeled = []
    for r in me_by_parent[:25]:
        pid = r[0]
        labeled.append({
            "parent_id": pid,
            "parent_label": sbn_label.get(pid, pid[:40] if pid else ""),
            "me_count": r[1],
        })
    out["me_per_sbn_top25"] = labeled

    # SBN tree: roots vs nested
    sbn_ids = {r[0] for r in sbns}
    roots = []
    nested = []
    for r in sbns:
        nid, label, parent, x, y = r
        if parent in sbn_ids:
            nested.append({"id": nid, "label": label, "parent": parent, "parent_label": sbn_label.get(parent, "")})
        else:
            roots.append({"id": nid, "label": label, "parent": parent, "x": x, "y": y})
    out["sbn_roots"] = roots
    out["sbn_nested_count"] = len(nested)
    out["sbn_nested_sample"] = nested[:30]

    # ME count under each root SBN (walk parents)
    parent_of = {r[0]: r[2] for r in sbns}
    me_parent_map = {r[0]: r[1] for r in me_by_parent}  # wrong - me_by_parent is list of (parent, count)

    # build node->parent for all nodes
    all_parents = q("select node_id, parent_node, node_type from ume_topo_node")
    pmap = {r[0]: r[1] for r in all_parents}
    ntype = {r[0]: r[2] for r in all_parents}

    def root_sbn(nid: str) -> str:
        seen = set()
        cur = nid
        last_sbn = nid if ntype.get(nid) == "TOPO_NODE_SBN" else ""
        # climb from parent
        cur = pmap.get(nid, "")
        while cur and cur not in seen:
            seen.add(cur)
            if ntype.get(cur) == "TOPO_NODE_SBN":
                last_sbn = cur
            # stop at non-uuid / MD= root
            if cur not in pmap and cur not in sbn_ids:
                break
            nxt = pmap.get(cur)
            if not nxt or nxt == cur:
                break
            cur = nxt
        return last_sbn or "unknown"

    root_me_counts: Counter = Counter()
    for r in q("select node_id from ume_topo_node where node_type='TOPO_NODE_ME'"):
        # climb from ME's parent
        parent = pmap.get(r[0], "")
        rs = parent
        # find topmost SBN
        seen = set()
        cur = parent
        top = parent if parent in sbn_ids else ""
        while cur and cur not in seen:
            seen.add(cur)
            if cur in sbn_ids:
                top = cur
            cur = pmap.get(cur, "")
            if not cur:
                break
        # prefer root among SBN chain: keep climbing while parent is SBN
        while top and parent_of.get(top) in sbn_ids:
            top = parent_of[top]
        root_me_counts[top or "no_sbn"] += 1

    out["me_per_root_sbn"] = [
        {"root_id": k, "label": sbn_label.get(k, k), "me_count": v}
        for k, v in root_me_counts.most_common()
    ]

    # per-root bbox for ME
    root_bbox = {}
    for r in q(
        "select node_id, parent_node, x_pos, y_pos from ume_topo_node where node_type='TOPO_NODE_ME' and x_pos is not null"
    ):
        nid, parent, x, y = r
        top = parent if parent in sbn_ids else ""
        seen = set()
        cur = parent
        while cur and cur not in seen:
            seen.add(cur)
            if cur in sbn_ids:
                top = cur
            cur = pmap.get(cur, "")
        while top and parent_of.get(top) in sbn_ids:
            top = parent_of[top]
        key = top or "no_sbn"
        b = root_bbox.setdefault(key, {"min_x": x, "max_x": x, "min_y": y, "max_y": y, "n": 0})
        b["min_x"] = min(b["min_x"], x)
        b["max_x"] = max(b["max_x"], x)
        b["min_y"] = min(b["min_y"], y)
        b["max_y"] = max(b["max_y"], y)
        b["n"] += 1
    out["root_sbn_bbox"] = [
        {
            "root_id": k,
            "label": sbn_label.get(k, k),
            "n": v["n"],
            "width": v["max_x"] - v["min_x"],
            "height": v["max_y"] - v["min_y"],
            "min_x": v["min_x"],
            "max_x": v["max_x"],
            "min_y": v["min_y"],
            "max_y": v["max_y"],
        }
        for k, v in sorted(root_bbox.items(), key=lambda kv: -kv[1]["n"])
    ]

    # grid density: how many ME in 100x100 cells
    cells = Counter()
    for r in q(
        "select x_pos, y_pos from ume_topo_node where node_type='TOPO_NODE_ME' and x_pos is not null"
    ):
        cells[(r[0] // 100, r[1] // 100)] += 1
    dens = sorted(cells.values(), reverse=True)
    out["grid100_density"] = {
        "cells": len(cells),
        "max_per_cell": dens[0] if dens else 0,
        "p95": dens[int(len(dens) * 0.05)] if dens else 0,
        "median": dens[len(dens) // 2] if dens else 0,
        "cells_gt_50": sum(1 for d in dens if d > 50),
        "cells_gt_20": sum(1 for d in dens if d > 20),
    }

    # UME links vs fabric: endpoint overlap
    out["link_stats"] = {
        "ume_links": scalar("select count(*) from ume_topo_link"),
        "ume_connected": scalar(
            "select count(*) from ume_topo_link where connection_status='Connected'"
        ),
        "unique_a_ptp": scalar("select count(distinct a_ptp) from ume_topo_link"),
        "links_both_in_fabric": scalar(
            """
            select count(*) from ume_topo_link l
            join topo_fabric_node a on a.ume_ne_id=l.a_ume_ne_id
            join topo_fabric_node z on z.ume_ne_id=l.z_ume_ne_id
            """
        ),
    }

    # approximate undirected edge key match UME vs LLDP fabric
    # fabric ports are like xxvgei-1/1/0/32; ume ptp is /p=1_28
    out["port_sample_ume"] = [
        dict(a_ptp=r[0], z_ptp=r[1], a_ne=r[2], z_ne=r[3])
        for r in q(
            "select a_ptp, z_ptp, a_ume_ne_id, z_ume_ne_id from ume_topo_link limit 5"
        )
    ]
    out["port_sample_fabric"] = [
        dict(a_port=r[0], b_port=r[1])
        for r in q("select a_port, b_port from topo_fabric_edge limit 5")
    ]

    # same NE-pair undirected overlap ignoring ports
    ume_pairs = {
        frozenset((r[0], r[1]))
        for r in q(
            "select a_ume_ne_id, z_ume_ne_id from ume_topo_link where a_ume_ne_id<>'' and z_ume_ne_id<>''"
        )
        if r[0] != r[1]
    }
    fab_pairs = set()
    for r in q(
        """
        select a.ume_ne_id, b.ume_ne_id
        from topo_fabric_edge e
        join topo_fabric_node a on a.id=e.a_node_id
        join topo_fabric_node b on b.id=e.b_node_id
        where a.ume_ne_id is not null and b.ume_ne_id is not null
        """
    ):
        if r[0] and r[1] and r[0] != r[1]:
            fab_pairs.add(frozenset((r[0], r[1])))
    inter = ume_pairs & fab_pairs
    out["ne_pair_overlap"] = {
        "ume_undirected_pairs": len(ume_pairs),
        "fabric_undirected_pairs": len(fab_pairs),
        "intersection": len(inter),
        "ume_only": len(ume_pairs - fab_pairs),
        "fabric_only": len(fab_pairs - ume_pairs),
    }

    # inventory vendor / device_level under topo
    out["inventory_levels"] = [
        {"device_level": r[0], "n": r[1]}
        for r in q(
            "select coalesce(nullif(device_level,''),'?'), count(*) from ume_inventory_ne group by 1 order by 2 desc"
        )
    ]

Path("ume/_topo_analysis.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
)
print("wrote ume/_topo_analysis.json")
for k in ("counts", "me_bbox", "me_parent_buckets", "ne_pair_overlap", "grid100_density"):
    print(k, json.dumps(out[k], ensure_ascii=False, default=str))
print("roots", len(out["sbn_roots"]), "nested_sbn", out["sbn_nested_count"])
print("me_per_root", json.dumps(out["me_per_root_sbn"][:15], ensure_ascii=False))
