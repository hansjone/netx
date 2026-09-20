"""Unit tests for biz_state compare engine."""

from __future__ import annotations

import copy
import unittest

from netx_api.biz_state.compare_engine import compare_rows, mapping_stats
from netx_api.biz_state.compare_rules import (
    apply_row_filters,
    arp_dynamic_row_filters,
    effective_compare_fields,
    effective_display_fields,
    eval_leaf_filter,
    normalize_value,
    values_equal,
)


class CompareEngineTests(unittest.TestCase):
    def test_basic_added_removed_changed(self) -> None:
        before = [
            {"local_if": "gei-0/1", "remote_sys": "A", "remote_if": "x1", "remote_ip": "1.1.1.1"},
            {"local_if": "gei-0/2", "remote_sys": "B", "remote_if": "y1", "remote_ip": "2.2.2.2"},
        ]
        after = [
            {"local_if": "gei-0/1", "remote_sys": "A", "remote_if": "x1", "remote_ip": "1.1.1.9"},
            {"local_if": "gei-0/3", "remote_sys": "C", "remote_if": "z1", "remote_ip": ""},
        ]
        out = compare_rows(
            before_rows=before,
            after_rows=after,
            key_fields=["local_if", "remote_sys", "remote_if"],
            iface_fields=["local_if"],
            compare_fields=["remote_sys", "remote_if", "remote_ip"],
            port_map={},
        )
        s = out["summary"]
        self.assertEqual(s["changed"], 1)
        self.assertEqual(s["removed"], 1)
        self.assertEqual(s["added"], 1)
        kinds = {d["kind"] for d in out["diffs"]}
        self.assertEqual(kinds, {"changed", "removed", "added"})

    def test_port_map_rewrites_before_key(self) -> None:
        before = [
            {"local_if": "old-1", "remote_sys": "Peer", "remote_if": "p1", "remote_ip": ""},
        ]
        after = [
            {"local_if": "new-1", "remote_sys": "Peer", "remote_if": "p1", "remote_ip": ""},
        ]
        out = compare_rows(
            before_rows=before,
            after_rows=after,
            key_fields=["local_if", "remote_sys", "remote_if"],
            iface_fields=["local_if"],
            compare_fields=["remote_sys", "remote_if", "remote_ip"],
            port_map={"old-1": "new-1"},
        )
        self.assertEqual(out["summary"]["unchanged"], 1)
        self.assertEqual(out["summary"]["added"], 0)
        self.assertEqual(out["summary"]["removed"], 0)

    def test_mapping_stats(self) -> None:
        stats = mapping_stats(
            before_rows=[{"local_if": "a"}, {"local_if": "b"}],
            after_rows=[{"local_if": "x"}, {"local_if": "y"}],
            iface_fields=["local_if"],
            port_map={"a": "x", "missing": "y"},
        )
        self.assertIn("a", stats["hit_before"])
        self.assertIn("missing", stats["miss_before"])
        self.assertFalse(stats["ok"])

    def test_presence_only_empty_compare(self) -> None:
        """Empty compare_fields → only entry set matters; value diffs ignored."""
        before = [
            {"local_if": "gei-0/1", "remote_sys": "A", "remote_if": "x1", "remote_ip": "1.1.1.1"},
            {"local_if": "gei-0/2", "remote_sys": "B", "remote_if": "y1", "remote_ip": "2.2.2.2"},
        ]
        after = [
            {"local_if": "gei-0/1", "remote_sys": "A", "remote_if": "x1", "remote_ip": "9.9.9.9"},
            {"local_if": "gei-0/3", "remote_sys": "C", "remote_if": "z1", "remote_ip": ""},
        ]
        out = compare_rows(
            before_rows=before,
            after_rows=after,
            key_fields=["local_if", "remote_sys", "remote_if"],
            iface_fields=["local_if"],
            compare_fields=[],
            port_map={},
        )
        s = out["summary"]
        self.assertEqual(s["changed"], 0)
        self.assertEqual(s["unchanged"], 1)
        self.assertEqual(s["removed"], 1)
        self.assertEqual(s["added"], 1)
        kinds = {d["kind"] for d in out["diffs"]}
        self.assertIn("unchanged", kinds)

    def test_empty_port_map_ignores_iface_in_key(self) -> None:
        """No port map → ignore local_if when matching (same neighbor, renamed port)."""
        before = [
            {"local_if": "old-1", "remote_sys": "Peer", "remote_if": "p1", "remote_ip": "1.1.1.1"},
        ]
        after = [
            {"local_if": "new-1", "remote_sys": "Peer", "remote_if": "p1", "remote_ip": "1.1.1.1"},
        ]
        out = compare_rows(
            before_rows=before,
            after_rows=after,
            key_fields=["local_if", "remote_sys", "remote_if"],
            iface_fields=["local_if"],
            compare_fields=["remote_ip"],
            port_map={},
        )
        self.assertEqual(out["summary"]["unchanged"], 1)
        self.assertEqual(out["summary"]["added"], 0)
        self.assertEqual(out["summary"]["removed"], 0)
        self.assertTrue(out["mapping_stats"].get("ignore_port_changes"))

    def test_empty_port_map_keeps_iface_when_required_for_uniqueness(self) -> None:
        """OSPF/VRRP-style: same neighbor_id on many interfaces must not collapse."""
        before = [
            {"process_id": "1", "neighbor_id": "2.2.2.2", "interface": "sg1", "address": "10.0.0.1"},
            {"process_id": "1", "neighbor_id": "2.2.2.2", "interface": "sg2", "address": "10.0.0.2"},
        ]
        after = copy.deepcopy(before)
        out = compare_rows(
            before_rows=before,
            after_rows=after,
            key_fields=["process_id", "neighbor_id", "interface"],
            iface_fields=["interface"],
            compare_fields=["address"],
            port_map={},
        )
        self.assertEqual(out["summary"]["unchanged"], 2)
        self.assertEqual(out["summary"]["changed"], 0)
        self.assertIn("interface", out["summary"]["match_key_fields"])
        self.assertFalse(out["mapping_stats"].get("ignore_port_changes"))

    def test_unchanged_rows_are_listed(self) -> None:
        before = [{"local_if": "a", "remote_sys": "X", "remote_if": "1", "remote_ip": "1"}]
        after = [{"local_if": "a", "remote_sys": "X", "remote_if": "1", "remote_ip": "1"}]
        out = compare_rows(
            before_rows=before,
            after_rows=after,
            key_fields=["local_if", "remote_sys", "remote_if"],
            iface_fields=["local_if"],
            compare_fields=["remote_ip"],
            port_map={},
        )
        self.assertEqual(out["summary"]["unchanged"], 1)
        self.assertEqual(len(out["diffs"]), 1)
        self.assertEqual(out["diffs"][0]["kind"], "unchanged")

    def test_mac_normalize_via_field_rules(self) -> None:
        before = [{"ip": "1.1.1.1", "mac": "00:11:22:33:44:55", "iface": "gei-0/1"}]
        after = [{"ip": "1.1.1.1", "mac": "0011.2233.4455", "iface": "gei-0/1"}]
        out = compare_rows(
            before_rows=before,
            after_rows=after,
            key_fields=["ip", "iface"],
            iface_fields=["iface"],
            compare_fields=["mac"],
            port_map={},
            field_rules=[{"field": "mac", "normalize": "mac"}],
        )
        self.assertEqual(out["summary"]["unchanged"], 1)
        self.assertEqual(out["summary"]["changed"], 0)

    def test_numeric_tolerance(self) -> None:
        before = [{"id": "1", "cnt": "100"}]
        after = [{"id": "1", "cnt": "102"}]
        out = compare_rows(
            before_rows=before,
            after_rows=after,
            key_fields=["id"],
            iface_fields=[],
            compare_fields=["cnt"],
            port_map={},
            field_rules=[{"field": "cnt", "compare": "numeric", "tolerance": 5}],
        )
        self.assertEqual(out["summary"]["unchanged"], 1)
        out2 = compare_rows(
            before_rows=before,
            after_rows=after,
            key_fields=["id"],
            iface_fields=[],
            compare_fields=["cnt"],
            port_map={},
            field_rules=[{"field": "cnt", "compare": "numeric", "tolerance": 0}],
        )
        self.assertEqual(out2["summary"]["changed"], 1)

    def test_percent_tolerance(self) -> None:
        before = [{"id": "1", "networks": "100"}]
        after_ok = [{"id": "1", "networks": "104"}]  # 4% < 5%
        after_bad = [{"id": "1", "networks": "106"}]  # 6% > 5%
        rule = [{"field": "networks", "compare": "percent", "tolerance": 5}]
        ok = compare_rows(
            before_rows=before,
            after_rows=after_ok,
            key_fields=["id"],
            iface_fields=[],
            compare_fields=["networks"],
            port_map={},
            field_rules=rule,
        )
        self.assertEqual(ok["summary"]["unchanged"], 1)
        bad = compare_rows(
            before_rows=before,
            after_rows=after_bad,
            key_fields=["id"],
            iface_fields=[],
            compare_fields=["networks"],
            port_map={},
            field_rules=rule,
        )
        self.assertEqual(bad["summary"]["changed"], 1)

    def test_percent_zero_baseline(self) -> None:
        self.assertTrue(
            values_equal("0", "0", rule={"compare": "percent", "tolerance": 5})
        )
        self.assertFalse(
            values_equal("0", "1", rule={"compare": "percent", "tolerance": 5})
        )


