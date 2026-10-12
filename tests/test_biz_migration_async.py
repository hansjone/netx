"""H2/H3/H5: background evaluation, manual route tables, duplicate-key scope."""
from __future__ import annotations

import unittest
from unittest import mock

from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker

from netx_api.biz_migration import service as mig
from netx_api.models import BizMigrationBatch, BizMigrationRun, BizStateBatchCommand, BizStateMetricRow
import tests.test_biz_migration_evidence as evidence


class _Inline:
    """threading.Thread stand-in: remember the target, run it on demand."""

    started: list = []

    def __init__(self, target=None, **_):
        self.target = target

    def start(self):
        _Inline.started.append(self.target)


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.ctx = evidence.EvidencePersistTests("test_evaluate_persists_display_ab_and_evidence_with_command")
        self.ctx.setUp()
        self.db = self.ctx.db
        self.factory = sessionmaker(bind=self.db.get_bind(), expire_on_commit=False)
        self.session_patch = mock.patch("netx_api.db.SessionLocal", self.factory)
        self.session_patch.start()
        _Inline.started = []
        proj = self.ctx._create_project()
        c = self.ctx
        for bid, tid in (("bl_old", c.old_p.id), ("bl_new", c.new_p.id), ("cur_old", proj["old_hf_task_id"]),
                         ("cur_new", proj["new_hf_task_id"])):
            c._mk_batch(bid, tid)
        c._seed_if_row(batch_id="bl_old", task_id=c.old_p.id, ne_id="ne-old", command_id="c1",
                       raw_command="show interface brief", interface="GE-old")
        c._seed_if_row(batch_id="cur_new", task_id=proj["new_hf_task_id"], ne_id="ne-new", command_id="c2",
                       raw_command="show interface brief", interface="xgei-new")
        mig.patch_project(self.db, proj["id"], {"old_baseline_batch_id": "bl_old", "new_baseline_batch_id": "bl_new"})
        self.batch = mig.create_batch(self.db, proj["id"], {"batch_label": "w", "expect_set": {"ports": ["gei-old"]}})
        mig.patch_batch(self.db, self.batch["id"], {"status": "active"})
        self.project_id = proj["id"]

    def tearDown(self) -> None:
        self.session_patch.stop()
        self.ctx.tearDown()

    def run_background(self):
        for target in list(_Inline.started):
            target()
        _Inline.started.clear()


