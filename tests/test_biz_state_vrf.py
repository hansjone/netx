"""Unit tests for Phase3 VRF binding expand / route summary parse."""

from __future__ import annotations

import unittest

from netx_api.biz_state.command_match import expand_from_bindings, match_command, preview_task_item
from netx_api.biz_state.parsers.vrf import normalize_vrf_list, normalize_vrf_route_summary
from netx_api.biz_state.profiles import get_profile, profiles_for_vendor, reload_profiles


class BizStateVrfTests(unittest.TestCase):
    def setUp(self) -> None:
        reload_profiles()

    def test_vrf_profiles_registered(self) -> None:
        cisco = profiles_for_vendor("cisco")
        self.assertTrue(any(p.profile_id == "cisco.vrf_list" and p.kind == "discover" for p in cisco))
        self.assertTrue(any(p.profile_id == "cisco.route_vrf_summary" for p in cisco))

    def test_expand_multi_vrf_bindings(self) -> None:
        p = get_profile("cisco.route_vrf_summary")
        assert p is not None
        pairs = expand_from_bindings(
            profile=p,
            bindings=[
                {"placeholder": "vrf", "value": "CUST_A"},
                {"placeholder": "vrf", "value": "CUST_B"},
            ],
        )
        self.assertEqual(len(pairs), 2)
        self.assertEqual(pairs[0][0], "show ip route vrf CUST_A summary")
        self.assertEqual(pairs[1][0], "show ip route vrf CUST_B summary")
        hit = match_command(vendor_key="cisco", command=pairs[0][0])
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.params.get("vrf"), "CUST_A")

    def test_expand_requires_bindings(self) -> None:
        p = get_profile("zte.route_vrf_summary")
        assert p is not None
        with self.assertRaises(ValueError):
            expand_from_bindings(profile=p, bindings=[])

    def test_preview_with_bindings(self) -> None:
        prev = preview_task_item(
            vendor_key="huawei",
            profile_id="huawei.route_vrf_summary",
            bindings=[{"vrf": "VPN1"}],
        )
        self.assertTrue(prev["ok"])
        self.assertEqual(len(prev["commands"]), 1)
        self.assertIn("VPN1", prev["commands"][0]["command"])

    def test_route_summary_parser(self) -> None:
        raw = """
IP routing table name is CUST_A
Route Source    Networks
connected       3
static          1
bgp 65001       42
Total           46
"""
        rows = normalize_vrf_route_summary(raw_text=raw, params={"vrf": "CUST_A"})
        by_src = {r["source"]: r["networks"] for r in rows}
        self.assertEqual(by_src["connected"], 3)
        self.assertEqual(by_src["bgp"], 42)
        self.assertEqual(by_src["total"], 46)
        self.assertTrue(all(r["vrf"] == "CUST_A" for r in rows))

    def test_vrf_list_fallback(self) -> None:
        raw = "CUST_A                           100:1                 ipv4\nCUST_B                           100:2                 ipv4\n"
        rows = normalize_vrf_list(raw_text=raw, command="")
        names = {r["vrf_name"] for r in rows}
        self.assertIn("CUST_A", names)
        self.assertIn("CUST_B", names)


if __name__ == "__main__":
    unittest.main()
