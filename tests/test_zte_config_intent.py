"""Synthetic tests for ZTE config-intent parsers (no live secrets / host data)."""

from __future__ import annotations

import unittest

from netx_api.biz_state.command_match import match_command
from netx_api.biz_state.compare_service import _default_zte_config_sheets, sheet_key
from netx_api.biz_state.parsers.zte import (
    normalize_config_bgp_peer,
    normalize_config_interface,
    normalize_config_isis,
    normalize_config_l2vpn_pw,
    normalize_config_ospf,
    normalize_config_static_route,
    normalize_config_vrf,
)
from netx_api.biz_state.parsers.zte.config_bgp_peer import _is_ip_neighbor

from netx_api.biz_state.profiles import get_profile, metric_field_map, reload_profiles


_CFG_VRF = """
!<vrf>
ip vrf CUST_A
 rd 100:1
 description "Customer A"
 address-family ipv4
  route-target export 100:1
  route-target import 100:1
 $
 address-family ipv6
  route-target export 100:2
 $
$
ip vrf CUST_B
 rd 200:2
 address-family ipv4
  route-target import 200:2
 $
$
!</vrf>
"""

_CFG_IF = """
!<if-intf>
interface gei-0/0/0/1
 description uplink
 ip vrf forwarding CUST_A
 ip address 10.0.0.1 255.255.255.0
 ip address 10.0.0.2 255.255.255.0 secondary
 ipv6 address 2001:db8::1/64
 ipv6 address 2001:db8::2/64 secondary
 mtu 9000
$
interface gei-0/0/0/2
 shutdown
 ip address 10.0.1.1 255.255.255.0
$
!</if-intf>
"""

_CFG_BGP = """
!<bgp>
router bgp 65000
 neighbor 10.0.0.1 remote-as 65001
 neighbor 10.0.0.1 update-source loopback1
 neighbor 10.0.0.1 password cipher SKIPME
 neighbor 10.0.0.1 route-map RM_IN in
 neighbor 10.0.0.1 peer-group CORE_RR
 neighbor CORE_RR peer-group
 neighbor CORE_RR remote-as 65009
 neighbor FC00:1::1 remote-as 65002
 address-family vpnv4
  neighbor 10.0.0.1 activate
  neighbor 10.0.0.1 route-map RM_OUT out
  neighbor CORE_RR activate
 $
 address-family ipv4 vrf CUST_A
  neighbor 10.0.0.2 remote-as 65003
  neighbor 10.0.0.2 activate
 $
 address-family l2vpn evpn
  neighbor 10.0.0.1 activate
 $
$
!</bgp>
"""

_CFG_L2VPN = """
!<l2vpn>
vpws VPN_A
 description "pw site a"
 access-point gei-0/0/0/1.100
  pseudo-wire pw1
   neighbour 10.0.0.1 vcid 1001
   encapsulation mpls
  $
 $
$
vpls VPN_B
 access-point smartgroup1
  pseudo-wire pw2
   neighbour 10.0.0.2 vcid 2002
   encapsulation mpls
  $
 $
$
!</l2vpn>
"""

_CFG_STATIC_V4 = """
!<static>
ip route 0.0.0.0 0.0.0.0 10.0.0.1 name default_gw
ip route 10.9.9.0 255.255.255.0 null1
ip route 10.8.8.0 255.255.255.0 nexthop-vrf OTHER_VRF
ip route vrf CUST_A 10.1.1.0 255.255.255.0 bvi12.100 10.1.1.1
ip route vrf CUST_A 10.2.2.0 255.255.255.0 gei-0/0/0/1.10 10.2.2.1 bfd enable
ip route vrf CUST_B 10.3.3.0 255.255.255.0 null1 metric 255
ip route vrf CUST_A 10.4.4.0 255.255.255.0 smartgroup1.1 10.4.4.1 track LINK_A
ip route 10.5.5.0 255.255.255.0 xxvgei-0/2/0/1
ip route 10.6.6.0 255.255.255.0 te_tunnel36
ip route 10.7.7.0 255.255.255.0 10.7.7.1 xxvgei-0/2/0/2 name nh_then_if
!</static>
"""

_CFG_STATIC_V6 = """
!<ipv6-static-route>
ipv6 route ::/0 null1 track LINK_BFD
ipv6 route vrf CUST_A 2001:db8:1::/64 2001:db8:1::1
!</ipv6-static-route>
"""

_CFG_OSPF = """
!<ospfv2>
router ospf 3 vrf CUST_A
  router-id 1.1.1.1
  area 0.0.0.0
    interface smartgroup1.10
      cost 20
      hello-interval 10
      dead-interval 40
      network point-to-point
      bfd interval 50 min-rx 50
      authentication message-digest
      message-digest-key 1 md5 encrypted SKIPME
    $
  $
  area 10.0.0.1
    nssa default-information-originate no-summary
    interface gei-0/0/0/1
      cost 10
    $
  $
  area 20.0.0.1
    stub no-summary
  $
  redistribute static route-map RM1
  redistribute connected
$
!</ospfv2>
"""

