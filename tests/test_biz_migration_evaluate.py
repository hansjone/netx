"""Unit tests for cutover migration evaluate helpers (port status focus)."""

from __future__ import annotations

import unittest

from netx_api.biz_migration.evaluate import (
    KEY_SEP,
    classify_status,
    dual_verdict,
    evaluate_metric_dual,
    expect_keys_for_metric,
    parse_expect_set,
    port_sheet_def,
    port_status_label,
    side_verdict,
    _remap_key_str,
)


PORT_OVERRIDE = {
    "metric_id": "interface_brief",
    "status_fields": ["admin", "phy", "prot"],
    "down_values": ["down"],
    "up_values": ["up"],
    "success": [{"old": ["removed", "down"], "new": ["added", "up", "unchanged"]}],
}


class ParseExpectSetTests(unittest.TestCase):
    def test_items_sheet_id_bucket(self):
        got = parse_expect_set(
            {
                "items": [
                    {"metric_id": "bgp_peer", "sheet_id": "bgp_peer.vpnv4", "key": "1.1.1.1"},
                    {"metric_id": "bgp_peer", "sheet_id": "bgp_peer.ipv4", "key": "2.2.2.2"},
                ]
            }
        )
        self.assertEqual(got["bgp_peer.vpnv4"], {"1.1.1.1"})
        self.assertEqual(got["bgp_peer.ipv4"], {"2.2.2.2"})
        self.assertNotIn("bgp_peer", got)

    def test_expect_keys_prefer_sheet_id(self):
        expect = parse_expect_set(
            {
                "items": [
                    {"metric_id": "bgp_peer", "sheet_id": "bgp_peer.vpnv4", "key": "1.1.1.1"},
                    {"metric_id": "bgp_peer", "key": "9.9.9.9"},
                ]
            }
        )
        self.assertEqual(
            expect_keys_for_metric(
                expect, metric_id="bgp_peer", iface_fields=[], sheet_id="bgp_peer.vpnv4"
            ),
            {"1.1.1.1"},
        )
        self.assertEqual(
            expect_keys_for_metric(expect, metric_id="bgp_peer", iface_fields=[]),
            {"9.9.9.9"},
        )

    def test_override_for_sheet_prefers_sheet_id(self):
        from netx_api.biz_migration.evaluate import override_for_sheet

        overrides = [
            {"metric_id": "bgp_peer", "status_fields": ["state"]},
            {"metric_id": "bgp_peer", "sheet_id": "bgp_peer.vpnv4", "status_fields": ["state", "pfx_rcd"]},
        ]
        hit = override_for_sheet(
            overrides, sheet_id="bgp_peer.vpnv4", metric_id="bgp_peer"
        )
        self.assertEqual(hit["status_fields"], ["state", "pfx_rcd"])
        legacy = override_for_sheet(
            overrides, sheet_id="bgp_peer.ipv4", metric_id="bgp_peer"
        )
        self.assertEqual(legacy["status_fields"], ["state"])

    def test_ports_go_to_interface_brief(self):
        got = parse_expect_set({"ports": ["gei-1", "gei-2", ""]})
        self.assertEqual(got["interface_brief"], {"gei-1", "gei-2"})
        self.assertEqual(got["_ports"], {"gei-1", "gei-2"})

    def test_items_multi_metric(self):
        got = parse_expect_set(
            {
                "items": [
                    {"metric_id": "bgp_peer", "keys": ["AS1", "1.1.1.1"]},
                    {"metric_id": "arp", "key": "10.0.0.1"},
                ]
            }
        )
        self.assertEqual(got["bgp_peer"], {KEY_SEP.join(["AS1", "1.1.1.1"])})
        self.assertEqual(got["arp"], {"10.0.0.1"})

    def test_multi_select_each_key_is_separate(self):
        """UI must send one item per selected row, not a flat keys list."""
        got = parse_expect_set(
            {
                "items": [
                    {"metric_id": "bgp_peer", "key": "1.1.1.1"},
                    {"metric_id": "bgp_peer", "key": "2.2.2.2"},
                    {"metric_id": "arp", "key": "10.0.0.1"},
                ]
            }
        )
        self.assertEqual(got["bgp_peer"], {"1.1.1.1", "2.2.2.2"})
        self.assertEqual(got["arp"], {"10.0.0.1"})

    def test_flat_keys_list_is_one_composite(self):
        got = parse_expect_set(
            {"items": [{"metric_id": "bgp_peer", "keys": ["1.1.1.1", "2.2.2.2"]}]}
        )
        self.assertEqual(got["bgp_peer"], {KEY_SEP.join(["1.1.1.1", "2.2.2.2"])})

    def test_nested_keys_list(self):
        got = parse_expect_set(
            {
                "items": [
                    {"metric_id": "isis_adjacency", "keys": [["p1", "gei-1", "sys1"], ["p1", "gei-2", "sys2"]]},
                    {"metric_id": "arp", "key": ["10.0.0.1", "vrf1"]},
                ]
            }
        )
        self.assertEqual(
            got["isis_adjacency"],
            {
                KEY_SEP.join(["p1", "gei-1", "sys1"]),
                KEY_SEP.join(["p1", "gei-2", "sys2"]),
            },
        )
        self.assertEqual(got["arp"], {KEY_SEP.join(["10.0.0.1", "vrf1"])})

    def test_legacy_pipe_key_normalized(self):
        got = parse_expect_set(
            {"items": [{"metric_id": "bgp_peer", "key": "AS1|1.1.1.1"}]}
        )
        self.assertEqual(got["bgp_peer"], {KEY_SEP.join(["AS1", "1.1.1.1"])})

    def test_ports_only_apply_to_interface_brief(self):
        expect = parse_expect_set({"ports": ["gei-1"]})
        self.assertEqual(
            expect_keys_for_metric(expect, metric_id="interface_brief", iface_fields=["interface"]),
            {"gei-1"},
        )
        self.assertEqual(
            expect_keys_for_metric(expect, metric_id="arp", iface_fields=["interface"]),
            set(),
        )


