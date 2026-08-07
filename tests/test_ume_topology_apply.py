"""Unit tests for UME port normalize + fabric apply provenance."""

from __future__ import annotations

import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import netx_api.models  # noqa: F401
from netx_api.db import Base
from netx_api.models import (
    TopoFabricEdge,
    TopoFabricNode,
    UmeInventoryNE,
    UmeSyncJob,
    UmeTopoLink,
    UmeTopoNode,
)
from netx_api.ume_port_normalize import (
    extract_ifnames_from_user_label,
    port_keys_compatible,
    port_suffix_from_tp_ref,
    prefer_richer_ifname,
    resolve_link_ifnames,
)
from netx_api.ume_topology_apply import (
    apply_ume_dock_to_fabric_if_needed,
    apply_ume_topology_to_fabric,
    ume_topology_apply_gap,
)
from netx_api.topology_fabric_links import upsert_fabric_edge


class UmePortNormalizeTests(unittest.TestCase):
    def test_label_xxvgei_pair(self):
        label = (
            "MDN-UJSB-EN1-Z20HS-SMGD[1/1/0]_xxvgei-1/1/0/32(10G:to_MDN-UJSR-EN1-Z680H_"
            "xgei-1/2/0/1:via_OTB)_MDN-UJSR"
        )
        tokens = extract_ifnames_from_user_label(label)
        self.assertEqual(tokens[0], "xxvgei-1/1/0/32")
        self.assertEqual(tokens[1], "xgei-1/2/0/1")
        a, z = resolve_link_ifnames(
            a_end_tp_ref="ME{a},EQ={/r=0/sh=1/sl=1},PTP={/p=1_32}",
            z_end_tp_ref="ME{b},EQ={/r=0/sh=1/sl=2},PTP={/p=1_1}",
            user_label=label,
        )
        self.assertEqual(a, "xxvgei-1/1/0/32")
        self.assertEqual(z, "xgei-1/2/0/1")

    def test_eq_ptp_suffix(self):
        self.assertEqual(
            port_suffix_from_tp_ref("ME{x},EQ={/r=0/sh=1/sl=1},PTP={/p=1_28}"),
            "1/1/0/28",
        )
        self.assertEqual(
            port_suffix_from_tp_ref("ME{x},EQ={/r=0/sh=0/sl=0/ssl=1},PTP={/p=1_4}"),
            "0/0/1/4",
        )

    def test_eth_colon_fallback(self):
        label = "JBI-SIMK-EN1-Z20HS-[1/1/0]_ETH:28_JBI-OTOB-EN1-Z20HS-[1/1/0]_ETH:28"
        a, z = resolve_link_ifnames(
            a_end_tp_ref="ME{a},EQ={/r=0/sh=1/sl=1},PTP={/p=1_28}",
            z_end_tp_ref="ME{b},EQ={/r=0/sh=1/sl=1},PTP={/p=1_28}",
            user_label=label,
        )
        self.assertEqual(a, "1/1/0/28")
        self.assertEqual(z, "1/1/0/28")

    def test_prefer_richer_ifname(self):
        from netx_api.ume_port_normalize import prefer_richer_ifname

        self.assertEqual(
            prefer_richer_ifname("1/1/0/32", "xxvgei-1/1/0/32"),
            "xxvgei-1/1/0/32",
        )
        self.assertEqual(
            prefer_richer_ifname("xxvgei-1/1/0/32", "1/1/0/32"),
            "xxvgei-1/1/0/32",
        )
        self.assertEqual(
            prefer_richer_ifname("1/1/0/32", "xxvgei-1/1/0/28"),
            "1/1/0/32",
        )

    def test_port_compatible(self):
        self.assertTrue(port_keys_compatible("xxvgei-1/1/0/32", "1/1/0/32"))
        self.assertTrue(port_keys_compatible("xxvgei-1/1/0/32", "xxvgei-1/1/0/32"))
        self.assertFalse(port_keys_compatible("xxvgei-1/1/0/32", "xxvgei-1/1/0/28"))
        # Must not false-match via endswith (11/… vs 1/…).
        self.assertFalse(port_keys_compatible("11/1/0/1", "1/1/0/1"))
        self.assertFalse(port_keys_compatible("xxvgei-11/1/0/1", "1/1/0/1"))

    def test_no_tp_does_not_invent_port_pair(self):
        a, z = resolve_link_ifnames(
            a_end_tp_ref="ME{a}",
            z_end_tp_ref="ME{b}",
            user_label="NE-A_xxvgei-1/1/0/32_NE-B_xxvgei-1/1/0/28",
        )
        self.assertEqual(a, "")
        self.assertEqual(z, "")

    def test_tp_first_ignores_swapped_neighbor_ports_in_label(self):
        """Real UME label embeds the *peer* port beside each end — order swaps A/Z.

        TP pins local ports (A=14, Z=16); media prefix is taken from whichever
        token matches that numeric tail (even if it appears under the other end).
        """
        label = (
            "RSFRL22-RMP01-SMGD[0-1-1]-25GE:14(NNI | RSFRLR1-RMP01 xxvgei-1/1/0/16 "
            "| CLARO | FO | RSCSL15-RSNHO06-01)_RSFRLR1-RMP01-SMGD[0-1-1]-25GE:16"
            "(NNI | RSFRL22-RMP01 xxvgei-1/1/0/14 | CLARO | FO | RSCSL15-RSNHO06-01)"
        )
        a_tp = (
            "ME{4e598e5d-fe42-4c79-9f62-7d3e5d4eb5b2},EQ={/r=0/sh=1/sl=1},PTP={/p=1_14}"
        )
        z_tp = (
            "ME{b1215371-491a-4641-a1fd-9d59c8002e77},EQ={/r=0/sh=1/sl=1},PTP={/p=1_16}"
        )
        tokens = extract_ifnames_from_user_label(label)
        # Label order is neighbor-first (16 then 14) — must not drive A/Z.
        self.assertEqual(tokens[0], "xxvgei-1/1/0/16")
        self.assertEqual(tokens[1], "xxvgei-1/1/0/14")
        self.assertEqual(port_suffix_from_tp_ref(a_tp), "1/1/0/14")
        self.assertEqual(port_suffix_from_tp_ref(z_tp), "1/1/0/16")
        a, z = resolve_link_ifnames(
            a_end_tp_ref=a_tp, z_end_tp_ref=z_tp, user_label=label
        )
        self.assertEqual(a, "xxvgei-1/1/0/14")
        self.assertEqual(z, "xxvgei-1/1/0/16")

    def test_tp_suffix_without_matching_media_stays_bare(self):
        a, z = resolve_link_ifnames(
            a_end_tp_ref="ME{a},EQ={/r=0/sh=1/sl=1},PTP={/p=1_14}",
            z_end_tp_ref="ME{b},EQ={/r=0/sh=1/sl=1},PTP={/p=1_16}",
            user_label="no-cli-tokens-here",
        )
        self.assertEqual(a, "1/1/0/14")
        self.assertEqual(z, "1/1/0/16")
        self.assertTrue(port_keys_compatible(a, "xxvgei-1/1/0/14"))
        self.assertTrue(port_keys_compatible(z, "cgei-1/1/0/16"))