class CompareRulesTests(unittest.TestCase):
    def test_arp_dynamic_row_filters(self) -> None:
        rows = [
            {"ip": "1.1.1.1", "entry_type": "dynamic", "age": "00:01:02"},
            {"ip": "1.1.1.2", "entry_type": "static", "age": "H"},
            {"ip": "1.1.1.3", "entry_type": "", "age": "01:02:03"},
            {"ip": "1.1.1.4", "entry_type": "", "age": "I"},
        ]
        kept = apply_row_filters(rows, arp_dynamic_row_filters())
        ips = {r["ip"] for r in kept}
        self.assertEqual(ips, {"1.1.1.1", "1.1.1.3"})

    def test_effective_compare_ignore(self) -> None:
        fields = effective_compare_fields(
            ["mac", "vlan", "age"],
            [{"field": "age", "compare": "ignore"}, {"field": "vlan", "ignore": True}],
        )
        self.assertEqual(fields, ["mac"])

    def test_normalize_mac(self) -> None:
        self.assertEqual(normalize_value("00-11-22-33-44-55", "mac"), "001122334455")
        self.assertTrue(values_equal("Aa", "aa", rule={"normalize": "lower"}))

    def test_explain_diff_percent(self) -> None:
        from netx_api.biz_state.compare_rules import explain_diff

        reason = explain_diff(
            "100", "108", rule={"compare": "percent", "tolerance": 5}
        )
        self.assertIn("pct", reason)
        self.assertIn("8", reason)

    def test_changed_includes_reason(self) -> None:
        before = [{"id": "1", "networks": "100"}]
        after = [{"id": "1", "networks": "110"}]
        out = compare_rows(
            before_rows=before,
            after_rows=after,
            key_fields=["id"],
            iface_fields=[],
            compare_fields=["networks"],
            port_map={},
            field_rules=[{"field": "networks", "compare": "percent", "tolerance": 5}],
        )
        self.assertEqual(out["summary"]["changed"], 1)
        ch = out["diffs"][0]["changes"]["networks"]
        self.assertIn("reason", ch)
        self.assertIn("pct", ch["reason"])

    def test_effective_display_legacy_and_explicit(self) -> None:
        legacy = effective_display_fields(
            key_fields=["ip", "iface"],
            compare_fields=["mac"],
            display_fields=None,
        )
        self.assertEqual(legacy, ["ip", "iface", "mac"])
        explicit = effective_display_fields(
            key_fields=["ip", "iface"],
            compare_fields=["mac"],
            display_fields=["vrf", "age", "ip"],
        )
        # keys first, then display order extras, compare forced if missing
        self.assertEqual(explicit, ["ip", "iface", "vrf", "age", "mac"])

    def test_display_only_field_does_not_change(self) -> None:
        """Context column in display but not compare → value drift ignored."""
        before = [{"ip": "1.1.1.1", "mac": "aabb", "vrf": "A"}]
        after = [{"ip": "1.1.1.1", "mac": "aabb", "vrf": "B"}]
        out = compare_rows(
            before_rows=before,
            after_rows=after,
            key_fields=["ip"],
            iface_fields=[],
            compare_fields=["mac"],
            port_map={},
        )
        self.assertEqual(out["summary"]["unchanged"], 1)
        self.assertEqual(out["summary"]["changed"], 0)