class PortSheetTests(unittest.TestCase):
    def test_port_sheet_fields(self):
        s = port_sheet_def()
        self.assertEqual(s["metric_id"], "interface_brief")
        self.assertEqual(s["key_fields"], ["interface"])
        self.assertEqual(s["compare_fields"], ["admin", "phy", "prot"])

    def test_status_label(self):
        self.assertEqual(
            port_status_label({"admin": "up", "phy": "up", "prot": "up"}),
            "up/up/up",
        )
        self.assertEqual(port_status_label({}), "—")


class ClassifyStatusTests(unittest.TestCase):
    def test_up_down(self):
        self.assertEqual(
            classify_status({"admin": "up", "phy": "up", "prot": "up"}, PORT_OVERRIDE),
            "up",
        )
        self.assertEqual(
            classify_status({"admin": "up", "phy": "down", "prot": "up"}, PORT_OVERRIDE),
            "down",
        )
        self.assertEqual(classify_status({}, PORT_OVERRIDE), "none")
        self.assertEqual(classify_status({"admin": "up"}, None), "none")


class VerdictTests(unittest.TestCase):
    def test_side_anomaly_gone(self):
        self.assertEqual(
            side_verdict(kind="removed", in_expect=False, window_active=True),
            ("anomaly_gone", "red"),
        )
        self.assertEqual(
            side_verdict(kind="removed", in_expect=True, window_active=True),
            ("expected_gone", "yellow"),
        )

    def test_dual_migrated(self):
        self.assertEqual(
            dual_verdict(old_kind="removed", new_kind="added", in_expect=True, window_active=True),
            ("migrated", "green"),
        )

    def test_dual_lost(self):
        self.assertEqual(
            dual_verdict(old_kind="removed", new_kind="", in_expect=True, window_active=False),
            ("lost", "red"),
        )

    def test_acceptance_unfinished_is_red(self):
        self.assertEqual(
            dual_verdict(
                old_kind="unchanged",
                new_kind="",
                in_expect=True,
                window_active=False,
                acceptance=True,
            ),
            ("unfinished", "red"),
        )
        self.assertEqual(
            dual_verdict(
                old_kind="unchanged",
                new_kind="",
                in_expect=True,
                window_active=True,
                acceptance=False,
            ),
            ("migrating", "yellow"),
        )

    def test_dual_down_up_via_success_patterns(self):
        self.assertEqual(
            dual_verdict(
                old_kind="changed",
                new_kind="changed",
                in_expect=True,
                window_active=True,
                old_status="down",
                new_status="up",
                success_patterns=PORT_OVERRIDE["success"],
            ),
            ("migrated", "green"),
        )

    def test_out_of_expect_ignore(self):
        self.assertEqual(
            dual_verdict(
                old_kind="removed",
                new_kind="",
                in_expect=False,
                window_active=False,
                out_of_expect="ignore",
            ),
            ("not_involved", "gray"),
        )

    def test_out_of_expect_warn_is_yellow(self):
        self.assertEqual(
            dual_verdict(
                old_kind="removed",
                new_kind="unchanged",
                in_expect=False,
                window_active=False,
                out_of_expect="warn",
            ),
            ("anomaly", "yellow"),
        )

    def test_out_of_expect_new_side_only_removed(self):
        self.assertEqual(
            dual_verdict(
                old_kind="unchanged",
                new_kind="removed",
                in_expect=False,
                window_active=False,
                out_of_expect="strict",
            ),
            ("anomaly", "red"),
        )
        self.assertEqual(
            dual_verdict(
                old_kind="unchanged",
                new_kind="removed",
                in_expect=False,
                window_active=False,
                out_of_expect="warn",
            ),
            ("anomaly", "yellow"),
        )

    def test_rules_mode_skips_auto_migrate(self):
        """With success patterns configured, bare kind migrate is not invented."""
        verdict, color = dual_verdict(
            old_kind="removed",
            new_kind="added",
            in_expect=True,
            window_active=True,
            success_patterns=[
                {
                    "old_groups": [[{"type": "presence", "value": "removed"}]],
                    "new_groups": [
                        [
                            {"type": "presence", "value": "added"},
                            {"type": "value", "field": "state", "op": "eq", "value": "up"},
                        ]
                    ],
                }
            ],
            new_row={"state": "down"},
        )
        self.assertNotEqual(verdict, "migrated")
        self.assertEqual(color, "yellow")

    def test_rules_mode_skips_catch_all_anomaly(self):
        """With anomaly list present, mid-cutover stays migrating not invented anomaly."""
        self.assertEqual(
            dual_verdict(
                old_kind="changed",
                new_kind="unchanged",
                in_expect=True,
                window_active=True,
                success_patterns=[
                    {
                        "old_groups": [[{"type": "presence", "value": "removed"}]],
                        "new_groups": [[{"type": "presence", "value": "added"}]],
                    }
                ],
                anomaly_patterns=[
                    {
                        "old_groups": [[{"type": "presence", "value": "removed"}]],
                        "new_groups": [[{"type": "presence", "value": "removed"}]],
                    }
                ],
            ),
            ("migrating", "yellow"),
        )

    def test_field_token_success(self):
        self.assertEqual(
            dual_verdict(
                old_kind="changed",
                new_kind="changed",
                in_expect=True,
                window_active=True,
                old_status="other",
                new_status="other",
                success_patterns=[
                    {"old": ["field:state:idle"], "new": ["field:state:established"]}
                ],
                old_row={"state": "Idle"},
                new_row={"state": "Established"},
                sheet_override={"status_fields": ["state"]},
            ),
            ("migrated", "green"),
        )

    def test_structured_field_conds_all_mode(self):
        self.assertEqual(
            dual_verdict(
                old_kind="changed",
                new_kind="changed",
                in_expect=True,
                window_active=True,
                old_status="other",
                new_status="other",
                success_patterns=[
                    {
                        "old_mode": "all",
                        "new_mode": "all",
                        "old_conds": [
                            {"type": "kind", "value": "changed"},
                            {"type": "field", "field": "state", "op": "in", "value": ["idle", "active"]},
                        ],
                        "new_conds": [
                            {"type": "kind", "value": "changed"},
                            {"type": "field", "field": "state", "op": "eq", "value": "established"},
                        ],
                    }
                ],
                old_row={"state": "Idle"},
                new_row={"state": "Established"},
            ),
            ("migrated", "green"),
        )

    def test_structured_conds_fail_when_all_incomplete(self):
        verdict, _color = dual_verdict(
            old_kind="unchanged",
            new_kind="changed",
            in_expect=True,
            window_active=True,
            success_patterns=[
                {
                    "old_mode": "all",
                    "new_mode": "any",
                    "old_conds": [
                        {"type": "kind", "value": "unchanged"},
                        {"type": "field", "field": "state", "op": "eq", "value": "idle"},
                    ],
                    "new_conds": [{"type": "kind", "value": "changed"}],
                }
            ],
            old_row={"state": "Active"},
            new_row={"state": "Established"},
        )
        self.assertNotEqual(verdict, "migrated")

    def test_groups_or_of_and(self):
        """Within a group = AND; between groups = OR."""
        patterns = [
            {
                "old_groups": [
                    [{"type": "presence", "value": "removed"}],
                    [
                        {"type": "presence", "value": "changed"},
                        {"type": "value", "field": "state", "op": "in", "value": ["idle", "active"]},
                    ],
                ],
                "new_groups": [
                    [{"type": "value", "field": "state", "op": "eq", "value": "established"}],
                ],
            }
        ]
        self.assertEqual(
            dual_verdict(
                old_kind="removed",
                new_kind="changed",
                in_expect=True,
                window_active=True,
                success_patterns=patterns,
                old_row={},
                new_row={"state": "Established"},
            ),
            ("migrated", "green"),
        )
        self.assertEqual(
            dual_verdict(
                old_kind="changed",
                new_kind="changed",
                in_expect=True,
                window_active=True,
                success_patterns=patterns,
                old_row={"state": "Idle"},
                new_row={"state": "Established"},
            ),
            ("migrated", "green"),
        )
        verdict, _color = dual_verdict(
            old_kind="changed",
            new_kind="changed",
            in_expect=True,
            window_active=True,
            success_patterns=patterns,
            old_row={"state": "Established"},
            new_row={"state": "Established"},
        )
        self.assertNotEqual(verdict, "migrated")

    def test_value_changed_vs_baseline(self):
        self.assertEqual(
            dual_verdict(
                old_kind="unchanged",
                new_kind="changed",
                in_expect=True,
                window_active=True,
                success_patterns=[
                    {
                        "old_groups": [[{"type": "presence", "value": "unchanged"}]],
                        "new_groups": [
                            [{"type": "value", "field": "state", "op": "changed"}]
                        ],
                    }
                ],
                old_row={"state": "Idle"},
                new_row={"state": "Established"},
                old_base={"state": "Idle"},
                new_base={"state": "Idle"},
            ),
            ("migrated", "green"),
        )

    def test_screenshot_style_success(self):
        """Old removed OR state!=up; new added AND state=up."""
        patterns = [
            {
                "old_groups": [
                    [{"type": "presence", "value": "removed"}],
                    [{"type": "value", "field": "state", "op": "ne", "value": "up"}],
                ],
                "new_groups": [
                    [
                        {"type": "presence", "value": "added"},
                        {"type": "value", "field": "state", "op": "eq", "value": "up"},
                    ]
                ],
            }
        ]
        self.assertEqual(
            dual_verdict(
                old_kind="removed",
                new_kind="added",
                in_expect=True,
                window_active=True,
                success_patterns=patterns,
                old_row={},
                new_row={"state": "up"},
            ),
            ("migrated", "green"),
        )

    def test_anomaly_both_gone(self):
        self.assertEqual(
            dual_verdict(
                old_kind="removed",
                new_kind="removed",
                in_expect=True,
                window_active=True,
                success_patterns=[
                    {
                        "old_groups": [[{"type": "presence", "value": "removed"}]],
                        "new_groups": [[{"type": "presence", "value": "added"}]],
                    }
                ],
                anomaly_patterns=[
                    {
                        "old_groups": [[{"type": "presence", "value": "removed"}]],
                        "new_groups": [[{"type": "presence", "value": "removed"}]],
                    }
                ],
            ),
            ("anomaly", "red"),
        )

    def test_anomaly_new_side_dont_care_old(self):
        """Side-only anomaly is deferred while window is active (avoid false red)."""
        anomaly = [
            {
                "old_groups": [],
                "new_groups": [
                    [{"type": "presence", "value": "removed"}],
                    [
                        {
                            "type": "value",
                            "field": "state",
                            "op": "ne",
                            "value": "up",
                        }
                    ],
                ],
            }
        ]
        success = [
            {
                "old_groups": [[{"type": "presence", "value": "removed"}]],
                "new_groups": [[{"type": "presence", "value": "added"}]],
            }
        ]
        self.assertEqual(
            dual_verdict(
                old_kind="unchanged",
                new_kind="removed",
                in_expect=True,
                window_active=True,
                acceptance=False,
                success_patterns=success,
                anomaly_patterns=anomaly,
                new_row={},
            ),
            ("migrating", "yellow"),
        )
        self.assertEqual(
            dual_verdict(
                old_kind="unchanged",
                new_kind="removed",
                in_expect=True,
                window_active=False,
                acceptance=True,
                success_patterns=success,
                anomaly_patterns=anomaly,
                new_row={},
            ),
            ("anomaly", "red"),
        )

    def test_success_beats_anomaly(self):
        success = [
            {
                "old_groups": [[{"type": "presence", "value": "removed"}]],
                "new_groups": [[{"type": "presence", "value": "added"}]],
            }
        ]
        anomaly = [
            {
                "old_groups": [[{"type": "presence", "value": "removed"}]],
                "new_groups": [[{"type": "presence", "value": "added"}]],
            }
        ]
        self.assertEqual(
            dual_verdict(
                old_kind="removed",
                new_kind="added",
                in_expect=True,
                window_active=True,
                success_patterns=success,
                anomaly_patterns=anomaly,
            ),
            ("migrated", "green"),
        )