class UmeFabricApplyTests(unittest.TestCase):
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

    def _seed_ume(self):
        self.db.add(
            UmeInventoryNE(
                ne_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                ne_name="ME{a}",
                user_label="NE-A",
                host_name="NE-A",
                ip_address="10.0.0.1",
            )
        )
        self.db.add(
            UmeInventoryNE(
                ne_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                ne_name="ME{b}",
                user_label="NE-B",
                host_name="NE-B",
                ip_address="10.0.0.2",
            )
        )
        self.db.add(
            UmeTopoNode(
                node_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                name="MD=ZTE;ME=aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                node_type="TOPO_NODE_ME",
                user_label="NE-A",
                parent_node="sbn-1",
                x_pos=100,
                y_pos=200,
                ume_ne_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                first_seen_at=self.now,
                last_seen_at=self.now,
            )
        )
        self.db.add(
            UmeTopoNode(
                node_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                name="MD=ZTE;ME=bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                node_type="TOPO_NODE_ME",
                user_label="NE-B",
                parent_node="sbn-1",
                x_pos=300,
                y_pos=400,
                ume_ne_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                first_seen_at=self.now,
                last_seen_at=self.now,
            )
        )
        self.db.add(
            UmeTopoLink(
                link_id="link-1",
                name="TL{…}",
                user_label="NE-A_xxvgei-1/1/0/32_NE-B_xxvgei-1/1/0/28",
                a_end_tp_ref="ME{aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa},EQ={/r=0/sh=1/sl=1},PTP={/p=1_32}",
                z_end_tp_ref="ME{bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb},EQ={/r=0/sh=1/sl=1},PTP={/p=1_28}",
                a_ume_ne_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                z_ume_ne_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                a_ptp="/p=1_32",
                z_ptp="/p=1_28",
                a_ifname="xxvgei-1/1/0/32",
                z_ifname="xxvgei-1/1/0/28",
                first_seen_at=self.now,
                last_seen_at=self.now,
            )
        )
        self.db.commit()

    def test_apply_sets_coords_and_ume_edge(self):
        self._seed_ume()
        stats = apply_ume_topology_to_fabric(self.db)
        self.assertEqual(stats["nodes_coords"], 2)
        self.assertGreaterEqual(stats["edges_upserted"] + stats["edges_merged"], 1)
        a = (
            self.db.query(TopoFabricNode)
            .filter(TopoFabricNode.ume_ne_id == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
            .one()
        )
        self.assertEqual((a.attrs or {}).get("ume_local_x"), 100.0)
        self.assertEqual((a.attrs or {}).get("ume_local_y"), 200.0)
        # Flat packing: single SBN slot → local relative to cluster min.
        self.assertEqual(a.world_x, 0.0)
        self.assertEqual(a.world_y, 0.0)
        edges = self.db.query(TopoFabricEdge).all()
        self.assertEqual(len(edges), 1)
        self.assertIn("ume", (edges[0].attrs or {}).get("sources", []))

    def test_lldp_edge_survives_ume_clear(self):
        self._seed_ume()
        apply_ume_topology_to_fabric(self.db)
        a = (
            self.db.query(TopoFabricNode)
            .filter(TopoFabricNode.ume_ne_id == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
            .one()
        )
        b = (
            self.db.query(TopoFabricNode)
            .filter(TopoFabricNode.ume_ne_id == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
            .one()
        )
        edge = self.db.query(TopoFabricEdge).one()
        # Simulate LLDP also seeing it
        edge.source = "lldp"
        edge.attrs = {"sources": ["ume", "lldp"]}
        self.db.commit()

        # Remove UME link → apply should keep edge (lldp remains)
        self.db.query(UmeTopoLink).delete()
        self.db.commit()
        apply_ume_topology_to_fabric(self.db)
        edge2 = self.db.query(TopoFabricEdge).one()
        self.assertEqual(edge2.status, "active")
        self.assertIn("lldp", (edge2.attrs or {}).get("sources", []))
        self.assertNotIn("ume", (edge2.attrs or {}).get("sources", []))

    def test_pure_ume_becomes_missing_when_gone(self):
        self._seed_ume()
        apply_ume_topology_to_fabric(self.db)
        self.db.query(UmeTopoLink).delete()
        self.db.commit()
        apply_ume_topology_to_fabric(self.db)
        edge = self.db.query(TopoFabricEdge).one()
        self.assertEqual(edge.status, "missing")

    def test_manual_edge_never_cleared(self):
        self._seed_ume()
        apply_ume_topology_to_fabric(self.db)
        a = (
            self.db.query(TopoFabricNode)
            .filter(TopoFabricNode.ume_ne_id == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
            .one()
        )
        b = (
            self.db.query(TopoFabricNode)
            .filter(TopoFabricNode.ume_ne_id == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
            .one()
        )
        upsert_fabric_edge(
            self.db,
            a_node_id=a.id,
            b_node_id=b.id,
            a_port="gi0/1",
            b_port="gi0/2",
            source="manual",
        )
        self.db.commit()
        self.db.query(UmeTopoLink).delete()
        self.db.commit()
        apply_ume_topology_to_fabric(self.db)
        manuals = (
            self.db.query(TopoFabricEdge).filter(TopoFabricEdge.source == "manual").all()
        )
        self.assertEqual(len(manuals), 1)
        self.assertEqual(manuals[0].status, "active")

    def test_apply_reresolves_and_upgrades_fabric_ports(self):
        """Apply always re-resolves dock ifnames and upgrades compatible Fabric ports."""
        from netx_api.ume_port_normalize import numeric_port_tail

        self._seed_ume()
        apply_ume_topology_to_fabric(self.db)
        link = self.db.query(UmeTopoLink).one()
        edge = self.db.query(TopoFabricEdge).one()
        # Simulate stale bare dock + fabric ports after an older resolve.
        link.a_ifname = "1/1/0/32"
        link.z_ifname = "1/1/0/28"
        edge.a_port = numeric_port_tail(edge.a_port)
        edge.b_port = numeric_port_tail(edge.b_port)
        self.db.commit()

        apply_ume_topology_to_fabric(self.db)
        self.db.refresh(link)
        self.db.refresh(edge)
        self.assertEqual({link.a_ifname, link.z_ifname}, {"xxvgei-1/1/0/32", "xxvgei-1/1/0/28"})
        self.assertEqual({edge.a_port, edge.b_port}, {"xxvgei-1/1/0/32", "xxvgei-1/1/0/28"})

    def test_lldp_upgrades_bare_ume_ports_and_keeps_ume_primary(self):
        from netx_api.ume_port_normalize import numeric_port_tail

        self._seed_ume()
        # Apply creates bare-capable UME edge with media from label.
        apply_ume_topology_to_fabric(self.db)
        edge = self.db.query(TopoFabricEdge).one()
        # Force bare ports as if UME had no media prefix (keep A/B assignment).
        rich_a, rich_b = edge.a_port, edge.b_port
        edge.a_port = numeric_port_tail(edge.a_port)
        edge.b_port = numeric_port_tail(edge.b_port)
        edge.source = "ume"
        edge.attrs = {"sources": ["ume"], "ume_link_id": "link-1"}
        self.db.commit()
        a = (
            self.db.query(TopoFabricNode)
            .filter(TopoFabricNode.ume_ne_id == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
            .one()
        )
        b = (
            self.db.query(TopoFabricNode)
            .filter(TopoFabricNode.ume_ne_id == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
            .one()
        )
        edge2, action = upsert_fabric_edge(
            self.db,
            a_node_id=a.id,
            b_node_id=b.id,
            a_port=rich_a,
            b_port=rich_b,
            source="lldp",
        )
        self.db.commit()
        self.assertEqual(action, "updated")
        self.assertIsNotNone(edge2)
        assert edge2 is not None
        self.assertEqual(edge2.id, edge.id)
        self.assertEqual(edge2.source, "ume")
        self.assertIn("lldp", (edge2.attrs or {}).get("sources", []))
        ports = {edge2.a_port, edge2.b_port}
        self.assertEqual(ports, {"xxvgei-1/1/0/32", "xxvgei-1/1/0/28"})

    def test_lldp_can_add_edge_between_ume_nes_when_ume_has_no_link(self):
        """UME dump may omit a physical link — LLDP may still create it (dashed)."""
        self._seed_ume()
        apply_ume_topology_to_fabric(self.db)
        a = (
            self.db.query(TopoFabricNode)
            .filter(TopoFabricNode.ume_ne_id == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
            .one()
        )
        b = (
            self.db.query(TopoFabricNode)
            .filter(TopoFabricNode.ume_ne_id == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
            .one()
        )
        before = self.db.query(TopoFabricEdge).count()
        edge, action = upsert_fabric_edge(
            self.db,
            a_node_id=a.id,
            b_node_id=b.id,
            a_port="xxvgei-1/1/0/99",
            b_port="xxvgei-1/1/0/98",
            source="lldp",
        )
        self.db.commit()
        self.assertEqual(action, "added")
        self.assertIsNotNone(edge)
        assert edge is not None
        self.assertEqual(edge.source, "lldp")
        self.assertEqual(self.db.query(TopoFabricEdge).count(), before + 1)

    def test_gap_needs_apply_when_dock_only(self):
        self._seed_ume()
        self.db.commit()
        gap = ume_topology_apply_gap(self.db)
        self.assertGreater(gap["dock_me_count"], 0)
        self.assertEqual(gap["fabric_ume_count"], 0)
        self.assertTrue(gap["needs_apply"])

    def test_gap_needs_apply_on_partial_job(self):
        self._seed_ume()
        apply_ume_topology_to_fabric(self.db)
        self.db.add(
            UmeSyncJob(
                domain="topology",
                status="partial",
                trigger_mode="manual",
                started_at=self.now,
                ended_at=self.now,
                error_message="dock_ok_fabric_apply_failed:boom",
            )
        )
        self.db.commit()
        gap = ume_topology_apply_gap(self.db)
        self.assertTrue(gap["partial_apply"])
        self.assertTrue(gap["needs_apply"])

    def test_apply_if_needed_skips_when_healthy(self):
        self._seed_ume()
        apply_ume_topology_to_fabric(self.db)
        # World folder may still be missing → force a clean gap check after ensure.
        from netx_api.ume_topology_world import ensure_ume_world_and_sbn_folders

        ensure_ume_world_and_sbn_folders(self.db)
        self.db.add(
            UmeSyncJob(
                domain="topology",
                status="done",
                trigger_mode="manual",
                started_at=self.now,
                ended_at=self.now,
            )
        )
        self.db.commit()
        gap = ume_topology_apply_gap(self.db)
        self.assertFalse(gap["needs_apply"])
        self.assertIsNone(apply_ume_dock_to_fabric_if_needed(self.db, reason="test"))


if __name__ == "__main__":
    unittest.main()
