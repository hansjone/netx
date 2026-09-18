"""Unit tests for cutover migration evaluate helpers (port status focus)."""

from __future__ import annotations

import unittest

from netx_api.biz_migration.evaluate import (
    classify_status,
    dual_verdict,
    evaluate_metric_dual,
    parse_expect_set,
    port_sheet_def,
    port_status_label,
    side_verdict,
)


PORT_OVERRIDE = {
    "metric_id": "interface_brief",
    "status_fields": ["admin", "phy", "prot"],
    "down_values": ["down"],
    "up_values": ["up"],
    "success": [{"old": ["removed", "down"], "new": ["added", "up", "unchanged"]}],
}


class ParseExpectSetTests(unittest.TestCase):
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
        self.assertEqual(got["bgp_peer"], {"AS1|1.1.1.1"})
        self.assertEqual(got["arp"], {"10.0.0.1"})


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


if __name__ == "__main__":
    unittest.main()