class EvaluateMetricDualTests(unittest.TestCase):
    def test_port_migration_happy_path(self):
        old_base = [{"interface": "gei-old", "admin": "up", "phy": "up", "prot": "up"}]
        old_cur: list[dict] = []
        new_cur = [{"interface": "gei-new", "admin": "up", "phy": "up", "prot": "up"}]
        expect = parse_expect_set({"ports": ["gei-old"]})
        out = evaluate_metric_dual(
            metric_id="interface_brief",
            key_fields=["interface"],
            iface_fields=["interface"],
            compare_fields=["admin", "phy", "prot"],
            old_baseline_rows=old_base,
            old_current_rows=old_cur,
            new_baseline_rows=None,
            new_current_rows=new_cur,
            port_map={"gei-old": "gei-new"},
            expect=expect,
            window_active=True,
            sheet_override=PORT_OVERRIDE,
        )
        self.assertEqual(out["progress_total"], 1)
        migrated = [r for r in out["rows"] if r["verdict"] == "migrated"]
        self.assertTrue(migrated, out["rows"])
        self.assertEqual(migrated[0]["color"], "green")
        self.assertEqual(migrated[0]["old_status"], "gone")
        self.assertEqual(migrated[0]["new_status"], "up/up/up")
        self.assertEqual(out["progress_ok"], 1)

    def test_port_down_to_up_is_migrated(self):
        old_base = [{"interface": "gei-old", "admin": "up", "phy": "up", "prot": "up"}]
        old_cur = [{"interface": "gei-old", "admin": "down", "phy": "down", "prot": "down"}]
        new_cur = [{"interface": "gei-new", "admin": "up", "phy": "up", "prot": "up"}]
        expect = parse_expect_set({"ports": ["gei-old"]})
        out = evaluate_metric_dual(
            metric_id="interface_brief",
            key_fields=["interface"],
            iface_fields=["interface"],
            compare_fields=["admin", "phy", "prot"],
            old_baseline_rows=old_base,
            old_current_rows=old_cur,
            new_baseline_rows=None,
            new_current_rows=new_cur,
            port_map={"gei-old": "gei-new"},
            expect=expect,
            window_active=True,
            sheet_override=PORT_OVERRIDE,
        )
        migrated = [r for r in out["rows"] if r["verdict"] == "migrated"]
        self.assertTrue(migrated, out["rows"])
        self.assertEqual(out["progress_ok"], 1)

    def test_bgp_presence_only_migrated(self):
        """Metrics without status_fields use kind-only dual (presence)."""
        old_base = [{"peer": "1.1.1.1", "state": "Established"}]
        old_cur: list[dict] = []
        new_cur = [{"peer": "1.1.1.1", "state": "Established"}]
        expect = parse_expect_set({"items": [{"metric_id": "bgp_peer", "key": "1.1.1.1"}]})
        out = evaluate_metric_dual(
            metric_id="bgp_peer",
            key_fields=["peer"],
            iface_fields=[],
            compare_fields=[],
            old_baseline_rows=old_base,
            old_current_rows=old_cur,
            new_baseline_rows=None,
            new_current_rows=new_cur,
            port_map={},
            expect=expect,
            window_active=True,
            sheet_override={},
        )
        migrated = [r for r in out["rows"] if r["verdict"] == "migrated"]
        self.assertTrue(migrated, out["rows"])

    def test_arp_presence_migrated(self):
        old_base = [{"ip": "10.0.0.1", "mac": "aaaa.bbbb.cccc"}]
        old_cur: list[dict] = []
        new_cur = [{"ip": "10.0.0.1", "mac": "aaaa.bbbb.cccc"}]
        expect = parse_expect_set({"items": [{"metric_id": "arp", "key": "10.0.0.1"}]})
        out = evaluate_metric_dual(
            metric_id="arp",
            key_fields=["ip"],
            iface_fields=[],
            compare_fields=[],
            old_baseline_rows=old_base,
            old_current_rows=old_cur,
            new_baseline_rows=None,
            new_current_rows=new_cur,
            port_map={},
            expect=expect,
            window_active=True,
            sheet_override={
                "metric_id": "arp",
                "success": [{"old": ["removed"], "new": ["added", "unchanged"]}],
            },
        )
        migrated = [r for r in out["rows"] if r["verdict"] == "migrated"]
        self.assertTrue(migrated, out["rows"])

    def test_bgp_idle_to_established(self):
        bgp_ov = {
            "metric_id": "bgp_peer",
            "status_fields": ["state"],
            "down_values": ["idle", "active", "connect", "down"],
            "up_values": ["established"],
            "success": [{"old": ["removed", "down"], "new": ["added", "up", "unchanged"]}],
        }
        old_base = [{"peer": "1.1.1.1", "state": "Established"}]
        old_cur = [{"peer": "1.1.1.1", "state": "Idle"}]
        new_base = [{"peer": "1.1.1.1", "state": "Idle"}]
        new_cur = [{"peer": "1.1.1.1", "state": "Established"}]
        expect = parse_expect_set({"items": [{"metric_id": "bgp_peer", "key": "1.1.1.1"}]})
        out = evaluate_metric_dual(
            metric_id="bgp_peer",
            key_fields=["peer"],
            iface_fields=[],
            compare_fields=["state"],
            old_baseline_rows=old_base,
            old_current_rows=old_cur,
            new_baseline_rows=new_base,
            new_current_rows=new_cur,
            port_map={},
            expect=expect,
            window_active=True,
            sheet_override=bgp_ov,
        )
        migrated = [r for r in out["rows"] if r["verdict"] == "migrated"]
        self.assertTrue(migrated, out["rows"])

    def test_out_of_expect_ignore_no_red(self):
        old_base = [{"interface": "gei-keep", "admin": "up", "phy": "up", "prot": "up"}]
        old_cur: list[dict] = []
        new_cur = [{"interface": "gei-keep", "admin": "up", "phy": "up", "prot": "up"}]
        expect = parse_expect_set({"ports": []})
        out = evaluate_metric_dual(
            metric_id="interface_brief",
            key_fields=["interface"],
            iface_fields=["interface"],
            compare_fields=["admin", "phy", "prot"],
            old_baseline_rows=old_base,
            old_current_rows=old_cur,
            new_baseline_rows=None,
            new_current_rows=new_cur,
            port_map={},
            expect=expect,
            window_active=False,
            out_of_expect="ignore",
        )
        reds = [r for r in out["rows"] if r["color"] == "red"]
        self.assertFalse(reds, out["rows"])
        involved = [r for r in out["rows"] if r["verdict"] != "not_involved"]
        self.assertFalse(involved, out["rows"])

    def test_unexpected_loss_is_anomaly(self):
        old_base = [{"interface": "gei-keep", "admin": "up", "phy": "up", "prot": "up"}]
        old_cur: list[dict] = []
        new_cur = [{"interface": "gei-keep", "admin": "up", "phy": "up", "prot": "up"}]
        expect = parse_expect_set({"ports": []})
        out = evaluate_metric_dual(
            metric_id="interface_brief",
            key_fields=["interface"],
            iface_fields=["interface"],
            compare_fields=["admin", "phy", "prot"],
            old_baseline_rows=old_base,
            old_current_rows=old_cur,
            new_baseline_rows=None,
            new_current_rows=new_cur,
            port_map={},
            expect=expect,
            window_active=False,
        )
        reds = [r for r in out["rows"] if r["color"] == "red"]
        self.assertTrue(reds, out["rows"])

    def test_new_baseline_missing_flag(self):
        old_base = [{"peer": "1.1.1.1", "state": "Established"}]
        new_cur = [{"peer": "1.1.1.1", "state": "Established"}]
        expect = parse_expect_set({"items": [{"metric_id": "bgp_peer", "key": "1.1.1.1"}]})
        out = evaluate_metric_dual(
            metric_id="bgp_peer",
            key_fields=["peer"],
            iface_fields=[],
            compare_fields=["state"],
            old_baseline_rows=old_base,
            old_current_rows=old_base,
            new_baseline_rows=None,
            new_current_rows=new_cur,
            port_map={},
            expect=expect,
            window_active=True,
            sheet_override={},
        )
        self.assertTrue(out["new_baseline_missing"])
        self.assertEqual(out["new_baseline_mode"], "empty")
        # With empty new before, current rows are all "added"
        kinds = {r["new_kind"] for r in out["rows"] if r.get("in_expect")}
        self.assertIn("added", kinds)

    def test_new_baseline_port_mapped_not_missing(self):
        old_base = [{"interface": "gei-old", "admin": "up", "phy": "up", "prot": "up"}]
        new_cur = [{"interface": "gei-new", "admin": "up", "phy": "up", "prot": "up"}]
        expect = parse_expect_set({"ports": ["gei-old"]})
        out = evaluate_metric_dual(
            metric_id="interface_brief",
            key_fields=["interface"],
            iface_fields=["interface"],
            compare_fields=["admin", "phy", "prot"],
            old_baseline_rows=old_base,
            old_current_rows=[],
            new_baseline_rows=None,
            new_current_rows=new_cur,
            port_map={"gei-old": "gei-new"},
            expect=expect,
            window_active=True,
            sheet_override={},
        )
        self.assertFalse(out["new_baseline_missing"])
        self.assertEqual(out["new_baseline_mode"], "port_mapped")

    def test_empty_provided_new_baseline_falls_back_or_missing(self):
        old_base = [{"peer": "1.1.1.1", "state": "Established"}]
        new_cur = [{"peer": "1.1.1.1", "state": "Established"}]
        expect = parse_expect_set({"items": [{"metric_id": "bgp_peer", "key": "1.1.1.1"}]})
        out = evaluate_metric_dual(
            metric_id="bgp_peer",
            key_fields=["peer"],
            iface_fields=[],
            compare_fields=["state"],
            old_baseline_rows=old_base,
            old_current_rows=[],
            new_baseline_rows=[],
            new_current_rows=new_cur,
            port_map={},
            expect=expect,
            window_active=True,
            sheet_override={},
        )
        self.assertTrue(out["new_baseline_missing"])
        self.assertEqual(out["new_baseline_mode"], "empty")
        # Still reports presence as added vs empty before — callers should heed the flag.
        kinds = {r["new_kind"] for r in out["rows"] if r.get("in_expect")}
        self.assertIn("added", kinds)

    def test_value_ne_false_when_removed(self):
        verdict, color = dual_verdict(
            old_kind="removed",
            new_kind="unchanged",
            in_expect=True,
            window_active=True,
            success_patterns=[
                {
                    "old_groups": [
                        [{"type": "value", "field": "state", "op": "ne", "value": "up"}]
                    ],
                    "new_groups": [[{"type": "presence", "value": "unchanged"}]],
                }
            ],
            old_row={},
            new_row={"state": "up"},
        )
        self.assertNotEqual(verdict, "migrated")

    def test_composite_key_port_remap(self):
        mapped = _remap_key_str(
            KEY_SEP.join(["gei-old", "vrf1"]),
            key_fields=["interface", "vrf"],
            iface_fields=["interface"],
            port_map={"gei-old": "gei-new"},
        )
        self.assertEqual(mapped, KEY_SEP.join(["gei-new", "vrf1"]))
        # Legacy pipe-separated input still remaps
        mapped2 = _remap_key_str(
            "gei-old|vrf1",
            key_fields=["interface", "vrf"],
            iface_fields=["interface"],
            port_map={"gei-old": "gei-new"},
        )
        self.assertEqual(mapped2, KEY_SEP.join(["gei-new", "vrf1"]))

    def test_anomaly_not_gated_by_field_tokens_on_removed(self):
        self.assertEqual(
            dual_verdict(
                old_kind="removed",
                new_kind="removed",
                in_expect=True,
                window_active=True,
                success_patterns=[
                    {
                        "old_groups": [[{"type": "presence", "value": "removed"}]],
                        "new_groups": [[{"type": "presence", "value": "added"}]],
                    }
                ],
                anomaly_patterns=[
                    {
                        "old_groups": [[{"type": "presence", "value": "removed"}]],
                        "new_groups": [[{"type": "presence", "value": "removed"}]],
                    }
                ],
                sheet_override={
                    "field_tokens": [{"side": "old", "field": "state", "in": ["up"]}],
                },
                old_row={},
                new_row={},
            ),
            ("anomaly", "red"),
        )

    def test_side_only_anomaly_deferred_during_window(self):
        """Mid-cutover: new-side gone alone must not red while window is active."""
        side_only = [
            {
                "old_groups": [],
                "new_groups": [[{"type": "presence", "value": "removed"}]],
            }
        ]
        self.assertEqual(
            dual_verdict(
                old_kind="unchanged",
                new_kind="removed",
                in_expect=True,
                window_active=True,
                acceptance=False,
                success_patterns=[
                    {
                        "old_groups": [[{"type": "presence", "value": "removed"}]],
                        "new_groups": [[{"type": "presence", "value": "added"}]],
                    }
                ],
                anomaly_patterns=side_only,
            ),
            ("migrating", "yellow"),
        )
        # Acceptance / window closed → side-only fires
        self.assertEqual(
            dual_verdict(
                old_kind="unchanged",
                new_kind="removed",
                in_expect=True,
                window_active=False,
                acceptance=True,
                success_patterns=[
                    {
                        "old_groups": [[{"type": "presence", "value": "removed"}]],
                        "new_groups": [[{"type": "presence", "value": "added"}]],
                    }
                ],
                anomaly_patterns=side_only,
            ),
            ("anomaly", "red"),
        )

    def test_both_gone_anomaly_still_fires_during_window(self):
        self.assertEqual(
            dual_verdict(
                old_kind="removed",
                new_kind="removed",
                in_expect=True,
                window_active=True,
                success_patterns=[
                    {
                        "old_groups": [[{"type": "presence", "value": "removed"}]],
                        "new_groups": [[{"type": "presence", "value": "added"}]],
                    }
                ],
                anomaly_patterns=[
                    {
                        "old_groups": [[{"type": "presence", "value": "removed"}]],
                        "new_groups": [[{"type": "presence", "value": "removed"}]],
                    }
                ],
            ),
            ("anomaly", "red"),
        )

    def test_bare_unchanged_not_success_with_tight_preset(self):
        from netx_api.biz_migration.monitor_templates import preset_override_for_metric

        ov = preset_override_for_metric("interface_brief")
        # Old removed + new still present but down → not green
        self.assertEqual(
            dual_verdict(
                old_kind="removed",
                new_kind="unchanged",
                in_expect=True,
                window_active=True,
                old_status="none",
                new_status="down",
                success_patterns=ov["success"],
                anomaly_patterns=ov["anomaly"],
                old_row={},
                new_row={"admin": "down", "phy": "down"},
            ),
            ("migrating", "yellow"),
        )
        # Old removed + new up → green
        self.assertEqual(
            dual_verdict(
                old_kind="removed",
                new_kind="unchanged",
                in_expect=True,
                window_active=True,
                old_status="none",
                new_status="up",
                success_patterns=ov["success"],
                anomaly_patterns=ov["anomaly"],
                old_row={},
                new_row={"admin": "up", "phy": "up"},
            ),
            ("migrated", "green"),
        )

    def test_arp_preset_requires_added_not_bare_unchanged(self):
        from netx_api.biz_migration.monitor_templates import preset_override_for_metric

        ov = preset_override_for_metric("arp")
        self.assertEqual(
            dual_verdict(
                old_kind="removed",
                new_kind="unchanged",
                in_expect=True,
                window_active=True,
                success_patterns=ov["success"],
                anomaly_patterns=ov["anomaly"],
            ),
            ("migrating", "yellow"),
        )
        self.assertEqual(
            dual_verdict(
                old_kind="removed",
                new_kind="added",
                in_expect=True,
                window_active=True,
                success_patterns=ov["success"],
                anomaly_patterns=ov["anomaly"],
            ),
            ("migrated", "green"),
        )

    def test_rule_hit_labels(self):
        from netx_api.biz_migration.evaluate import dual_verdict_ex

        v, c, hit = dual_verdict_ex(
            old_kind="removed",
            new_kind="added",
            in_expect=True,
            window_active=True,
            success_patterns=[
                {
                    "old_groups": [[{"type": "presence", "value": "removed"}]],
                    "new_groups": [[{"type": "presence", "value": "added"}]],
                }
            ],
        )
        self.assertEqual((v, c), ("migrated", "green"))
        self.assertTrue(hit.startswith("success#"))


