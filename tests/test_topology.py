"""Unit tests for fabric topology + LLDP parsers (no CDP discovery)."""

from __future__ import annotations

import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from netx_api import topology_lldp as lldp
from netx_api import topology_service as svc
from netx_api.db import Base, SessionLocal, engine
from netx_api.device_types import LLDP_DISCOVERED_NE_SOURCE, WEBCRT_NE_SOURCE
from netx_api.models import (
    ManagedNE,
    TopoDiscoverJob,
    TopoDiscoverJobItem,
    TopoFabricEdge,
    TopoFabricNode,
    TopoFolder,
    TopoView,
    TopoViewEdgeStyle,
    TopoViewNode,
)
from netx_api.topology_schemas import (
    FabricDiscoverRequest,
    TopologyFolderCreate,
    TopologyViewCreate,
    ViewNodesAdd,
    ViewPopulateRequest,
    ViewPositionsPatch,
    ViewNodeIn,
)


CISCO_LLDP_BRIEF = """
Capability codes:
  (R) Router, (B) Bridge

Device ID           Local Intf     Hold-time  Capability      Port ID
R1                  Gi0/0          120        R               Gi0/1
R3                  Gi0/1          120        R               Gi0/0
"""

CISCO_LLDP_DETAIL = """
------------------------------------------------
Local Intf: Gi0/1
Chassis id: 707b.5c6e.d130
Port id: Ethernet1/0/1
System Name: r1
Management Addresses:
    IP: 192.168.0.1
"""

HUAWEI_LLDP = """
GigabitEthernet0/0/1 has 1 neighbor(s):

Neighbor index : 1
Chassis ID     : 00e0-fc12-3456
Port ID        : GigabitEthernet0/0/2
System name    : r1
Management address : 192.168.0.127
Local Interface: GigabitEthernet0/0/1
"""

ZTE_LLDP_BRIEF = """
Local Interface      Chassis ID         Port ID              System Name
gei-0/1/0/1          0011.2233.4455     gei-0/1/0/2          R1
"""


class LldpParserTests(unittest.TestCase):
    def test_cisco_brief(self) -> None:
        hits = lldp.parse_cisco_lldp(CISCO_LLDP_BRIEF)
        self.assertGreaterEqual(len(hits), 1)
        self.assertTrue(any(h.remote_name.upper().startswith("R1") for h in hits))

    def test_huawei(self) -> None:
        hits = lldp.parse_huawei_lldp(HUAWEI_LLDP)
        self.assertGreaterEqual(len(hits), 1)
        self.assertEqual(hits[0].remote_name.lower(), "r1")

    def test_pick_command_lldp_only(self) -> None:
        cmd, tag = lldp.pick_neighbor_command(protocol="cdp", vendor="Cisco", device_type="cisco_ios")
        self.assertEqual(tag, "lldp")
        self.assertIn("lldp", cmd.lower())


class FabricTopologyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        Base.metadata.create_all(bind=engine)
        from netx_api.topology_migrate import ensure_topology_schema

        with engine.begin() as conn:
            ensure_topology_schema(conn)

    def setUp(self) -> None:
        self.db = SessionLocal()
        # Shared SQLite DB across tests — wipe fabric/view state for isolation.
        for model in (
            TopoViewEdgeStyle,
            TopoViewNode,
            TopoView,
            TopoFolder,
            TopoFabricEdge,
            TopoFabricNode,
            TopoDiscoverJobItem,
            TopoDiscoverJob,
        ):
            self.db.query(model).delete()
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def _region(self, name: str = "Test-Region") -> str:
        return svc.create_folder(
            self.db, TopologyFolderCreate(name=name, kind="region")
        ).id

    def test_view_crud_and_positions(self) -> None:
        suffix = uuid4().hex[:8]
        ne = ManagedNE(
            id=f"ne-{suffix}",
            name=f"R-{suffix}",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address=f"10.0.0.{(int(suffix[:2], 16) % 200) + 1}",
        )
        self.db.add(ne)
        self.db.commit()

        view = svc.create_view(
            self.db,
            TopologyViewCreate(name=f"V-{suffix}", folder_id=self._region(f"R-{suffix}")),
        )
        graph = svc.add_nodes_to_view(
            self.db, view.id, ViewNodesAdd(managed_ne_ids=[ne.id])
        )
        self.assertEqual(len(graph.nodes), 1)
        fid = graph.nodes[0].fabric_node_id
        graph2 = svc.patch_view_positions(
            self.db,
            view.id,
            ViewPositionsPatch(positions=[ViewNodeIn(fabric_node_id=fid, x=120, y=80)]),
        )
        self.assertEqual(graph2.nodes[0].x, 120)
        self.assertEqual(graph2.nodes[0].y, 80)

        summary = svc.get_fabric_summary(self.db)
        self.assertGreaterEqual(summary.node_count, 1)

        svc.delete_view(self.db, view.id)
        self.db.delete(ne)
        self.db.commit()

    def test_topology_tree_root_region_and_leaf(self) -> None:
        tree = svc.get_topology_tree(self.db)
        self.assertIsNotNone(tree.root)
        assert tree.root is not None
        self.assertEqual(tree.root.kind, "root")
        # No default Unassigned region — only user-created regions under hidden root.
        self.assertEqual(tree.root.children, [])

        region = svc.create_folder(
            self.db, TopologyFolderCreate(name="East", kind="region")
        )
        # Creating a site auto-creates a physical map.
        tree1 = svc.get_topology_tree(self.db)
        assert tree1.root is not None
        east0 = next(c for c in tree1.root.children if c.id == region.id)
        self.assertTrue(any(v.kind == "physical" for v in east0.views))

        view = svc.create_view(
            self.db,
            TopologyViewCreate(
                name="East-Custom",
                folder_id=region.id,
                kind="custom",
            ),
        )
        self.assertEqual(view.folder_id, region.id)
        self.assertEqual(view.kind, "custom")
        self.assertIn("membership", view.filter)

        tree2 = svc.get_topology_tree(self.db)
        assert tree2.root is not None
        east = next(c for c in tree2.root.children if c.id == region.id)
        self.assertTrue(any(v.id == view.id for v in east.views))
        # Flat siblings: physical + custom
        self.assertGreaterEqual(len(east.views), 2)
        self.assertTrue(all(not getattr(v, "children", None) for v in east.views))

    def test_site_physical_and_custom_flat(self) -> None:
        region = svc.create_folder(
            self.db, TopologyFolderCreate(name="Site-R", kind="region")
        )
        tree = svc.get_topology_tree(self.db)
        assert tree.root is not None
        reg = next(c for c in tree.root.children if c.id == region.id)
        physicals = [v for v in reg.views if v.kind == "physical"]
        self.assertEqual(len(physicals), 1)
        phys = physicals[0]

        custom = svc.create_view(
            self.db,
            TopologyViewCreate(
                name="Custom-A",
                folder_id=region.id,
                kind="custom",
            ),
        )
        tree2 = svc.get_topology_tree(self.db)
        assert tree2.root is not None
        reg2 = next(c for c in tree2.root.children if c.id == region.id)
        ids = {v.id for v in reg2.views}
        self.assertIn(phys.id, ids)
        self.assertIn(custom.id, ids)
        # physical first
        self.assertEqual(reg2.views[0].kind, "physical")

        with self.assertRaises(Exception):
            svc.delete_view(self.db, phys.id)
        svc.delete_view(self.db, custom.id)
        # force-delete physical recreates a fresh physical map
        svc.delete_view(self.db, phys.id, force=True)
        tree3 = svc.get_topology_tree(self.db)
        assert tree3.root is not None
        reg3 = next(c for c in tree3.root.children if c.id == region.id)
        self.assertEqual(sum(1 for v in reg3.views if v.kind == "physical"), 1)

    def test_delete_region_cascades_views(self) -> None:
        region = svc.create_folder(
            self.db, TopologyFolderCreate(name="Del-Region", kind="region")
        )
        custom = svc.create_view(
            self.db,
            TopologyViewCreate(
                name="Custom-Del",
                folder_id=region.id,
                kind="custom",
            ),
        )
        tree = svc.get_topology_tree(self.db)
        assert tree.root is not None
        reg = next(c for c in tree.root.children if c.id == region.id)
        self.assertGreaterEqual(len(reg.views), 2)

        out = svc.delete_folder(self.db, region.id)
        self.assertTrue(out.get("deleted"))
        tree2 = svc.get_topology_tree(self.db)
        assert tree2.root is not None
        self.assertFalse(any(c.id == region.id for c in tree2.root.children))
        self.assertIsNone(self.db.get(TopoView, custom.id))
        self.assertEqual(
            self.db.query(TopoView).filter(TopoView.folder_id == region.id).count(),
            0,
        )

    def test_classify_role_region_and_slices(self) -> None:
        from netx_api import topology_classify as clf
        from netx_api.topology_schemas import (
            ClassifyRuleCreate,
            SliceGenerateRequest,
        )

        suffix = uuid4().hex[:6]
        region = svc.create_folder(
            self.db, TopologyFolderCreate(name=f"East-{suffix}", kind="region")
        )
        nes = []
        for name, ip in (
            (f"CORE-A-{suffix}", "10.1.1.1"),
            (f"CORE-B-{suffix}", "10.1.1.2"),
            (f"AGG-A-{suffix}", "10.1.2.1"),
            (f"ACC-A-{suffix}", "10.1.3.1"),
        ):
            ne = ManagedNE(
                id=f"clf-{suffix}-{name[:8]}",
                name=name,
                vendor="Cisco",
                device_type="cisco_ios",
                ip_address=ip,
            )
            self.db.add(ne)
            nes.append(ne)
        self.db.commit()
        nodes = [svc.ensure_fabric_node_for_managed(self.db, ne) for ne in nes]
        self.db.commit()
        svc.upsert_fabric_edge(
            self.db,
            a_node_id=nodes[0].id,
            b_node_id=nodes[2].id,
            a_port="Gi0/0",
            b_port="Gi0/1",
            source="lldp",
        )
        svc.upsert_fabric_edge(
            self.db,
            a_node_id=nodes[2].id,
            b_node_id=nodes[3].id,
            a_port="Gi0/2",
            b_port="Gi0/3",
            source="lldp",
        )
        self.db.commit()

        clf.create_rule(
            self.db,
            ClassifyRuleCreate(
                scope="role",
                name="core",
                pattern=r"^CORE-",
                priority=10,
                payload={"role": "core"},
            ),
        )
        clf.create_rule(
            self.db,
            ClassifyRuleCreate(
                scope="role",
                name="agg",
                pattern=r"^AGG-",
                priority=20,
                payload={"role": "aggregation"},
            ),
        )
        clf.create_rule(
            self.db,
            ClassifyRuleCreate(
                scope="role",
                name="acc",
                pattern=r"^ACC-",
                priority=30,
                payload={"role": "access"},
            ),
        )
        clf.create_rule(
            self.db,
            ClassifyRuleCreate(
                scope="region",
                name="east",
                pattern=rf"-{suffix}$",
                priority=10,
                payload={"folder_id": region.id},
            ),
        )
        prev = clf.preview_classify(self.db)
        self.assertGreaterEqual(prev.role_matched, 4)
        applied = clf.apply_classify(self.db)
        self.assertGreaterEqual(applied.role_updated, 4)
        self.db.refresh(nodes[0])
        self.assertEqual(nodes[0].role, "core")
        self.assertEqual(nodes[0].region_folder_id, region.id)

        dry = clf.generate_slices(
            self.db,
            SliceGenerateRequest(
                folder_id=region.id,
                template="core_agg",
                dry_run=True,
                max_nodes=50,
            ),
        )
        self.assertGreaterEqual(dry.map_count, 1)
        self.assertTrue(dry.dry_run)
        real = clf.generate_slices(
            self.db,
            SliceGenerateRequest(
                folder_id=region.id,
                template="core_agg",
                dry_run=False,
                max_nodes=50,
            ),
        )
        self.assertFalse(real.dry_run)
        self.assertTrue(real.created_view_ids)
        search = clf.search_fabric_nodes_with_views(self.db, keyword=f"CORE-A-{suffix}")
        self.assertGreaterEqual(search["total"], 1)
        self.assertTrue(search["items"][0].get("views"))

        from netx_api.topology_schemas import FabricNodesBulkTagRequest, FabricNodesMatchRequest

        matched = clf.match_fabric_nodes(
            self.db,
            FabricNodesMatchRequest(pattern=rf"^AGG-A-{suffix}$", match_field="name"),
        )
        self.assertEqual(matched.total_matched, 1)
        bulk = clf.bulk_tag_fabric_nodes(
            self.db,
            FabricNodesBulkTagRequest(
                pattern=rf"^ACC-A-{suffix}$",
                role="access",
                region_folder_id=region.id,
            ),
        )
        self.assertEqual(bulk.updated, 1)

    def test_project_neighbors_respects_max_nodes(self) -> None:
        suffix = uuid4().hex[:8]
        nes = []
        for i in range(4):
            ne = ManagedNE(
                id=f"cap-{suffix}-{i}",
                name=f"N{i}-{suffix}",
                vendor="Cisco",
                device_type="cisco_ios",
                ip_address=f"10.9.{(int(suffix[:2], 16) % 200)}.{i + 1}",
            )
            self.db.add(ne)
            nes.append(ne)
        self.db.commit()
        nodes = [svc.ensure_fabric_node_for_managed(self.db, ne) for ne in nes]
        self.db.commit()
        # Line: 0-1-2-3
        for a, b in ((0, 1), (1, 2), (2, 3)):
            svc.upsert_fabric_edge(
                self.db,
                a_node_id=nodes[a].id,
                b_node_id=nodes[b].id,
                a_port=f"Gi0/{a}",
                b_port=f"Gi0/{b}",
                source="lldp",
            )
        self.db.commit()

        view = svc.create_view(
            self.db,
            TopologyViewCreate(
                name=f"Cap-{suffix}",
                folder_id=self._region(f"CapR-{suffix}"),
                role="core",
                filter={
                    "membership": {
                        "expand_hops": 3,
                        "max_nodes": 2,
                        "frozen": False,
                    }
                },
            ),
        )
        svc.add_nodes_to_view(self.db, view.id, ViewNodesAdd(managed_ne_ids=[nes[0].id]))
        g = svc.project_fabric_neighbors_to_view(self.db, view.id)
        self.assertLessEqual(len(g.nodes), 2)
        self.assertTrue(g.truncated or len(g.nodes) == 2)

        pop = svc.populate_view(
            self.db,
            view.id,
            ViewPopulateRequest(
                dry_run=True,
                membership={"seed_fabric_node_ids": [nodes[0].id], "expand_hops": 3, "max_nodes": 2},
            ),
        )
        self.assertLessEqual(pop.candidate_count, 4)
        self.assertTrue(pop.truncated or pop.candidate_count <= 2 or pop.would_add <= 2)

    def test_lldp_discover_writes_fabric_edge(self) -> None:
        suffix = uuid4().hex[:8]
        ne_a = ManagedNE(
            id=f"nea-{suffix}",
            name="R2",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address=f"203.0.113.{(int(suffix[:2], 16) % 100) + 1}",
        )
        ne_b = ManagedNE(
            id=f"neb-{suffix}",
            name="R1",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address=f"203.0.113.{(int(suffix[2:4], 16) % 100) + 101}",
        )
        self.db.add(ne_a)
        self.db.add(ne_b)
        self.db.commit()
        # Ensure fabric nodes exist for matching
        svc.ensure_fabric_node_for_managed(self.db, ne_a)
        svc.ensure_fabric_node_for_managed(self.db, ne_b)
        self.db.commit()

        fake_exec = {
            "ok": True,
            "output": CISCO_LLDP_DETAIL,
            "commands": ["show lldp neighbors detail"],
        }
        with patch.object(svc, "execute_managed_ne_commands", return_value=fake_exec):
            job = svc.start_discover_job(
                self.db,
                FabricDiscoverRequest(scope="ne_ids", ne_ids=[ne_a.id], concurrency=1),
            )
            # Wait for background thread
            for _ in range(50):
                out = svc.get_discover_job(self.db, job.id)
                if out.status in {"done", "failed"}:
                    break
                time.sleep(0.05)
            out = svc.get_discover_job(self.db, job.id)
        self.assertEqual(out.status, "done", out.error)
        edges = svc.list_fabric_edges(self.db, page=1, page_size=50)
        self.assertGreaterEqual(edges["total"], 1)

        self.db.delete(ne_a)
        self.db.delete(ne_b)
        self.db.commit()

    def test_merge_duplicate_and_project_skips_orphans(self) -> None:
        suffix = uuid4().hex[:8]
        ne_a = ManagedNE(
            id=f"nea-{suffix}",
            name="R2",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address=f"203.0.113.{(int(suffix[:2], 16) % 80) + 10}",
        )
        ne_b = ManagedNE(
            id=f"neb-{suffix}",
            name="R1",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address=f"203.0.113.{(int(suffix[2:4], 16) % 80) + 100}",
        )
        self.db.add(ne_a)
        self.db.add(ne_b)
        self.db.commit()
        fa = svc.ensure_fabric_node_for_managed(self.db, ne_a)
        fb = svc.ensure_fabric_node_for_managed(self.db, ne_b)
        orphan = TopoFabricNode(
            id=uuid4().hex,
            managed_ne_id=None,
            ume_ne_id=None,
            name="r1",
            ip="",
            vendor="",
            device_type="",
            attrs={"from_lldp_unmatched": True},
        )
        # Simulate raced duplicate of R2 (same managed id not allowed by constraint —
        # use name/ip collision path with a second orphan twin).
        orphan_r2 = TopoFabricNode(
            id=uuid4().hex,
            managed_ne_id=None,
            ume_ne_id=None,
            name="R2",
            ip=ne_a.ip_address,
            vendor="",
            device_type="",
            attrs={"from_lldp_unmatched": True},
        )
        self.db.add(orphan)
        self.db.add(orphan_r2)
        self.db.commit()
        # Path: orphan_r2 -- fb -- fa -- orphan  (the bug shape)
        svc.upsert_fabric_edge(
            self.db, a_node_id=orphan_r2.id, b_node_id=fb.id, a_port="g0", b_port="g1", source="lldp"
        )
        svc.upsert_fabric_edge(
            self.db, a_node_id=fb.id, b_node_id=fa.id, a_port="g2", b_port="g3", source="lldp"
        )
        svc.upsert_fabric_edge(
            self.db, a_node_id=fa.id, b_node_id=orphan.id, a_port="g4", b_port="g5", source="lldp"
        )
        self.db.commit()

        view = svc.create_view(
            self.db,
            TopologyViewCreate(name=f"V-{suffix}", folder_id=self._region(f"MR-{suffix}")),
        )
        graph = svc.add_nodes_to_view(
            self.db,
            view.id,
            ViewNodesAdd(fabric_node_ids=[fa.id, fb.id, orphan.id, orphan_r2.id]),
        )
        self.assertEqual(len(graph.nodes), 4)

        merged = svc.merge_duplicate_fabric_nodes(self.db)
        self.assertGreaterEqual(merged["merged"], 2)
        self.db.expire_all()
        self.assertIsNone(self.db.get(TopoFabricNode, orphan.id))
        self.assertIsNone(self.db.get(TopoFabricNode, orphan_r2.id))

        # Re-place inventory nodes only, then project should stay at 2.
        graph2 = svc.add_nodes_to_view(
            self.db, view.id, ViewNodesAdd(managed_ne_ids=[ne_a.id, ne_b.id])
        )
        # View may still have stale placements pointing at deleted ids — project cleans.
        projected = svc.project_fabric_neighbors_to_view(self.db, view.id)
        self.assertEqual(len(projected.nodes), 2)
        labels = sorted((n.name or n.label).upper() for n in projected.nodes)
        self.assertEqual(labels, ["R1", "R2"])
        self.assertGreaterEqual(len(projected.edges), 1)
        endpoint_ids = {n.fabric_node_id for n in projected.nodes}
        for e in projected.edges:
            self.assertIn(e.a_node_id, endpoint_ids)
            self.assertIn(e.b_node_id, endpoint_ids)

        self.db.delete(ne_a)
        self.db.delete(ne_b)
        self.db.commit()

    def test_match_hit_prefers_inventory(self) -> None:
        suffix = uuid4().hex[:8]
        ne = ManagedNE(
            id=f"ne-{suffix}",
            name="R1",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address=f"198.51.100.{(int(suffix[:2], 16) % 80) + 20}",
        )
        self.db.add(ne)
        self.db.commit()
        inv = svc.ensure_fabric_node_for_managed(self.db, ne)
        orphan = TopoFabricNode(
            id=uuid4().hex,
            name="r1",
            ip="",
            attrs={"from_lldp_unmatched": True},
        )
        self.db.add(orphan)
        self.db.commit()
        hit = lldp.NeighborHit(
            remote_name="r1",
            remote_ip="",
            local_port="Gi0/0",
            remote_port="Gi0/1",
        )
        peer = svc._match_hit_to_fabric_node(self.db, hit, self_id="self")
        self.assertEqual(peer.id, inv.id)
        self.db.delete(ne)
        self.db.commit()

    def test_lldp_unmatched_creates_ssh_placeholder_ne(self) -> None:
        suffix = uuid4().hex[:8]
        peer_name = f"Peer-{suffix}"
        ne_a = ManagedNE(
            id=f"nea-{suffix}",
            name=f"Core-{suffix}",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address=f"203.0.113.{(int(suffix[:2], 16) % 80) + 40}",
            source="manual",
        )
        self.db.add(ne_a)
        self.db.commit()
        svc.ensure_fabric_node_for_managed(self.db, ne_a)
        self.db.commit()

        lldp_out = f"""
------------------------------------------------
Local Intf: Gi0/1
Chassis id: 707b.5c6e.d130
Port id: Ethernet1/0/1
System Name: {peer_name}
Management Addresses:
    IP: 198.51.100.200
"""
        fake_exec = {
            "ok": True,
            "output": lldp_out,
            "commands": ["show lldp neighbors detail"],
        }
        with patch.object(svc, "execute_managed_ne_commands", return_value=fake_exec):
            job = svc.start_discover_job(
                self.db,
                FabricDiscoverRequest(
                    scope="ne_ids", ne_ids=[ne_a.id], concurrency=1, auto_add_unmatched=True
                ),
            )
            for _ in range(50):
                out = svc.get_discover_job(self.db, job.id)
                if out.status in {"done", "failed"}:
                    break
                time.sleep(0.05)
            out = svc.get_discover_job(self.db, job.id)
        self.assertEqual(out.status, "done", out.error)

        placeholders = (
            self.db.query(ManagedNE)
            .filter(ManagedNE.source == LLDP_DISCOVERED_NE_SOURCE)
            .all()
        )
        placeholder = next(
            (p for p in placeholders if svc._norm_host(p.name) == svc._norm_host(peer_name)),
            None,
        )
        self.assertIsNotNone(placeholder)
        assert placeholder is not None
        self.assertEqual(placeholder.ip_address, "")
        self.assertEqual(placeholder.username, "")
        self.assertEqual(placeholder.password_enc, "")
        self.assertEqual(placeholder.protocol, "ssh")
        self.assertEqual(placeholder.device_type, "generic")
        self.assertEqual(placeholder.source_ref, "198.51.100.200")

        view = svc.create_view(
            self.db,
            TopologyViewCreate(name=f"V-{suffix}", folder_id=self._region(f"PR-{suffix}")),
        )
        svc.add_nodes_to_view(self.db, view.id, ViewNodesAdd(managed_ne_ids=[ne_a.id]))
        projected = svc.project_fabric_neighbors_to_view(self.db, view.id)
        self.assertEqual(len(projected.nodes), 2)
        self.assertGreaterEqual(len(projected.edges), 1)

        self.db.delete(ne_a)
        self.db.delete(placeholder)
        self.db.commit()

    def test_match_prefers_real_ne_over_webcrt_same_ip(self) -> None:
        suffix = uuid4().hex[:8]
        ip = f"198.51.100.{(int(suffix[:2], 16) % 80) + 30}"
        real = ManagedNE(
            id=f"real-{suffix}",
            name="R2",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address=ip,
            source="manual",
        )
        webcrt = ManagedNE(
            id=f"wcrt-{suffix}",
            name=f"{ip} (1)",
            vendor="Other",
            device_type="generic",
            ip_address=ip,
            source=WEBCRT_NE_SOURCE,
        )
        self.db.add(real)
        self.db.add(webcrt)
        self.db.commit()
        inv = svc.ensure_fabric_node_for_managed(self.db, real)
        ghost = svc.ensure_fabric_node_for_managed(self.db, webcrt)
        self.db.commit()
        hit = lldp.NeighborHit(remote_name="", remote_ip=ip, local_port="Gi0/0", remote_port="Gi0/1")
        peer = svc._match_hit_to_fabric_node(self.db, hit, self_id="self")
        self.assertEqual(peer.id, inv.id)
        self.assertNotEqual(peer.id, ghost.id)

        merged = svc.merge_duplicate_fabric_nodes(self.db)
        self.assertGreaterEqual(merged["merged"], 1)
        self.db.expire_all()
        self.assertIsNone(self.db.get(TopoFabricNode, ghost.id))
        self.db.delete(real)
        self.db.delete(webcrt)
        self.db.commit()

    def test_neighborhood(self) -> None:
        suffix = uuid4().hex[:8]
        ne_a = ManagedNE(
            id=f"nea-{suffix}",
            name=f"A-{suffix}",
            ip_address=f"198.51.100.{(int(suffix[:2], 16) % 80) + 10}",
            vendor="ZTE",
            device_type="zte_zxros",
        )
        ne_b = ManagedNE(
            id=f"neb-{suffix}",
            name=f"B-{suffix}",
            ip_address=f"198.51.100.{(int(suffix[2:4], 16) % 80) + 100}",
            vendor="ZTE",
            device_type="zte_zxros",
        )
        self.db.add(ne_a)
        self.db.add(ne_b)
        self.db.commit()
        fa = svc.ensure_fabric_node_for_managed(self.db, ne_a)
        fb = svc.ensure_fabric_node_for_managed(self.db, ne_b)
        self.db.commit()
        svc.upsert_fabric_edge(
            self.db,
            a_node_id=fa.id,
            b_node_id=fb.id,
            a_port="gei-0/1/0/1",
            b_port="gei-0/1/0/2",
            source="lldp",
        )
        self.db.commit()
        nb = svc.get_fabric_neighborhood(self.db, fa.id, depth=1)
        self.assertEqual(len(nb.nodes), 2)
        self.assertEqual(len(nb.edges), 1)
        self.db.delete(ne_a)
        self.db.delete(ne_b)
        self.db.commit()

    def _pair_nodes(self, suffix: str) -> tuple[TopoFabricNode, TopoFabricNode, ManagedNE, ManagedNE]:
        ne_a = ManagedNE(
            id=f"nea-{suffix}",
            name=f"A-{suffix}",
            ip_address=f"198.51.100.{(int(suffix[:2], 16) % 80) + 10}",
            vendor="Cisco",
            device_type="cisco_ios",
        )
        ne_b = ManagedNE(
            id=f"neb-{suffix}",
            name=f"B-{suffix}",
            ip_address=f"198.51.100.{(int(suffix[2:4], 16) % 80) + 100}",
            vendor="Cisco",
            device_type="cisco_ios",
        )
        self.db.add(ne_a)
        self.db.add(ne_b)
        self.db.commit()
        fa = svc.ensure_fabric_node_for_managed(self.db, ne_a)
        fb = svc.ensure_fabric_node_for_managed(self.db, ne_b)
        self.db.commit()
        return fa, fb, ne_a, ne_b

    def test_merge_lldp_placeholder_into_real_inventory(self) -> None:
        suffix = uuid4().hex[:8]
        # Real inventory NE + fabric node.
        real = ManagedNE(
            id=f"real-{suffix}",
            name=f"R1-{suffix}",
            ip_address=f"198.51.100.{(int(suffix[:2], 16) % 80) + 10}",
            vendor="Cisco",
            device_type="cisco_ios",
        )
        self.db.add(real)
        self.db.commit()
        fr = svc.ensure_fabric_node_for_managed(self.db, real)
        # LLDP placeholder with same hostname key + seen mgmt IP.
        ph = svc.ensure_lldp_discovered_managed_ne(
            self.db,
            remote_name=f"R1-{suffix}",
            remote_ip=real.ip_address,
        )
        fp = svc.ensure_fabric_node_for_managed(self.db, ph)
        self.db.commit()
        # Edge hanging off placeholder should retarget to real.
        edge, _ = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fr.id,
            b_node_id=fp.id,
            a_port="Gi0/0",
            b_port="Gi0/1",
            source="lldp",
        )
        # Need a third node so edge isn't self-loop after merge… actually A=real B=placeholder
        # after absorb B→A becomes self-loop and edge is deleted. Use external peer.
        peer_ne = ManagedNE(
            id=f"peer-{suffix}",
            name=f"P-{suffix}",
            ip_address=f"198.51.100.{(int(suffix[2:4], 16) % 80) + 100}",
            vendor="Cisco",
            device_type="cisco_ios",
        )
        self.db.add(peer_ne)
        self.db.commit()
        fpeer = svc.ensure_fabric_node_for_managed(self.db, peer_ne)
        edge2, _ = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fp.id,
            b_node_id=fpeer.id,
            a_port="Gi1/0",
            b_port="Gi1/1",
            source="lldp",
        )
        self.db.commit()
        edge2_id = edge2.id

        out = svc.merge_duplicate_fabric_nodes(self.db)
        self.assertGreaterEqual(out["merged"], 1)
        self.assertGreaterEqual(out.get("placeholders_removed", 0), 1)
        self.db.expire_all()
        self.assertIsNone(self.db.get(TopoFabricNode, fp.id))
        self.assertIsNone(self.db.get(ManagedNE, ph.id))
        # Edge from placeholder→peer should now be real→peer.
        moved = self.db.get(TopoFabricEdge, edge2_id)
        self.assertIsNotNone(moved)
        assert moved is not None
        ends = {moved.a_node_id, moved.b_node_id}
        self.assertEqual(ends, {fr.id, fpeer.id})

        self.db.delete(real)
        self.db.delete(peer_ne)
        self.db.commit()

    def test_list_fabric_edges_missing_filter_and_names(self) -> None:
        suffix = uuid4().hex[:8]
        fa, fb, ne_a, ne_b = self._pair_nodes(suffix)
        edge, _ = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fa.id,
            b_node_id=fb.id,
            a_port="Gi0/0",
            b_port="Gi0/1",
            source="lldp",
        )
        self.db.commit()
        svc._apply_missing_and_purge(
            self.db, scanned_ok={fa.id}, touched_edge_ids=set()
        )
        self.db.commit()

        missing = svc.list_fabric_edges(self.db, status="missing", page=1, page_size=50)
        self.assertGreaterEqual(missing["total"], 1)
        hit = next(i for i in missing["items"] if i["id"] == edge.id)
        self.assertEqual(hit["status"], "missing")
        self.assertTrue(hit["a_name"] or hit["b_name"])
        self.assertGreaterEqual(int((hit.get("attrs") or {}).get("miss_count") or 0), 1)

        by_kw = svc.list_fabric_edges(
            self.db, keyword=ne_a.name[:6], status="missing", page=1, page_size=50
        )
        self.assertTrue(any(i["id"] == edge.id for i in by_kw["items"]))

        active = svc.list_fabric_edges(self.db, status="active", page=1, page_size=50)
        self.assertFalse(any(i["id"] == edge.id for i in active["items"]))

        self.db.delete(ne_a)
        self.db.delete(ne_b)
        self.db.commit()

    def test_edge_missing_after_one_absent_cycle(self) -> None:
        suffix = uuid4().hex[:8]
        fa, fb, ne_a, ne_b = self._pair_nodes(suffix)
        edge, _ = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fa.id,
            b_node_id=fb.id,
            a_port="Gi0/0",
            b_port="Gi0/1",
            source="lldp",
        )
        self.db.commit()

        newly, purged = svc._apply_missing_and_purge(
            self.db,
            scanned_ok={fa.id},
            touched_edge_ids=set(),
        )
        self.db.commit()
        self.assertEqual(newly, 1)
        self.assertEqual(purged, 0)
        self.db.refresh(edge)
        self.assertEqual(edge.status, "missing")
        self.assertEqual(int((edge.attrs or {}).get("miss_count") or 0), 1)

        self.db.delete(ne_a)
        self.db.delete(ne_b)
        self.db.commit()

    def test_edge_purge_after_four_missing_cycles(self) -> None:
        suffix = uuid4().hex[:8]
        fa, fb, ne_a, ne_b = self._pair_nodes(suffix)
        edge, _ = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fa.id,
            b_node_id=fb.id,
            a_port="Gi0/0",
            b_port="Gi0/1",
            source="lldp",
        )
        edge_id = edge.id
        self.db.commit()

        for cycle in range(1, 5):
            newly, purged = svc._apply_missing_and_purge(
                self.db,
                scanned_ok={fa.id},
                touched_edge_ids=set(),
            )
            self.db.commit()
            if cycle < 4:
                self.assertEqual(purged, 0)
                row = self.db.get(TopoFabricEdge, edge_id)
                self.assertIsNotNone(row)
                self.assertEqual(int((row.attrs or {}).get("miss_count") or 0), cycle)
            else:
                self.assertEqual(purged, 1)
                self.assertIsNone(self.db.get(TopoFabricEdge, edge_id))

        self.db.delete(ne_a)
        self.db.delete(ne_b)
        self.db.commit()

    def test_peer_replace_marks_old_edge_missing(self) -> None:
        suffix = uuid4().hex[:8]
        fa, fb, ne_a, ne_b = self._pair_nodes(suffix)
        ne_c = ManagedNE(
            id=f"nec-{suffix}",
            name=f"C-{suffix}",
            ip_address=f"198.51.100.{(int(suffix[4:6], 16) % 80) + 50}",
            vendor="Cisco",
            device_type="cisco_ios",
        )
        self.db.add(ne_c)
        self.db.commit()
        fc = svc.ensure_fabric_node_for_managed(self.db, ne_c)
        self.db.commit()

        old, _ = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fa.id,
            b_node_id=fb.id,
            a_port="Gi0/0",
            b_port="Gi0/1",
            source="lldp",
        )
        new, _ = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fa.id,
            b_node_id=fc.id,
            a_port="Gi0/0",
            b_port="Gi0/2",
            source="lldp",
        )
        self.db.commit()

        handled = svc._mark_replaced_port_peers(
            self.db,
            self_id=fa.id,
            local_port="Gi0/0",
            peer_id=fc.id,
            new_edge_id=new.id,
        )
        self.db.commit()
        self.assertIn(old.id, handled)
        self.db.refresh(old)
        self.assertEqual(old.status, "missing")
        self.assertEqual((old.attrs or {}).get("replaced_by_edge_id"), new.id)
        self.assertEqual(int((old.attrs or {}).get("miss_count") or 0), 1)

        # Same-job: replaced id in touched → no second miss bump.
        newly, purged = svc._apply_missing_and_purge(
            self.db,
            scanned_ok={fa.id},
            touched_edge_ids={new.id, *handled},
        )
        self.db.commit()
        self.assertEqual(newly, 0)
        self.assertEqual(purged, 0)
        self.db.refresh(old)
        self.assertEqual(int((old.attrs or {}).get("miss_count") or 0), 1)

        self.db.delete(ne_a)
        self.db.delete(ne_b)
        self.db.delete(ne_c)
        self.db.commit()

    def test_edge_reactivate_clears_miss_attrs(self) -> None:
        suffix = uuid4().hex[:8]
        fa, fb, ne_a, ne_b = self._pair_nodes(suffix)
        edge, _ = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fa.id,
            b_node_id=fb.id,
            a_port="Gi0/0",
            b_port="Gi0/1",
            source="lldp",
        )
        self.db.commit()
        svc._apply_missing_and_purge(
            self.db, scanned_ok={fa.id}, touched_edge_ids=set()
        )
        self.db.commit()
        self.db.refresh(edge)
        self.assertEqual(edge.status, "missing")

        edge2, action = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fa.id,
            b_node_id=fb.id,
            a_port="Gi0/0",
            b_port="Gi0/1",
            source="lldp",
        )
        self.db.commit()
        self.assertEqual(action, "updated")
        self.assertEqual(edge2.id, edge.id)
        self.assertEqual(edge2.status, "active")
        attrs = edge2.attrs or {}
        self.assertNotIn("miss_count", attrs)
        self.assertNotIn("first_missing_at", attrs)
        self.assertNotIn("replaced_by_edge_id", attrs)

        self.db.delete(ne_a)
        self.db.delete(ne_b)
        self.db.commit()

    def test_scan_fail_does_not_mark_missing(self) -> None:
        """Endpoint not in scanned_ok (SSH/parse fail) → leave edge active."""
        suffix = uuid4().hex[:8]
        fa, fb, ne_a, ne_b = self._pair_nodes(suffix)
        edge, _ = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fa.id,
            b_node_id=fb.id,
            a_port="Gi0/0",
            b_port="Gi0/1",
            source="lldp",
        )
        self.db.commit()

        newly, purged = svc._apply_missing_and_purge(
            self.db,
            scanned_ok=set(),  # scan failed — nothing judged
            touched_edge_ids=set(),
        )
        self.db.commit()
        self.assertEqual(newly, 0)
        self.assertEqual(purged, 0)
        self.db.refresh(edge)
        self.assertEqual(edge.status, "active")

        # Manual edges never auto-missing.
        manual, _ = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fa.id,
            b_node_id=fb.id,
            a_port="Gi1/0",
            b_port="Gi1/1",
            source="manual",
        )
        self.db.commit()
        newly, purged = svc._apply_missing_and_purge(
            self.db,
            scanned_ok={fa.id},
            touched_edge_ids=set(),
        )
        self.db.commit()
        self.db.refresh(manual)
        self.assertEqual(manual.status, "active")
        self.assertEqual(manual.source, "manual")

        self.db.delete(ne_a)
        self.db.delete(ne_b)
        self.db.commit()

    def test_delete_managed_detaches_fabric_keeps_topology(self) -> None:
        from netx_api import ne_service
        from netx_api.topology_inventory_lifecycle import reconcile_dangling_fabric_links

        suffix = uuid4().hex[:8]
        ne = ManagedNE(
            id=f"detach-{suffix}",
            name=f"DET-{suffix}",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address=f"10.66.{(int(suffix[:2], 16) % 200) + 1}.1",
        )
        self.db.add(ne)
        self.db.commit()
        region_id = self._region(f"Detach-{suffix}")
        view = svc.create_view(
            self.db,
            TopologyViewCreate(name=f"DV-{suffix}", folder_id=region_id),
        )
        graph = svc.add_nodes_to_view(
            self.db, view.id, ViewNodesAdd(managed_ne_ids=[ne.id])
        )
        fid = graph.nodes[0].fabric_node_id
        peer = TopoFabricNode(
            id=f"peer-{suffix}",
            name=f"PEER-{suffix}",
            ip=f"10.66.{(int(suffix[:2], 16) % 200) + 1}.2",
        )
        self.db.add(peer)
        self.db.commit()
        edge, _ = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fid,
            b_node_id=peer.id,
            a_port="Gi0/0",
            b_port="Gi0/1",
            source="lldp",
        )
        self.db.commit()

        ne_service.delete_managed_ne(self.db, ne.id)

        fab = self.db.get(TopoFabricNode, fid)
        self.assertIsNotNone(fab)
        assert fab is not None
        self.assertFalse(str(fab.managed_ne_id or "").strip())
        self.assertEqual(
            self.db.query(TopoViewNode)
            .filter(TopoViewNode.view_id == view.id, TopoViewNode.fabric_node_id == fid)
            .count(),
            1,
        )
        self.assertIsNotNone(self.db.get(TopoFabricEdge, edge.id))

        orphaned = svc.list_fabric_nodes(self.db, link_status="orphaned", page_size=500)
        self.assertTrue(any(x["id"] == fid for x in orphaned["items"]))
        hit = next(x for x in orphaned["items"] if x["id"] == fid)
        self.assertEqual(hit["link_status"], "orphaned")
        self.assertFalse(hit["managed_alive"])

        # Historical dangling ref (pre-lifecycle) is cleared by reconcile.
        ghost_id = f"ghost-{suffix}"
        dangling = TopoFabricNode(
            id=f"dang-{suffix}",
            managed_ne_id=ghost_id,
            name=f"DANG-{suffix}",
            ip="10.66.9.9",
        )
        self.db.add(dangling)
        self.db.commit()
        stats = reconcile_dangling_fabric_links(self.db)
        self.db.commit()
        self.assertGreaterEqual(int(stats["detached_managed_nodes"]), 1)
        self.db.refresh(dangling)
        self.assertFalse(str(dangling.managed_ne_id or "").strip())

    def test_delete_fabric_node_only_orphans_and_placeholders(self) -> None:
        from fastapi import HTTPException

        from netx_api.topology_inventory_lifecycle import delete_fabric_nodes

        suffix = uuid4().hex[:8]
        real = ManagedNE(
            id=f"real-{suffix}",
            name=f"REAL-{suffix}",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address=f"10.77.{(int(suffix[:2], 16) % 200) + 1}.1",
            source="",
        )
        ph = ManagedNE(
            id=f"ph-{suffix}",
            name=f"PH-{suffix}",
            vendor="Other",
            device_type="generic",
            ip_address="",
            source=LLDP_DISCOVERED_NE_SOURCE,
        )
        self.db.add(real)
        self.db.add(ph)
        self.db.commit()
        fab_real = svc.ensure_fabric_node_for_managed(self.db, real)
        fab_ph = svc.ensure_fabric_node_for_managed(self.db, ph)
        orphan = TopoFabricNode(
            id=f"orp-{suffix}",
            name=f"ORP-{suffix}",
            ip="10.77.0.9",
        )
        ume_only = TopoFabricNode(
            id=f"ume-{suffix}",
            name=f"UME-{suffix}",
            ip="10.77.0.8",
            ume_ne_id=f"ume-ne-{suffix}",
        )
        self.db.add(orphan)
        self.db.add(ume_only)
        self.db.commit()

        with self.assertRaises(HTTPException) as ctx:
            delete_fabric_nodes(self.db, [fab_real.id])
        self.assertEqual(ctx.exception.status_code, 400)

        with self.assertRaises(HTTPException) as ctx_ume:
            delete_fabric_nodes(self.db, [ume_only.id])
        self.assertEqual(ctx_ume.exception.status_code, 400)

        out = delete_fabric_nodes(self.db, [fab_ph.id, orphan.id])
        self.assertEqual(out["deleted"], 2)
        self.assertIsNone(self.db.get(TopoFabricNode, fab_ph.id))
        self.assertIsNone(self.db.get(TopoFabricNode, orphan.id))
        self.assertIsNotNone(self.db.get(TopoFabricNode, fab_real.id))
        self.assertIsNotNone(self.db.get(TopoFabricNode, ume_only.id))
        self.assertIsNotNone(self.db.get(ManagedNE, real.id))
        self.assertIsNotNone(self.db.get(ManagedNE, ph.id))


if __name__ == "__main__":
    unittest.main()