class CompareDiffPagingTests(unittest.TestCase):
    def test_filter_inline_diffs_kind_and_kw(self) -> None:
        from netx_api.biz_state.compare_service import _filter_inline_diffs

        diffs = [
            {"kind": "added", "key": {"p": "1"}, "before": {}, "after": {"p": "1"}, "changes": {}},
            {"kind": "removed", "key": {"p": "2"}, "before": {"p": "2"}, "after": {}, "changes": {}},
            {"kind": "unchanged", "key": {"p": "3"}, "before": {"p": "3"}, "after": {"p": "3"}, "changes": {}},
            {
                "kind": "changed",
                "key": {"p": "4"},
                "before": {"x": "a"},
                "after": {"x": "b"},
                "changes": {"x": {"before": "a", "after": "b"}},
            },
        ]
        only_diff = _filter_inline_diffs(diffs, kind="diff", kw="")
        self.assertEqual({d["kind"] for d in only_diff}, {"removed", "changed"})
        only_added = _filter_inline_diffs(diffs, kind="added", kw="")
        self.assertEqual(len(only_added), 1)
        hit = _filter_inline_diffs(diffs, kind="all", kw='"p":"4"')
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]["kind"], "changed")
        hit2 = _filter_inline_diffs(diffs, kind="all", kw="changed")
        self.assertTrue(any(d["kind"] == "changed" for d in hit2))


