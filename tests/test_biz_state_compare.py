"""Unit tests for biz_state compare engine."""

from __future__ import annotations

import unittest

from netx_api.biz_state.compare_engine import compare_rows, mapping_stats


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
        self.assertEqual({d["kind"] for d in only_diff}, {"added", "removed", "changed"})
        only_added = _filter_inline_diffs(diffs, kind="added", kw="")
        self.assertEqual(len(only_added), 1)
        hit = _filter_inline_diffs(diffs, kind="all", kw='"p":"4"')
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]["kind"], "changed")
        hit2 = _filter_inline_diffs(diffs, kind="all", kw="changed")
        self.assertTrue(any(d["kind"] == "changed" for d in hit2))


if __name__ == "__main__":
    unittest.main()
