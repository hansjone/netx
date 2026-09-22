"""Unit tests for biz_state ParseProfile match / expand (no device)."""

from __future__ import annotations

import unittest

from netx_api.biz_state.command_match import expand_from_bindings, match_command, preview_task_item
from netx_api.biz_state.profiles import (
    AuxCommand,
    ParseProfile,
    all_profiles,
    get_profile,
    profile_to_public_dict,
    profiles_for_vendor,
    reload_profiles,
    _finalize_profiles,
    _normalize_discover_aux,
)
from netx_api.biz_state.parsers import normalize_lldp_neighbors
from netx_api.biz_state.profiles import PlaceholderDef
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

    def test_bgp_neighbor_discover_and_aux_both_config_bgp(self) -> None:
        reload_profiles()
        for pid in (
            "zte.bgp_vpnv4_neighbor_in",
            "zte.bgp_vpnv4_neighbor_out",
            "zte.bgp_vpnv6_neighbor_in",
            "zte.bgp_vpnv6_neighbor_out",
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

    def test_discover_backed_profiles_omit_explicit_aux(self) -> None:
        """Source declarations should rely on framework derive (no redundant aux)."""
        import inspect
        from netx_api.biz_state import profiles as profiles_mod

        src = inspect.getsource(profiles_mod._zte_status_profiles)
        # ARP may keep explicit aux; discover-backed must not hardcode config_bgp_peer aux
        self.assertNotIn(
            'AuxCommand(key="config_bgp_peer"',
            src,
            "discover-backed profiles must not list config_bgp_peer aux explicitly",
        )
        reload_profiles()
        p = get_profile("zte.bgp_vpnv4_neighbor_in")
        assert p is not None
        self.assertEqual([a.profile_id for a in p.aux_commands], ["zte.config_bgp_peer"])

    def test_normalize_discover_aux_overwrites_wrong_aux(self) -> None:
        """Framework replaces explicit aux when discover_select is present."""
        fake = ParseProfile(
            profile_id="zte.test_neighbor_routes",
            vendor_key="zte",
            metric_id="bgp_route",
            parser_id="bgp_route",
            title="test",
            command_template="show bgp neighbor in <neighbor> | one-line",
            match=r".*",
            placeholders=[
                PlaceholderDef(
                    name="neighbor",
                    schema_field="neighbor",
                    bind_mode="discover_select",
                    discover_profile_id="zte.config_bgp_peer",
                    discover_value_field="neighbor",
                )
            ],
            # Intentionally wrong — summary instead of config
            aux_commands=[
                AuxCommand(key="bgp_summary", profile_id="zte.bgp_vpnv4_summary"),
            ],
        )
        _normalize_discover_aux(fake)
        self.assertEqual(
            [(a.key, a.profile_id) for a in fake.aux_commands],
            [("config_bgp_peer", "zte.config_bgp_peer")],
        )

    def test_arp_keeps_explicit_aux_without_discover(self) -> None:
        reload_profiles()
        arp = get_profile("zte.arp")
        assert arp is not None
        self.assertTrue(any(a.profile_id == "zte.config_interface" for a in arp.aux_commands))
        self.assertFalse(
            any(str(ph.bind_mode or "") == "discover_select" for ph in (arp.placeholders or []))
        )

    def test_finalize_rejects_aux_placeholder_outside_primary(self) -> None:
        target = ParseProfile(
            profile_id="zte.fake_vrf_summary",
            vendor_key="zte",
            metric_id="bgp_peer",
            parser_id="bgp_peer",
            title="fake",
            command_template="show bgp vpnv4 unicast vrf <vrf> summary | one-line",
            match=r".*",
        )
        bad = ParseProfile(
            profile_id="zte.bad_aux",
            vendor_key="zte",
            metric_id="x",
            parser_id="x",
            title="bad",
            command_template="show foo",
            match=r".*",
            placeholders=[],
            aux_commands=[AuxCommand(key="vrf_sum", profile_id="zte.fake_vrf_summary")],
        )
        with self.assertRaises(RuntimeError) as ctx:
            _finalize_profiles([bad, target])
        self.assertIn("placeholders", str(ctx.exception))

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
