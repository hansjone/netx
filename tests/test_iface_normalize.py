"""Unit tests for iface normalize + subinterface port-map inheritance."""

from __future__ import annotations

import unittest

from netx_api.biz_state.compare_engine import apply_port_map, compare_rows
from netx_api.biz_state.iface_normalize import (
    default_zte_iface_normalize_rules,
    normalize_iface_name,
    normalize_iface_rules,
    resolve_mapped_iface,
)


class IfaceNormalizeTests(unittest.TestCase):
    def test_prefix_rules_longest_first(self) -> None:
        rules = default_zte_iface_normalize_rules()
        self.assertEqual(normalize_iface_name("GE1.100", rules), "gei1.100")
        self.assertEqual(normalize_iface_name("SG11", rules), "smartgroup11")
        self.assertEqual(normalize_iface_name("XGE-0/1/0/1", rules), "xgei-0/1/0/1")
        self.assertEqual(normalize_iface_name("XXVGE-0/2/0/10", rules), "xxvgei-0/2/0/10")
        self.assertEqual(normalize_iface_name("CGE0/4/1/3", rules), "cgei0/4/1/3")

    def test_does_not_mangle_canonical(self) -> None:
        rules = default_zte_iface_normalize_rules()
        self.assertEqual(normalize_iface_name("gei-0/0/0/1", rules), "gei-0/0/0/1")
        self.assertEqual(normalize_iface_name("smartgroup11", rules), "smartgroup11")
        self.assertEqual(normalize_iface_name("xgei-0/3/1/20.219", rules), "xgei-0/3/1/20.219")

    def test_parse_text_rules(self) -> None:
        rules = normalize_iface_rules("GE,gei\n# comment\nSG\tsmartgroup\n")
        self.assertEqual(rules[0]["from"], "GE")
        self.assertEqual(rules[1]["from"], "SG")


class PortMapSubifTests(unittest.TestCase):
    def test_exact_wins_over_parent(self) -> None:
        pmap = {
            "gei-0/0/0/1": "xgei-0/1/0/1",
            "gei-0/0/0/1.100": "xgei-0/1/0/9.100",
        }
        self.assertEqual(resolve_mapped_iface("gei-0/0/0/1.100", pmap), "xgei-0/1/0/9.100")
        self.assertEqual(resolve_mapped_iface("gei-0/0/0/1.200", pmap), "xgei-0/1/0/1.200")

    def test_parent_inherits_suffix(self) -> None:
        pmap = {"gei-0/0/0/1": "xgei-0/1/0/1"}
        self.assertEqual(resolve_mapped_iface("gei-0/0/0/1", pmap), "xgei-0/1/0/1")
        self.assertEqual(resolve_mapped_iface("gei-0/0/0/1.100", pmap), "xgei-0/1/0/1.100")
        self.assertEqual(resolve_mapped_iface("gei-0/0/0/2.100", pmap), "gei-0/0/0/2.100")

    def test_qinq_multi_level_parent(self) -> None:
        """QinQ a.b.c: map parent a.b or a, keep remaining suffix."""
        pmap = {"gei-0/1/0/1": "xgei-0/2/0/1"}
        self.assertEqual(
            resolve_mapped_iface("gei-0/1/0/1.100.200", pmap),
            "xgei-0/2/0/1.100.200",
        )
        pmap2 = {"gei-0/1/0/1.100": "xgei-0/2/0/1.100"}
        self.assertEqual(
            resolve_mapped_iface("gei-0/1/0/1.100.200", pmap2),
            "xgei-0/2/0/1.100.200",
        )

    def test_apply_port_map_row(self) -> None:
        row = apply_port_map(
            {"interface": "gei-0/0/0/1.55", "admin": "up"},
            iface_fields=["interface"],
            port_map={"gei-0/0/0/1": "xgei-0/1/0/1"},
        )
        self.assertEqual(row["interface"], "xgei-0/1/0/1.55")


class ComparePipelineTests(unittest.TestCase):
    def test_normalize_then_map_matches(self) -> None:
        before = [{"interface": "GE-0/0/0/1.100", "admin": "up"}]
        after = [{"interface": "xgei-0/1/0/1.100", "admin": "up"}]
        out = compare_rows(
            before_rows=before,
            after_rows=after,
            key_fields=["interface"],
            iface_fields=["interface"],
            compare_fields=["admin"],
            port_map={"gei-0/0/0/1": "xgei-0/1/0/1"},
            iface_normalize_rules=default_zte_iface_normalize_rules(),
        )
        self.assertEqual(out["summary"]["unchanged"], 1)
        self.assertEqual(out["summary"]["added"], 0)
        self.assertEqual(out["summary"]["removed"], 0)


if __name__ == "__main__":
    unittest.main()
