"""Hierarchical UME level canvas + flat packing tests."""

from __future__ import annotations

import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import netx_api.models  # noqa: F401
from netx_api.db import Base
from netx_api.models import TopoFabricNode, TopoFolder, TopoView, UmeInventoryNE, UmeTopoLink, UmeTopoNode
from netx_api.ume_topology_apply import apply_ume_topology_to_fabric
from netx_api.ume_topology_flat_coords import recompute_flat_world_coords
from netx_api.ume_topology_world import ensure_ume_world_and_sbn_folders, get_world_view
from netx_api.ume_topology_world_graph import get_level_view_graph, get_flat_view_graph


class UmeHierarchyTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        TestingSessionLocal = sessionmaker(
            bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
        )
        Base.metadata.create_all(bind=engine)
        self.db = TestingSessionLocal()
        self.now = datetime(2026, 8, 6, 12, 0, 0)

    def tearDown(self):
        self.db.close()

    def _seed_tree(self):
        # MD
        #  └─ root-sbn (x=10,y=20)
        #       ├─ city-a (x=100,y=100) → ME-A1, ME-A2
        #       └─ city-b (x=200,y=100) → ME-B1
        #  └─ direct ME under MD (rare)
        for sid, label, parent, x, y in [
            ("root-sbn", "Root SBN", "md-virtual", 10, 20),
            ("city-a", "City A", "root-sbn", 100, 100),
            ("city-b", "City B", "root-sbn", 200, 100),
        ]:
            self.db.add(
                UmeTopoNode(
                    node_id=sid,
                    name=f"SBN={sid}",
                    node_type="TOPO_NODE_SBN",
                    user_label=label,
                    parent_node=parent,
                    x_pos=x,
                    y_pos=y,
                    first_seen_at=self.now,
                    last_seen_at=self.now,
                )
            )
        mes = [
            ("me-a1", "A1", "city-a", 10, 10),
            ("me-a2", "A2", "city-a", 50, 10),
            ("me-b1", "B1", "city-b", 10, 10),  # same local as A1 — would collide if global
        ]
        for uid, label, parent, x, y in mes:
            self.db.add(
                UmeInventoryNE(
                    ne_id=uid,
                    ne_name=f"ME{{{uid}}}",
                    user_label=label,
                    host_name=label,
                    ip_address=f"10.0.0.{label[-1]}",
                )
            )
            self.db.add(
                UmeTopoNode(
                    node_id=uid,
                    name=f"ME={uid}",
                    node_type="TOPO_NODE_ME",
                    user_label=label,
                    parent_node=parent,
                    x_pos=x,
                    y_pos=y,
                    ume_ne_id=uid,
                    first_seen_at=self.now,
                    last_seen_at=self.now,
                )
            )
        # Cross-city link A1—B1
        self.db.add(
            UmeTopoLink(
                link_id="link-ab",
                name="TL{ab}",
                user_label="A1_xxvgei-1/1/0/1_B1_xxvgei-1/1/0/1",
                a_end_tp_ref="ME{me-a1},EQ={/r=0/sh=1/sl=1},PTP={/p=1_1}",
                z_end_tp_ref="ME{me-b1},EQ={/r=0/sh=1/sl=1},PTP={/p=1_1}",
                a_ume_ne_id="me-a1",
                z_ume_ne_id="me-b1",
                a_ifname="xxvgei-1/1/0/1",
                z_ifname="xxvgei-1/1/0/1",
                first_seen_at=self.now,
                last_seen_at=self.now,
            )
        )
        # Intra-city A1—A2
        self.db.add(
            UmeTopoLink(
                link_id="link-aa",
                name="TL{aa}",
                user_label="A1_xxvgei-1/1/0/2_A2_xxvgei-1/1/0/2",
                a_end_tp_ref="ME{me-a1},EQ={/r=0/sh=1/sl=1},PTP={/p=1_2}",
                z_end_tp_ref="ME{me-a2},EQ={/r=0/sh=1/sl=1},PTP={/p=1_2}",
                a_ume_ne_id="me-a1",
                z_ume_ne_id="me-a2",
                a_ifname="xxvgei-1/1/0/2",
                z_ifname="xxvgei-1/1/0/2",
                first_seen_at=self.now,
                last_seen_at=self.now,
            )
        )
        self.db.commit()

    def test_seed_views_and_level_graph(self):
        self._seed_tree()
        apply_ume_topology_to_fabric(self.db)
        stats = ensure_ume_world_and_sbn_folders(self.db)
        self.assertTrue(stats["world_view_id"])
        self.assertTrue(stats["world_flat_view_id"])
        self.assertGreaterEqual(stats["sbn_views"], 3)

        world = get_world_view(self.db)
        self.assertIsNotNone(world)
        # World view hangs under World drill folder, Flat under UME World container.
        from netx_api.ume_topology_world import WORLD_DRILL_REF, WORLD_FOLDER_NAME

        drill = (
            self.db.query(TopoFolder)
            .filter(TopoFolder.external_ref == WORLD_DRILL_REF)
            .one()
        )
        container = (
            self.db.query(TopoFolder)
            .filter(TopoFolder.name == WORLD_FOLDER_NAME)
            .one()
        )
        self.assertEqual(drill.parent_id, container.id)
        self.assertEqual(world.folder_id, drill.id)
        root_sbn = (
            self.db.query(TopoFolder)
            .filter(TopoFolder.external_ref == "root-sbn")
            .one()
        )
        self.assertEqual(root_sbn.parent_id, drill.id)

        g = get_level_view_graph(self.db, world)
        kinds = {n.kind for n in g.nodes}
        self.assertIn("region", kinds)
        # Root level shows root-sbn as region, not city MEs
        region_ids = {n.ume_ne_id for n in g.nodes if n.kind == "region"}
        self.assertIn("root-sbn", region_ids)
        self.assertNotIn("city-a", region_ids)

        # Open root-sbn level: cities as regions + logical link between them
        root_view = (
            self.db.query(TopoView)
            .filter(TopoView.filter["sbn_id"].as_string() == "root-sbn")
            .first()
        )
        # SQLite JSON path may differ — find by filter scan
        if root_view is None:
            for v in self.db.query(TopoView).all():
                if str((v.filter or {}).get("sbn_id") or "") == "root-sbn":
                    root_view = v
                    break
        self.assertIsNotNone(root_view)
        g2 = get_level_view_graph(self.db, root_view)
        regions = [n for n in g2.nodes if n.kind == "region"]
        self.assertEqual({n.ume_ne_id for n in regions}, {"city-a", "city-b"})
        logical = [e for e in g2.edges if e.layer == "logical"]
        self.assertGreaterEqual(len(logical), 1)

        # Leaf city-a: only MEs + physical edge
        leaf = None
        for v in self.db.query(TopoView).all():
            if str((v.filter or {}).get("sbn_id") or "") == "city-a":
                leaf = v
                break
        self.assertIsNotNone(leaf)
        g3 = get_level_view_graph(self.db, leaf)
        self.assertTrue(all(n.kind == "ne" for n in g3.nodes))
        self.assertEqual(len(g3.nodes), 2)
        self.assertTrue(any(e.layer == "physical" for e in g3.edges))

    def test_region_drag_position_persists_on_ume_level(self):
        """Drag/save writes TopoViewNode; reload must not snap back to UME x_pos."""
        from netx_api.topology_schemas import ViewNodeIn, ViewPositionsPatch
        from netx_api.topology_views_graph import get_view_graph, patch_view_positions

        self._seed_tree()
        apply_ume_topology_to_fabric(self.db)
        ensure_ume_world_and_sbn_folders(self.db)
        world = get_world_view(self.db)
        self.assertIsNotNone(world)
        g0 = get_level_view_graph(self.db, world)
        region = next(n for n in g0.nodes if n.kind == "region" and n.ume_ne_id == "root-sbn")
        self.assertEqual(region.x, 10.0)
        self.assertEqual(region.y, 20.0)

        saved = patch_view_positions(
            self.db,
            world.id,
            ViewPositionsPatch(
                positions=[ViewNodeIn(fabric_node_id=region.fabric_node_id, x=777.0, y=888.0)],
                return_graph=True,
            ),
        )
        # Save return_graph must stay on the UME canvas (not empty membership graph).
        self.assertTrue(any(n.kind == "region" for n in saved.nodes))
        moved = next(n for n in saved.nodes if n.fabric_node_id == region.fabric_node_id)
        self.assertEqual(moved.x, 777.0)
        self.assertEqual(moved.y, 888.0)

        g1 = get_level_view_graph(self.db, world)
        again = next(n for n in g1.nodes if n.fabric_node_id == region.fabric_node_id)
        self.assertEqual(again.x, 777.0)
        self.assertEqual(again.y, 888.0)

        via_get = get_view_graph(self.db, world.id)
        via = next(n for n in via_get.nodes if n.fabric_node_id == region.fabric_node_id)
        self.assertEqual(via.x, 777.0)
        self.assertEqual(via.y, 888.0)

    def test_world_map_2a_and_tree_visibility(self):
        from netx_api.topology_views_tree import get_topology_tree
        from netx_api.ume_topology_world import (
            WORLD_FLAT_VIEW_NAME,
            get_world_container_folder,
            get_world_flat_view,
            world_map_should_exist,
        )

        self._seed_tree()
        apply_ume_topology_to_fabric(self.db)
        stats = ensure_ume_world_and_sbn_folders(self.db)
        self.assertTrue(world_map_should_exist(self.db))
        self.assertTrue(stats.get("world_map_visible"))
        flat = get_world_flat_view(self.db)
        self.assertIsNotNone(flat)
        self.assertFalse(bool((flat.filter or {}).get("suppressed")))

        tree = get_topology_tree(self.db)
        container = get_world_container_folder(self.db)
        self.assertIsNotNone(container)
        # Find container in tree
        def find(folder, fid):
            if folder.id == fid:
                return folder
            for c in folder.children or []:
                hit = find(c, fid)
                if hit:
                    return hit
            return None

        node = find(tree.root, container.id)
        self.assertIsNotNone(node)
        view_names = {v.name for v in node.views}
        self.assertIn(WORLD_FLAT_VIEW_NAME, view_names)
        # Only one L2 region child (World drill)
        region_kids = [c for c in node.children if c.kind == "region"]
        drill = [c for c in region_kids if c.external_ref == "ume:world:drill" or c.name == "World"]
        self.assertEqual(len(drill), 1)

    def test_world_map_forbids_add_nes(self):
        from fastapi import HTTPException

        from netx_api.topology_schemas import ViewNodesAdd
        from netx_api.topology_views_graph import add_nodes_to_view
        from netx_api.ume_topology_world import get_world_flat_view

        self._seed_tree()
        apply_ume_topology_to_fabric(self.db)
        ensure_ume_world_and_sbn_folders(self.db)
        flat = get_world_flat_view(self.db)
        self.assertIsNotNone(flat)
        with self.assertRaises(HTTPException) as ctx:
            add_nodes_to_view(
                self.db,
                flat.id,
                ViewNodesAdd(fabric_node_ids=["nope"], return_graph=False),
            )
        self.assertEqual(ctx.exception.detail, "world_map_no_direct_nes")

    def test_flat_lod_overview_and_detail(self):
        from netx_api.ume_topology_world import get_world_flat_view
        from netx_api.ume_topology_world_graph import (
            WORLD_FLAT_DETAIL_CAP,
            WORLD_FLAT_OVERVIEW_CAP,
            get_flat_view_graph,
        )

        self._seed_tree()
        apply_ume_topology_to_fabric(self.db)
        ensure_ume_world_and_sbn_folders(self.db)
        recompute_flat_world_coords(self.db)
        flat = get_world_flat_view(self.db)
        self.assertIsNotNone(flat)

        overview = get_flat_view_graph(self.db, flat, lod="overview")
        self.assertIsNotNone(overview.world_transform)
        self.assertEqual(overview.world_transform.lod, "overview")
        self.assertGreater(len(overview.nodes), 0)
        self.assertLessEqual(len(overview.nodes), WORLD_FLAT_OVERVIEW_CAP)
        self.assertEqual(len(overview.edges), 0)
        self.assertEqual(float(overview.world_transform.scale or 0), 1.0)
        # Intra-block spacing matches UME local deltas (1:1, not crushed).
        a1 = next(n for n in overview.nodes if n.ume_ne_id == "me-a1" or n.name == "A1")
        a2 = next(n for n in overview.nodes if n.ume_ne_id == "me-a2" or n.name == "A2")
        self.assertAlmostEqual(float(a2.x) - float(a1.x), 40.0)

        # Viewport around an existing overview node (display coords).
        hit = overview.nodes[0]
        detail = get_flat_view_graph(
            self.db,
            flat,
            lod="detail",
            min_x=float(hit.x) - 50,
            max_x=float(hit.x) + 50,
            min_y=float(hit.y) - 50,
            max_y=float(hit.y) + 50,
        )
        self.assertEqual(detail.world_transform.lod, "detail")
        self.assertGreater(len(detail.nodes), 0)
        self.assertLessEqual(len(detail.nodes), WORLD_FLAT_DETAIL_CAP)
        self.assertTrue(any(n.fabric_node_id == hit.fabric_node_id for n in detail.nodes))

        auto = get_flat_view_graph(self.db, flat, lod="auto")
        self.assertEqual(auto.world_transform.lod, "overview")

    def test_flat_drag_override_survives_recompute(self):
        from netx_api.topology_schemas import ViewNodeIn, ViewPositionsPatch
        from netx_api.topology_views_graph import patch_view_positions
        from netx_api.ume_topology_world import get_world_flat_view
        from netx_api.ume_topology_world_graph import get_flat_view_graph

        self._seed_tree()
        apply_ume_topology_to_fabric(self.db)
        ensure_ume_world_and_sbn_folders(self.db)
        flat = get_world_flat_view(self.db)
        self.assertIsNotNone(flat)
        g0 = get_flat_view_graph(self.db, flat)
        self.assertGreater(len(g0.nodes), 0)
        target = g0.nodes[0]
        patch_view_positions(
            self.db,
            flat.id,
            ViewPositionsPatch(
                positions=[ViewNodeIn(fabric_node_id=target.fabric_node_id, x=1111.0, y=2222.0)],
                return_graph=False,
            ),
        )
        recompute_flat_world_coords(self.db)
        g1 = get_flat_view_graph(self.db, flat)
        moved = next(n for n in g1.nodes if n.fabric_node_id == target.fabric_node_id)
        self.assertEqual(moved.x, 1111.0)
        self.assertEqual(moved.y, 2222.0)

    def test_create_folder_under_ume_world_remounts_to_drill(self):
        from netx_api.topology_schemas import TopologyFolderCreate
        from netx_api.topology_views_tree import create_folder
        from netx_api.ume_topology_world import get_world_container_folder, get_world_drill_folder

        self._seed_tree()
        apply_ume_topology_to_fabric(self.db)
        ensure_ume_world_and_sbn_folders(self.db)
        container = get_world_container_folder(self.db)
        drill = get_world_drill_folder(self.db)
        self.assertIsNotNone(container)
        self.assertIsNotNone(drill)
        out = create_folder(
            self.db,
            TopologyFolderCreate(name="Manual City", parent_id=container.id, kind="region"),
        )
        row = self.db.get(TopoFolder, out.id)
        self.assertEqual(row.parent_id, drill.id)

    def test_flat_slots_do_not_overlap(self):
        self._seed_tree()
        apply_ume_topology_to_fabric(self.db)
        ensure_ume_world_and_sbn_folders(self.db)
        recompute_flat_world_coords(self.db)
        by_sbn: dict[str, list[TopoFabricNode]] = {}
        for n in self.db.query(TopoFabricNode).all():
            sid = str((n.attrs or {}).get("ume_sbn_id") or "")
            by_sbn.setdefault(sid, []).append(n)
        a_nodes = by_sbn.get("city-a") or []
        b_nodes = by_sbn.get("city-b") or []
        self.assertEqual(len(a_nodes), 2)
        self.assertEqual(len(b_nodes), 1)
        # Bounding boxes of the two cities must not intersect
        def bbox(nodes):
            xs = [float(n.world_x) for n in nodes]
            ys = [float(n.world_y) for n in nodes]
            return min(xs), max(xs), min(ys), max(ys)

        ax0, ax1, ay0, ay1 = bbox(a_nodes)
        bx0, bx1, by0, by1 = bbox(b_nodes)
        overlap_x = not (ax1 < bx0 or bx1 < ax0)
        overlap_y = not (ay1 < by0 or by1 < ay0)
        self.assertFalse(overlap_x and overlap_y)

        # Intra-city relative delta preserved (A2 - A1 local was 40 in x)
        a1 = next(n for n in a_nodes if n.ume_ne_id == "me-a1")
        a2 = next(n for n in a_nodes if n.ume_ne_id == "me-a2")
        self.assertAlmostEqual(float(a2.world_x) - float(a1.world_x), 40.0)


if __name__ == "__main__":
    unittest.main()
