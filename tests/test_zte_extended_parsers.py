"""Synthetic (desensitized) tests for extended ZTE biz_state parsers."""

from __future__ import annotations

import unittest
from pathlib import Path

from netx_api.biz_state.collect_session import resolve_aux_command
from netx_api.biz_state.command_match import expand_from_bindings, match_command
from netx_api.biz_state.enrich import apply_enrich_joins
from netx_api.biz_state.parsers.common.vrf_list import normalize_vrf_list
from netx_api.biz_state.parsers.zte import (
    normalize_bgp_peer,
    normalize_bgp_route,
    normalize_ip_route,
    normalize_ipv6_route,
    normalize_l2vpn_mac,
    normalize_l2vpn_pw,
    normalize_l2vpn_pw_detail,
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
cgei-0/1/0/1      100G-10km-QSFP28  1310nm      1.5/[-10.6,4.5]       0.9/[-4.3,5.5]        Normal    Normal
                                                2.1/[-10.6,4.5]       1.5/[-4.3,5.5]        Normal    Normal
                                                1.6/[-10.6,4.5]       2.1/[-4.3,5.5]        Normal    Normal
                                                0.9/[-10.6,4.5]       2.2/[-4.3,5.5]        Normal    Normal
                                                7.5                   7.7
gei-0/0/0/11      offline
"""

# 400G QSFP-DD inserts lane-count token "8X" between Type and Wavelength.
_OPTICAL_QSFP_DD_SAMPLE = """
Interface         Type              Wavelength  RxPower(dBm)          TxPower(dBm)          Status    Intensity(Rx)
cdgei-0/1/0/1     400G-10km-QSFP-DD 8X  1310nm  N/A/[-9.0,6.1]        0.9/[-2.7,6.1]        Unknown   Unknown
                                                N/A/[-9.0,6.1]        1.0/[-2.7,6.1]        Unknown   Unknown
                                                N/A/[-9.0,6.1]        1.2/[-2.7,6.1]        Unknown   Unknown
                                                N/A/[-9.0,6.1]        1.2/[-2.7,6.1]        Unknown   Unknown
                                                                      7.1
cdgei-0/1/0/3     400G-10km-QSFP-DD 8X  1310nm  1.3/[-9.0,5.1]        2.2/[-2.7,6.1]        Normal    Normal
                                                1.0/[-9.0,5.1]        2.1/[-2.7,6.1]        Normal    Normal
                                                1.0/[-9.0,5.1]        2.2/[-2.7,6.1]        Normal    Normal
                                                0.3/[-9.0,5.1]        1.7/[-2.7,6.1]        Normal    Normal
                                                6.9                   8.1
cdgei-0/1/0/5     400G-80km-QSFP-DD 8X  1547.715nm  -8.8/[-20.0,3.0]  -8.3/[-11.0,2.0]      Normal    Normal
                                                -8.8                  -8.3
cdgei-0/1/0/6     offline
cgei-0/1/0/2:1    400G-500m-QSFP-DD 8X  1310nm  N/A/[-5.9,5.0]        2.3/[-2.9,5.0]        Unknown   Unknown
                                                N/A/[-5.9,5.0]        2.4/[-2.9,5.0]        Unknown   Unknown
                                                N/A/[-5.9,5.0]        2.3/[-2.9,5.0]        Unknown   Unknown
                                                N/A/[-5.9,5.0]        2.2/[-2.9,5.0]        Unknown   Unknown
                                                                      8.3
xgei-0/2/0/1      10G-10km-SFP+     1310nm      -2.4/[-14.4,0.5]      -1.8/[-8.2,1.5]       Normal    Normal
"""

_BGP_ROUTE_IN = """
Routes Learned From This Neighbor:
     Network             Next Hop        Metric     LocPrf     RtPrf   Path
*    10.1.0.0/24         10.0.0.1                          20      65001 ?
*    10.2.0.0/24         10.0.0.1                          20      65001 65009 ?
"""

_BGP_ROUTE_EMPTY_DEST = """
Current AS: 100. Other AS: 24208, 3.45420

Routes Learned From This Neighbor:
Status codes: * valid, < last valid, i - internal, s - stale
Origin codes: i - IGP, e - EGP, ? - incomplete
Local router ID 1.11.1.1, Local AS 100, Local port 0
Remote router ID 0.0.0.0, Remote AS 100, Remote port 0
Total number of routes: 0
  Valid routes        : 0
  Invalid routes      : 0
     Dest                Next Hop        Metric     LocPrf     InTag   Path
"""

_BGP_ROUTE_EMPTY_OUT = """
Current AS: 100. Other AS: 24208, 3.45420

Routes Sent To This Neighbor:
Origin codes: i - IGP, e - EGP, ? - incomplete
Total number of routes: 0
Network          Next Hop        From            Metric LocPrf Tag     Path
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

_L2VPN_MAC = """
Total MAC Entries:  502

Headers: Src--Source filter, Dst--Destination filter
         E--Exter-VLAN ID  I--Inter-VLAN ID

MAC            VPN            VLAN Outgoing Information             Attribute
-------------- -------------- ---- -------------------------------- ------------
0000.9999.0002 EVPNvpls501    0    SID fc00:a000:1005::567b:0, 2400:9800:7000:1005::  Static
0011.2232.42e3 mxy-bvi-ag4-ag3  0  SID fc00:a000:1002::3469:0, 2400:9800:7000:1002::  Dynamic
0099.2232.7788 css-irb-ag3-1  0    ESI:0:109501000010000001         Static
004b.0100.0001 css-irb-ag3-1  0    smartgroup11.31, E:31            Dynamic
0011.2232.42e4 mxy-kompella-2 0    auto_pw3, 24.11.100.5            Dynamic
0011.2232.42e4 mxy-kompella-1 0    PW1,1.1.1.1                      Dynamic
0000.2200.0001 EVPNvpls501    0    smartgroup2345.2501, E:2501      Dynamic
"""

_L2VPN_PW_DETAIL = """
Service type and instance name:[VPLS qualified demo-vpls-1]
  Peer IP address         : 10.0.0.6                  VCID         : 19001
  Connection mode         : HUB                       VCID Extend  : 0
  Signaling protocol      : LDP                       VC type      : VLAN
  Last status change time : 09:46:46                  Create time  : 3d 07:08:42
  MPLS VC local label     : 165840                    Remote label : 167978
  PW name                 : pw19001                   Control Word : ENABLE
  Activation status       : ENABLE
  Band Width              : 0 kbps
  Tunnel destination      : 10.0.0.6
  Related interface name  : -
  FRR type                : NULL
  VC status               : UP
  Remote status           : ALLOK
  MC selection            : -
  VCCV CC type            : CWORD
  VCCV CV type            : LSP

Service type and instance name:[VPLS demo-vpls-2]
  Peer IP address         : 10.0.0.6                  VCID         : 19011
  Connection mode         : HUB                       VCID Extend  : 0
  Signaling protocol      : LDP                       VC type      : Ethernet
  Last status change time : 09:46:48                  Create time  : 1d 01:00:00
  MPLS VC local label     : 165841                    Remote label : 167981
  PW name                 : pw19011                   Control Word : DISABLE
  Activation status       : ENABLE
  Band Width              : 0 kbps
  Tunnel destination      : 10.0.0.6
  Related interface name  : -
  FRR type                : NULL
  VC status               : DOWN
  Remote status           : PWSA
  MC selection            : -
  VCCV CC type            : ALERT_LABEL
  VCCV CV type            : LSP|BFD_BASIC_HEAD
"""

_L2VPN_PW_DETAIL_SAMPLE = (
    Path(__file__).resolve().parents[2] / "test" / "show-zte" / "show-l2vpn-forwarding-detail"
)


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

        # Real wrapped vpnv6 summary + IPv6 neighbor in/out routes
        sample_dir = Path(__file__).resolve().parents[2] / "test" / "show-zte"
        v6_sum = (sample_dir / "show-bgp-vpnv6").read_text(encoding="utf-8", errors="replace")
        v6_real = normalize_bgp_peer(
            raw_text=v6_sum, command="show bgp vpnv6 unicast summary | one-line"
        )
        self.assertEqual(len(v6_real), 2)
        self.assertTrue(all(":" in r["neighbor"] for r in v6_real))
        self.assertTrue(all(r["afi"] == "vpnv6" for r in v6_real))

        v6_in = (sample_dir / "show-bgp-vpnv6-neighbor-router-in-vrf").read_text(
            encoding="utf-8", errors="replace"
        )
        routes_in = normalize_bgp_route(
            raw_text=v6_in,
            command="show bgp vpnv6 unicast vrf vpn.giims neighbor in 2407::45:0:0:5:1",
            params={
                "vrf": "vpn.giims",
                "neighbor": "2407::45:0:0:5:1",
                "direction": "in",
                "afi": "vpnv6",
            },
        )
        self.assertEqual(len(routes_in), 9)
        self.assertTrue(all(r["network"].startswith("2407::") for r in routes_in))
        self.assertTrue(all(r["next_hop"].startswith("2407::") for r in routes_in))
        self.assertFalse(any(r["network"].startswith("09:") for r in routes_in))

        v6_out = (sample_dir / "show-bgp-vpnv6-neighbor-router-out-vrf").read_text(
            encoding="utf-8", errors="replace"
        )
        routes_out = normalize_bgp_route(
            raw_text=v6_out,
            command="show bgp vpnv6 unicast vrf vpn.giims neighbor out 2407::45:0:0:5:1",
            params={
                "vrf": "vpn.giims",
                "neighbor": "2407::45:0:0:5:1",
                "direction": "out",
            },
        )
        self.assertEqual(len(routes_out), 1)
        self.assertEqual(routes_out[0]["network"], "2407::45:0:0:5:0/127")
        self.assertIn("4761", routes_out[0]["path"])

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

        opt = normalize_optical_brief(
            raw_text=_OPTICAL_SAMPLE,
            vendor="zte",
            device_type="zte_zxros",
            command="show opticalinfo brief",
        )
        self.assertEqual(len(opt), 3)
        by = {r["interface"]: r for r in opt}
        self.assertEqual(by["gei-0/0/0/1"]["rx_power"], "-6.0")
        self.assertEqual(by["gei-0/0/0/1"]["rx_threshold"], "[-20.0,-3.0]")
        self.assertEqual(by["gei-0/0/0/1"]["tx_power"], "-5.9")
        self.assertEqual(by["gei-0/0/0/1"]["tx_threshold"], "[-9.0,-3.0]")
        self.assertEqual(by["gei-0/0/0/1"]["status"], "Normal")
        self.assertEqual(by["cgei-0/1/0/1"]["rx_power"], "1.5,2.1,1.6,0.9")
        self.assertEqual(by["cgei-0/1/0/1"]["tx_power"], "0.9,1.5,2.1,2.2")
        self.assertEqual(by["cgei-0/1/0/1"]["rx_threshold"], "[-10.6,4.5]")
        self.assertEqual(by["cgei-0/1/0/1"]["tx_threshold"], "[-4.3,5.5]")
        self.assertEqual(by["gei-0/0/0/11"]["optic_type"], "offline")

    def test_optical_qsfp_dd_lane_count_8x(self) -> None:
        """400G QSFP-DD prints '8X' between Type and Wavelength — must not shift Rx/Tx."""
        opt = normalize_optical_brief(
            raw_text=_OPTICAL_QSFP_DD_SAMPLE,
            vendor="zte",
            device_type="zte_zxros",
            command="show opticalinfo brief | one-line",
        )
        by = {r["interface"]: r for r in opt}
        self.assertIn("cdgei-0/1/0/1", by)
        self.assertEqual(by["cdgei-0/1/0/1"]["rx_power"], "N/A,N/A,N/A,N/A")
        self.assertEqual(by["cdgei-0/1/0/1"]["tx_power"], "0.9,1.0,1.2,1.2")
        self.assertTrue(str(by["cdgei-0/1/0/1"]["wavelength"]).startswith("8X"))
        self.assertEqual(by["cdgei-0/1/0/3"]["rx_power"], "1.3,1.0,1.0,0.3")
        self.assertEqual(by["cdgei-0/1/0/3"]["tx_power"], "2.2,2.1,2.2,1.7")
        # Summary-only continuation (bare numbers / tx-only) must not add lanes
        self.assertEqual(by["cdgei-0/1/0/5"]["rx_power"], "-8.8")
        self.assertEqual(by["cdgei-0/1/0/5"]["tx_power"], "-8.3")
        self.assertEqual(by["cdgei-0/1/0/6"]["status"], "offline")
        self.assertEqual(by["cgei-0/1/0/2:1"]["tx_power"], "2.3,2.4,2.3,2.2")
        self.assertEqual(by["xgei-0/2/0/1"]["rx_power"], "-2.4")

    def test_l2vpn_mac_outgoing_split(self) -> None:
        fsm_rows = apply_rule(
            platform="zte_zxros",
            rule_key="zte_zxros_show_mac_l2vpn",
            text=_L2VPN_MAC,
            command="show mac l2vpn",
        )
        self.assertEqual(len(fsm_rows), 7)
        self.assertTrue(all("----" not in r.get("MAC", "") for r in fsm_rows))

        rows = normalize_l2vpn_mac(
            raw_text=_L2VPN_MAC,
            vendor="zte",
            device_type="zte_zxros",
            command="show mac l2vpn | one-line",
        )
        self.assertEqual(len(rows), 7)
        by = {(r["mac"], r["vpn"]): r for r in rows}

        sid = by[("0000.9999.0002", "EVPNvpls501")]
        self.assertEqual(sid["vpn_sid"], "fc00:a000:1005::567b:0")
        self.assertEqual(sid["neighbor_sid"], "2400:9800:7000:1005::")
        self.assertEqual(sid["pw"], "")
        self.assertEqual(sid["ac_port"], "")
        self.assertTrue(sid["outgoing"].startswith("SID "))

        ac = by[("004b.0100.0001", "css-irb-ag3-1")]
        self.assertEqual(ac["ac_port"], "smartgroup11.31")
        self.assertEqual(ac["exter_vlan"], "31")
        self.assertEqual(ac["pw"], "")

        pw = by[("0011.2232.42e4", "mxy-kompella-2")]
        self.assertEqual(pw["pw"], "auto_pw3")
        self.assertEqual(pw["neighbor"], "24.11.100.5")
        self.assertEqual(pw["ac_port"], "")

        pw2 = by[("0011.2232.42e4", "mxy-kompella-1")]
        self.assertEqual(pw2["pw"], "PW1")
        self.assertEqual(pw2["neighbor"], "1.1.1.1")

        esi = by[("0099.2232.7788", "css-irb-ag3-1")]
        self.assertEqual(esi["outgoing"], "ESI:0:109501000010000001")
        self.assertEqual(esi["pw"], "")
        self.assertEqual(esi["vpn_sid"], "")

        fields = {f.name for f in metric_field_map()["l2vpn_mac"]}
        self.assertTrue({"pw", "neighbor", "ac_port", "exter_vlan", "vpn_sid", "neighbor_sid"} <= fields)

    def test_bgp_route_and_aux_render(self) -> None:
        routes = normalize_bgp_route(
            raw_text=_BGP_ROUTE_IN,
            command="show bgp vpnv4 unicast vrf CUST_A neighbor in 10.0.0.1",
            params={"vrf": "CUST_A", "neighbor": "10.0.0.1", "direction": "in"},
        )
        self.assertEqual(len(routes), 2)
        self.assertEqual(routes[0]["network"], "10.1.0.0/24")
        self.assertEqual(routes[0]["direction"], "in")

        from netx_api.biz_state.enrich import EnrichJoin
        from netx_api.biz_state.parsers.zte.config_bgp_peer import normalize_config_bgp_peer

        intent = normalize_config_bgp_peer(
            raw_text="""
!<bgp>
  neighbor 10.0.0.1 remote-as 65001
  address-family ipv4 vrf CUST_A
    neighbor 10.0.0.1 activate
    neighbor 10.0.0.1 route-map RM_IN in
  $
$
""",
            command="show running-config bgp",
        )
        apply_enrich_joins(
            routes,
            {"config_bgp_peer": intent},
            [
                EnrichJoin(
                    from_aux="config_bgp_peer",
                    left_on="vrf,neighbor",
                    right_on="vrf,neighbor",
                    take=("remote_as", "activate", "route_map_in", "route_map_out"),
                )
            ],
        )
        self.assertEqual(routes[0]["remote_as"], "65001")
        self.assertEqual(routes[0]["activate"], "enable")
        self.assertEqual(routes[0]["route_map_in"], "RM_IN")

        # Same neighbor in two VRFs must not collide
        routes_b = [
            {"vrf": "CUST_A", "neighbor": "10.0.0.1", "afi": "vpnv4"},
            {"vrf": "CUST_B", "neighbor": "10.0.0.1", "afi": "vpnv4"},
        ]
        intent2 = normalize_config_bgp_peer(
            raw_text="""
!<bgp>
  neighbor 10.0.0.1 remote-as 65001
  address-family ipv4 vrf CUST_A
    neighbor 10.0.0.1 activate
    neighbor 10.0.0.1 route-map RM_A in
  $
  address-family ipv4 vrf CUST_B
    neighbor 10.0.0.1 activate
    neighbor 10.0.0.1 route-map RM_B in
  $
$
""",
            command="show running-config bgp",
        )
        apply_enrich_joins(
            routes_b,
            {"config_bgp_peer": intent2},
            [
                EnrichJoin(
                    from_aux="config_bgp_peer",
                    left_on="vrf,neighbor",
                    right_on="vrf,neighbor",
                    take=("route_map_in",),
                )
            ],
        )
        self.assertEqual(routes_b[0]["route_map_in"], "RM_A")
        self.assertEqual(routes_b[1]["route_map_in"], "RM_B")

        # Global VPNv4 must not pick ipv4-unicast AF row for same neighbor
        global_routes = [
            {"afi": "vpnv4", "vrf": "", "neighbor": "10.0.0.1", "network": "1.0.0.0/24"},
        ]
        intent3 = normalize_config_bgp_peer(
            raw_text="""
!<bgp>
  neighbor 10.0.0.1 remote-as 65001
  address-family ipv4
    neighbor 10.0.0.1 activate
    neighbor 10.0.0.1 route-map RM_IPV4 in
  $
  address-family vpnv4
    neighbor 10.0.0.1 activate
    neighbor 10.0.0.1 route-map RM_VPNV4 in
  $
$
""",
            command="show running-config bgp",
        )
        apply_enrich_joins(
            global_routes,
            {"config_bgp_peer": intent3},
            [
                EnrichJoin(
                    from_aux="config_bgp_peer",
                    left_on="afi,neighbor",
                    right_on="afi,neighbor",
                    take=("route_map_in",),
                )
            ],
        )
        self.assertEqual(global_routes[0]["route_map_in"], "RM_VPNV4")

        ra = resolve_aux_command(
            AuxCommand(key="config_bgp_peer", profile_id="zte.config_bgp_peer"),
            params={"vrf": "CUST_A", "neighbor": "10.0.0.1"},
        )
        self.assertEqual(ra.command, "show running-config bgp | one-line")
        # Aux has no placeholders — params must not leak into the CLI
        self.assertNotIn("<", ra.command)
        self.assertNotIn("CUST_A", ra.command)

    def test_bgp_route_empty_total_skips_header(self) -> None:
        empty_in = normalize_bgp_route(
            raw_text=_BGP_ROUTE_EMPTY_DEST,
            command="show bgp vpnv4 unicast neighbor in 10.22.9.2 | one-line",
            params={"neighbor": "10.22.9.2", "direction": "in"},
        )
        self.assertEqual(empty_in, [])

        empty_out = normalize_bgp_route(
            raw_text=_BGP_ROUTE_EMPTY_OUT,
            command="show bgp vpnv4 unicast neighbor out 10.22.9.2 | one-line",
        )
        self.assertEqual(empty_out, [])

        hit_in = match_command(
            vendor_key="zte",
            command="show bgp vpnv4 unicast neighbor in 10.22.9.2 | one-line",
        )
        self.assertIsNotNone(hit_in)
        assert hit_in is not None
        self.assertEqual(hit_in.profile.profile_id, "zte.bgp_vpnv4_neighbor_in")
        self.assertEqual(hit_in.params.get("direction"), "in")
        self.assertEqual(hit_in.params.get("neighbor"), "10.22.9.2")

        hit_out = match_command(
            vendor_key="zte",
            command="show bgp vpnv4 unicast neighbor out 10.22.9.2 | one-line",
        )
        self.assertIsNotNone(hit_out)
        assert hit_out is not None
        self.assertEqual(hit_out.profile.profile_id, "zte.bgp_vpnv4_neighbor_out")
        self.assertEqual(hit_out.params.get("direction"), "out")
        self.assertEqual(hit_out.params.get("neighbor"), "10.22.9.2")

        # Direction detected from command when params omit it
        parsed = normalize_bgp_route(
            raw_text=_BGP_ROUTE_IN,
            command="show bgp vpnv4 unicast neighbor out 10.0.0.1",
        )
        self.assertEqual(parsed[0]["direction"], "out")
        self.assertEqual(parsed[0]["neighbor"], "10.0.0.1")

    def test_ip_ipv6_route_and_pw(self) -> None:
        ip = normalize_ip_route(
            raw_text=_IP_ROUTE,
            command="show ip forwarding route vrf CUST_A",
            params={"vrf": "CUST_A"},
        )
        self.assertEqual(len(ip), 2)
        self.assertEqual(ip[0]["dest"], "0.0.0.0/0")
        self.assertEqual(ip[0]["vrf"], "CUST_A")
        self.assertEqual(ip[0]["address_families"], "ipv4")

        v6 = normalize_ipv6_route(
            raw_text=_IPV6_ROUTE,
            command="show ipv6 forwarding route vrf CUST_B",
            params={"vrf": "CUST_B"},
        )
        self.assertGreaterEqual(len(v6), 1)
        self.assertTrue(any(r["dest"].startswith("2001:db8") for r in v6))
        self.assertTrue(all(r.get("address_families") == "ipv6" for r in v6))

        pw = normalize_l2vpn_pw(raw_text=_L2VPN_PW, command="show l2vpn forwardinfo")
        self.assertEqual(len(pw), 3)
        self.assertEqual(pw[0]["state"].upper(), "UP")
        self.assertEqual(pw[1]["state"].upper(), "DOWN")

    def test_l2vpn_pw_detail(self) -> None:
        rows = normalize_l2vpn_pw_detail(
            raw_text=_L2VPN_PW_DETAIL,
            vendor="zte",
            device_type="zte_zxros",
            command="show l2vpn forwardinfo detail",
        )
        self.assertEqual(len(rows), 2)
        by = {r["pw_name"]: r for r in rows}
        self.assertEqual(by["pw19001"]["vc_status"].upper(), "UP")
        self.assertEqual(by["pw19001"]["remote_status"], "ALLOK")
        self.assertEqual(by["pw19001"]["vcid"], "19001")
        self.assertEqual(by["pw19001"]["peer"], "10.0.0.6")
        self.assertEqual(by["pw19001"]["service_instance_type"], "VPLS")
        self.assertEqual(by["pw19001"]["service_instance"], "qualified demo-vpls-1")
        self.assertEqual(by["pw19011"]["service_instance_type"], "VPLS")
        self.assertEqual(by["pw19011"]["service_instance"], "demo-vpls-2")
        self.assertEqual(by["pw19011"]["vc_status"].upper(), "DOWN")
        self.assertEqual(by["pw19011"]["vccv_cv"], "LSP|BFD_BASIC_HEAD")

        fsm_rows = apply_rule(
            platform="zte_zxros",
            rule_key="zte_zxros_show_l2vpn_forwardinfo_detail",
            text=_L2VPN_PW_DETAIL,
        )
        self.assertGreaterEqual(len(fsm_rows), 2)

        brief = match_command(
            vendor_key="zte", command="show l2vpn forwardinfo detail | exclude Tunnel"
        )
        self.assertIsNotNone(brief)
        assert brief is not None
        self.assertEqual(brief.profile.profile_id, "zte.l2vpn_pw_detail")
        no_detail = match_command(vendor_key="zte", command="show l2vpn forwardinfo | one-line")
        self.assertIsNotNone(no_detail)
        assert no_detail is not None
        self.assertEqual(no_detail.profile.profile_id, "zte.l2vpn_pw")

    @unittest.skipUnless(
        _L2VPN_PW_DETAIL_SAMPLE.is_file(),
        "test/show-zte/show-l2vpn-forwarding-detail not present",
    )
    def test_l2vpn_pw_detail_real_sample(self) -> None:
        raw = _L2VPN_PW_DETAIL_SAMPLE.read_text(encoding="utf-8", errors="replace")
        rows = normalize_l2vpn_pw_detail(
            raw_text=raw,
            vendor="zte",
            device_type="zte_zxros",
            command="show l2vpn forwardinfo detail",
        )
        self.assertEqual(len(rows), 6)
        self.assertTrue(all(r["vc_status"].upper() == "UP" for r in rows))
        self.assertTrue(all(r["service_instance_type"] == "VPLS" for r in rows))
        self.assertEqual(rows[0]["pw_name"], "pw19001")
        self.assertEqual(rows[0]["service_instance"], "qualified mxy-martini-vpls-1")
        self.assertEqual(rows[2]["service_instance"], "VPLS_4202")
        self.assertEqual(rows[-1]["pw_name"], "pw1230002")

    def test_profiles_and_expand(self) -> None:
        for mid in (
            "ospf_neighbor",
            "vrrp",
            "optical_brief",
            "bgp_route",
            "ip_route",
            "ipv6_route",
            "l2vpn_pw",
            "l2vpn_pw_detail",
            "l2vpn_mac",
            "evpn_mac",
            "interface_detail",
        ):
            self.assertIn(mid, metric_field_map())

        self.assertIsNotNone(get_profile("zte.bgp_ipv6_summary"))
        self.assertIsNotNone(get_profile("zte.bgp_vpnv4_vrf_summary"))
        self.assertIsNotNone(get_profile("zte.ospf_neighbor"))
        self.assertIsNotNone(get_profile("zte.interface_detail"))
        self.assertIsNotNone(get_profile("zte.l2vpn_pw_detail"))
        self.assertIsNotNone(get_profile("zte.bgp_vpnv6_neighbor_in"))
        self.assertIsNotNone(get_profile("zte.bgp_vpnv6_neighbor_out"))

        p = get_profile("zte.bgp_vpnv4_vrf_neighbor_in")
        assert p is not None
        self.assertEqual([a.key for a in p.aux_commands], ["config_bgp_peer"])
        self.assertEqual(p.aux_commands[0].profile_id, "zte.config_bgp_peer")
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

        # BGP VRF summary / Forwarding VRF: required bind + config_vrf aux
        from netx_api.biz_state.command_match import expand_bindings_from_discover_records
        from netx_api.biz_state.collect_session import resolve_aux_command

        for pid in (
            "zte.bgp_vpnv4_vrf_summary",
            "zte.bgp_vpnv6_vrf_summary",
            "zte.ip_route_vrf",
            "zte.ipv6_route_vrf",
        ):
            prof = get_profile(pid)
            assert prof is not None
            self.assertTrue(prof.placeholders)
            self.assertTrue(prof.placeholders[0].required)
            self.assertEqual(prof.placeholders[0].discover_profile_id, "zte.config_vrf")
            self.assertTrue(any(a.profile_id == "zte.config_vrf" for a in prof.aux_commands))

        v4 = get_profile("zte.bgp_vpnv4_vrf_summary")
        assert v4 is not None
        self.assertEqual([a.key for a in v4.aux_commands], ["config_vrf"])
        with self.assertRaises(ValueError):
            expand_from_bindings(profile=v4, bindings=[])
        bound = expand_from_bindings(profile=v4, bindings=[{"vrf": "CUST_A"}])
        self.assertEqual(bound[0][0], "show bgp vpnv4 unicast vrf CUST_A summary | one-line")
        ra_cfg = resolve_aux_command(
            next(a for a in v4.aux_commands if a.key == "config_vrf"),
            params={"vrf": "CUST_A"},
        )
        self.assertEqual(ra_cfg.command, "show running-config vrf | one-line")

        v6 = get_profile("zte.bgp_vpnv6_vrf_summary")
        assert v6 is not None
        self.assertEqual([a.key for a in v6.aux_commands], ["config_vrf"])

        records = [
            {"vrf_name": "CUST_A", "address_families": "ipv4,ipv6", "rd": "100:1"},
            {"vrf_name": "CUST_B", "address_families": "ipv4", "rd": "100:2"},
            {"vrf_name": "CUST_C", "address_families": "ipv6", "rd": "100:3"},
        ]
        ipv4_pairs = expand_bindings_from_discover_records(profile=v4, records=records)
        self.assertEqual({p[1]["vrf"] for p in ipv4_pairs}, {"CUST_A", "CUST_B"})
        ipv6_pairs = expand_bindings_from_discover_records(profile=v6, records=records)
        self.assertEqual({p[1]["vrf"] for p in ipv6_pairs}, {"CUST_A", "CUST_C"})

        # BGP neighbor in/out: discover from config_bgp_peer with AF filters
        from netx_api.biz_state.command_match import (
            filter_discover_records,
            normalize_binding_dicts,
            shared_discover_placeholders,
        )

        glob_v4 = get_profile("zte.bgp_vpnv4_neighbor_in")
        assert glob_v4 is not None
        self.assertEqual(glob_v4.placeholders[0].discover_profile_id, "zte.config_bgp_peer")
        self.assertEqual(glob_v4.placeholders[0].discover_filter_contains, "vpnv4")
        self.assertEqual(glob_v4.placeholders[0].bind_mode, "discover_select")

        glob_v6 = get_profile("zte.bgp_vpnv6_neighbor_in")
        assert glob_v6 is not None
        self.assertEqual(glob_v6.placeholders[0].discover_filter_contains, "vpnv6")

        peer_recs = [
            {"afi": "vpnv4", "vrf": "", "neighbor": "10.0.0.1", "remote_as": "65001"},
            {"afi": "vpnv4", "vrf": "", "neighbor": "", "peer_group": "CORE_RR", "remote_as": "65009"},
            {"afi": "vpnv4", "vrf": "", "neighbor": "10.0.0.9", "peer_group": "CORE_RR", "remote_as": "65019"},
            {"afi": "vpnv6", "vrf": "", "neighbor": "FC00::1", "remote_as": "65002"},
            {"afi": "ipv4", "vrf": "CUST_A", "neighbor": "10.0.0.2", "remote_as": "65003"},
            {"afi": "ipv4", "vrf": "", "neighbor": "10.0.0.9", "remote_as": "65004"},
            {"afi": "ipv6", "vrf": "CUST_B", "neighbor": "FC00::2", "remote_as": "65005"},
        ]
        self.assertEqual(
            filter_discover_records(peer_recs, glob_v4.placeholders[0]),
            ["10.0.0.1", "10.0.0.9"],
        )
        self.assertEqual(
            filter_discover_records(peer_recs, glob_v6.placeholders[0]),
            ["FC00::1"],
        )
        # afi filter is exact: ipv4 must not match vpnv4
        vrf_nei = get_profile("zte.bgp_vpnv4_vrf_neighbor_in")
        assert vrf_nei is not None
        ipv4_nei_ph = next(ph for ph in vrf_nei.placeholders if ph.name == "neighbor")
        self.assertEqual(ipv4_nei_ph.discover_filter_contains, "ipv4")
        self.assertEqual(
            filter_discover_records(peer_recs, ipv4_nei_ph),
            ["10.0.0.2"],
        )

        shared = shared_discover_placeholders(vrf_nei)
        self.assertEqual(len(shared), 2)
        self.assertTrue(all(ph.discover_profile_id == "zte.config_bgp_peer" for ph in shared))
        pair_cmds = expand_bindings_from_discover_records(profile=vrf_nei, records=peer_recs)
        self.assertEqual(len(pair_cmds), 1)
        self.assertEqual(pair_cmds[0][1], {"vrf": "CUST_A", "neighbor": "10.0.0.2"})
        self.assertIn("vrf CUST_A", pair_cmds[0][0])
        self.assertIn("10.0.0.2", pair_cmds[0][0])

        # Interleaved placeholder/value rows zip into combined bindings
        zipped = normalize_binding_dicts(
            [
                {"placeholder": "vrf", "value": "CUST_A"},
                {"placeholder": "neighbor", "value": "10.0.0.2"},
                {"placeholder": "vrf", "value": "CUST_B"},
                {"placeholder": "neighbor", "value": "10.0.0.3"},
            ],
            placeholders=vrf_nei.placeholders,
        )
        self.assertEqual(
            zipped,
            [
                {"vrf": "CUST_A", "neighbor": "10.0.0.2"},
                {"vrf": "CUST_B", "neighbor": "10.0.0.3"},
            ],
        )
        expanded = expand_from_bindings(profile=vrf_nei, bindings=zipped)
        self.assertEqual(len(expanded), 2)

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
        self.assertEqual(hit6.params.get("direction"), "in")

        routes = normalize_bgp_route(
            raw_text=_BGP_ROUTE_IN,
            command="show bgp vpnv6 unicast neighbor in FC00:1::1",
            params={"neighbor": "FC00:1::1", "direction": "in", "afi": "vpnv6"},
        )
        self.assertGreaterEqual(len(routes), 1)
        self.assertEqual(routes[0]["afi"], "vpnv6")
        self.assertEqual(routes[0]["direction"], "in")

        # IPv4 neighbor routes: same wrap join as vpnv6
        v4_wrap = normalize_bgp_route(
            raw_text="""
Routes Learned From This Neighbor:
     Network          Next Hop        Metric     LocPrf     RtPrf   Path
*    10.1.0.0/24
                      10.0.0.1
                                                            20      65001 ?
""",
            command="show bgp ipv4 unicast neighbor in 10.0.0.1",
            params={"neighbor": "10.0.0.1", "direction": "in", "afi": "ipv4"},
        )
        self.assertEqual(len(v4_wrap), 1)
        self.assertEqual(v4_wrap[0]["network"], "10.1.0.0/24")
        self.assertEqual(v4_wrap[0]["next_hop"], "10.0.0.1")
        self.assertEqual(v4_wrap[0]["afi"], "ipv4")


if __name__ == "__main__":
    unittest.main()
