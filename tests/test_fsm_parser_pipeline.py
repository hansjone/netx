"""Tests for TextFSM rule table + parser dual-input pipeline."""

from __future__ import annotations

import unittest

from netx_api.biz_state.parsers import get_parser_meta, run_parser
from netx_api.ntc_parse import apply_rule, apply_rules, rules_for_command


ZTE_BRIEF = """\
Interface               Attribute  Mode         BW    Admin Phy   Prot  Description
xgei-1/1/0/1            optical    Duplex/full  1G    up    up    up    C2930L100-EQ2
xgei-1/1/0/2            optical    Duplex/full  1G    up    down  down
"""

ZTE_ARP = """\
IP               Age        Hardware address   Interface            Exter Interface     VPN name
192.168.1.1      03:22:07   0011.2233.4455     gei-0/1/0/1          N/A                 ---
192.168.1.2      H          00aa.bbcc.ddee     gei-0/1/0/1          N/A                 ---
"""


class ApplyRulesTests(unittest.TestCase):
    def test_apply_rules_interface_brief(self) -> None:
        stems = rules_for_command("zte_zxros", "show interface brief")
        self.assertIn("zte_zxros_show_interface_brief", stems)
        tables = apply_rules(
            platform="zte_zxros",
            text=ZTE_BRIEF,
            rule_keys=("zte_zxros_show_interface_brief",),
            command="show interface brief",
        )
        rows = tables.get("zte_zxros_show_interface_brief") or []
        self.assertGreaterEqual(len(rows), 2)
        self.assertEqual(rows[0].get("INTERFACE") or rows[0].get("interface"), "xgei-1/1/0/1")

    def test_apply_rule_missing_stem(self) -> None:
        rows = apply_rule(
            platform="zte_zxros",
            rule_key="zte_zxros_show_arp_does_not_exist",
            text=ZTE_ARP,
        )
        self.assertEqual(rows, [])

    def test_apply_rules_empty_key_on_miss(self) -> None:
        tables = apply_rules(
            platform="zte_zxros",
            text=ZTE_ARP,
            rule_keys=("no_such_template_xyz",),
        )
        self.assertEqual(tables.get("no_such_template_xyz"), [])


class ParserMetaTests(unittest.TestCase):
    def test_interface_brief_meta(self) -> None:
        meta = get_parser_meta("interface_brief")
        self.assertIsNotNone(meta)
        assert meta is not None
        self.assertIn("zte_zxros_show_interface_brief", meta["rule_keys"])

    def test_arp_empty_rule_keys(self) -> None:
        meta = get_parser_meta("arp")
        self.assertIsNotNone(meta)
        assert meta is not None
        self.assertEqual(meta["rule_keys"], ())

    def test_run_parser_interface_brief(self) -> None:
        records, tables, keys = run_parser(
            "interface_brief",
            raw_text=ZTE_BRIEF,
            vendor="zte",
            device_type="zte_zxros",
            command="show interface brief",
        )
        self.assertIn("zte_zxros_show_interface_brief", keys)
        self.assertGreaterEqual(len(tables.get("zte_zxros_show_interface_brief") or []), 2)
        self.assertGreaterEqual(len(records), 2)
        self.assertEqual(records[0]["interface"], "xgei-1/1/0/1")

    def test_run_parser_arp_hand_only(self) -> None:
        # Declared empty RULE_KEYS → no index auto-bind; hand parse only.
        records, tables, keys = run_parser(
            "arp",
            raw_text=ZTE_ARP,
            vendor="zte",
            device_type="zte_zxros",
            command="show arp",
        )
        self.assertEqual(keys, [])
        self.assertEqual(tables, {})
        self.assertGreaterEqual(len(records), 1)
        self.assertTrue(any(r["ip"] == "192.168.1.1" for r in records))


if __name__ == "__main__":
    unittest.main()
