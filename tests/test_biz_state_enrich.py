"""Unit tests for EnrichJoin single- and composite-key joins."""

from __future__ import annotations

import unittest

from netx_api.biz_state.enrich import EnrichJoin, apply_enrich_joins


class EnrichJoinTests(unittest.TestCase):
    def test_single_key(self) -> None:
        rows = [{"neighbor": "10.0.0.1", "network": "1.1.1.0/24"}]
        apply_enrich_joins(
            rows,
            {
                "config_bgp_peer": [
                    {"neighbor": "10.0.0.1", "remote_as": "65001", "activate": "enable"},
                ]
            },
            [EnrichJoin(from_aux="config_bgp_peer", on="neighbor", take=("remote_as", "activate"))],
        )
        self.assertEqual(rows[0]["remote_as"], "65001")

    def test_composite_vrf_neighbor(self) -> None:
        rows = [
            {"vrf": "A", "neighbor": "10.0.0.1", "network": "1.0.0.0/24"},
            {"vrf": "B", "neighbor": "10.0.0.1", "network": "2.0.0.0/24"},
        ]
        apply_enrich_joins(
            rows,
            {
                "config_bgp_peer": [
                    {"vrf": "A", "neighbor": "10.0.0.1", "remote_as": "1"},
                    {"vrf": "B", "neighbor": "10.0.0.1", "remote_as": "2"},
                ]
            },
            [
                EnrichJoin(
                    from_aux="config_bgp_peer",
                    left_on="vrf,neighbor",
                    right_on="vrf,neighbor",
                    take=("remote_as",),
                )
            ],
        )
        self.assertEqual(rows[0]["remote_as"], "1")
        self.assertEqual(rows[1]["remote_as"], "2")

    def test_afi_neighbor_avoids_wrong_af(self) -> None:
        rows = [{"afi": "vpnv4", "neighbor": "10.0.0.1"}]
        apply_enrich_joins(
            rows,
            {
                "config_bgp_peer": [
                    {"afi": "ipv4", "neighbor": "10.0.0.1", "route_map_in": "RM_V4"},
                    {"afi": "vpnv4", "neighbor": "10.0.0.1", "route_map_in": "RM_VPN"},
                ]
            },
            [
                EnrichJoin(
                    from_aux="config_bgp_peer",
                    left_on="afi,neighbor",
                    right_on="afi,neighbor",
                    take=("route_map_in",),
                )
            ],
        )
        self.assertEqual(rows[0]["route_map_in"], "RM_VPN")


if __name__ == "__main__":
    unittest.main()
