"""Unit tests for topology CRUD and LLDP/CDP parsers."""

from __future__ import annotations

import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

from netx_api import topology_lldp as lldp
from netx_api import topology_service as svc
from netx_api.db import Base, SessionLocal, engine
from netx_api.models import ManagedNE, TopologyMap
from netx_api.topology_schemas import (
    TopologyDiscoverRequest,
    TopologyEdgeIn,
    TopologyGraphPut,
    TopologyMapCreate,
    TopologyNodeIn,
)


CISCO_LLDP_BRIEF = """
Capability codes:
  (R) Router, (B) Bridge

Device ID           Local Intf     Hold-time  Capability      Port ID
R1                  Gi0/0          120        R               Gi0/1
R3                  Gi0/1          120        R               Gi0/0
"""

CISCO_LLDP_DETAIL = """
R2#show lldp neighbors  detail
------------------------------------------------
Local Intf: Gi0/1
Chassis id: 707b.5c6e.d130
Port id: Ethernet1/0/1
Port Description - not advertised
System Name: r1

System Description: 
Huawei Versatile Routing Platform Software
VRP (R) software, Version 8.180 (NE40E V800R011C00SPC607B607)
Copyright (C) 2012-2018 Huawei Technologies Co., Ltd.
HUAWEI NE40E


Time remaining: 97 seconds
System Capabilities: B,R
Enabled Capabilities: B,R
Management Addresses:
    Other: 70 7B 5C 6E FF 30 00
    OID:
        0.6.8.43.6.1.2.1.17.1.1.
Auto Negotiation - supported, enabled
Physical media capabilities - not advertised
Media Attachment Unit type - not advertised
Vlan ID: - not advertised


Total entries displayed: 1
"""

CISCO_CDP_DETAIL = """
-------------------------
Device ID: R1.lab.local
IP address: 192.168.0.1
Platform: Cisco,  Capabilities: Router
Interface: GigabitEthernet0/0,  Port ID (outgoing port): GigabitEthernet0/1

-------------------------
Device ID: R3
IP address: 192.168.0.3
Interface: GigabitEthernet0/1,  Port ID (outgoing port): GigabitEthernet0/0
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

HUAWEI_LLDP_LAB = """
]display lldp  neighbor 
Ethernet1/0/0 has 0 neighbor(s)

Ethernet1/0/1 has 1 neighbor(s):

Neighbor index                     :1
Chassis type                       :macAddress
Chassis ID                         :5000-0003-0000
Port ID type                       :interfaceName
Port ID                            :Gi0/1
Port description                   :GigabitEthernet0/1            
System name                        :R2.example.com                
System description                 :Cisco IOS Software, IOSv Software (VIOS-ADVENTERPRISEK9-M), Version 15.9(3)M4, RELEASE SOFTWARE (fc3)
Technical Support: http://www.cisco.com/techsupport
Copyright (c) 1986-2021 by Cisco Systems, Inc.
Compiled Wed 04-Aug-21 08:13 by mcpre
System capabilities supported      :bridge router
System capabilities enabled        :router
Management address type            :ipv4
Management address                 :192.168.0.128
Expired time                       :110s

Port VLAN ID(PVID)                 :--
Discovered time                    :2026-06-05 17:07:21

Ethernet1/0/2 has 0 neighbor(s)

