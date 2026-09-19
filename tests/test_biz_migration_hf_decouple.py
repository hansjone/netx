"""Integration: portrait vs cutover-HF task slot decoupling."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from netx_api.db import Base
from netx_api.models import (
    BizMigrationProject,
    BizMonitorTemplate,
    BizStateBatch,
    BizStateTask,
)
from netx_api.biz_migration import service as mig
from netx_api.biz_state import service as biz_svc


def _stub_catalog(*_a, **_k):
    return {
        "source_profile_id": "zte.interface_brief",
        "kind": "catalog",
        "enabled": True,
        "title": "IF",
    }


def _stub_ne_meta(*_a, **_k):
    return {
        "ne_name": "ne",
        "ne_ip": "10.0.0.1",
        "vendor": "zte",
        "device_type": "router",
    }


class HfDecoupleFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        TestingSession = sessionmaker(
            bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
        )
        Base.metadata.create_all(bind=engine)
        self.db = TestingSession()

        self.mt = BizMonitorTemplate(
            id="mt1",
            name="port",
            compare_template_id="",
            collect_metric_ids_json=["interface_brief"],
            defaults_json={},
            sheet_overrides_json=[],
        )
        self.db.add(self.mt)

        self.old_portrait = self._mk_task("old_p", ne_id="ne-old", note="画像全量", purpose="")
        self.new_portrait = self._mk_task("new_p", ne_id="ne-new", note="画像全量", purpose="")
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def _mk_task(
        self,
        tid: str,
        *,
        ne_id: str,
        note: str = "",
        purpose: str = "",
        interval_sec: int = 3600,
        vendor: str = "zte",
        device_type: str = "router",
    ) -> BizStateTask:
        t = BizStateTask(
            id=tid,
            source="managed",
            ne_id=ne_id,
            ne_name=ne_id,
            ne_ip="10.0.0.1",
            vendor=vendor,
            device_type=device_type,
            note=note,
            purpose=purpose,
            status="running",
            interval_sec=interval_sec,
            retention_days=30,
        )
        self.db.add(t)
        return t

    def _mk_batch(self, bid: str, task_id: str) -> BizStateBatch:
        now = datetime.utcnow()
        b = BizStateBatch(
            id=bid,
            task_id=task_id,
            status="success",
            started_at=now,
            ended_at=now,
            row_count=1,
        )
        self.db.add(b)
        self.db.commit()
        return b

    def _create_project(self, **extra) -> dict:
        body = {
            "name": "cutover-night-1",
            "old_task_id": self.old_portrait.id,
            "new_task_id": self.new_portrait.id,
            "monitor_template_id": self.mt.id,
            "collect_now": False,
            **extra,
        }
        with mock.patch("netx_api.biz_state.collect_runner.dispatch_collect"), mock.patch.object(
            mig, "_catalog_item_for_metric", side_effect=_stub_catalog
        ), mock.patch.object(biz_svc, "_ne_meta", side_effect=_stub_ne_meta):
            return mig.create_project(self.db, body)

    def _ensure(self, pid: str, **kw):
        with mock.patch("netx_api.biz_state.collect_runner.dispatch_collect"), mock.patch.object(
            mig, "_catalog_item_for_metric", side_effect=_stub_catalog
        ), mock.patch.object(biz_svc, "_ne_meta", side_effect=_stub_ne_meta):
            return mig.ensure_highfreq(self.db, pid, collect_now=False, **kw)

    def test_create_keeps_portrait_slots(self) -> None:
        out = self._create_project()
        self.assertEqual(out["old_task_id"], "old_p")
        self.assertEqual(out["new_task_id"], "new_p")
        # One-shot create always spawns HF
        self.assertTrue(out["old_hf_task_id"])
        self.assertTrue(out["new_hf_task_id"])
        self.assertNotEqual(out["old_hf_task_id"], "old_p")
        self.assertNotEqual(out["new_hf_task_id"], "new_p")

    def test_ensure_highfreq_does_not_overwrite_portrait(self) -> None:
        proj = self._create_project()
        res = self._ensure(proj["id"], interval_sec=60)
        self.assertEqual(res["project"]["old_task_id"], "old_p")
        self.assertEqual(res["project"]["new_task_id"], "new_p")
        self.assertTrue(res["project"]["old_hf_task_id"])
        self.assertNotEqual(res["project"]["old_hf_task_id"], "old_p")
        old_hf = self.db.get(BizStateTask, res["project"]["old_hf_task_id"])
        assert old_hf is not None
        self.assertEqual(old_hf.purpose, mig.PURPOSE_CUTOVER_HF)
        self.assertEqual(old_hf.interval_sec, 60)
        old_p = self.db.get(BizStateTask, "old_p")
        assert old_p is not None
        self.assertEqual(old_p.note, "画像全量")

    def test_evaluate_current_from_hf_not_portrait(self) -> None:
        proj = self._create_project()
        pid = proj["id"]
        old_hf_id = proj["old_hf_task_id"]
        new_hf_id = proj["new_hf_task_id"]
        self._mk_batch("bl_old", "old_p")
        self._mk_batch("bl_new", "new_p")
        self._mk_batch("cur_old", old_hf_id)
        self._mk_batch("cur_new", new_hf_id)
        self._mk_batch("portrait_cur_old", "old_p")
        self._mk_batch("portrait_cur_new", "new_p")
        mig.patch_project(
            self.db,
            pid,
            {"old_baseline_batch_id": "bl_old", "new_baseline_batch_id": "bl_new"},
        )
        batch = mig.create_batch(
            self.db, pid, {"batch_label": "n1", "expect_set": {"ports": ["gei-0/1"]}}
        )
        with mock.patch.object(mig, "resolve_evaluate_sheets", return_value=([], [], {}, [])):
            out = mig.run_evaluate(self.db, batch_id=batch["id"])
        self.assertEqual(out["old_batch_id"], "cur_old")
        self.assertEqual(out["new_batch_id"], "cur_new")

    def test_collect_now_requires_hf_not_portrait(self) -> None:
        # Project without HF: build manually (create always spawns HF now)
        p = BizMigrationProject(
            id="nohf",
            name="nohf",
            old_task_id=self.old_portrait.id,
            new_task_id=self.new_portrait.id,
            monitor_template_id=self.mt.id,
        )
        self.db.add(p)
        self.db.commit()
        with mock.patch("netx_api.biz_state.collect_runner.dispatch_collect") as disp:
            out = mig.collect_project_now(self.db, p.id)
            disp.assert_not_called()
        old = out["old"]
        new = out["new"]
        if isinstance(old, list):
            self.assertEqual(old[0].get("error"), "hf_task_missing")
        else:
            self.assertEqual(old.get("error"), "hf_task_missing")
        if isinstance(new, list):
            self.assertEqual(new[0].get("error"), "hf_task_missing")
        else:
            self.assertEqual(new.get("error"), "hf_task_missing")

    def test_collect_now_dispatches_hf_only(self) -> None:
        proj = self._create_project()
        with mock.patch("netx_api.biz_state.collect_runner.dispatch_collect") as disp:
            out = mig.collect_project_now(self.db, proj["id"])
        self.assertTrue(any(x.get("ok") for x in out["old"]))
        self.assertTrue(any(x.get("ok") for x in out["new"]))
        called = {c.args[0] for c in disp.call_args_list}
        self.assertEqual(
            called,
            {proj["old_hf_task_id"], proj["new_hf_task_id"]},
        )
        self.assertNotIn("old_p", called)

    def test_project_collect_override(self) -> None:
        proj = self._create_project()
        mig.patch_project(self.db, proj["id"], {"collect_metric_ids": ["interface_brief"]})
        p = self.db.get(BizMigrationProject, proj["id"])
        assert p is not None
        self.assertEqual(mig.resolve_collect_metric_ids(self.db, p), ["interface_brief"])

    def test_empty_monitor_collect_expands_to_sheets(self) -> None:
        self.mt.collect_metric_ids_json = []
        self.db.commit()
        sheets = [
            {"metric_id": "interface_brief", "key_fields": ["if_name"]},
            {"metric_id": "arp", "key_fields": ["ip"]},
            {"metric_id": "bgp_summary", "key_fields": ["peer"]},
        ]
        with mock.patch.object(
            mig, "resolve_evaluate_sheets", return_value=(sheets, [], {}, [])
        ):
            proj = self._create_project(collect_metric_ids=[])
            p = self.db.get(BizMigrationProject, proj["id"])
            assert p is not None
            self.assertEqual(
                mig.resolve_collect_metric_ids(self.db, p),
                ["interface_brief", "arp", "bgp_summary"],
            )
            self.assertEqual(
                proj.get("collect_metric_ids_effective"),
                ["interface_brief", "arp", "bgp_summary"],
            )

    def test_legacy_migrate_moves_hf_out_of_portrait_slot(self) -> None:
        hf = self._mk_task(
            "legacy_hf",
            ne_id=self.old_portrait.ne_id,
            note="割接高频/interface_brief/x",
            purpose=mig.PURPOSE_CUTOVER_HF,
            interval_sec=60,
        )
        self.db.commit()
        p = BizMigrationProject(
            id="leg1",
            name="legacy",
            old_task_id=hf.id,
            new_task_id=self.new_portrait.id,
            monitor_template_id=self.mt.id,
        )
        self.db.add(p)
        self.db.commit()
        mig.migrate_project_hf_slots(self.db, p, commit=True)
        self.db.refresh(p)
        self.assertEqual(p.old_hf_task_id, "legacy_hf")
        self.assertEqual(p.old_task_id, "old_p")

    def test_hf_window_paused_when_project_done(self) -> None:
        out = self._create_project()
        mig.patch_project(self.db, out["id"], {"status": "done"})
        p = self.db.get(BizMigrationProject, out["id"])
        assert p is not None
        self.assertEqual(mig._hf_window_status(p), "paused")
        old_hf = self.db.get(BizStateTask, out["old_hf_task_id"])
        assert old_hf is not None
        self.assertEqual(old_hf.status, "paused")

    def test_hf_window_paused_when_ended(self) -> None:
        proj = self._create_project(
            hf_end_at=(datetime.utcnow() - timedelta(hours=1)).isoformat() + "Z",
        )
        p = self.db.get(BizMigrationProject, proj["id"])
        assert p is not None
        self.assertEqual(mig._hf_window_status(p), "paused")
        self.assertTrue(str(proj.get("hf_end_at") or "").endswith("Z"))

    def test_ensure_pauses_orphan_interval_tasks(self) -> None:
        proj = self._create_project(
            collect_metric_ids=["interface_brief", "arp"],
            metric_interval_sec={"arp": 120},
            hf_interval_sec=60,
        )
        bindings = proj.get("old_hf_bindings") or []
        self.assertGreaterEqual(len(bindings), 2)
        orphan_id = next(b["task_id"] for b in bindings if int(b["interval_sec"]) == 120)
        # Shrink to single interval group
        mig.patch_project(
            self.db,
            proj["id"],
            {"collect_metric_ids": ["interface_brief"], "metric_interval_sec": {}},
        )
        self._ensure(proj["id"], interval_sec=60)
        orphan = self.db.get(BizStateTask, orphan_id)
        assert orphan is not None
        self.assertEqual(orphan.status, "paused")

    def test_parse_dt_offset_to_naive_utc(self) -> None:
        # +08:00 wall 16:00 → UTC 08:00
        dt = mig._parse_dt("2026-09-19T16:00:00+08:00")
        assert dt is not None
        self.assertEqual(dt.tzinfo, None)
        self.assertEqual(dt.hour, 8)
        self.assertEqual(mig._dt_iso(dt), "2026-09-19T08:00:00Z")
        # Z suffix
        dt2 = mig._parse_dt("2026-09-19T08:00:00Z")
        assert dt2 is not None
        self.assertEqual(dt2, dt)

    def test_list_tasks_purpose_filter(self) -> None:
        self._mk_task("hf1", ne_id="ne-x", purpose=mig.PURPOSE_CUTOVER_HF, note="割接高频/x")
        self.db.commit()
        portrait = biz_svc.list_tasks(self.db, purpose="portrait")
        hf = biz_svc.list_tasks(self.db, purpose=mig.PURPOSE_CUTOVER_HF)
        self.assertTrue(any(x["id"] == "hf1" for x in hf))
        self.assertFalse(any(x["id"] == "hf1" for x in portrait))
        self.assertTrue(any(x["id"] == "old_p" for x in portrait))

    def test_second_ensure_reuses_hf_slot(self) -> None:
        proj = self._create_project()
        r1 = self._ensure(proj["id"])
        r2 = self._ensure(proj["id"])
        self.assertEqual(r1["project"]["old_hf_task_id"], r2["project"]["old_hf_task_id"])
        self.assertFalse(r2["old_created"])
        self.assertFalse(r2["new_created"])

    def test_create_from_ne_spawns_hf(self) -> None:
        out = self._create_project(
            old_task_id="",
            new_task_id="",
            old_ne={"source": "managed", "ne_id": "ne-old"},
            new_ne={"source": "managed", "ne_id": "ne-new"},
        )
        self.assertTrue(out["old_hf_task_id"])
        self.assertTrue(out["new_hf_task_id"])
        old_hf = self.db.get(BizStateTask, out["old_hf_task_id"])
        assert old_hf is not None
        self.assertEqual(old_hf.purpose, mig.PURPOSE_CUTOVER_HF)
        self.assertEqual(old_hf.ne_id, "ne-old")

    def test_metric_intervals_split_hf_bindings(self) -> None:
        out = self._create_project(
            collect_metric_ids=["interface_brief", "arp"],
            metric_interval_sec={"arp": 120},
            hf_interval_sec=60,
        )
        bindings = out.get("old_hf_bindings") or []
        self.assertGreaterEqual(len(bindings), 2)
        intervals = sorted(int(b["interval_sec"]) for b in bindings)
        self.assertEqual(intervals, [60, 120])
        task_ids = {b["task_id"] for b in bindings}
        self.assertEqual(len(task_ids), 2)

    def test_pin_batch_only_applies_to_matching_hf_task(self) -> None:
        proj = self._create_project(
            collect_metric_ids=["interface_brief", "arp"],
            metric_interval_sec={"arp": 120},
            hf_interval_sec=60,
        )
        bindings = proj.get("old_hf_bindings") or []
        tid_60 = next(b["task_id"] for b in bindings if int(b["interval_sec"]) == 60)
        tid_120 = next(b["task_id"] for b in bindings if int(b["interval_sec"]) == 120)
        self._mk_batch("pin60", tid_60)
        self._mk_batch("cur120", tid_120)
        p = self.db.get(BizMigrationProject, proj["id"])
        assert p is not None
        # Pin points at 60s task; arp (120s) must still use its own latest batch
        got_if = mig._current_batch_for_metric(
            self.db, p, "old", "interface_brief", pinned_batch_id="pin60"
        )
        got_arp = mig._current_batch_for_metric(
            self.db, p, "old", "arp", pinned_batch_id="pin60"
        )
        assert got_if is not None and got_arp is not None
        self.assertEqual(got_if.id, "pin60")
        self.assertEqual(got_arp.id, "cur120")

    def test_catalog_rejects_placeholder_metrics(self) -> None:
        from types import SimpleNamespace

        fake = SimpleNamespace(
            metric_id="bgp_vrf",
            kind="collect",
            profile_id="zte.bgp_vrf",
            title="BGP VRF",
            placeholders=[SimpleNamespace(name="vrf")],
        )
        with mock.patch(
            "netx_api.biz_state.profiles.profiles_for_vendor", return_value=[fake]
        ), mock.patch(
            "netx_api.lldp_shared.resolve_vendor_key", return_value="zte"
        ):
            with self.assertRaises(Exception) as cm:
                mig._catalog_item_for_metric(
                    vendor="zte", device_type="router", metric_id="bgp_vrf"
                )
        detail = getattr(cm.exception, "detail", str(cm.exception))
        self.assertIn("metric_needs_bindings:bgp_vrf", str(detail))

    def test_evaluate_marks_current_missing_no_cross_task_fallback(self) -> None:
        proj = self._create_project(
            collect_metric_ids=["interface_brief", "arp"],
            metric_interval_sec={"arp": 120},
            hf_interval_sec=60,
        )
        pid = proj["id"]
        bindings = proj.get("old_hf_bindings") or []
        tid_60 = next(b["task_id"] for b in bindings if int(b["interval_sec"]) == 60)
        new_bindings = proj.get("new_hf_bindings") or []
        new_60 = next(b["task_id"] for b in new_bindings if int(b["interval_sec"]) == 60)
        self._mk_batch("bl_old", "old_p")
        self._mk_batch("bl_new", "new_p")
        self._mk_batch("cur_old_60", tid_60)
        self._mk_batch("cur_new_60", new_60)
        # deliberately no batch on 120s tasks
        mig.patch_project(
            self.db,
            pid,
            {"old_baseline_batch_id": "bl_old", "new_baseline_batch_id": "bl_new"},
        )
        batch = mig.create_batch(
            self.db, pid, {"batch_label": "n1", "expect_set": {"ports": ["gei-0/1"]}}
        )
        sheets = [
            {"metric_id": "interface_brief", "key_fields": ["interface"], "sheet_id": "if"},
            {"metric_id": "arp", "key_fields": ["ip"], "sheet_id": "arp"},
        ]
        with mock.patch.object(
            mig, "resolve_evaluate_sheets", return_value=(sheets, [], {}, [])
        ), mock.patch.object(mig, "evaluate_metric_dual") as ev:
            ev.return_value = {
                "progress_ok": 0,
                "progress_total": 0,
                "anomaly": 0,
                "anomaly_in_expect": 0,
                "old_summary": {},
                "new_summary": {},
                "rows": [],
                "new_baseline_mode": "provided",
                "new_baseline_missing": False,
            }
            out = mig.run_evaluate(self.db, batch_id=batch["id"])
        cards = (out.get("summary") or {}).get("sheet_cards") or []
        by_mid = {c["metric_id"]: c for c in cards}
        self.assertFalse(by_mid["interface_brief"].get("current_missing"))
        self.assertTrue(by_mid["arp"].get("current_missing"))
        self.assertIn("arp", " ".join((out.get("summary") or {}).get("current_missing_metrics") or []))

    def test_baseline_expect_filters_to_collect_metrics(self) -> None:
        proj = self._create_project(collect_metric_ids=["interface_brief"])
        self._mk_batch("bl_old2", "old_p")
        mig.patch_project(self.db, proj["id"], {"old_baseline_batch_id": "bl_old2"})
        sheets = [
            {"metric_id": "interface_brief", "key_fields": ["interface"], "sheet_id": "if"},
            {"metric_id": "arp", "key_fields": ["ip"], "sheet_id": "arp"},
        ]
        with mock.patch.object(
            mig, "resolve_evaluate_sheets", return_value=(sheets, [], {}, [])
        ), mock.patch.object(mig, "_load_metric_rows", return_value=[]):
            out = mig.list_baseline_expect_objects(self.db, proj["id"])
        mids = [s["metric_id"] for s in out.get("sheets") or []]
        self.assertEqual(mids, ["interface_brief"])

    def test_project_to_dict_is_readonly(self) -> None:
        hf = self._mk_task(
            "legacy_hf2",
            ne_id=self.old_portrait.ne_id,
            note="割接高频/x",
            purpose=mig.PURPOSE_CUTOVER_HF,
        )
        self.db.commit()
        p = BizMigrationProject(
            id="ro1",
            name="ro",
            old_task_id=hf.id,
            new_task_id=self.new_portrait.id,
            monitor_template_id=self.mt.id,
        )
        self.db.add(p)
        self.db.commit()
        d = mig.project_to_dict(self.db, p)
        self.assertEqual(d["old_task_id"], "legacy_hf2")
        self.assertEqual(d["old_hf_task_id"], "")
        self.db.refresh(p)
        self.assertEqual(p.old_task_id, "legacy_hf2")


if __name__ == "__main__":
    unittest.main()