class PortMapScopeTests(unittest.TestCase):
    def test_mapping_is_expect_for_iface_sheet(self):
        old_base = [
            {"interface": "gei-old", "admin": "up", "phy": "up", "prot": "up"},
            {"interface": "gei-keep", "admin": "up", "phy": "up", "prot": "up"},
        ]
        new_base = [
            {"interface": "gei-new", "admin": "up", "phy": "up", "prot": "up"},
            {"interface": "gei-keep", "admin": "up", "phy": "up", "prot": "up"},
        ]
        new_cur = [
            {"interface": "gei-new", "admin": "up", "phy": "up", "prot": "up"},
            {"interface": "gei-keep", "admin": "up", "phy": "up", "prot": "up"},
        ]
        out = evaluate_metric_dual(
            metric_id="interface_brief",
            key_fields=["interface"],
            iface_fields=["interface"],
            compare_fields=["admin", "phy", "prot"],
            old_baseline_rows=old_base,
            old_current_rows=[
                {"interface": "gei-keep", "admin": "up", "phy": "up", "prot": "up"},
            ],
            new_baseline_rows=new_base,
            new_current_rows=new_cur,
            port_map={"gei-old": "gei-new"},
            expect=parse_expect_set({}),
            window_active=True,
            sheet_override=PORT_OVERRIDE,
        )
        self.assertEqual(out["progress_total"], 1)
        keys = {r["key_str"] for r in out["rows"]}
        self.assertIn("gei-old", keys)
        self.assertNotIn("gei-keep", keys)

    def test_unmapped_same_iface_old_down_new_up_is_anomaly(self):
        old_base = [
            {"interface": "gei-old", "admin": "up", "phy": "up", "prot": "up"},
            {"interface": "gei-keep", "admin": "up", "phy": "up", "prot": "up"},
        ]
        old_cur = [{"interface": "gei-keep", "admin": "down", "phy": "down", "prot": "down"}]
        new_base = [{"interface": "gei-keep", "admin": "up", "phy": "up", "prot": "up"}]
        new_cur = [{"interface": "gei-keep", "admin": "up", "phy": "up", "prot": "up"}]
        out = evaluate_metric_dual(
            metric_id="interface_brief",
            key_fields=["interface"],
            iface_fields=["interface"],
            compare_fields=["admin", "phy", "prot"],
            old_baseline_rows=old_base,
            old_current_rows=old_cur,
            new_baseline_rows=new_base,
            new_current_rows=new_cur,
            port_map={"gei-old": "gei-new"},
            expect=parse_expect_set({}),
            window_active=True,
            sheet_override=PORT_OVERRIDE,
        )
        keep = [r for r in out["rows"] if r["key_str"] == "gei-keep"]
        self.assertEqual(len(keep), 1, out["rows"])
        self.assertEqual(keep[0]["verdict"], "anomaly")
        self.assertEqual(keep[0]["rule_hit"], "unmapped_same_iface")
        self.assertFalse(keep[0]["in_expect"])

    def test_arp_filtered_by_iface_mapping(self):
        old_base = [
            {"ip": "10.0.0.1", "interface": "gei-old"},
            {"ip": "10.0.0.2", "interface": "gei-keep"},
        ]
        new_cur = [{"ip": "10.0.0.1", "interface": "gei-new"}]
        out = evaluate_metric_dual(
            metric_id="arp",
            key_fields=["ip", "interface"],
            iface_fields=["interface"],
            compare_fields=[],
            old_baseline_rows=old_base,
            old_current_rows=[],
            new_baseline_rows=[{"ip": "9.9.9.9", "interface": "gei-other"}],
            new_current_rows=new_cur,
            port_map={"gei-old": "gei-new"},
            expect=parse_expect_set({}),
            window_active=True,
            sheet_override={
                "success": [
                    {
                        "old_groups": [[{"type": "presence", "value": "removed"}]],
                        "new_groups": [[{"type": "presence", "value": "added"}]],
                    }
                ],
            },
        )
        keys = {r["key_str"] for r in out["rows"]}
        self.assertIn("10.0.0.1|gei-old", keys)
        self.assertNotIn("10.0.0.2|gei-keep", keys)
        migrated = [r for r in out["rows"] if r["verdict"] == "migrated"]
        self.assertTrue(migrated, out["rows"])
        self.assertEqual(migrated[0]["new_key_str"], "10.0.0.1|gei-new")

    def test_bgp_still_uses_expect_picker(self):
        out = evaluate_metric_dual(
            metric_id="bgp_peer",
            key_fields=["peer"],
            iface_fields=["interface"],
            compare_fields=["state"],
            old_baseline_rows=[{"peer": "1.1.1.1", "interface": "gei-old", "state": "Established"}],
            old_current_rows=[],
            new_baseline_rows=[{"peer": "1.1.1.1", "interface": "gei-new", "state": "Idle"}],
            new_current_rows=[{"peer": "1.1.1.1", "interface": "gei-new", "state": "Established"}],
            port_map={"gei-keep": "gei-other"},
            expect=parse_expect_set({"items": [{"metric_id": "bgp_peer", "key": "1.1.1.1"}]}),
            window_active=True,
            sheet_override={
                "success": [{"old": ["removed"], "new": ["changed", "up"]}],
                "status_fields": ["state"],
                "up_values": ["established"],
                "down_values": ["idle"],
            },
        )
        self.assertTrue(any(r["key_str"] == "1.1.1.1" and r["in_expect"] for r in out["rows"]))


