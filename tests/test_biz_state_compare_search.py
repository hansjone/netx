"""Regression tests: searching a pair must not change its comparison verdict."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from netx_api.biz_state.compare_engine import compare_rows
from netx_api.biz_state.compare_service import (
    _load_metric_rows_for_search,
    _literal_search_pattern,
    _persist_sheet_diffs,
    _search_stored_diffs,
    list_run_diffs,
)
from netx_api.db import Base
from netx_api.models import (
    BizCompareDiff, BizCompareRun, BizCompareTemplate, BizPortMappingRow,
    BizStateLldpNeighbor, BizStateMetricRow,
)


class CompareSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine, tables=[m.__table__ for m in (
            BizCompareDiff, BizCompareRun, BizCompareTemplate, BizPortMappingRow,
            BizStateLldpNeighbor, BizStateMetricRow,
        )])
        self.db = Session(self.engine)

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def seed(self, before, after, *, metric="test", keys=None, fields=None,
             iface=None, mapping=None, sample=False, compact=False, filters=None):
        keys = keys or ["id"]
        iface = iface or []
        fields = fields or ["state"]
        for side, rows in (("before", before), ("after", after)):
            for seq, row in enumerate(rows):
                rid = f"{side}-{seq}"
                if metric == "lldp_neighbor":
                    self.db.add(BizStateLldpNeighbor(id=rid, batch_id=side, **row))
                else:
                    self.db.add(BizStateMetricRow(id=rid, batch_id=side, metric_id=metric,
                                                seq=seq, data_json=dict(row)))
                row["_netx"] = {"row_id": rid}
        if mapping:
            for b, a in mapping.items():
                self.db.add(BizPortMappingRow(mapping_id="map", before_if=b, after_if=a))
        result = compare_rows(before_rows=before, after_rows=after, key_fields=keys,
                              compare_fields=fields, iface_fields=iface, port_map=mapping or {},
                              include_unchanged=True, compact_unchanged=compact)
        sheet = {"sheet_id": "sheet", "metric_id": metric, "key_fields": keys,
                 "compare_fields": fields, "iface_fields": iface,
                 "summary": {**result["summary"], "unchanged_listed": 0 if sample
                             else result["summary"]["unchanged"]},
                 "row_filters": filters or []}
        self.db.add(BizCompareRun(id="run", status="success", before_batch_id="before",
                                 after_batch_id="after", mapping_id="map" if mapping else "",
                                 summary_json={"sheets": [sheet]}))
        self.db.commit()
        _persist_sheet_diffs(self.db, run_id="run", metric_id="sheet",
                             diffs=[d for d in result["diffs"]
                                    if not sample or d["kind"] != "unchanged"])
        return result

    def search(self, **kwargs):
        return list_run_diffs(self.db, "run", metric_id="sheet", **kwargs)

    def test_search_before_value_preserves_changed_verdict(self):
        self.seed([{"id": "1", "state": "up"}], [{"id": "1", "state": "down"}])
        with patch("netx_api.biz_state.compare_service.compare_rows") as compare:
            result = self.search(kw="up", kind="changed")
            compare.assert_not_called()
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["after"]["state"], "down")
        self.assertEqual(self.search(kw="up", kind="removed")["total"], 0)

    def test_field_filter_matches_after_value_on_compact_diff(self):
        self.seed([{"id": "1", "state": "up"}], [{"id": "1", "state": "down"}], compact=True)
        result = self.search(qf={"state": "down"}, kind="changed")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["before"]["state"], "up")

    def test_sample_lookup_completes_pair_matching_only_one_side(self):
        self.seed([{"id": "1", "state": "up", "age": "old"}],
                  [{"id": "1", "state": "up", "age": "new"}], sample=True)
        result = self.search(kw="old", kind="unchanged")
        self.assertEqual(result["source"], "live")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["after"]["age"], "new")

    def test_sample_lookup_does_not_turn_changed_into_removed(self):
        self.seed([{"id": "1", "state": "up"}, {"id": "2", "state": "up"}],
                  [{"id": "1", "state": "down"}, {"id": "2", "state": "up"}], sample=True)
        result = self.search(qf={"id": "1", "state": "up"}, kind="all")
        self.assertEqual([d["kind"] for d in result["items"]], ["changed"])

    def test_sample_lookup_keeps_duplicate_group_pairing(self):
        self.seed([{"id": "1", "state": "up", "note": "first"},
                   {"id": "1", "state": "down", "note": "second"}],
                  [{"id": "1", "state": "up", "note": "other"},
                   {"id": "1", "state": "down", "note": "other"}], sample=True)
        result = self.search(kw="second", kind="all")
        self.assertEqual([d["kind"] for d in result["items"]], ["unchanged"])
        self.assertEqual(result["items"][0]["after"]["state"], "down")

    def test_sample_search_is_not_limited_by_persisted_search_text_size(self):
        self.seed([{"id": "1", "state": "up", "note": "x" * 5000 + "needle"}],
                  [{"id": "1", "state": "up", "note": "other"}], sample=True)
        self.assertEqual(self.search(kw="needle", kind="unchanged")["total"], 1)

    def test_sample_lookup_applies_port_mapping_before_matching(self):
        self.seed([{"id": "1", "interface": "old", "state": "up"}],
                  [{"id": "1", "interface": "new", "state": "up"}],
                  keys=["id", "interface"], iface=["interface"], mapping={"old": "new"}, sample=True)
        result = self.search(qf={"interface": "old"}, kind="unchanged")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["after"]["interface"], "new")

    def test_lldp_source_search(self):
        self.seed([{"local_if": "gei-1", "remote_sys": "peer", "remote_if": "port", "remote_ip": "1"}],
                  [{"local_if": "gei-1", "remote_sys": "peer", "remote_if": "port", "remote_ip": "1"}],
                  metric="lldp_neighbor", keys=["local_if", "remote_sys"], fields=["remote_ip"],
                  iface=["local_if"], sample=True)
        self.assertEqual(self.search(kw="peer", kind="unchanged")["total"], 1)

    def test_lldp_compact_diff_field_search(self):
        self.seed([{"local_if": "gei-1", "remote_sys": "peer", "remote_ip": "1"}],
                  [{"local_if": "gei-1", "remote_sys": "peer", "remote_ip": "2"}],
                  metric="lldp_neighbor", keys=["local_if", "remote_sys"], fields=["remote_ip"],
                  iface=["local_if"], compact=True)
        self.assertEqual(self.search(qf={"remote_ip": "2"}, kind="changed")["total"], 1)

    def test_exact_load_cap_is_not_truncated(self):
        self.seed([{"id": "1", "state": "up"}], [{"id": "1", "state": "up"}])
        rows, truncated = _load_metric_rows_for_search(self.db, batch_id="before", metric_id="test",
                                                       row_filters=[], key_fields=["id"], kw="up", cap=1)
        self.assertEqual(len(rows), 1)
        self.assertFalse(truncated)

    def test_incomplete_duplicate_groups_are_omitted(self):
        self.seed([{"id": "1", "state": "up"}, {"id": "1", "state": "down"}],
                  [{"id": "1", "state": "up"}, {"id": "1", "state": "down"}], sample=True)
        with patch("netx_api.biz_state.compare_service._LIVE_SEARCH_GROUP_LOAD_CAP", 1):
            result = self.search(kw="up", kind="all")
        self.assertEqual(result["items"], [])
        self.assertTrue(result["truncated"])

    def test_postgres_search_pattern_treats_wildcards_literally(self):
        self.assertEqual(_literal_search_pattern("a_10%"), "%a\\_10\\%%")

    def test_postgres_unsupported_filter_uses_python(self):
        self.seed([{"id": "1", "state": "up", "age": "H"}],
                  [{"id": "1", "state": "up", "age": "H"}])
        with patch("netx_api.biz_state.compare_sql._dialect_is_postgres", return_value=True):
            rows, _ = _load_metric_rows_for_search(self.db, batch_id="before", metric_id="test",
                         row_filters=[{"field": "age", "op": "age_timer"}], key_fields=["id"], kw="up")
        self.assertEqual(rows, [])

    def test_unknown_sheet_is_rejected(self):
        self.seed([{"id": "1", "state": "up"}], [{"id": "1", "state": "up"}])
        with self.assertRaises(HTTPException) as error:
            list_run_diffs(self.db, "run", metric_id="missing")
        self.assertEqual(error.exception.detail, "sheet_not_found")

    def test_stored_search_escapes_like_wildcards(self):
        self.seed([{"id": "x_1", "state": "up"}, {"id": "xy1", "state": "up"}],
                  [{"id": "x_1", "state": "down"}, {"id": "xy1", "state": "down"}])
        self.assertEqual(self.search(qf={"id": "x_1"}, kind="changed")["total"], 1)
        query = _search_stored_diffs(self.db.query(BizCompareDiff), metric_id="test",
                                    kw="up", field_q={"id": "x_1"})
        compiled = query.statement.compile(dialect=postgresql.dialect())
        self.assertIn("LEFT OUTER JOIN", str(compiled))


if __name__ == "__main__":
    unittest.main()
