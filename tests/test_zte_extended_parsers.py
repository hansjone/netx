"""Synthetic (desensitized) tests for extended ZTE biz_state parsers."""

from __future__ import annotations

import unittest

from netx_api.biz_state.collect_session import resolve_aux_command
from netx_api.biz_state.command_match import expand_from_bindings, match_command
from netx_api.biz_state.enrich import apply_enrich_joins
from netx_api.biz_state.parsers.common.vrf_list import normalize_vrf_list
from netx_api.biz_state.parsers.zte import (
    normalize_bgp_peer,
    normalize_bgp_route,
    normalize_ip_route,
    normalize_ipv6_route,
    normalize_l2vpn_pw,
    normalize_optical_brief,
    normalize_ospf_neighbor,
    normalize_vrrp,
)
from netx_api.biz_state.profiles import AuxCommand, get_profile, metric_field_map, reload_profiles
from netx_api.ntc_parse import apply_rule


_VRF_SAMPLE = """
Name                             Default RD            Protocols VRF ID
CUST_A                           100:1                 ipv4      1
CUST_B                           200:2                 ipv4,ipv6 2
mng                              <not set>             ipv4,ipv6 3
"""

_BGP_V4_SUMMARY = """
Neighbor        Ver As          MsgRcvd    MsgSend    Up/Down      State/PfxRcd
10.0.0.1        4   65001       100        200        1w0d         5
10.0.0.2        4   65002       10         20         00:01:02     Connect
"""

_BGP_V6_SUMMARY = """
Neighbor        Ver As          MsgRcvd    MsgSend    Up/Down      State/PfxRcd
FC00:1::1
                4   65001       100        200        1w0d         0
FC00:1::2
                4   65002       10         20         2d3h         3
"""

_OSPF_SAMPLE = """
            OSPF Router with ID (1.1.1.1) (Process ID 1)

Neighbor ID     Pri State        DeadTime  Address         Interface
2.2.2.2         1   FULL/--      00:00:39  10.0.0.2        smartgroup1
3.3.3.3         1   FULL/DR      00:00:30  10.0.0.3        gei-0/0/0/1

            OSPF Router with ID (1.1.1.1) (Process ID 20)

Neighbor ID     Pri State        DeadTime  Address         Interface
4.4.4.4         1   FULL/--      00:00:40  10.1.0.2        smartgroup2
"""

_VRRP_SAMPLE = """
Interface         vrID Pri Time   A P L State  Master addr     VRouter addr
vlan10            10   110 1000     P   Master 10.0.0.2        10.0.0.1
gei-0/0/0/1.100   100  90  1000     P   Backup 10.0.1.2        10.0.1.1
smartgroup1.5     5    110 1000     P   Init   0.0.0.0         10.0.2.1
"""

_OPTICAL_SAMPLE = """
Interface         Type              Wavelength  RxPower(dBm)          TxPower(dBm)          Status    Intensity(Rx)
gei-0/0/0/1       1G-10km-SFP       1310nm      -6.0/[-20.0,-3.0]     -5.9/[-9.0,-3.0]      Normal    Normal
cgei-0/1/0/1      100G-LR4          1310nm      -2.1/[-8.0,2.0]       -1.5/[-4.0,2.0]       Normal    Normal
"""

_BGP_ROUTE_IN = """
Routes Learned From This Neighbor:
     Network             Next Hop        Metric     LocPrf     RtPrf   Path
*    10.1.0.0/24         10.0.0.1                          20      65001 ?
*    10.2.0.0/24         10.0.0.1                          20      65001 65009 ?
"""

_IP_ROUTE = """
    Dest               Gw              Interface          Owner       Pri Metric
*>  0.0.0.0/0          10.0.0.1        smartgroup1        BGP         200 1
*>  10.1.0.0/24        10.0.0.2        gei-0/0/0/1        connected   0   0
"""

_IPV6_ROUTE = """
  Dest                                      Protocol     Pri Metric Flag
  Nexthop,Interface
  2001:db8::/32                             B            200 0
    2001:db8:1::1,smartgroup1
  fc00:1::/64                               C            0   0
    ::,gei-0/0/0/1.10
"""

