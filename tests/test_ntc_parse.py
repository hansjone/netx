"""Tests for NetX/community TextFSM parse adapter."""

from __future__ import annotations

import unittest

from netx_api.ntc_parse import parse_cli, resolve_cli_platform
from netx_api.port_traffic_parsers import parse_interface_brief, parse_interface_detail
from netx_api import topology_lldp as lldp


ZTE_LLDP = """\
Local Interface      Chassis ID         Port ID              System Name
gei-0/1/0/1          0011.2233.4455     gei-0/1/0/2          R1
"""

ZTE_LLDP_SCOPE = """\
Total neighbors: 12
Local Interface   Scope  Chassis ID      Port ID           Holdtime  System Name
-----------------------------------------------------------------------------------------
xgei-1/1/0/1     NB     026e.8219.bc57  xgei-1/1/0/26     100       CSR1_6120HSC
xgei-1/1/0/3     NB     00d0.0000.081f  gei-1/2/0/3       113       OLT/CPE_6180H
xxvgei-1/1/0/16  NB     026e.8219.bc57  xxvgei-1/1/0/32   100       CSR1_6120HSC
cgei-1/1/0/34    NB     0022.9354.6e60  cgei-0/3/0/34     110       AG5
"""

ZTE_BRIEF = """\
Interface               Attribute  Mode         BW    Admin Phy   Prot  Description
xgei-1/1/0/1            optical    Duplex/full  1G    up    up    up    C2930L100-EQ2
xgei-1/1/0/2            optical    Duplex/full  1G    up    down  down
"""

ZTE_DETAIL = """\
xgei-1/1/0/1 is up, ifindex: 8194
  Description: C2930L100-EQ2
  BW 1 Gbit/s
  Rate period     : 30 s
   Input          : 824 bit/s            1 packet/s
   Output         : 824 bit/s            1 packet/s
  Intf utilization: input 0%             output 0%
"""

CISCO_LLDP_DETAIL = """\
------------------------------------------------
Local Intf: Gi0/1
Chassis id: 707b.5c6e.d130
Port id: Ethernet1/0/1
System Name: r1
Management Addresses:
    IP: 192.168.0.1
"""


class PlatformMapTests(unittest.TestCase):
    def test_map_common(self) -> None:
        self.assertEqual(resolve_cli_platform(device_type="cisco_ios"), "cisco_ios")
        self.assertEqual(resolve_cli_platform(device_type="cisco_nxos"), "cisco_nxos")
        self.assertEqual(resolve_cli_platform(device_type="huawei"), "huawei_vrp")
        self.assertEqual(resolve_cli_platform(vendor_key="zte"), "zte_zxros")
        self.assertEqual(resolve_cli_platform(device_type="zte_zxros"), "zte_zxros")
        self.assertEqual(resolve_cli_platform(device_type="hp_comware"), "hp_comware")
        self.assertEqual(resolve_cli_platform(vendor_key="h3c"), "hp_comware")
        self.assertEqual(resolve_cli_platform(device_type="juniper_junos"), "juniper_junos")
        self.assertEqual(resolve_cli_platform(device_type="nokia_sros"), "alcatel_sros")
        self.assertEqual(resolve_cli_platform(device_type="alcatel_aos"), "alcatel_aos")
        self.assertEqual(resolve_cli_platform(device_type="mikrotik_routeros"), "mikrotik_routeros")