_CFG_OSPF_V3 = """
!<ospfv3>
ipv6 router ospf 120 vrf CORE
  router-id 2.2.2.2
  redistribute connected
  area 0.0.0.0
    interface vlan918
      cost 10
      network point-to-point
    $
  $
$
!</ospfv3>
"""

_CFG_ISIS = """
!<isis>
router isis 1
  area 62.0047
  system-id 1241.9509.6248
  router-id 1.1.1.1
  is-type level-2-only
  authentication encrypted SKIPME
  address-family ipv6
    multi-topology
    segment-routing srv6 locator MAIN
      end-sid func-code ::1
    $
  $
  interface loopback0
    ipv6 router isis
    passive-mode
  $
  interface smartgroup1
    circuit-type level-2-only
    ipv6 bfd-enable
    ipv6 metric 5
    ipv6 router isis
    network point-to-point
    hello-authentication encrypted SKIPME
    bfd-enable
  $
$
!</isis>
"""


class ZteConfigIntentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        reload_profiles()

    def test_profiles_and_field_schema(self) -> None:
        for pid, mid in (
            ("zte.config_vrf", "config_vrf"),
            ("zte.config_interface", "config_interface"),
            ("zte.config_bgp_peer", "config_bgp_peer"),
            ("zte.config_l2vpn_pw", "config_l2vpn_pw"),
            ("zte.config_static_route", "config_static_route"),
            ("zte.config_static_route_v6", "config_static_route"),
            ("zte.config_ospf", "config_ospf"),
            ("zte.config_ospf_v3", "config_ospf"),
            ("zte.config_isis", "config_isis"),
        ):
            p = get_profile(pid)
            self.assertIsNotNone(p)
            assert p is not None
            self.assertEqual(p.metric_id, mid)
            self.assertTrue(p.enabled)
            fields = metric_field_map().get(mid) or []
            self.assertTrue(any(f.is_key for f in fields))

    def test_if_intf_cli_prefers_config_interface(self) -> None:
        hit = match_command(vendor_key="zte", command="show running-config if-intf")
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.profile.profile_id, "zte.config_interface")
        ii = get_profile("zte.if_intf")
        assert ii is not None
        self.assertFalse(ii.enabled)

    def test_config_vrf(self) -> None:
        rows = normalize_config_vrf(raw_text=_CFG_VRF, command="show running-config vrf")
        by = {r["vrf_name"]: r for r in rows}
        self.assertEqual(set(by), {"CUST_A", "CUST_B"})
        self.assertEqual(by["CUST_A"]["rd"], "100:1")
        self.assertIn("ipv4", by["CUST_A"]["address_families"])
        self.assertIn("ipv6", by["CUST_A"]["address_families"])
        self.assertIn("100:1", by["CUST_A"]["rt_export"])
        self.assertIn("100:2", by["CUST_A"]["rt_export"])
        self.assertEqual(by["CUST_A"]["description"], "Customer A")
        self.assertIn("200:2", by["CUST_B"]["rt_import"])

    def test_config_interface(self) -> None:
        rows = normalize_config_interface(
            raw_text=_CFG_IF, command="show running-config if-intf"
        )
        by = {r["interface"]: r for r in rows}
        self.assertEqual(by["gei-0/0/0/1"]["vrf"], "CUST_A")
        self.assertEqual(by["gei-0/0/0/1"]["admin"], "up")
        self.assertEqual(
            by["gei-0/0/0/1"]["ip_address"],
            "10.0.0.1/255.255.255.0,10.0.0.2/255.255.255.0",
        )
        self.assertEqual(by["gei-0/0/0/1"]["secondary_tag"], "M,S")
        self.assertEqual(
            by["gei-0/0/0/1"]["ipv6_address"],
            "2001:db8::1/64,2001:db8::2/64",
        )
        self.assertEqual(by["gei-0/0/0/1"]["ipv6_secondary_tag"], "M,S")
        self.assertEqual(by["gei-0/0/0/1"]["mtu"], "9000")
        self.assertEqual(by["gei-0/0/0/2"]["admin"], "down")
        self.assertEqual(by["gei-0/0/0/2"]["secondary_tag"], "M")
        self.assertEqual(by["gei-0/0/0/2"]["ipv6_secondary_tag"], "")

    def test_config_bgp_peer_skips_password(self) -> None:
        rows = normalize_config_bgp_peer(
            raw_text=_CFG_BGP, command="show running-config bgp"
        )
        blob = " ".join(str(v) for r in rows for v in r.values())
        self.assertNotIn("SKIPME", blob)
        self.assertNotIn("password", blob.lower())

        by = {(r["afi"], r["vrf"], r["neighbor"], r["peer_group"]): r for r in rows}
        self.assertIn(("vpnv4", "", "10.0.0.1", "CORE_RR"), by)
        self.assertEqual(by[("vpnv4", "", "10.0.0.1", "CORE_RR")]["remote_as"], "65001")
        self.assertEqual(by[("vpnv4", "", "10.0.0.1", "CORE_RR")]["activate"], "enable")
        self.assertEqual(by[("vpnv4", "", "10.0.0.1", "CORE_RR")]["update_source"], "loopback1")
        self.assertEqual(by[("vpnv4", "", "10.0.0.1", "CORE_RR")]["route_map_in"], "RM_IN")
        self.assertEqual(by[("vpnv4", "", "10.0.0.1", "CORE_RR")]["route_map_out"], "RM_OUT")
        # AF-scoped RM must not bleed into other address-families
        self.assertEqual(by[("l2vpn-evpn", "", "10.0.0.1", "CORE_RR")]["route_map_in"], "RM_IN")
        self.assertEqual(by[("l2vpn-evpn", "", "10.0.0.1", "CORE_RR")]["route_map_out"], "")
        # peer-group name is not an IP → neighbor empty, peer_group set
        self.assertIn(("vpnv4", "", "", "CORE_RR"), by)
        self.assertEqual(by[("vpnv4", "", "", "CORE_RR")]["remote_as"], "65009")
        self.assertEqual(by[("ipv4", "CUST_A", "10.0.0.2", "")]["remote_as"], "65003")
        self.assertIn(("l2vpn-evpn", "", "10.0.0.1", "CORE_RR"), by)
        # global IPv6 neighbor without AF activate
        self.assertIn(("global", "", "FC00:1::1", ""), by)
        # no name left in neighbor column
        self.assertTrue(all((not r["neighbor"]) or _is_ip_neighbor(r["neighbor"]) for r in rows))

    def test_config_l2vpn_pw(self) -> None:
        rows = normalize_config_l2vpn_pw(
            raw_text=_CFG_L2VPN, command="show running-config l2vpn"
        )
        by = {(r["vpn_name"], r["pw_name"]): r for r in rows}
        self.assertEqual(by[("VPN_A", "pw1")]["peer"], "10.0.0.1")
        self.assertEqual(by[("VPN_A", "pw1")]["vcid"], "1001")
        self.assertEqual(by[("VPN_A", "pw1")]["vpn_type"], "vpws")
        self.assertEqual(by[("VPN_A", "pw1")]["access_point"], "gei-0/0/0/1.100")
        self.assertEqual(by[("VPN_B", "pw2")]["vpn_type"], "vpls")
        self.assertEqual(by[("VPN_B", "pw2")]["vcid"], "2002")

    def test_config_interface_merges_duplicate_blocks(self) -> None:
        raw = """
!<if-intf>
interface te_tunnel36
 description LSP
 shutdown
$
interface te_tunnel36
 ip unnumbered loopback0
$
!</if-intf>
"""
        rows = normalize_config_interface(raw_text=raw, command="show running-config if-intf")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["admin"], "down")
        self.assertEqual(rows[0]["description"], "LSP")

    def test_config_l2vpn_backup_pw_not_overwrite(self) -> None:
        raw = """
!<l2vpn>
vpws 18163
 access-point gei-0/0/1/9.1751
  pseudo-wire pw_primary
   neighbour 10.0.0.1 vcid 18163
    encapsulation raw
  $
  backup-pw pw_backup protect pw_primary
   neighbour 10.0.0.2 vcid 18163
  $
 $
$
!</l2vpn>
"""
        rows = normalize_config_l2vpn_pw(raw_text=raw, command="show running-config l2vpn")
        by = {r["pw_name"]: r for r in rows}
        self.assertEqual(by["pw_primary"]["peer"], "10.0.0.1")
        self.assertEqual(by["pw_backup"]["peer"], "10.0.0.2")
        self.assertEqual(by["pw_primary"]["vcid"], "18163")

    def test_config_static_route_v4(self) -> None:
        rows = normalize_config_static_route(
            raw_text=_CFG_STATIC_V4, command="show running-config static"
        )
        self.assertTrue(all(r["af"] == "ipv4" for r in rows))
        by = {(r["vrf"], r["prefix"], r["interface"], r["next_hop"]): r for r in rows}
        self.assertEqual(by[("", "0.0.0.0", "", "10.0.0.1")]["route_name"], "default_gw")
        self.assertEqual(by[("", "10.9.9.0", "null1", "")]["mask"], "255.255.255.0")
        self.assertEqual(by[("", "10.8.8.0", "", "")]["nexthop_vrf"], "OTHER_VRF")
        self.assertEqual(
            by[("CUST_A", "10.1.1.0", "bvi12.100", "10.1.1.1")]["mask"], "255.255.255.0"
        )
        self.assertEqual(by[("CUST_A", "10.2.2.0", "gei-0/0/0/1.10", "10.2.2.1")]["bfd"], "enable")
        self.assertEqual(by[("CUST_B", "10.3.3.0", "null1", "")]["metric"], "255")
        self.assertEqual(by[("CUST_A", "10.4.4.0", "smartgroup1.1", "10.4.4.1")]["track"], "LINK_A")
        # Interface-only (no next-hop address) — including xxvgei / te_tunnel.
        self.assertEqual(by[("", "10.5.5.0", "xxvgei-0/2/0/1", "")]["mask"], "255.255.255.0")
        self.assertEqual(by[("", "10.6.6.0", "te_tunnel36", "")]["interface"], "te_tunnel36")
        # next-hop then interface
        self.assertEqual(
            by[("", "10.7.7.0", "xxvgei-0/2/0/2", "10.7.7.1")]["route_name"], "nh_then_if"
        )

    def test_config_static_route_v6(self) -> None:
        rows = normalize_config_static_route(
            raw_text=_CFG_STATIC_V6, command="show running-config ipv6-static-route"
        )
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["af"] == "ipv6" for r in rows))
        by = {(r["vrf"], r["prefix"]): r for r in rows}
        self.assertEqual(by[("", "::/0")]["interface"], "null1")
        self.assertEqual(by[("", "::/0")]["track"], "LINK_BFD")
        self.assertEqual(by[("CUST_A", "2001:db8:1::/64")]["next_hop"], "2001:db8:1::1")

    def test_compare_config_sheets(self) -> None:
        sheets = _default_zte_config_sheets()
        ids = [sheet_key(s) for s in sheets]
        self.assertIn("config_vrf", ids)
        self.assertIn("config_interface", ids)
        self.assertIn("config_bgp_peer", ids)
        self.assertIn("config_l2vpn_pw", ids)
        self.assertIn("config_static_route.ipv4", ids)
        self.assertIn("config_static_route.ipv6", ids)
        self.assertIn("config_ospf.ipv4", ids)
        self.assertIn("config_ospf.ipv6", ids)
        self.assertIn("config_isis", ids)
        for s in sheets:
            self.assertTrue(s.get("key_fields"))
            self.assertTrue(s.get("compare_fields") or s.get("key_fields"))

    def test_config_ospf(self) -> None:
        rows = normalize_config_ospf(raw_text=_CFG_OSPF, command="show running-config ospfv2")
        blob = " ".join(str(v) for r in rows for v in r.values())
        self.assertNotIn("SKIPME", blob)
        by = {(r["area"], r["interface"]): r for r in rows}
        self.assertEqual(by[("0.0.0.0", "smartgroup1.10")]["cost"], "20")
        self.assertEqual(by[("0.0.0.0", "smartgroup1.10")]["network_type"], "point-to-point")
        self.assertEqual(by[("0.0.0.0", "smartgroup1.10")]["bfd"], "enable")
        self.assertEqual(by[("0.0.0.0", "smartgroup1.10")]["vrf"], "CUST_A")
        self.assertIn("static", by[("0.0.0.0", "smartgroup1.10")]["redistribute"])
        self.assertEqual(by[("10.0.0.1", "gei-0/0/0/1")]["area_type"], "nssa")
        self.assertEqual(by[("20.0.0.1", "")]["area_type"], "stub")
        v3 = normalize_config_ospf(raw_text=_CFG_OSPF_V3, command="show running-config ospfv3")
        self.assertEqual(len(v3), 1)
        self.assertEqual(v3[0]["af"], "ipv6")
        self.assertEqual(v3[0]["interface"], "vlan918")

    def test_config_isis(self) -> None:
        rows = normalize_config_isis(raw_text=_CFG_ISIS, command="show running-config isis")
        blob = " ".join(str(v) for r in rows for v in r.values())
        self.assertNotIn("SKIPME", blob)
        by = {r["interface"]: r for r in rows}
        self.assertEqual(set(by), {"loopback0", "smartgroup1"})
        self.assertEqual(by["loopback0"]["passive"], "yes")
        self.assertEqual(by["loopback0"]["ipv6_enable"], "yes")
        self.assertEqual(by["loopback0"]["area"], "62.0047")
        self.assertEqual(by["smartgroup1"]["circuit_type"], "level-2-only")
        self.assertEqual(by["smartgroup1"]["ipv6_metric"], "5")
        self.assertEqual(by["smartgroup1"]["ipv6_bfd"], "enable")
        self.assertEqual(by["smartgroup1"]["bfd"], "enable")
        self.assertEqual(by["smartgroup1"]["network_type"], "point-to-point")


if __name__ == "__main__":
    unittest.main()
