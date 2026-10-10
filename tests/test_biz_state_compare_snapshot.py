"""Historical comparisons must use the settings captured when they were queued."""

from __future__ import annotations

import csv
import io
import json
import unittest
import zipfile
from copy import deepcopy
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from netx_api.biz_state.compare_service import (
    _create_running_run, _execute_compare_into_run, cancel_compare_run,
    export_run_zip, get_run, list_run_diffs, template_metrics,
)
from netx_api.db import Base
from netx_api.models import (
    BizCompareDiff, BizCompareJob, BizCompareTemplate, BizPortMapping,
    BizPortMappingRow, BizStateMetricRow,
)


class CompareSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.factory()
        # Progress uses a separate session in production; keep every test write local.
        self.session_patch = patch("netx_api.db.SessionLocal", self.factory)
        self.session_patch.start()
        self.sheet = {
            "sheet_id": "sheet", "metric_id": "test", "title": "Original sheet",
            "key_fields": ["id", "interface"], "iface_fields": ["interface"],
            "compare_fields": ["state"], "display_fields": ["id", "interface", "state"],
            "row_filters": [], "ignore_port_changes": False,
        }
        self.tpl = BizCompareTemplate(
            id="tpl", name="Original template", metrics_json=[deepcopy(self.sheet)],
            iface_normalize_json=[{"from": "GE", "to": "gei"}],
        )
        self.job = BizCompareJob(id="job", name="Original job", template_id="tpl",
                                 mapping_id="map", store_unchanged="sample")
        self.mapping = BizPortMapping(id="map", name="Original mapping")
        self.pair = BizPortMappingRow(mapping_id="map", before_if="gei-1", after_if="gei-2")
        self.db.add_all([self.tpl, self.job, self.mapping, self.pair])
        for side, iface, note in (("before", "GE-1.100", "old"), ("after", "GE-2.100", "new")):
            self.db.add(BizStateMetricRow(id=side, batch_id=side, metric_id="test", seq=0,
                                         data_json={"id": "1", "interface": iface,
                                                    "state": "up", "note": note}))
        self.db.commit()
        self.run = _create_running_run(
            self.db, job=self.job, tpl=self.tpl, before_batch_id="before",
            after_batch_id="after", sheets_cfg=template_metrics(self.tpl),
        )

    def tearDown(self):
        self.session_patch.stop()
        self.db.close()
        self.engine.dispose()

    def edit_current_settings(self):
        changed = {**self.sheet, "title": "Edited sheet", "compare_fields": ["note"],
                   "row_filters": [{"field": "state", "op": "eq", "value": "down"}]}
        self.tpl.name = "Edited template"
        self.tpl.metrics_json = [changed]
        self.tpl.iface_normalize_json = [{"from": "GE", "to": "other"}]
        self.pair.after_if = "gei-99"
        self.job.name = "Edited job"
        self.job.store_unchanged = "never"
        self.job.enabled_sheet_ids = ["different-sheet"]
        self.mapping.name = "Edited mapping"
        self.db.commit()

    def execute(self):
        return _execute_compare_into_run(self.db, self.run.id)

    def test_queued_run_uses_original_sheet_rules_mapping_and_storage(self):
        original = deepcopy(self.run.summary_json["config_snapshot"])
        self.edit_current_settings()
        detail = self.execute()
        self.assertEqual(detail["status"], "success")
        self.assertEqual(detail["summary"]["unchanged"], 1)
        self.assertEqual(detail["summary"]["unchanged_listed"], 1)
        self.assertEqual(detail["summary"]["store_unchanged"], "sample")
        self.assertEqual(detail["sheets"][0]["compare_fields"], ["state"])
        self.assertEqual(self.run.summary_json["config_snapshot"], original)

    def test_compact_hydration_restores_original_normalized_values(self):
        self.execute()
        self.edit_current_settings()
        diff = list_run_diffs(self.db, self.run.id, metric_id="sheet", kind="unchanged")["items"][0]
        self.assertEqual(diff["before"]["interface"], "GE-1.100")
        self.assertEqual(diff["mapped_before"]["interface"], "gei-2.100")
        self.assertEqual(diff["after"]["interface"], "gei-2.100")

    def test_live_search_does_not_inherit_new_filters_or_transforms(self):
        with patch("netx_api.biz_state.compare_service._UNCHANGED_SAMPLE_MAX", 0):
            self.execute()
        self.edit_current_settings()
        result = list_run_diffs(self.db, self.run.id, metric_id="sheet", kind="unchanged", kw="old")
        self.assertEqual(result["source"], "live")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["mapped_before"]["interface"], "gei-2.100")

    def test_snapshot_can_be_read_after_mapping_and_template_are_deleted(self):
        self.execute()
        self.db.query(BizPortMappingRow).delete()
        self.db.delete(self.mapping)
        self.db.delete(self.tpl)
        self.db.commit()
        detail = get_run(self.db, self.run.id)
        self.assertEqual(detail["template_name"], "Original template")
        self.assertEqual(detail["mapping_name"], "Original mapping")
        diff = list_run_diffs(self.db, self.run.id, metric_id="sheet", kind="unchanged")["items"][0]
        self.assertEqual(diff["mapped_before"]["interface"], "gei-2.100")

    def test_queued_run_can_execute_after_its_template_and_mapping_are_deleted(self):
        self.db.query(BizPortMappingRow).delete()
        self.db.delete(self.mapping)
        self.db.delete(self.tpl)
        self.db.commit()
        detail = self.execute()
        self.assertEqual(detail["status"], "success")
        self.assertEqual(detail["summary"]["unchanged"], 1)

    def test_detail_returns_original_names_and_independent_template_copy(self):
        self.edit_current_settings()
        detail = get_run(self.db, self.run.id)
        self.assertEqual(detail["job_name"], "Original job")
        self.assertEqual(detail["config_snapshot_version"], 1)
        self.assertEqual(detail["template"]["iface_normalize_rules"], [{"from": "GE", "to": "gei"}])
        self.assertNotIn("config_snapshot", detail["summary"])
        detail["template"]["iface_normalize_rules"].clear()
        self.assertTrue(self.run.summary_json["config_snapshot"]["template"]["iface_normalize_rules"])

    def test_export_contains_original_config_and_hydrated_csv(self):
        self.execute()
        self.edit_current_settings()
        with zipfile.ZipFile(io.BytesIO(export_run_zip(self.db, self.run.id))) as archive:
            config = json.loads(archive.read("config_snapshot.json"))
            self.assertEqual(config["port_map"], {"gei-1": "gei-2"})
            self.assertEqual(config["template"]["name"], "Original template")
            rows = list(csv.DictReader(io.StringIO(archive.read("tables/sheet.csv").decode("utf-8-sig"))))
            self.assertEqual(rows[0]["interface"], "gei-2.100")

    def test_failed_and_cancelled_runs_keep_snapshot(self):
        original = deepcopy(self.run.summary_json["config_snapshot"])
        with patch("netx_api.biz_state.compare_service._run_sheet", side_effect=RuntimeError("test-failure")):
            with self.assertRaisesRegex(RuntimeError, "test-failure"):
                self.execute()
        self.assertEqual(self.run.summary_json["config_snapshot"], original)
        self.run.status = "running"
        self.db.commit()
        cancel_compare_run(self.db, self.run.id)
        self.assertEqual(self.run.summary_json["config_snapshot"], original)

    def test_legacy_runs_without_snapshot_remain_readable(self):
        self.execute()
        self.run.summary_json = {k: v for k, v in self.run.summary_json.items() if k != "config_snapshot"}
        self.db.commit()
        detail = get_run(self.db, self.run.id)
        self.assertEqual(detail["status"], "success")
        self.assertIsNone(detail["config_snapshot_version"])
        self.assertEqual(list_run_diffs(self.db, self.run.id, metric_id="sheet", kind="unchanged")["total"], 1)
        self.assertEqual(self.db.query(BizCompareDiff).count(), 1)
