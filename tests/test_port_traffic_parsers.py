"""Unit tests for ZTE port traffic brief/detail parsers (sample from oclaw Untitled-1.ps1)."""

from __future__ import annotations

import unittest

from netx_api.port_traffic_commands import commands_for_vendor, detail_command
from netx_api.port_traffic_parsers import (
    parse_bw_to_bps,
    parse_zte_interface_brief,
    parse_zte_interface_detail,
)

SAMPLE_DETAIL = """\
AL5458-ACC-6120HS#show interface xgei-1/1/0/1
13:47:08 Africa/Algiers Fri Jul 31 2026
xgei-1/1/0/1 is up, ifindex: 8194
  Description: C2930L100-EQ2
  Line protocol is up, IPv4 protocol is up, IPv6 protocol is down,
 detected status is RX-OK/TX-OK
  Last line protocol up time :  2026-05-26 15:14:49
  Hardware is XGigabit Ethernet, address is 00d0.0000.088f
  Internet address is 192.168.1.1/30
  BW 1 Gbit/s
  IP MTU 1500 bytes
  MTU 1562 bytes
  MPLS MTU 1548 bytes

  Fec-eth : N/A
  Fec-bypass : N/A
  ARP type ARP
  ARP Timeout 04:00:00
  Last Clear Time : 2026-05-26 15:13:48  Last Refresh Time: 2026-07-31 13:47:00
  Rate period     : 30 s
   Input          : 824 bit/s            1 packet/s
   Output         : 824 bit/s            1 packet/s
  Peak rate:
   Input          : 3536 bit/s           peak time          2026-05-26 15:17:10
   Output         : 6896 bit/s           peak time          2026-05-26 15:17:10
  Intf utilization: input 0%             output 0%
  HardwareCounters:
  In_Bytes          462427066            In_Packets         6529765
"""

SAMPLE_BRIEF = """\
AL5458-ACC-6120HS#show interface brief
13:43:00 Africa/Algiers Fri Jul 31 2026
Interface               Attribute  Mode         BW    Admin Phy   Prot  Description
xgei-1/1/0/1            optical    Duplex/full  1G    up    up    up    C2930L100-EQ2
xgei-1/1/0/2            optical    Duplex/full  1G    up    down  down
xgei-1/1/0/4            optical    Duplex/full  10G   up    down  down  123456test11255
cgei-1/1/0/33           optical    Duplex/full  100G  up    down  down
xxvgei-1/1/0/11         optical    Duplex/full  25G   up    down  down
smartgroup1             N/A        N/A                up    up    down
smartgroup10            N/A        N/A          1G    up    up    up
bvi2                    N/A        N/A                up    up    up
"""


class BwParseTests(unittest.TestCase):
    def test_compact(self):
        self.assertEqual(parse_bw_to_bps("1G"), 1_000_000_000)
        self.assertEqual(parse_bw_to_bps("10G"), 10_000_000_000)
        self.assertEqual(parse_bw_to_bps("100G"), 100_000_000_000)
        self.assertEqual(parse_bw_to_bps("25G"), 25_000_000_000)
        self.assertEqual(parse_bw_to_bps("1M"), 1_000_000)

    def test_detail_line(self):
        self.assertEqual(parse_bw_to_bps("BW 1 Gbit/s"), 1_000_000_000)


class BriefParserTests(unittest.TestCase):
    def test_sample_brief(self):
        rows = parse_zte_interface_brief(SAMPLE_BRIEF)
        by_name = {r.ifname: r for r in rows}
        self.assertIn("xgei-1/1/0/1", by_name)
        r1 = by_name["xgei-1/1/0/1"]
        self.assertEqual(r1.bw_bps, 1_000_000_000)
        self.assertEqual(r1.admin, "up")
        self.assertEqual(r1.phy, "up")
        self.assertEqual(r1.prot, "up")
        self.assertEqual(r1.description, "C2930L100-EQ2")
        self.assertEqual(by_name["xgei-1/1/0/4"].bw_bps, 10_000_000_000)
        self.assertEqual(by_name["cgei-1/1/0/33"].bw_bps, 100_000_000_000)
        self.assertEqual(by_name["xxvgei-1/1/0/11"].bw_bps, 25_000_000_000)
        self.assertEqual(by_name["smartgroup1"].bw_bps, 0)
        self.assertEqual(by_name["smartgroup10"].bw_bps, 1_000_000_000)
        self.assertEqual(by_name["xgei-1/1/0/2"].phy, "down")


class DetailParserTests(unittest.TestCase):
    def test_sample_detail(self):
        d = parse_zte_interface_detail(SAMPLE_DETAIL)
        self.assertEqual(d.ifname, "xgei-1/1/0/1")
        self.assertEqual(d.bw_bps, 1_000_000_000)
        self.assertEqual(d.rate_period_sec, 30)
        self.assertEqual(d.in_bps, 824.0)
        self.assertEqual(d.out_bps, 824.0)
        self.assertEqual(d.in_util_pct, 0.0)
        self.assertEqual(d.out_util_pct, 0.0)
        self.assertEqual(d.description, "C2930L100-EQ2")
        # Peak rates must not override Rate period values
        self.assertNotEqual(d.in_bps, 3536.0)


class CommandsTests(unittest.TestCase):
    def test_zte_matrix(self):
        cmds = commands_for_vendor("ZTE", "zxros")
        assert cmds is not None
        self.assertEqual(cmds.brief, "show interface brief")
        self.assertEqual(detail_command(cmds, "xgei-1/1/0/1"), "show interface xgei-1/1/0/1")
        self.assertIsNone(commands_for_vendor("Cisco", "ios"))


if __name__ == "__main__":
    unittest.main()
