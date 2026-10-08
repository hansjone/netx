"""Unit tests for SQL compare eligibility and filter compilation."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from netx_api.biz_state.compare_sql import (
    can_sql_compare,
    compile_row_filters_sql,
    sql_compare_skip_reason,
    _field_rules_sql_compatible,
    _filters_sql_compatible,
    _safe_field,
)


class _FakeDialect:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeBind:
    def __init__(self, name: str) -> None:
        self.dialect = _FakeDialect(name)


def _db(dialect: str = "postgresql") -> MagicMock:
    db = MagicMock()
    db.get_bind.return_value = _FakeBind(dialect)
    return db


class CompareSqlGateTests(unittest.TestCase):
    def test_safe_field_rejects_injection(self) -> None:
        with self.assertRaises(ValueError):
            _safe_field("a'; drop table x;--")
        with self.assertRaises(ValueError):
            _safe_field("x.y")
        self.assertEqual(_safe_field("network"), "network")

    def test_requires_postgres(self) -> None:
        sheet = {
            "metric_id": "bgp_route",
            "key_fields": ["network", "next_hop"],
            "compare_fields": ["path"],
            "iface_fields": [],
            "field_rules": [],
            "row_filters": [],
        }
        self.assertFalse(can_sql_compare(_db("sqlite"), sheet, port_map={}))
        self.assertTrue(can_sql_compare(_db("postgresql"), sheet, port_map={}))

    def test_rejects_port_map(self) -> None:
        sheet = {
            "metric_id": "lldp_neighbor",
            "key_fields": ["local_if", "remote_sys"],
            "compare_fields": [],
            "iface_fields": ["local_if"],
            "ignore_port_changes": False,
            "field_rules": [],
            "row_filters": [],
        }
        self.assertFalse(
            can_sql_compare(_db(), sheet, port_map={"gei-0/1": "gei-0/2"})
        )

    def test_rejects_auto_ignore_ports_with_iface_key(self) -> None:
        sheet = {
            "metric_id": "lldp_neighbor",
            "key_fields": ["local_if", "remote_sys"],
            "compare_fields": [],
            "iface_fields": ["local_if"],
            "ignore_port_changes": None,
            "field_rules": [],
            "row_filters": [],
        }
        self.assertFalse(can_sql_compare(_db(), sheet, port_map={}))

    def test_rejects_mac_normalize(self) -> None:
        sheet = {
            "metric_id": "arp",
            "key_fields": ["ip", "vrf"],
            "compare_fields": ["mac"],
            "iface_fields": [],
            "field_rules": [{"field": "mac", "normalize": "mac"}],
            "row_filters": [],
        }
        self.assertFalse(can_sql_compare(_db(), sheet, port_map={}))

    def test_rejects_age_timer_filter(self) -> None:
        sheet = {
            "metric_id": "arp",
            "key_fields": ["ip"],
            "compare_fields": [],
            "iface_fields": [],
            "field_rules": [],
            "row_filters": [{"field": "age", "op": "age_timer"}],
        }
        self.assertFalse(can_sql_compare(_db(), sheet, port_map={}))

    def test_accepts_bgp_route_style(self) -> None:
        sheet = {
            "metric_id": "bgp_route",
            "key_fields": [
                "local_as",
                "afi",
                "vrf",
                "neighbor",
                "direction",
                "rd",
                "network",
                "next_hop",
            ],
            "compare_fields": ["path", "as_num", "pfx_rcd"],
            "iface_fields": [],
            "field_rules": [
                {"field": "pfx_rcd", "compare": "percent", "tolerance": 5},
            ],
            "row_filters": [
                {"field": "vrf", "op": "empty"},
                {"field": "afi", "op": "eq", "value": "ipv4"},
            ],
        }
        self.assertTrue(can_sql_compare(_db(), sheet, port_map={}))
        # BGP afi/vrf sheet splits + percent rules must not force Python
        self.assertEqual(sql_compare_skip_reason(_db(), sheet, port_map={}), "")

    def test_rejects_iface_normalize_when_key_uses_iface(self) -> None:
        sheet = {
            "metric_id": "interface_brief",
            "key_fields": ["interface"],
            "compare_fields": ["admin"],
            "iface_fields": ["interface"],
            "ignore_port_changes": False,
            "field_rules": [],
            "row_filters": [],
        }
        self.assertFalse(
            can_sql_compare(
                _db(),
                sheet,
                port_map={},
                iface_normalize_rules=[{"from": "GE", "to": "gei"}],
            )
        )

    def test_accepts_ignore_port_changes_false_without_normalize(self) -> None:
        sheet = {
            "metric_id": "interface_brief",
            "key_fields": ["interface"],
            "compare_fields": ["admin"],
            "iface_fields": ["interface"],
            "ignore_port_changes": False,
            "field_rules": [],
            "row_filters": [],
        }
        self.assertTrue(can_sql_compare(_db(), sheet, port_map={}, iface_normalize_rules=[]))


class CompareSqlFilterCompileTests(unittest.TestCase):
    def test_empty_filters(self) -> None:
        sql, params = compile_row_filters_sql([])
        self.assertEqual(sql, "TRUE")
        self.assertEqual(params, {})

    def test_eq_case_insensitive(self) -> None:
        sql, params = compile_row_filters_sql(
            [{"field": "afi", "op": "eq", "value": "IPv4"}]
        )
        self.assertIn("lower(", sql)
        self.assertIn("afi", sql)
        self.assertEqual(list(params.values()), ["ipv4"])

    def test_contains(self) -> None:
        sql, params = compile_row_filters_sql(
            [{"field": "af", "op": "contains", "value": "IPv4"}]
        )
        self.assertIn("LIKE", sql)
        self.assertEqual(list(params.values()), ["%ipv4%"])

    def test_any_all_nesting(self) -> None:
        sql, params = compile_row_filters_sql(
            [
                {
                    "any": [
                        {"field": "entry_type", "op": "eq", "value": "dynamic"},
                        {
                            "all": [
                                {"field": "entry_type", "op": "empty"},
                                {"field": "ip", "op": "not_empty"},
                            ]
                        },
                    ]
                }
            ]
        )
        self.assertIn(" OR ", sql)
        self.assertIn(" AND ", sql)
        self.assertTrue(_filters_sql_compatible([{"any": [{"field": "a", "op": "eq", "value": "1"}]}]))

    def test_in_list(self) -> None:
        sql, params = compile_row_filters_sql(
            [{"field": "afi", "op": "in", "value": ["ipv4", "ipv6"]}]
        )
        self.assertIn(" IN (", sql)
        self.assertEqual(sorted(params.values()), ["ipv4", "ipv6"])

    def test_field_rules_matrix(self) -> None:
        self.assertTrue(_field_rules_sql_compatible([{"field": "mac", "normalize": "lower"}]))
        self.assertFalse(_field_rules_sql_compatible([{"field": "mac", "normalize": "mac"}]))
        self.assertTrue(
            _field_rules_sql_compatible(
                [{"field": "rx", "compare": "numeric", "tolerance": 1}]
            )
        )
        self.assertTrue(
            _field_rules_sql_compatible(
                [{"field": "pfx_rcd", "compare": "percent", "tolerance": 5}]
            )
        )
        self.assertTrue(
            _field_rules_sql_compatible([{"field": "x", "compare": "ignore"}])
        )


if __name__ == "__main__":
    unittest.main()
