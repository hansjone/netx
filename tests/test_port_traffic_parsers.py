"""Unit tests for ZTE / Huawei port traffic brief/detail parsers."""

from __future__ import annotations

import unittest

from netx_api.port_traffic_commands import commands_for_vendor, detail_command
from netx_api.port_traffic_parsers import (
    parse_bw_to_bps,
    parse_cisco_interface_brief,
    parse_cisco_interface_detail,
    parse_huawei_interface_brief,
    parse_huawei_interface_detail,
    parse_interface_brief,
    parse_interface_detail,
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

HUAWEI_BRIEF = """\
<r1>display interface brief 
PHY: Physical
*down: administratively down
InUti/OutUti: input utility/output utility
Interface                   PHY   Protocol  InUti OutUti   inErrors  outErrors
Ethernet1/0/0               up    up        0.01%     0%          0          0
Ethernet1/0/1               up    down         0%     0%          0          0
GigabitEthernet0/0/0        up    down         0%     0%          0          0
NULL0                       up    up(s)        0%     0%          0          0
<r1>
"""

HUAWEI_DETAIL = """\
<r1>display interface Ethernet1/0/0
Ethernet1/0/0 current state : UP (ifindex: 5)
Line protocol current state : UP 
Description: 
Route Port,The Maximum Transmit Unit is 1500 
    Last 300 seconds input rate: 0 bits/sec, 1 packets/sec
    Last 300 seconds output rate: 0 bits/sec, 0 packets/sec
    Input peak rate 0 bits/sec, Record time: -
    Output peak rate 0 bits/sec, Record time: -
    Last 300 seconds input utility rate:  0.01%
    Last 300 seconds output utility rate: 0.00%
<r1>
"""

CISCO_BRIEF = """\
R2#show ip interface brief 
Interface                  IP-Address      OK? Method Status                Protocol
GigabitEthernet0/0         192.168.0.128   YES NVRAM  up                    up      
GigabitEthernet0/1         172.16.0.2      YES manual up                    up      
GigabitEthernet0/2         unassigned      YES NVRAM  administratively down down    
GigabitEthernet0/3         unassigned      YES NVRAM  administratively down down    
R2#
"""

CISCO_DETAIL = """\
R2#show interfaces GigabitEthernet0/1
GigabitEthernet0/1 is up, line protocol is up 
  Hardware is iGbE, address is 5000.0003.0001 (bia 5000.0003.0001)
  Internet address is 172.16.0.2/30
  MTU 1500 bytes, BW 1000000 Kbit/sec, DLY 10 usec, 
     reliability 255/255, txload 1/255, rxload 1/255
  Encapsulation ARPA, loopback not set
  Last clearing of "show interface" counters never
  5 minute input rate 0 bits/sec, 0 packets/sec
  5 minute output rate 0 bits/sec, 0 packets/sec
     524 packets input, 158824 bytes, 0 no buffer
R2#
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

    def test_cisco_kbit_sec(self):
        self.assertEqual(parse_bw_to_bps("MTU 1500 bytes, BW 1000000 Kbit/sec, DLY 10 usec,"), 1_000_000_000)


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

    def test_rejects_trailing_hostname_prompt(self):
        text = SAMPLE_BRIEF + "\nAL5458-ACC-6120HS#\n"
        rows = parse_zte_interface_brief(text)
        names = [r.ifname for r in rows]
        self.assertNotIn("AL5458-ACC-6120HS#", names)
        self.assertNotIn("AL5458-ACC-6120HS", names)
        self.assertTrue(any(n.startswith("xgei-") for n in names))

    def test_rejects_prompt_without_hash(self):
        text = (
            SAMPLE_BRIEF
            + "\nAL5458-ACC-6120HS\n"
            + "AL5458-ACC-6120HS#show something\n"
        )
        rows = parse_zte_interface_brief(text)
        names = [r.ifname for r in rows]
        self.assertNotIn("AL5458-ACC-6120HS", names)
        self.assertNotIn("AL5458-ACC-6120HS#show", names)


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
        self.assertNotEqual(d.in_bps, 3536.0)


class HuaweiBriefParserTests(unittest.TestCase):
    def test_sample_brief(self):
        rows = parse_huawei_interface_brief(HUAWEI_BRIEF)
        by_name = {r.ifname: r for r in rows}
        self.assertEqual(len(rows), 4)
        self.assertEqual(by_name["Ethernet1/0/0"].phy, "up")
        self.assertEqual(by_name["Ethernet1/0/0"].prot, "up")
        self.assertEqual(by_name["Ethernet1/0/0"].bw_bps, 0)
        self.assertEqual(by_name["Ethernet1/0/1"].prot, "down")
        self.assertEqual(by_name["NULL0"].prot, "up")  # up(s)
        self.assertEqual(by_name["GigabitEthernet0/0/0"].ifname, "GigabitEthernet0/0/0")

    def test_dispatch(self):
        rows = parse_interface_brief(HUAWEI_BRIEF, "huawei")
        self.assertTrue(any(r.ifname == "Ethernet1/0/0" for r in rows))


class HuaweiDetailParserTests(unittest.TestCase):
    def test_sample_detail(self):
        d = parse_huawei_interface_detail(HUAWEI_DETAIL)
        self.assertEqual(d.ifname, "Ethernet1/0/0")
        self.assertEqual(d.admin_oper, "up")
        self.assertEqual(d.bw_bps, 0)
        self.assertEqual(d.rate_period_sec, 300)
        self.assertEqual(d.in_bps, 0.0)
        self.assertEqual(d.out_bps, 0.0)
        self.assertEqual(d.in_util_pct, 0.01)
        self.assertEqual(d.out_util_pct, 0.0)

    def test_dispatch(self):
        d = parse_interface_detail(HUAWEI_DETAIL, "huawei")
        self.assertEqual(d.in_util_pct, 0.01)


class CiscoBriefParserTests(unittest.TestCase):
    def test_sample_brief(self):
        rows = parse_cisco_interface_brief(CISCO_BRIEF)
        by_name = {r.ifname: r for r in rows}
        self.assertEqual(len(rows), 4)
        self.assertEqual(by_name["GigabitEthernet0/0"].phy, "up")
        self.assertEqual(by_name["GigabitEthernet0/0"].prot, "up")
        self.assertEqual(by_name["GigabitEthernet0/1"].admin, "up")
        self.assertEqual(by_name["GigabitEthernet0/2"].admin, "down")
        self.assertEqual(by_name["GigabitEthernet0/2"].phy, "down")
        self.assertEqual(by_name["GigabitEthernet0/2"].prot, "down")

    def test_dispatch(self):
        rows = parse_interface_brief(CISCO_BRIEF, "cisco")
        self.assertTrue(any(r.ifname == "GigabitEthernet0/1" for r in rows))


class CiscoDetailParserTests(unittest.TestCase):
    def test_sample_detail(self):
        d = parse_cisco_interface_detail(CISCO_DETAIL)
        self.assertEqual(d.ifname, "GigabitEthernet0/1")
        self.assertEqual(d.admin_oper, "up")
        self.assertEqual(d.bw_bps, 1_000_000_000)
        self.assertEqual(d.rate_period_sec, 300)
        self.assertEqual(d.in_bps, 0.0)
        self.assertEqual(d.out_bps, 0.0)
        self.assertEqual(d.in_util_pct, 0.0)
        self.assertEqual(d.out_util_pct, 0.0)

    def test_dispatch(self):
        d = parse_interface_detail(CISCO_DETAIL, "cisco")
        self.assertEqual(d.bw_bps, 1_000_000_000)


class CommandsTests(unittest.TestCase):
    def test_zte_matrix(self):
        cmds = commands_for_vendor("ZTE", "zxros")
        assert cmds is not None
        self.assertEqual(cmds.brief, "show interface brief")
        self.assertEqual(detail_command(cmds, "xgei-1/1/0/1"), "show interface xgei-1/1/0/1")
        self.assertIsNone(commands_for_vendor("Nokia", "sros"))

    def test_huawei_matrix(self):
        cmds = commands_for_vendor("Huawei", "huawei_vrp")
        assert cmds is not None
        self.assertEqual(cmds.vendor_key, "huawei")
        self.assertEqual(cmds.brief, "display interface brief")
        self.assertEqual(
            detail_command(cmds, "Ethernet1/0/0"),
            "display interface Ethernet1/0/0",
        )

    def test_cisco_matrix(self):
        cmds = commands_for_vendor("Cisco", "cisco_ios")
        assert cmds is not None
        self.assertEqual(cmds.vendor_key, "cisco")
        self.assertEqual(cmds.brief, "show ip interface brief")
        self.assertEqual(
            detail_command(cmds, "GigabitEthernet0/1"),
            "show interfaces GigabitEthernet0/1",
        )


if __name__ == "__main__":
    unittest.main()