class AsyncEvaluateTests(_Fixture):
    def test_evaluate_returns_a_running_run_immediately_then_completes(self):
        with mock.patch.object(mig.threading, "Thread", _Inline):
            started = mig.enqueue_evaluate(self.db, self.batch["id"])
        self.assertEqual(started["status"], "running")
        self.assertEqual(self.db.get(BizMigrationRun, started["id"]).status, "running")
        self.assertEqual(len(_Inline.started), 1)
        self.run_background()
        self.db.expire_all()
        run = self.db.get(BizMigrationRun, started["id"])
        self.assertEqual(run.status, "success")
        self.assertEqual(run.purpose, "manual")
        self.assertIn("sheet_cards", run.summary_json)
        self.assertEqual(self.db.query(BizMigrationRun).count(), 1)  # the claim row was filled, not duplicated

    def test_second_request_while_one_runs_is_409(self):
        with mock.patch.object(mig.threading, "Thread", _Inline):
            mig.enqueue_evaluate(self.db, self.batch["id"])
            with self.assertRaises(HTTPException) as exc:
                mig.enqueue_evaluate(self.db, self.batch["id"])
            with self.assertRaises(HTTPException) as exc_finish:
                mig.enqueue_finish(self.db, self.batch["id"])
        self.assertEqual(exc.exception.status_code, 409)
        self.assertEqual(exc.exception.detail, "evaluation_in_progress")
        self.assertEqual(exc_finish.exception.status_code, 409)

    def test_failure_is_recorded_on_the_run_and_releases_the_project(self):
        with mock.patch.object(mig.threading, "Thread", _Inline):
            started = mig.enqueue_evaluate(self.db, self.batch["id"])
        with mock.patch.object(mig, "run_evaluate", side_effect=HTTPException(status_code=400, detail="boom")):
            self.run_background()
        self.db.expire_all()
        run = self.db.get(BizMigrationRun, started["id"])
        self.assertEqual((run.status, run.message), ("failed", "boom"))
        with mock.patch.object(mig.threading, "Thread", _Inline):
            mig.enqueue_evaluate(self.db, self.batch["id"])  # no longer blocked

    def test_restart_cancels_orphaned_runs(self):
        with mock.patch.object(mig.threading, "Thread", _Inline):
            started = mig.enqueue_evaluate(self.db, self.batch["id"])
        self.assertEqual(mig.recover_interrupted_evaluations_on_startup(self.db), 1)
        self.db.expire_all()
        run = self.db.get(BizMigrationRun, started["id"])
        self.assertEqual((run.status, run.message), ("cancelled", "interrupted_by_restart"))
        # a cancelled claim must not be resurrected by a late worker
        with self.assertRaises(HTTPException) as exc:
            mig.run_evaluate(self.db, batch_id=self.batch["id"], _run_id=started["id"])
        self.assertEqual(exc.exception.status_code, 409)

    def test_background_evaluation_locks_the_project_only_for_the_final_write(self):
        from sqlalchemy.orm import Query

        calls: list[str] = []
        original = Query.with_for_update

        def spy(self_, *a, **k):
            calls.append("lock")
            return original(self_, *a, **k)

        with mock.patch.object(mig.threading, "Thread", _Inline):
            mig.enqueue_evaluate(self.db, self.batch["id"])  # claim = one short lock
        with mock.patch.object(Query, "with_for_update", spy):
            self.run_background()
        self.assertEqual(len(calls), 1, "computation must be unlocked; only the write takes the project lock")
        calls.clear()
        with mock.patch.object(Query, "with_for_update", spy):
            self.db.query(BizMigrationRun).delete()
            self.db.commit()
            mig.run_evaluate(self.db, batch_id=self.batch["id"])  # synchronous callers keep the old behaviour
        self.assertGreaterEqual(len(calls), 1)

    def test_finish_runs_in_the_background_and_closes_the_wave_only_on_success(self):
        with mock.patch.object(mig.threading, "Thread", _Inline):
            started = mig.enqueue_finish(self.db, self.batch["id"])
        self.assertEqual((started["status"], started["run"]["purpose"]), ("running", "acceptance"))
        self.assertEqual(self.db.get(BizMigrationBatch, self.batch["id"]).status, "active")
        self.run_background()
        self.db.expire_all()
        batch = self.db.get(BizMigrationBatch, self.batch["id"])
        self.assertEqual(batch.status, "review")
        self.assertEqual(batch.accept_run_id, started["run_id"])
        self.assertEqual(self.db.get(BizMigrationRun, started["run_id"]).status, "success")

    def test_finish_failure_keeps_the_wave_operable(self):
        with mock.patch.object(mig.threading, "Thread", _Inline):
            started = mig.enqueue_finish(self.db, self.batch["id"])
        with mock.patch.object(mig, "run_evaluate", side_effect=HTTPException(status_code=400, detail="x")):
            self.run_background()
        self.db.expire_all()
        self.assertEqual(self.db.get(BizMigrationBatch, self.batch["id"]).status, "active")
        self.assertEqual(self.db.get(BizMigrationRun, started["run_id"]).status, "failed")

    def test_auto_monitor_stays_out_of_a_running_evaluation(self):
        from netx_api.biz_migration import auto_monitor

        with mock.patch.object(mig.threading, "Thread", _Inline):
            mig.enqueue_evaluate(self.db, self.batch["id"])
        with mock.patch.object(mig, "run_evaluate") as run_evaluate:
            auto_monitor.try_auto_monitor_for_task(self.db, self.ctx.old_p.id)
        run_evaluate.assert_not_called()


