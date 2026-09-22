"""Discover CLI/parse cache reuse across collect profiles."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from netx_api.biz_state import discover as disc_mod
from netx_api.biz_state.discover import clear_discover_cache, discover_params
from netx_api.biz_state.profiles import get_profile


class DiscoverCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_discover_cache()

    def tearDown(self) -> None:
        clear_discover_cache()

    def test_shared_bgp_peer_discover_reuses_cli(self) -> None:
        v4 = get_profile("zte.bgp_vpnv4_neighbor_in")
        v6 = get_profile("zte.bgp_vpnv6_neighbor_in")
        assert v4 is not None and v6 is not None
        self.assertEqual(v4.placeholders[0].discover_profile_id, "zte.config_bgp_peer")
        self.assertEqual(v6.placeholders[0].discover_profile_id, "zte.config_bgp_peer")

        records = [
            {"afi": "vpnv4", "vrf": "", "neighbor": "10.0.0.1", "remote_as": "65001"},
            {"afi": "vpnv6", "vrf": "", "neighbor": "FC00::1", "remote_as": "65002"},
        ]
        db = MagicMock()

        with (
            patch.object(disc_mod, "resolve_cli_target", return_value=({"vendor": "zte"}, {"vendor": "zte", "device_type": "zte_zxros"})),
            patch.object(disc_mod, "cli_creds_skip_reason", return_value=None),
            patch.object(disc_mod, "open_netmiko_connection", return_value=MagicMock()) as open_conn,
            patch.object(disc_mod, "disable_target_paging"),
            patch.object(disc_mod, "send_show_command", return_value="raw-bgp") as send_cmd,
            patch.object(disc_mod, "close_netmiko_connection"),
            patch.object(disc_mod, "get_parser", return_value=True),
            patch.object(
                disc_mod,
                "run_parser",
                return_value=(records, {}, ()),
            ) as run_parser,
        ):
            first = discover_params(
                db,
                source="managed",
                ne_id="ne1",
                collect_profile_id="zte.bgp_vpnv4_neighbor_in",
                placeholder="neighbor",
            )
            self.assertTrue(first["ok"])
            self.assertFalse(first.get("cache_hit"))
            self.assertEqual([c["value"] for c in first["candidates"]], ["10.0.0.1"])
            self.assertEqual(open_conn.call_count, 1)
            self.assertEqual(send_cmd.call_count, 1)
            self.assertEqual(run_parser.call_count, 1)

            second = discover_params(
                db,
                source="managed",
                ne_id="ne1",
                collect_profile_id="zte.bgp_vpnv6_neighbor_in",
                placeholder="neighbor",
            )
            self.assertTrue(second["ok"])
            self.assertTrue(second.get("cache_hit"))
            self.assertEqual([c["value"] for c in second["candidates"]], ["FC00::1"])
            # Same discover profile → no second SSH/parse
            self.assertEqual(open_conn.call_count, 1)
            self.assertEqual(send_cmd.call_count, 1)
            self.assertEqual(run_parser.call_count, 1)

            forced = discover_params(
                db,
                source="managed",
                ne_id="ne1",
                collect_profile_id="zte.bgp_vpnv4_neighbor_in",
                placeholder="neighbor",
                force_refresh=True,
            )
            self.assertTrue(forced["ok"])
            self.assertFalse(forced.get("cache_hit"))
            self.assertEqual(open_conn.call_count, 2)
            self.assertEqual(send_cmd.call_count, 2)


if __name__ == "__main__":
    unittest.main()
