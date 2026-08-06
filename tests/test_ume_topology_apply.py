"""Unit tests for UME port normalize + fabric apply provenance."""

from __future__ import annotations

import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import netx_api.models  # noqa: F401
from netx_api.db import Base
from netx_api.models import TopoFabricEdge, TopoFabricNode, UmeInventoryNE, UmeTopoLink, UmeTopoNode
from netx_api.ume_port_normalize import (
    extract_ifnames_from_user_label,
    port_keys_compatible,
    port_suffix_from_tp_ref,
    resolve_link_ifnames,
)
from netx_api.ume_topology_apply import apply_ume_topology_to_fabric
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

    def test_port_compatible(self):
        self.assertTrue(port_keys_compatible("xxvgei-1/1/0/32", "1/1/0/32"))
        self.assertTrue(port_keys_compatible("xxvgei-1/1/0/32", "xxvgei-1/1/0/32"))
        self.assertFalse(port_keys_compatible("xxvgei-1/1/0/32", "xxvgei-1/1/0/28"))


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


if __name__ == "__main__":
    unittest.main()
