"""Unit tests for VRF list discover profiles."""

from __future__ import annotations

import unittest

from netx_api.biz_state.parsers.vrf import normalize_vrf_list
from netx_api.biz_state.profiles import get_profile, profiles_for_vendor, reload_profiles


class BizStateVrfTests(unittest.TestCase):
    def setUp(self) -> None:
        reload_profiles()

    def test_vrf_list_profiles_registered(self) -> None:
        cisco = profiles_for_vendor("cisco")
        self.assertTrue(any(p.profile_id == "cisco.vrf_list" and p.kind == "discover" for p in cisco))
        self.assertFalse(any(p.metric_id == "vrf_route_summary" for p in cisco))
        self.assertIsNone(get_profile("cisco.route_vrf_summary"))
        self.assertIsNone(get_profile("zte.route_vrf_summary"))

    def test_vrf_list_fallback(self) -> None:
        raw = "CUST_A                           100:1                 ipv4\nCUST_B                           100:2                 ipv4\n"
        rows = normalize_vrf_list(raw_text=raw, command="")
        names = {r["vrf_name"] for r in rows}
        self.assertIn("CUST_A", names)
        self.assertIn("CUST_B", names)


if __name__ == "__main__":
    unittest.main()