class CustomTemplateTests(unittest.TestCase):
    def test_zte_lldp_custom(self) -> None:
        rows = parse_cli(
            platform="zte_zxros",
            command="show lldp neighbor brief",
            text=ZTE_LLDP,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get("local_interface"), "gei-0/1/0/1")
        self.assertEqual(rows[0].get("neighbor_name"), "R1")

    def test_zte_lldp_scope_holdtime(self) -> None:
        rows = parse_cli(
            platform="zte_zxros",
            command="show lldp neighbor brief",
            text=ZTE_LLDP_SCOPE,
        )
        self.assertEqual(len(rows), 4)
        by_local = {r.get("local_interface"): r for r in rows}
        self.assertEqual(by_local["xgei-1/1/0/1"].get("neighbor_name"), "CSR1_6120HSC")
        self.assertEqual(by_local["xgei-1/1/0/3"].get("neighbor_name"), "OLT/CPE_6180H")
        self.assertEqual(by_local["xgei-1/1/0/3"].get("neighbor_port_id"), "gei-1/2/0/3")
        self.assertEqual(by_local["cgei-1/1/0/34"].get("scope"), "NB")

    def test_zte_brief_custom(self) -> None:
        rows = parse_cli(
            platform="zte_zxros",
            command="show interface brief",
            text=ZTE_BRIEF,
        )
        self.assertGreaterEqual(len(rows), 2)
        self.assertEqual(rows[0].get("interface"), "xgei-1/1/0/1")

    def test_zte_detail_custom(self) -> None:
        rows = parse_cli(
            platform="zte_zxros",
            command="show interface xgei-1/1/0/1",
            text=ZTE_DETAIL,
        )
        self.assertGreaterEqual(len(rows), 1)
        hit = next((r for r in rows if str(r.get("input_bps") or "")), rows[0])
        self.assertEqual(str(hit.get("input_bps")), "824")


class WiredParserTests(unittest.TestCase):
    def test_zte_lldp_parser(self) -> None:
        hits = lldp.parse_zte_lldp(ZTE_LLDP)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].remote_name, "R1")
        self.assertEqual(hits[0].local_port, "gei-0/1/0/1")

    def test_zte_lldp_scope_parser(self) -> None:
        hits = lldp.parse_zte_lldp(ZTE_LLDP_SCOPE)
        self.assertEqual(len(hits), 4)
        by_local = {h.local_port: h for h in hits}
        self.assertEqual(by_local["xgei-1/1/0/3"].remote_name, "OLT/CPE_6180H")
        self.assertEqual(by_local["xxvgei-1/1/0/16"].remote_port, "xxvgei-1/1/0/32")

    def test_cisco_lldp_community(self) -> None:
        hits = lldp.parse_cisco_lldp(CISCO_LLDP_DETAIL)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].remote_name.lower(), "r1")
        self.assertEqual(hits[0].remote_ip, "192.168.0.1")

    def test_zte_brief_wired(self) -> None:
        ports = parse_interface_brief(ZTE_BRIEF, "zte")
        self.assertGreaterEqual(len(ports), 2)
        self.assertEqual(ports[0].ifname, "xgei-1/1/0/1")
        self.assertEqual(ports[0].admin, "up")

    def test_zte_detail_wired(self) -> None:
        detail = parse_interface_detail(
            ZTE_DETAIL,
            "zte",
            command="show interface xgei-1/1/0/1",
            ifname="xgei-1/1/0/1",
        )
        self.assertEqual(detail.ifname, "xgei-1/1/0/1")
        self.assertEqual(detail.in_bps, 824.0)
        self.assertEqual(detail.out_bps, 824.0)
        self.assertGreaterEqual(detail.bw_bps, 1_000_000_000)

    def test_unknown_command_returns_empty(self) -> None:
        ports = parse_interface_brief(ZTE_BRIEF, "zte", command="show totally unknown")
        self.assertEqual(ports, [])

    def test_h3c_juniper_lldp_community(self) -> None:
        h3c = """\
Local Interface Chassis ID      Port ID        System Name
GE1/0/1         0000-5e00-0101  GE1/0/2        SW-A
"""
        hits = lldp.parse_h3c_lldp(h3c)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].remote_name, "SW-A")
        self.assertEqual(hits[0].local_port, "GE1/0/1")

        junos = """\
Local Interface    Parent Interface    Chassis Id          Port info     System Name
ge-0/0/1.0         ge-0/0/1            00:11:22:33:44:55   ge-0/0/0.0    r1
"""
        jhits = lldp.parse_juniper_lldp(junos)
        self.assertEqual(len(jhits), 1)
        self.assertEqual(jhits[0].remote_name, "r1")
        self.assertEqual(jhits[0].local_port, "ge-0/0/1.0")


if __name__ == "__main__":
    unittest.main()
