"""Unit tests for cutover migration evaluate helpers (port status focus)."""

from __future__ import annotations

import unittest

from netx_api.biz_migration.evaluate import (
    dual_verdict,
    evaluate_metric_dual,
    parse_expect_set,
    port_sheet_def,
    port_status_label,
    side_verdict,
)


class ParseExpectSetTests(unittest.TestCase):
    def test_ports_go_to_interface_brief(self):
        got = parse_expect_set({"ports": ["gei-1", "gei-2", ""]})
        self.assertEqual(got["interface_brief"], {"gei-1", "gei-2"})
        self.assertEqual(got["_ports"], {"gei-1", "gei-2"})


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
        # window mode still yellow
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
        )
        self.assertEqual(out["progress_total"], 1)
        migrated = [r for r in out["rows"] if r["verdict"] == "migrated"]
        self.assertTrue(migrated, out["rows"])
        self.assertEqual(migrated[0]["color"], "green")
        self.assertEqual(migrated[0]["old_status"], "gone")
        self.assertEqual(migrated[0]["new_status"], "up/up/up")
        self.assertEqual(out["progress_ok"], 1)

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