GigabitEthernet0/0/0 has 0 neighbor(s)
"""


class TopologyLldpParseTests(unittest.TestCase):
    def test_parse_cisco_lldp_brief_fallback(self) -> None:
        hits = lldp.parse_cisco_lldp(CISCO_LLDP_BRIEF)
        self.assertGreaterEqual(len(hits), 2)
        self.assertEqual(hits[0].remote_name, "R1")
        self.assertEqual(hits[0].local_port, "Gi0/0")

    def test_parse_cisco_lldp_detail_lab(self) -> None:
        hits = lldp.parse_neighbor_output(
            CISCO_LLDP_DETAIL, protocol="lldp", vendor="Cisco", device_type="cisco_ios"
        )
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].remote_name, "r1")
        self.assertEqual(hits[0].local_port, "Gi0/1")
        self.assertEqual(hits[0].remote_port, "Ethernet1/0/1")
        self.assertEqual(hits[0].remote_ip, "")  # Other: MAC only in this sample

    def test_parse_cdp_detail(self) -> None:
        hits = lldp.parse_neighbor_output(CISCO_CDP_DETAIL, protocol="cdp")
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0].remote_ip, "192.168.0.1")
        self.assertIn("GigabitEthernet0/0", hits[0].local_port)

    def test_parse_huawei_legacy_and_lab(self) -> None:
        legacy = lldp.parse_neighbor_output(
            HUAWEI_LLDP, protocol="lldp", vendor="Huawei", device_type="huawei"
        )
        self.assertEqual(len(legacy), 1)
        self.assertEqual(legacy[0].remote_name, "r1")
        self.assertEqual(legacy[0].remote_ip, "192.168.0.127")

        lab = lldp.parse_neighbor_output(
            HUAWEI_LLDP_LAB, protocol="lldp", vendor="Huawei", device_type="huawei_vrp"
        )
        self.assertEqual(len(lab), 1)
        self.assertEqual(lab[0].local_port, "Ethernet1/0/1")
        self.assertEqual(lab[0].remote_port, "Gi0/1")
        self.assertEqual(lab[0].remote_name, "R2.example.com")
        self.assertEqual(lab[0].remote_ip, "192.168.0.128")

    def test_pick_command_auto(self) -> None:
        cmd, proto = lldp.pick_neighbor_command(protocol="auto", vendor="Cisco", device_type="cisco_ios")
        self.assertEqual(proto, "lldp")
        self.assertEqual(cmd, "show lldp neighbors detail")
        cmd2, proto2 = lldp.pick_neighbor_command(protocol="auto", vendor="Huawei", device_type="huawei")
        self.assertEqual(proto2, "lldp")
        self.assertEqual(cmd2, "display lldp neighbor")
        cmd3, proto3 = lldp.pick_neighbor_command(protocol="cdp", vendor="Cisco", device_type="cisco_ios")
        self.assertEqual(proto3, "cdp")
        self.assertIn("cdp", cmd3.lower())

    def test_vendor_profiles_cover_requested_vendors(self) -> None:
        expected = {
            "cisco": "show lldp neighbors detail",
            "huawei": "display lldp neighbor",
            "h3c": "display lldp neighbor-information list",
            "zte": "show lldp neighbors",
            "juniper": "show lldp neighbors",
            "nokia": "show system lldp neighbor",
            "ericsson": "show lldp neighbors",
        }
        # Prefer real Netmiko device_type values from inventory.
        samples = {
            "cisco": ("Cisco", "cisco_ios"),
            "huawei": ("Huawei", "huawei_vrp"),
            "h3c": ("H3C", "hp_comware"),
            "zte": ("ZTE", "zte_zxros"),
            "juniper": ("Juniper", "juniper_junos"),
            "nokia": ("Nokia", "nokia_sros"),
            "ericsson": ("Ericsson", "ericsson_ipos"),
        }
        for key, (vendor, dtype) in samples.items():
            self.assertEqual(
                lldp.resolve_vendor_key(vendor, dtype),
                key,
                msg=f"device_type={dtype!r} should map to {key}",
            )
            self.assertEqual(lldp.lldp_command_for_vendor(vendor, dtype), expected[key])
            parser = lldp._VENDOR_PARSERS[key]
            self.assertIsInstance(parser(""), list)

        # device_type alone is enough (no vendor label).
        self.assertEqual(lldp.resolve_vendor_key("", "zte_zxros"), "zte")
        self.assertEqual(lldp.resolve_vendor_key("", "cisco_xe"), "cisco")
        # vendor label fallback when device_type missing.
        self.assertEqual(lldp.resolve_vendor_key("ZTE", ""), "zte")

    def test_parse_routes_by_vendor(self) -> None:
        cisco_hits = lldp.parse_neighbor_output(
            CISCO_LLDP_DETAIL, protocol="lldp", vendor="Cisco", device_type="cisco_ios"
        )
        self.assertEqual(len(cisco_hits), 1)
        hw_hits = lldp.parse_neighbor_output(
            HUAWEI_LLDP_LAB, protocol="lldp", vendor="Huawei", device_type="huawei"
        )
        self.assertEqual(len(hw_hits), 1)
        # Placeholder vendors: empty until lab echo wired.
        self.assertEqual(lldp.parse_juniper_lldp("junk"), [])
        self.assertEqual(lldp.parse_nokia_lldp("junk"), [])
        self.assertEqual(lldp.parse_ericsson_lldp("junk"), [])
        self.assertEqual(lldp.parse_h3c_lldp("junk"), [])


class TopologyServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        Base.metadata.create_all(bind=engine)
        self.db = SessionLocal()
        # Clean topology tables between tests
        for m in self.db.query(TopologyMap).all():
            svc.delete_map(self.db, m.id)

    def tearDown(self) -> None:
        self.db.close()

    def test_map_crud_and_graph_put(self) -> None:
        created = svc.create_map(self.db, TopologyMapCreate(name="Lab", remark="demo"))
        self.assertEqual(created.name, "Lab")
        mid = created.id

        graph = svc.put_graph(
            self.db,
            mid,
            TopologyGraphPut(
                nodes=[
                    TopologyNodeIn(id="n1", label="A", x=10, y=20, managed_ne_id=""),
                    TopologyNodeIn(id="n2", label="B", x=100, y=20, managed_ne_id=""),
                ],
                edges=[
                    TopologyEdgeIn(
                        id="e1",
                        source_node_id="n1",
                        target_node_id="n2",
                        source="manual",
                    )
                ],
            ),
        )
        self.assertEqual(len(graph.nodes), 2)
        self.assertEqual(len(graph.edges), 1)
        self.assertEqual(graph.edges[0].source, "manual")

        listed = svc.list_maps(self.db)
        self.assertGreaterEqual(listed["total"], 1)

        got = svc.get_graph(self.db, mid)
        self.assertEqual(got.map.id, mid)

        svc.delete_map(self.db, mid)
        with self.assertRaises(HTTPException):
            svc.get_graph(self.db, mid)

    def test_discover_matches_by_name_and_skips_manual(self) -> None:
        suffix = uuid4().hex[:8]
        ne_a_id = f"nea-{suffix}"
        ne_b_id = f"neb-{suffix}"
        # Unique lab IPs in TEST-NET-3
        ip_a = f"203.0.113.{(int(suffix[:2], 16) % 100) + 1}"
        ip_b = f"203.0.113.{(int(suffix[2:4], 16) % 100) + 101}"
        ne_a = ManagedNE(
            id=ne_a_id,
            name="R2",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address=ip_a,
            connect_status="pass",
        )
        ne_b = ManagedNE(
            id=ne_b_id,
            name="R1",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address=ip_b,
            connect_status="pass",
        )
        self.db.add(ne_a)
        self.db.add(ne_b)
        self.db.commit()

        created = svc.create_map(self.db, TopologyMapCreate(name=f"Disc-{suffix}"))
        mid = created.id
        svc.put_graph(
            self.db,
            mid,
            TopologyGraphPut(
                nodes=[
                    TopologyNodeIn(id="n1", managed_ne_id=ne_a_id, label="R2", x=0, y=0),
                    TopologyNodeIn(id="n2", managed_ne_id=ne_b_id, label="R1", x=200, y=0),
                ],
                edges=[
                    TopologyEdgeIn(
                        id="manual1",
                        source_node_id="n1",
                        target_node_id="n2",
                        source_port="GigabitEthernet0/0",
                        target_port="GigabitEthernet0/1",
                        source="manual",
                    )
                ],
            ),
        )

        fake_exec = {
            "ok": True,
            "output": CISCO_CDP_DETAIL,
            "commands": ["show cdp neighbors detail"],
        }
        with patch.object(svc, "execute_managed_ne_commands", return_value=fake_exec):
            out = svc.discover_neighbors(
                self.db, mid, TopologyDiscoverRequest(protocol="cdp", ne_ids=[ne_a_id])
            )
        # Manual edge with same ports should be preserved (not overwritten).
        self.assertEqual(out.edges_added, 0)
        graph = svc.get_graph(self.db, mid)
        manuals = [e for e in graph.edges if e.source == "manual"]
        self.assertEqual(len(manuals), 1)

        # Clear ports so discovery can add a new edge key.
        svc.put_graph(
            self.db,
            mid,
            TopologyGraphPut(
                nodes=[
                    TopologyNodeIn(id="n1", managed_ne_id=ne_a_id, label="R2", x=0, y=0),
                    TopologyNodeIn(id="n2", managed_ne_id=ne_b_id, label="R1", x=200, y=0),
                ],
                edges=[],
            ),
        )
        with patch.object(svc, "execute_managed_ne_commands", return_value=fake_exec):
            out2 = svc.discover_neighbors(
                self.db, mid, TopologyDiscoverRequest(protocol="cdp", ne_ids=[ne_a_id])
            )
        self.assertGreaterEqual(out2.edges_added, 1)
        graph2 = svc.get_graph(self.db, mid)
        self.assertTrue(any(e.source == "cdp" for e in graph2.edges))

        svc.delete_map(self.db, mid)
        self.db.delete(ne_a)
        self.db.delete(ne_b)
        self.db.commit()


if __name__ == "__main__":
    unittest.main()