_L2VPN_PW = """
PWName   PeerIP          FEC    PWType      State Llabel  Rlabel  VPNOwner
pw1      10.0.0.1        128    Ethernet  H UP    100     200     L:VPN_A
pw2      10.0.0.2        128    Ethernet  S DOWN  -       -       L:VPN_B
pw3      10.0.0.3        128    Ethernet    UP    101     201     W:100
"""


class ZteExtendedParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        reload_profiles()

    def test_vrf_list_protocols(self) -> None:
        rows = normalize_vrf_list(
            raw_text=_VRF_SAMPLE,
            vendor="zte",
            device_type="zte_zxros",
            command="show ip vrf",
        )
        by = {r["vrf_name"]: r for r in rows}
        self.assertIn("CUST_A", by)
        self.assertEqual(by["CUST_A"]["protocols"], "ipv4")
        self.assertIn("ipv6", by["CUST_B"]["protocols"])
        self.assertEqual(by["CUST_B"]["vrf_id"], "2")

    def test_bgp_peer_ipv4_and_ipv6_wrap(self) -> None:
        v4 = normalize_bgp_peer(
            raw_text=_BGP_V4_SUMMARY, command="show bgp vpnv4 unicast summary"
        )
        self.assertEqual(len(v4), 2)
        self.assertEqual(v4[0]["afi"], "vpnv4")
        self.assertEqual(v4[0]["state"], "Established")
        self.assertEqual(v4[0]["pfx_rcd"], "5")
        self.assertEqual(v4[1]["state"], "Connect")

        v6 = normalize_bgp_peer(
            raw_text=_BGP_V6_SUMMARY, command="show bgp vpnv6 unicast summary"
        )
        self.assertEqual(len(v6), 2)
        self.assertTrue(all(r["afi"] == "vpnv6" for r in v6))
        self.assertEqual(v6[0]["neighbor"].upper(), "FC00:1::1")

        vrf = normalize_bgp_peer(
            raw_text=_BGP_V4_SUMMARY,
            command="show bgp vpnv4 unicast vrf CUST_A summary",
            params={"vrf": "CUST_A"},
        )
        self.assertTrue(all(r["vrf"] == "CUST_A" for r in vrf))

    def test_bgp_summary_fsm(self) -> None:
        rows = apply_rule(
            platform="zte_zxros",
            rule_key="zte_zxros_show_bgp_summary",
            text=_BGP_V4_SUMMARY,
            command="show bgp vpnv4 unicast summary",
        )
        self.assertGreaterEqual(len(rows), 2)

    def test_ospf_vrrp_optical(self) -> None:
        ospf = normalize_ospf_neighbor(raw_text=_OSPF_SAMPLE, command="show ip ospf neighbor")
        self.assertEqual(len(ospf), 3)
        procs = {r["process_id"] for r in ospf}
        self.assertEqual(procs, {"1", "20"})

        vrrp = normalize_vrrp(raw_text=_VRRP_SAMPLE, command="show vrrp ipv4 brief")
        self.assertEqual(len(vrrp), 3)
        self.assertTrue(all(r["af"] == "ipv4" for r in vrrp))
        states = {r["state"] for r in vrrp}
        self.assertIn("Master", states)

        opt = normalize_optical_brief(raw_text=_OPTICAL_SAMPLE, command="show opticalinfo brief")
        self.assertEqual(len(opt), 2)
        self.assertEqual(opt[0]["status"], "Normal")

    def test_bgp_route_and_aux_render(self) -> None:
        routes = normalize_bgp_route(
            raw_text=_BGP_ROUTE_IN,
            command="show bgp vpnv4 unicast vrf CUST_A neighbor in 10.0.0.1",
            params={"vrf": "CUST_A", "neighbor": "10.0.0.1", "direction": "in"},
        )
        self.assertEqual(len(routes), 2)
        self.assertEqual(routes[0]["network"], "10.1.0.0/24")
        self.assertEqual(routes[0]["direction"], "in")

        peers = normalize_bgp_peer(
            raw_text=_BGP_V4_SUMMARY, command="show bgp vpnv4 unicast vrf CUST_A summary"
        )
        from netx_api.biz_state.enrich import EnrichJoin

        apply_enrich_joins(
            routes,
            {"bgp_summary": peers},
            [EnrichJoin(from_aux="bgp_summary", on="neighbor", take=("as_num", "state", "pfx_rcd"))],
        )
        self.assertEqual(routes[0]["as_num"], "65001")
        self.assertEqual(routes[0]["state"], "Established")

        ra = resolve_aux_command(
            AuxCommand(key="bgp_summary", profile_id="zte.bgp_vpnv4_vrf_summary"),
            params={"vrf": "CUST_A"},
        )
        self.assertEqual(ra.command, "show bgp vpnv4 unicast vrf CUST_A summary")

    def test_ip_ipv6_route_and_pw(self) -> None:
        ip = normalize_ip_route(
            raw_text=_IP_ROUTE,
            command="show ip forwarding route vrf CUST_A",
            params={"vrf": "CUST_A"},
        )
        self.assertEqual(len(ip), 2)
        self.assertEqual(ip[0]["dest"], "0.0.0.0/0")
        self.assertEqual(ip[0]["vrf"], "CUST_A")

        v6 = normalize_ipv6_route(
            raw_text=_IPV6_ROUTE,
            command="show ipv6 forwarding route vrf CUST_B",
            params={"vrf": "CUST_B"},
        )
        self.assertGreaterEqual(len(v6), 1)
        self.assertTrue(any(r["dest"].startswith("2001:db8") for r in v6))

        pw = normalize_l2vpn_pw(raw_text=_L2VPN_PW, command="show l2vpn forwardinfo")
        self.assertEqual(len(pw), 3)
        self.assertEqual(pw[0]["state"].upper(), "UP")
        self.assertEqual(pw[1]["state"].upper(), "DOWN")

    def test_profiles_and_expand(self) -> None:
        for mid in (
            "ospf_neighbor",
            "vrrp",
            "optical_brief",
            "bgp_route",
            "ip_route",
            "ipv6_route",
            "l2vpn_pw",
            "l2vpn_mac",
            "evpn_mac",
            "interface_detail",
        ):
            self.assertIn(mid, metric_field_map())

        self.assertIsNotNone(get_profile("zte.bgp_ipv6_summary"))
        self.assertIsNotNone(get_profile("zte.bgp_vpnv4_vrf_summary"))
        self.assertIsNotNone(get_profile("zte.ospf_neighbor"))
        self.assertIsNotNone(get_profile("zte.interface_detail"))
        self.assertIsNotNone(get_profile("zte.bgp_vpnv6_neighbor_in"))
        self.assertIsNotNone(get_profile("zte.bgp_vpnv6_neighbor_out"))

        p = get_profile("zte.bgp_vpnv4_vrf_neighbor_in")
        assert p is not None
        pairs = expand_from_bindings(
            profile=p,
            bindings=[{"vrf": "CUST_A", "neighbor": "10.0.0.1"}],
        )
        self.assertEqual(len(pairs), 1)
        cmd, params = pairs[0]
        self.assertIn("vrf CUST_A", cmd)
        self.assertIn("10.0.0.1", cmd)
        hit = match_command(vendor_key="zte", command=cmd)
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.params.get("vrf"), "CUST_A")
        self.assertEqual(hit.params.get("neighbor"), "10.0.0.1")

    def test_interface_detail_and_vpnv6_neighbor(self) -> None:
        from netx_api.biz_state.parsers.zte import normalize_interface_detail

        sample = """
gei-0/0/0/1 is up, ifindex: 100
  Description: uplink-a
  The port is optical
  Negotiation force
  BW 1 Gbit/s
  IP MTU 1500 bytes
  MTU 1600 bytes
  MPLS MTU 1550 bytes
  Rate period     : 120 s
   Input          : 100 bit/s              1 packet/s
   Output         : 200 bit/s              2 packet/s
  Peak rate:
   Input          : 0 bit/s              peak time          N/A
   Output         : 300 bit/s            peak time          N/A
  Intf utilization: input 1%             output 2%
gei-0/0/0/2 is administratively down, ifindex: 101
  Description: spare
  BW 1 Gbit/s
  IP MTU 8978 bytes
  MTU 9000 bytes
  IPv6 MTU 8978 bytes
  MPLS MTU 8978 bytes
  Rate period     : 120 s
   Input          : 0 bit/s              0 packet/s
   Output         : 0 bit/s              0 packet/s
  Intf utilization: input 0%             output 0%
"""
        rows = normalize_interface_detail(
            raw_text=sample,
            vendor="zte",
            device_type="zte_zxros",
            command="show interface",
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["port_status"], "up")
        self.assertEqual(rows[0]["input_bps"], "100")
        self.assertEqual(rows[0]["out_util"], "2")
        self.assertEqual(rows[0]["ip_mtu"], "1500")
        self.assertEqual(rows[0]["mtu"], "1600")
        self.assertEqual(rows[0]["mpls_mtu"], "1550")
        self.assertEqual(rows[0]["ipv6_mtu"], "")
        self.assertEqual(rows[1]["port_status"], "admin-down")
        self.assertEqual(rows[1]["ip_mtu"], "8978")
        self.assertEqual(rows[1]["mtu"], "9000")
        self.assertEqual(rows[1]["ipv6_mtu"], "8978")
        self.assertEqual(rows[1]["mpls_mtu"], "8978")

        # No IPv6 MTU on second iface — must not inherit from previous Filldown.
        leak_sample = """
gei-0/0/0/1 is up, ifindex: 1
  IP MTU 1500 bytes
  MTU 1600 bytes
  IPv6 MTU 8978 bytes
  MPLS MTU 1550 bytes
  Rate period     : 120 s
   Input          : 1 bit/s
   Output         : 2 bit/s
  Intf utilization: input 1%             output 2%
gei-0/0/0/2 is up, ifindex: 2
  IP MTU 1500 bytes
  MTU 1600 bytes
  MPLS MTU 1550 bytes
  Rate period     : 120 s
   Input          : 0 bit/s
   Output         : 0 bit/s
  Intf utilization: input 0%             output 0%
"""
        leak_rows = normalize_interface_detail(
            raw_text=leak_sample,
            vendor="zte",
            device_type="zte_zxros",
            command="show interface",
        )
        self.assertEqual(len(leak_rows), 2)
        self.assertEqual(leak_rows[0]["ipv6_mtu"], "8978")
        self.assertEqual(leak_rows[1]["ipv6_mtu"], "")
        self.assertEqual(leak_rows[1]["ip_mtu"], "1500")
        self.assertEqual(leak_rows[1]["mpls_mtu"], "1550")

        cmd = (
            "show interface | include ifindex|BW|The port is|MTU|Negotiation|"
            "Description|Current|Rate|Peak|Input|Output|utilization"
        )
        hit = match_command(vendor_key="zte", command=cmd)
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.profile.profile_id, "zte.interface_detail")
        field_names = {f.name for f in hit.profile.fields}
        self.assertTrue({"ip_mtu", "mtu", "mpls_mtu", "ipv6_mtu"} <= field_names)

        hit6 = match_command(
            vendor_key="zte",
            command="show bgp vpnv6 unicast neighbor in FC00:1::1 | one-line",
        )
        self.assertIsNotNone(hit6)
        assert hit6 is not None
        self.assertEqual(hit6.profile.profile_id, "zte.bgp_vpnv6_neighbor_in")
        self.assertEqual(hit6.params.get("neighbor"), "FC00:1::1")

        routes = normalize_bgp_route(
            raw_text=_BGP_ROUTE_IN,
            command="show bgp vpnv6 unicast neighbor in FC00:1::1",
            params={"neighbor": "FC00:1::1", "direction": "in", "afi": "vpnv6"},
        )
        self.assertGreaterEqual(len(routes), 1)
        self.assertEqual(routes[0]["afi"], "vpnv6")
        self.assertEqual(routes[0]["direction"], "in")


if __name__ == "__main__":
    unittest.main()