class ManualRouteAndDuplicateScopeTests(_Fixture):
    """Share the fixture, add route sheets / duplicate rows to the compare template."""

    def _add_sheet(self, sheet):
        self.ctx.ct.metrics_json = [*self.ctx.ct.metrics_json, sheet]
        self.ctx.mt.collect_metric_ids_json = []
        self.db.commit()

    def test_uncollected_route_table_is_manual_pending_and_does_not_fail_acceptance(self):
        self._add_sheet({"sheet_id": "bgp_route", "title": "routes", "metric_id": "bgp_route",
                         "key_fields": ["network"], "iface_fields": [], "compare_fields": [], "row_filters": [],
                         "field_rules": []})
        out = mig.run_evaluate(self.db, batch_id=self.batch["id"], acceptance=True, purpose="acceptance")
        card = next(c for c in out["summary"]["sheet_cards"] if c["metric_id"] == "bgp_route")
        self.assertTrue(card["manual_pending"])
        self.assertFalse(card.get("current_missing"))
        self.assertIn("routes", out["summary"]["manual_pending_metrics"])
        self.assertNotIn("routes", out["summary"]["current_missing_metrics"])

    def test_duplicate_keys_outside_the_wave_are_warnings_not_failures(self):
        c = self.ctx
        for i, cmd in enumerate(("d1", "d2")):
            self.db.add(BizStateMetricRow(id=f"dup-{i}", batch_id="bl_old", metric_id="interface_brief", seq=10 + i,
                                          data_json={"interface": "GE-other", "admin": "up", "phy": "up", "prot": "up"}))
        self.db.commit()
        out = mig.run_evaluate(self.db, batch_id=self.batch["id"], acceptance=True, purpose="acceptance")
        summary = out["summary"]
        self.assertEqual(summary["duplicate_keys"], 0)
        self.assertEqual(summary["duplicate_warnings"][0]["count"] >= 1, True)

    def test_duplicate_keys_inside_the_wave_still_fail(self):
        for i in range(2):
            self.db.add(BizStateMetricRow(id=f"dup-{i}", batch_id="bl_old", metric_id="interface_brief", seq=10 + i,
                                          data_json={"interface": "GE-old", "admin": "up", "phy": "up", "prot": "up"}))
        self.db.commit()
        out = mig.run_evaluate(self.db, batch_id=self.batch["id"], acceptance=True, purpose="acceptance")
        self.assertGreaterEqual(out["summary"]["duplicate_keys"], 1)
        self.assertFalse(out["summary"]["coverage_complete"])


class BaselineCacheTests(_Fixture):
    def test_second_evaluation_does_not_read_baseline_rows_again(self):
        mig.BASELINE_ROWS.invalidate()
        real = mig._load_metric_rows
        loaded: list[str] = []

        def spy(db, *, batch_id, metric_id, **kw):
            loaded.append(batch_id)
            return real(db, batch_id=batch_id, metric_id=metric_id, **kw)

        with mock.patch.object(mig, "_load_metric_rows", spy):
            mig.run_evaluate(self.db, batch_id=self.batch["id"])
            first = list(loaded)
            loaded.clear()
            mig.run_evaluate(self.db, batch_id=self.batch["id"])
        self.assertIn("bl_old", first)
        self.assertNotIn("bl_old", loaded)
        self.assertNotIn("bl_new", loaded)
        self.assertIn("cur_new", loaded)  # current samples are always re-read

    def test_lru_is_bounded_by_total_rows_and_can_be_invalidated(self):
        cache = mig.BaselineRowCache(max_rows=5)
        rows = lambda n: [{"i": i} for i in range(n)]  # noqa: E731
        cache.get_or_load("a", "m", lambda: rows(3))
        cache.get_or_load("b", "m", lambda: rows(3))   # evicts a (3 + 3 > 5)
        calls: list[str] = []
        cache.get_or_load("a", "m", lambda: calls.append("a") or rows(3))
        self.assertEqual(calls, ["a"])
        cache.get_or_load("big", "m", lambda: rows(9))  # larger than the cap: never cached
        cache.get_or_load("big", "m", lambda: calls.append("big") or rows(9))
        self.assertEqual(calls, ["a", "big"])
        cache.invalidate("a")
        cache.get_or_load("a", "m", lambda: calls.append("a2") or rows(1))
        self.assertEqual(calls[-1], "a2")


