"""Unit tests for biz_state ParseProfile match / expand (no device)."""

from __future__ import annotations

import unittest

from netx_api.biz_state.command_match import expand_from_bindings, match_command, preview_task_item
from netx_api.biz_state.profiles import (
    all_profiles,
    get_profile,
    profile_to_public_dict,
    profiles_for_vendor,
)
from netx_api.biz_state.parsers import normalize_lldp_neighbors
from netx_api.lldp_shared import parse_neighbor_output
import re


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
        self.assertEqual(pairs[0][0], "show lldp neighbor brief | one-line")

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

    def test_vrf_neighbor_discover_and_aux_both_config_bgp(self) -> None:
        for pid in (
            "zte.bgp_vpnv4_vrf_neighbor_in",
            "zte.bgp_vpnv4_vrf_neighbor_out",
            "zte.bgp_vpnv6_vrf_neighbor_in",
            "zte.bgp_vpnv6_vrf_neighbor_out",
        ):
            p = get_profile(pid)
            assert p is not None
            for ph in p.placeholders:
                self.assertEqual(ph.discover_profile_id, "zte.config_bgp_peer")
            self.assertEqual([a.profile_id for a in p.aux_commands], ["zte.config_bgp_peer"])
            pub = profile_to_public_dict(p)
            aux = pub.get("aux_commands") or []
            self.assertEqual(len(aux), 1)
            self.assertIn("running-config bgp", aux[0]["command_template"])

    def test_aux_templates_placeholders_subset_of_primary(self) -> None:
        """Aux CLI must not introduce placeholders outside primary params.

        Prevents cases like VRF neighbor aux pointing at summary with <vrf>
        while the UI shows both as if they were independent unbound vars.
        """
        ph_re = re.compile(r"<([^>]+)>")
        for p in all_profiles():
            if not p.enabled or p.kind != "collect":
                continue
            primary_ph = {ph.name for ph in (p.placeholders or [])}
            for a in p.aux_commands or []:
                ap = get_profile(a.profile_id)
                if ap is None:
                    continue
                tmpl = str(ap.command_template or "")
                aux_ph = set(ph_re.findall(tmpl))
                extra = aux_ph - primary_ph
                self.assertFalse(
                    extra,
                    f"{p.profile_id} aux {a.key} ({a.profile_id}) has "
                    f"placeholders {extra} not in primary {primary_ph}",
                )


if __name__ == "__main__":
    unittest.main()
