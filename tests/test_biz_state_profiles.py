"""Unit tests for biz_state ParseProfile match / expand (no device)."""

from __future__ import annotations

import unittest

from netx_api.biz_state.command_match import expand_from_bindings, match_command, preview_task_item
from netx_api.biz_state.profiles import all_profiles, get_profile, profiles_for_vendor
from netx_api.biz_state.parsers import normalize_lldp_neighbors
from netx_api.lldp_shared import parse_neighbor_output


class BizStateProfileTests(unittest.TestCase):
    def test_lldp_profiles_registered(self) -> None:
        profiles = all_profiles()
        self.assertTrue(any(p.metric_id == "lldp_neighbor" for p in profiles))
        zte = profiles_for_vendor("zte")
        self.assertTrue(any(p.profile_id == "zte.lldp_neighbors" for p in zte))

    def test_match_zte_lldp_command(self) -> None:
        hit = match_command(vendor_key="zte", command="show lldp neighbor brief")
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.profile.parser_id, "lldp_neighbors")
        self.assertEqual(hit.profile.metric_id, "lldp_neighbor")

    def test_match_huawei_lldp_command(self) -> None:
        hit = match_command(vendor_key="huawei", command="display lldp neighbor")
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.profile.profile_id, "huawei.lldp_neighbors")

    def test_expand_no_placeholder(self) -> None:
        p = get_profile("zte.lldp_neighbors")
        self.assertIsNotNone(p)
        assert p is not None
        pairs = expand_from_bindings(profile=p)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0][0], "show lldp neighbor brief")

    def test_preview_custom_raw(self) -> None:
        prev = preview_task_item(
            vendor_key="zte",
            kind="custom_raw",
            command="show version",
        )
        self.assertTrue(prev["ok"])
        self.assertEqual(prev["parse"], "skipped_custom")

    def test_shared_parse_empty(self) -> None:
        rows = normalize_lldp_neighbors(
            raw_text="",
            vendor="zte",
            device_type="zte_zxros",
            command="show lldp neighbor brief",
        )
        self.assertEqual(rows, [])
        hits = parse_neighbor_output("", vendor="zte", device_type="zte_zxros")
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