class RedTicketResolvableTests(_Fixture):
    def _diff(self, run_id, key, color, verdict):
        from netx_api.models import BizMigrationDiff

        self.db.add(BizMigrationDiff(id=f"d-{run_id}-{key}", run_id=run_id, metric_id="interface_brief", color=color,
                                     verdict=verdict, key_json={"match_old_key": key, "sheet_id": "interface_brief",
                                                                "key_str": key}))

    def test_ticket_for_a_key_that_turned_green_is_flagged_not_closed(self):
        from netx_api.models import BizMigrationRedTicket

        pid = self.project_id
        for key in ("gei-1", "gei-2"):
            self.db.add(BizMigrationRedTicket(
                id=f"t-{key}", project_id=pid, batch_id="old", run_id="r0", metric_id="interface_brief",
                match_key_str=key, key_str=key, status="open", detail_json={"sheet_id": "interface_brief", "match_old_key": key}))
        self.db.add(BizMigrationRun(id="r1", project_id=pid, batch_id=self.batch["id"], purpose="acceptance"))
        self._diff("r1", "gei-1", "green", "migrated")
        self._diff("r1", "gei-2", "red", "anomaly")
        self.db.commit()
        mig._persist_red_tickets_from_run(self.db, project_id=pid, batch_id=self.batch["id"], run_id="r1")
        self.db.commit()
        from netx_api.models import BizMigrationRedTicket as T

        flagged = {t.id: mig._red_ticket_to_dict(t) for t in self.db.query(T)}
        self.assertTrue(flagged["t-gei-1"]["resolvable"])
        self.assertFalse(flagged["t-gei-2"]["resolvable"])
        self.assertEqual(flagged["t-gei-1"]["status"], "open")

        out = mig.resolve_resolvable_red_tickets(self.db, pid)
        self.assertEqual(out["ids"], ["t-gei-1"])
        self.assertEqual(self.db.get(T, "t-gei-1").status, "resolved")
        self.assertEqual(self.db.get(T, "t-gei-2").status, "open")


class UncoveredSliceTests(_Fixture):
    def test_a_slice_one_side_never_collected_is_not_collected_not_lost(self):
        sheet = {"sheet_id": "bgp_peer.ipv4", "metric_id": "bgp_peer", "key_fields": ["neighbor"],
                 "iface_fields": [], "compare_fields": ["state"],
                 "row_filters": [{"op": "eq", "field": "afi", "value": "ipv4"}], "field_rules": []}
        for batch, profile in (("cur_old", "zte.bgp_ipv4_summary"), ("cur_new", "zte.bgp_vpnv4_summary")):
            self.db.add(BizStateBatchCommand(id=f"slice-{batch}", batch_id=batch, metric_id="bgp_peer",
                                             profile_id=profile, parse_status="ok", row_count=0, raw_line_count=0))
        self.db.commit()
        self.assertEqual(mig._uncovered_slice_sides(self.db, sheet, "bgp_peer", "cur_old", "cur_new"), ["new"])
        self.assertEqual(mig._uncovered_slice_sides(self.db, {**sheet, "row_filters": []}, "bgp_peer",
                                                    "cur_old", "cur_new"), [])