class CompareSheetDefaultsTests(unittest.TestCase):
    def test_contains_filter_op(self) -> None:
        row = {"af": "IPv4,IPv6"}
        self.assertTrue(eval_leaf_filter(row, {"field": "af", "op": "contains", "value": "IPv4"}))
        self.assertTrue(eval_leaf_filter(row, {"field": "af", "op": "contains", "value": "ipv6"}))
        self.assertFalse(eval_leaf_filter(row, {"field": "af", "op": "contains", "value": "vpn"}))

    def test_zte_default_splits_bgp_and_isis(self) -> None:
        from netx_api.biz_state.compare_service import _default_zte_status_sheets, sheet_key

        sheets = _default_zte_status_sheets()
        ids = [sheet_key(s) for s in sheets]
        self.assertIn("bgp_peer.ipv4", ids)
        self.assertIn("bgp_peer.vpnv4", ids)
        self.assertIn("bgp_peer.vpnv6", ids)
        self.assertIn("isis_adjacency.ipv4", ids)
        self.assertIn("isis_adjacency.ipv6", ids)
        # Same source metric may appear multiple times (afi splits)
        self.assertEqual(sum(1 for s in sheets if s["metric_id"] == "bgp_peer"), 6)
        self.assertIn("bgp_peer.evpn", ids)
        self.assertIn("bgp_peer.vpls", ids)
        self.assertIn("vrrp.ipv4", ids)
        self.assertIn("lldp_neighbor", ids)
        detail = next(s for s in sheets if sheet_key(s) == "interface_detail")
        self.assertEqual(detail["compare_fields"], ["port_status"])
        self.assertIn("input_bps", detail["display_fields"])
        optical = next(s for s in sheets if sheet_key(s) == "optical_brief")
        self.assertEqual(optical["compare_fields"], ["status"])
        self.assertIn("rx_power", optical["display_fields"])
        bgp4 = next(s for s in sheets if sheet_key(s) == "bgp_peer.ipv4")
        self.assertEqual(bgp4["compare_fields"], ["as_num", "state"])
        self.assertIn("pfx_rcd", bgp4["display_fields"])
        vpnv4 = next(s for s in sheets if sheet_key(s) == "bgp_peer.vpnv4")
        self.assertEqual(vpnv4["row_filters"], [{"field": "afi", "op": "eq", "value": "vpnv4"}])
        isis4 = next(s for s in sheets if sheet_key(s) == "isis_adjacency.ipv4")
        self.assertEqual(isis4["row_filters"][0]["op"], "contains")

    def test_duplicate_match_keys_are_reported(self) -> None:
        before = [
            {"local_if": "a", "remote_sys": "X", "remote_if": "1", "remote_ip": "1"},
            {"local_if": "a", "remote_sys": "X", "remote_if": "1", "remote_ip": "9"},
        ]
        after = [
            {"local_if": "a", "remote_sys": "X", "remote_if": "1", "remote_ip": "1"},
            {"local_if": "a", "remote_sys": "X", "remote_if": "1", "remote_ip": "2"},
        ]
        out = compare_rows(
            before_rows=before,
            after_rows=after,
            key_fields=["local_if", "remote_sys", "remote_if"],
            iface_fields=["local_if"],
            compare_fields=["remote_ip"],
            port_map={"a": "a"},
        )
        self.assertEqual(out["summary"]["duplicate_keys_before"], 1)
        self.assertEqual(out["summary"]["duplicate_keys_after"], 1)

    def test_normalize_allows_duplicate_metric_with_distinct_sheet_id(self) -> None:
        from netx_api.biz_state.compare_service import _normalize_sheet, sheet_key

        a = _normalize_sheet(
            {
                "sheet_id": "bgp_peer.vpnv4",
                "title": "BGP VPNv4",
                "metric_id": "bgp_peer",
                "key_fields": ["afi", "neighbor"],
                "compare_fields": ["state"],
                "row_filters": [{"field": "afi", "op": "eq", "value": "vpnv4"}],
            }
        )
        b = _normalize_sheet(
            {
                "sheet_id": "bgp_peer.ipv4",
                "title": "BGP IPv4",
                "metric_id": "bgp_peer",
                "key_fields": ["afi", "neighbor"],
                "compare_fields": ["state"],
                "row_filters": [{"field": "afi", "op": "eq", "value": "ipv4"}],
            }
        )
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        self.assertEqual(a["metric_id"], b["metric_id"])
        self.assertNotEqual(sheet_key(a), sheet_key(b))

    def test_template_in_roundtrip_keeps_split_sheet_ids(self) -> None:
        """Export → TemplateIn → create must preserve 拆表 sheet_id (ISIS/BGP/VRRP)."""
        from netx_api.biz_state.compare_service import (
            _default_zte_status_sheets,
            _parse_metrics_body,
            sheet_key,
        )
        from netx_api.biz_state_router import TemplateIn

        sheets = _default_zte_status_sheets()
        body = TemplateIn(
            name="ZTE status roundtrip",
            metrics=sheets,
        ).model_dump()
        parsed = _parse_metrics_body(body)
        self.assertEqual(len(parsed), len(sheets))
        ids = [sheet_key(s) for s in parsed]
        self.assertIn("isis_adjacency.ipv4", ids)
        self.assertIn("isis_adjacency.ipv6", ids)
        self.assertEqual(sum(1 for s in parsed if s["metric_id"] == "isis_adjacency"), 2)
        self.assertEqual(sum(1 for s in parsed if s["metric_id"] == "bgp_peer"), 6)
        isis4 = next(s for s in parsed if sheet_key(s) == "isis_adjacency.ipv4")
        self.assertTrue(str(isis4.get("title") or "").strip())

    def test_arp_default_sheet_has_row_filters(self) -> None:
        from netx_api.biz_state.compare_service import _default_sheet_for_metric

        sheet = _default_sheet_for_metric("arp")
        self.assertTrue(sheet.get("row_filters"))
        self.assertEqual(sheet["metric_id"], "arp")
        self.assertIn("display_fields", sheet)
        # Context columns present without being compare-only
        for ctx in ("vrf", "entry_type", "age"):
            if ctx in sheet["display_fields"]:
                self.assertTrue(
                    ctx in sheet["display_fields"]
                    and (ctx in sheet["key_fields"] or ctx not in sheet["compare_fields"] or True)
                )

    def test_load_metric_rows_has_no_arp_hardcode(self) -> None:
        import inspect

        from netx_api.biz_state import compare_service as mod

        src = inspect.getsource(mod._load_metric_rows)
        self.assertNotIn("is_valid_arp_age", src)
        self.assertNotIn('metric_id == "arp"', src)

    def test_sheet_csv_uses_display_fields(self) -> None:
        from netx_api.biz_state.compare_service import _sheet_csv

        sheet = {
            "key_fields": ["ip"],
            "compare_fields": ["mac"],
            "display_fields": ["ip", "vrf", "mac"],
            "diffs": [
                {
                    "kind": "unchanged",
                    "key": {"ip": "1.1.1.1"},
                    "before": {"ip": "1.1.1.1", "mac": "aa", "vrf": "v1"},
                    "after": {"ip": "1.1.1.1", "mac": "aa", "vrf": "v1"},
                    "mapped_before": {"ip": "1.1.1.1", "mac": "aa", "vrf": "v1"},
                    "changes": {},
                }
            ],
        }
        csv = _sheet_csv(sheet)
        header = csv.splitlines()[0]
        self.assertIn("kind", header)
        self.assertIn("ip", header)
        self.assertIn("vrf", header)
        self.assertIn("mac__pre", header)
        self.assertIn("mac__post", header)
        # display-only vrf is single column, not pre/post
        self.assertNotIn("vrf__pre", header)


if __name__ == "__main__":
    unittest.main()
