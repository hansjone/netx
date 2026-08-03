"""Tests for pre-TextFSM wrap-line joining and ZTE wrapped CLI samples."""

from __future__ import annotations

import unittest

from netx_api.cli_wrap import apply_cli_wrap
from netx_api.ntc_parse import parse_cli
from netx_api.port_traffic_parsers import parse_interface_brief
from netx_api import topology_lldp as lldp


ZTE_BRIEF_PORTATTRIBUTE_WRAP = """\
CSR3_6120WA#show interface brief
Interface      Portattribute  Mode  BW(Mbps)  Admin Phy   Prot  Description 
gei-1/1/0/1    electric   Duplex/full  100    up    up    up    
xgei-1/1/0/28  optical    Duplex/full  100    up    up    up    BJX_Xinert-8/9
xgei-1/1/0/31  optical    Duplex/full  100    up    up    up    css-5/5-4
smartgroup101  N/A        N/A          100    up    up    up    yangzhen_CSR3_IX
                                                                IA
CSR3_6120WA# 
"""

ZTE_LLDP_WRAP_NAME = """\
MER1#show ll n b
11:21:03 Beijing Mon Aug 3 2026
Scope codes:
    NB    = Nearest Bridge
    NC    = Nearest Customer Bridge
    NTPMR = Nearest non-TPMR Bridge   

Total neighbors: 1
Local Interface  Scope  Chassis ID      Port ID         Holdtime  System Name   
--------------------------------------------------------------------------------
mgmt_eth         NB     fc44.9f67.4214  gei-0/1/1/16    99        NJ-NZ-N8-F3-AC
                                                                  C.R&D-3H3-6
MER1#
"""

ZTE_LLDP_WRAP_MIDWORD = """\
AL5458-ACC-6120HS#show ll n b
10:47:56 Africa/Algiers Mon Aug 3 2026
Scope codes:
    NB    = Nearest Bridge
    NC    = Nearest Customer Bridge
    NTPMR = Nearest non-TPMR Bridge   

Total neighbors: 2
Local Interface  Scope  Chassis ID      Port ID         Holdtime  System Name   
--------------------------------------------------------------------------------
xgei-1/1/0/1     NB     00d0.0000.088f  xgei-1/1/0/1    93        AL5458-ACC-612
                                                                  0HS
xgei-1/1/0/6     NB     00d0.0000.088f  xgei-1/1/0/6    93        AL5458-ACC-612
                                                                  0HS
AL5458-ACC-6120HS#
"""

ZTE_LLDP_NO_WRAP = """\
CSR3_6120WA#show ll n b
Scope codes:
    NB    = Nearest Bridge
    NC    = Nearest Customer Bridge
    NTPMR = Nearest non-TPMR Bridge   

Total neighbors: 4
Local Interface  Scope  Chassis ID      Port ID         Holdtime  System Name   
--------------------------------------------------------------------------------
gei-1/1/0/1      NB     00d0.0000.081f  gei-1/1/0/1     115       OLT/CPE_6180H
gei-1/1/0/2      NB     00d0.0000.081f  gei-1/1/0/2     103       OLT/CPE_6180H
xgei-1/1/0/27    NB     d80a.e69d.6e30  xgei-1/1/0/28   110       CSR4_6120HSC
xgei-1/1/0/30    NB     0247.8a3e.f910  xgei-1/1/0/2    96        PAG3_6120HS
CSR3_6120WA#
"""


class CliWrapUnitTests(unittest.TestCase):
    def test_join_interface_brief_description(self) -> None:
        flat = apply_cli_wrap(
            ZTE_BRIEF_PORTATTRIBUTE_WRAP,
            platform="zte_zxros",
            command="show interface brief",
        )
        self.assertIn("yangzhen_CSR3_IXIA", flat)
        self.assertNotIn("\n                                                                IA", flat)

    def test_join_lldp_system_name_midword(self) -> None:
        flat = apply_cli_wrap(
            ZTE_LLDP_WRAP_MIDWORD,
            platform="zte_zxros",
            command="show lldp neighbor brief",
        )
        self.assertIn("xgei-1/1/0/1     NB     00d0.0000.088f  xgei-1/1/0/1    93        AL5458-ACC-6120HS", flat)
        self.assertNotRegex(flat, r"AL5458-ACC-612\s*\n")

    def test_no_rule_for_unrelated_command(self) -> None:
        raw = "iface\n        cont"
        self.assertEqual(
            apply_cli_wrap(raw, platform="zte_zxros", command="show interface xgei-1/1/0/1"),
            raw,
        )


class ZteWrapParseTests(unittest.TestCase):
    def test_brief_portattribute_header_and_wrap(self) -> None:
        ports = parse_interface_brief(ZTE_BRIEF_PORTATTRIBUTE_WRAP, "zte")
        by_name = {p.ifname: p for p in ports}
        self.assertIn("smartgroup101", by_name)
        self.assertEqual(by_name["smartgroup101"].description, "yangzhen_CSR3_IXIA")
        self.assertEqual(by_name["xgei-1/1/0/28"].description, "BJX_Xinert-8/9")
        self.assertEqual(by_name["gei-1/1/0/1"].admin, "up")

    def test_lldp_wrapped_system_name(self) -> None:
        hits = lldp.parse_zte_lldp(ZTE_LLDP_WRAP_NAME)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].local_port, "mgmt_eth")
        self.assertEqual(hits[0].remote_name, "NJ-NZ-N8-F3-ACC.R&D-3H3-6")
        self.assertEqual(hits[0].remote_port, "gei-0/1/1/16")

    def test_lldp_wrapped_midword_hostname(self) -> None:
        hits = lldp.parse_zte_lldp(ZTE_LLDP_WRAP_MIDWORD)
        self.assertEqual(len(hits), 2)
        self.assertTrue(all(h.remote_name == "AL5458-ACC-6120HS" for h in hits))

    def test_lldp_unwrapped_still_works(self) -> None:
        rows = parse_cli(
            platform="zte_zxros",
            command="show lldp neighbor brief",
            text=ZTE_LLDP_NO_WRAP,
        )
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0].get("neighbor_name"), "OLT/CPE_6180H")


if __name__ == "__main__":
    unittest.main()
