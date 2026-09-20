"""Unit tests for ZTE ZXROS status parsers (samples from test/log)."""

from __future__ import annotations

import unittest
from pathlib import Path

from netx_api.biz_state.parsers.zte import (
    is_valid_arp_age,
    normalize_arp,
    normalize_bgp_peer,
    normalize_interface_brief,
    normalize_isis_adjacency,
    normalize_nd6_cache,
)
from netx_api.biz_state.profiles import get_profile, metric_field_map, profiles_for_vendor


def _log_text() -> str:
    p = Path(__file__).resolve().parents[2] / "test" / "log"
    if not p.is_file():
        # Fallback: relative to monorepo root when tests run from netx/
        p = Path(__file__).resolve().parents[3] / "test" / "log"
    return p.read_text(encoding="utf-8", errors="ignore") if p.is_file() else ""


def _section(blob: str, start_marker: str, end_markers: tuple[str, ...]) -> str:
    i = blob.find(start_marker)
    if i < 0:
        return ""
    rest = blob[i:]
    cut = len(rest)
    for em in end_markers:
        j = rest.find(em, len(start_marker))
        if j >= 0:
            cut = min(cut, j)
    return rest[:cut]


class ZteStatusParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.log = _log_text()

    def test_profiles_registered(self) -> None:
        zte = {p.profile_id for p in profiles_for_vendor("zte", kind="collect")}
        self.assertIn("zte.isis_adjacency", zte)
        self.assertIn("zte.interface_brief", zte)
        self.assertIn("zte.arp", zte)
        self.assertIn("zte.nd6_cache", zte)
        self.assertIn("zte.bgp_vpnv4_summary", zte)
        self.assertIn("zte.bgp_ipv4_summary", zte)
        self.assertIn("zte.bgp_vpnv6_summary", zte)
        for mid in ("isis_adjacency", "interface_brief", "arp", "nd6_cache", "bgp_peer", "if_intf"):
            self.assertIn(mid, metric_field_map())
        self.assertIsNotNone(get_profile("zte.isis_adjacency"))
        ii = get_profile("zte.if_intf")
        self.assertIsNotNone(ii)
        assert ii is not None
        self.assertFalse(ii.enabled)
        self.assertNotIn("zte.if_intf", zte)
        self.assertIn("zte.config_interface", zte)

    def test_isis_adjacency(self) -> None:
        text = _section(
            self.log,
            "show isis adjacency",
            ("show interface brief", "show arp", "M6000-4SE-3#show"),
        )
        rows = normalize_isis_adjacency(raw_text=text)
        self.assertGreaterEqual(len(rows), 5)
        procs = {r["process_id"] for r in rows}
        self.assertIn("1", procs)
        self.assertIn("20", procs)
        up = [r for r in rows if r["state"].upper() == "UP"]
        self.assertEqual(len(up), len(rows))

    def test_isis_adjacency_empty_mt_ipv4(self) -> None:
        """IPv4 rows often leave MT blank; must still parse NSF/AF."""
        text = """
Process ID: 0

Process ID: 1
Interface         System id        State     Lev     Holds       SNPA(802.2)    Pri     MT   NSF       AF       
smartgroup107     PAG7_6120HS      UP        L1      26          PPP            -            Disable   IPv4
smartgroup32766.1 AG4              UP/UP     L1L2    27/27       PPP            -/-          Disable   IPv4
smartgroup4501    C3               UP/UP     L1L2    30/30       PPP            -/-          Disable   IPv4
smartgroup11      MDN-SGI-P-ZM8SP  UP        L2      24          PPP            -       M    Disable   IPv6
"""
        rows = normalize_isis_adjacency(raw_text=text)
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(r["process_id"] == "1" for r in rows))
        v4 = [r for r in rows if r["af"] == "IPv4"]
        self.assertEqual(len(v4), 3)
        self.assertTrue(all(r["mt"] == "" for r in v4))
        self.assertEqual(v4[0]["interface"], "smartgroup107")
        self.assertEqual(v4[0]["system_id"], "PAG7_6120HS")
        self.assertEqual(v4[0]["state"], "UP")
        self.assertEqual(v4[0]["nsf"], "Disable")
        self.assertEqual(v4[1]["state"], "UP/UP")
        self.assertEqual(v4[1]["pri"], "-/-")
        v6 = next(r for r in rows if r["af"] == "IPv6")
        self.assertEqual(v6["mt"], "M")
        self.assertEqual(v6["nsf"], "Disable")

    def test_interface_brief(self) -> None:
        text = _section(
            self.log,
            "show interface brief",
            ("show arp", "show nd6", "M6000-4SE-3#show arp"),
        )
        rows = normalize_interface_brief(
            raw_text=text, vendor="zte", device_type="zte_zxros", command="show interface brief"
        )
        self.assertGreaterEqual(len(rows), 10)
        by_if = {r["interface"]: r for r in rows}
        self.assertIn("cgei-0/1/0/1", by_if)
        self.assertEqual(by_if["cgei-0/1/0/1"]["admin"].lower(), "up")
        self.assertEqual(by_if["cgei-0/1/0/3"]["admin"].lower(), "down")

    def test_arp(self) -> None:
        text = _section(self.log, "show arp", ("show nd6", "PAG3_", "M6000-4SE-3#show nd6"))
        from netx_api.biz_state.parsers import run_parser
        from netx_api.ntc_parse import apply_rule

        fsm = apply_rule(
            platform="zte_zxros",
            rule_key="zte_zxros_show_arp",
            text=text,
            command="show arp",
        )
        self.assertEqual(len(fsm), 13)

        records, tables, keys = run_parser(
            "arp",
            raw_text=text,
            vendor="zte",
            device_type="zte_zxros",
            command="show arp | one-line",
            textfsm_command="show arp",
        )
        self.assertEqual(keys, ["zte_zxros_show_arp"])
        self.assertEqual(len(tables["zte_zxros_show_arp"]), 13)

        rows = normalize_arp(
            raw_text=text, vendor="zte", device_type="zte_zxros", command="show arp"
        )
        self.assertEqual(rows, records)
        self.assertGreaterEqual(len(rows), 10)
        ips = {r["ip"] for r in rows}
        self.assertIn("192.166.1.65", ips)
        self.assertIn("10.229.234.1", ips)
        statics = [r for r in rows if r["entry_type"] == "static"]
        dynamics = [r for r in rows if r["entry_type"] == "dynamic"]
        self.assertGreaterEqual(len(statics), 5)
        self.assertGreaterEqual(len(dynamics), 5)
        self.assertTrue(all(is_valid_arp_age(r["age"]) for r in dynamics))
        self.assertFalse(is_valid_arp_age("H"))
        self.assertTrue(is_valid_arp_age("03:22:07"))
        # VLAN-subif row from TextFSM
        sub = next(r for r in rows if r["ip"] == "192.166.1.73")
        self.assertEqual(sub["interface"], "cgei-0/2/0/1.30")
        self.assertEqual(sub["exter_vlan"], "30")

    def test_nd6(self) -> None:
        text = _section(self.log, "show nd6 cache", ("PAG3_", "show bgp"))
        rows = normalize_nd6_cache(raw_text=text)
        self.assertGreaterEqual(len(rows), 5)
        addrs = {r["address"] for r in rows}
        self.assertTrue(any("fe80::" in a for a in addrs))

    def test_bgp_peers(self) -> None:
        v4 = _section(self.log, "show bgp vpnv4 unicast summary", ("show bgp ipv4",))
        rows = normalize_bgp_peer(raw_text=v4, command="show bgp vpnv4 unicast summary")
        self.assertGreaterEqual(len(rows), 8)
        self.assertTrue(all(r["afi"] == "vpnv4" for r in rows))
        est = [r for r in rows if r["state"] == "Established"]
        conn = [r for r in rows if r["state"] == "Connect"]
        self.assertGreaterEqual(len(est), 3)
        self.assertGreaterEqual(len(conn), 3)
        self.assertTrue(all("state_or_pfx" not in r for r in rows))
        self.assertTrue(all(r["pfx_rcd"].isdigit() for r in est))
        self.assertTrue(all(r["pfx_rcd"] == "" for r in conn))
        one = next(r for r in rows if r["neighbor"] == "10.206.63.205")
        self.assertEqual(one["state"], "Established")
        self.assertEqual(one["pfx_rcd"], "642")

        ipv4 = _section(self.log, "show bgp ipv4 unicast summary", ("show bgp vpnv6",))
        rows2 = normalize_bgp_peer(raw_text=ipv4, command="show bgp ipv4 unicast summary")
        self.assertGreaterEqual(len(rows2), 8)
        self.assertTrue(all(r["afi"] == "ipv4" for r in rows2))

        v6 = _section(self.log, "show bgp vpnv6 unicast summary", ("END",))
        # file ends after vpnv6 — take remainder
        if not v6.strip():
            i = self.log.find("show bgp vpnv6 unicast summary")
            v6 = self.log[i:] if i >= 0 else ""
        rows3 = normalize_bgp_peer(raw_text=v6, command="show bgp vpnv6 unicast summary")
        self.assertGreaterEqual(len(rows3), 5)
        self.assertTrue(all(r["afi"] == "vpnv6" for r in rows3))


if __name__ == "__main__":
    unittest.main()