class DisplayRawABTests(unittest.TestCase):
    """Board/AI must show device-raw A/B, not post-map BB."""

    def test_normalize_and_map_keeps_display_ab(self):
        # Old device shows GE-…; template normalizes GE→gei; map gei-old → xgei-new.
        # New device collected xgei-new. Internal match is gei-old↔xgei-new;
        # display must stay GE-old / xgei-new (never xgei-new / xgei-new).
        rules = [{"from": "GE", "to": "gei"}]
        out = evaluate_metric_dual(
            metric_id="interface_brief",
            key_fields=["interface"],
            iface_fields=["interface"],
            compare_fields=["admin", "phy", "prot"],
            old_baseline_rows=[
                {"interface": "GE-old", "admin": "up", "phy": "up", "prot": "up"}
            ],
            old_current_rows=[],
            new_baseline_rows=None,
            new_current_rows=[
                {"interface": "xgei-new", "admin": "up", "phy": "up", "prot": "up"}
            ],
            port_map={"gei-old": "xgei-new"},
            expect=parse_expect_set({"ports": ["gei-old"]}),
            window_active=True,
            sheet_override=PORT_OVERRIDE,
            iface_normalize_rules=rules,
        )
        migrated = [r for r in out["rows"] if r["verdict"] == "migrated"]
        self.assertTrue(migrated, out["rows"])
        row = migrated[0]
        self.assertEqual(row["old_key"], "GE-old")
        self.assertEqual(row["new_key"], "xgei-new")
        self.assertEqual(row["key_str"], "GE-old")
        self.assertEqual(row["new_key_str"], "xgei-new")
        self.assertEqual(row["match_old_key"], "gei-old")
        self.assertEqual(row["match_new_key"], "xgei-new")
        self.assertEqual(row["old"].get("interface"), "GE-old")
        self.assertEqual(row["new"].get("interface"), "xgei-new")
        pm = row["evidence"]["port_map"]
        self.assertTrue(pm["applied"])
        self.assertEqual(pm["display_before"], "GE-old")
        self.assertEqual(pm["display_after"], "xgei-new")
        self.assertEqual(pm["match_before"], "gei-old")
        self.assertEqual(pm["match_after"], "xgei-new")
        old_iface = row["evidence"]["old"]["iface"][0]
        self.assertEqual(old_iface["raw"], "GE-old")
        self.assertEqual(old_iface["normalized"], "gei-old")
        self.assertTrue(old_iface["mapped"])
        self.assertEqual(old_iface["map_to"], "xgei-new")


if __name__ == "__main__":
    unittest.main()
