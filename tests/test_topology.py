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
    TopoView,
    TopoViewEdgeStyle,
    TopoViewNode,
)
from netx_api.topology_schemas import (
    FabricDiscoverRequest,
    TopologyViewCreate,
    ViewNodesAdd,
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
            TopoFabricEdge,
            TopoFabricNode,
            TopoDiscoverJobItem,
            TopoDiscoverJob,
        ):
            self.db.query(model).delete()
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

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

        view = svc.create_view(self.db, TopologyViewCreate(name=f"V-{suffix}"))
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

        view = svc.create_view(self.db, TopologyViewCreate(name=f"V-{suffix}"))
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

        view = svc.create_view(self.db, TopologyViewCreate(name=f"V-{suffix}"))
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


if __name__ == "__main__":
    unittest.main()
