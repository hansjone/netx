"""LLDP collect policy / dashboard (network topology management)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from uuid import uuid4

from netx_api.db import Base, SessionLocal, engine
from netx_api.device_types import LLDP_DISCOVERED_NE_SOURCE
from netx_api.lldp_collect_schemas import LldpCollectPolicyUpdate
from netx_api.lldp_collect_service import (
    build_discover_request,
    ensure_policy,
    get_dashboard,
    has_running_job,
    last_finished_job,
    next_due_at,
    update_policy,
)
from netx_api.models import LldpCollectPolicy, ManagedNE, TopoDiscoverJob, TopoDiscoverJobItem
from netx_api.topology_service import prune_discover_jobs, reclaim_stale_discover_jobs


class LldpCollectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        Base.metadata.create_all(bind=engine)
        from netx_api.topology_migrate import ensure_topology_schema

        with engine.begin() as conn:
            ensure_topology_schema(conn)

    def setUp(self) -> None:
        self.db = SessionLocal()
        self.db.query(TopoDiscoverJobItem).delete()
        self.db.query(TopoDiscoverJob).delete()
        self.db.query(LldpCollectPolicy).delete()
        self.db.commit()

    def tearDown(self) -> None:
        # Do not leave scheduled collect enabled in shared DB.
        row = self.db.get(LldpCollectPolicy, 1)
        if row is not None:
            row.enabled = False
            self.db.commit()
        self.db.close()

    def test_policy_defaults_disabled(self) -> None:
        row = ensure_policy(self.db)
        self.assertEqual(row.id, 1)
        self.assertFalse(row.enabled)
        dash = get_dashboard(self.db)
        self.assertFalse(dash.policy.enabled)
        self.assertIsNone(dash.next_due_at)
        self.assertEqual(dash.policy.history_keep, 30)

    def test_policy_enable_updates(self) -> None:
        ensure_policy(self.db)
        out = update_policy(
            self.db,
            LldpCollectPolicyUpdate(
                enabled=True,
                interval_hours=48,
                concurrency=6,
                scope_mode="all",
                auto_add_unmatched=True,
                history_keep=5,
            ),
        )
        self.assertTrue(out.enabled)
        self.assertEqual(out.interval_hours, 48)
        self.assertEqual(out.interval_days, 2)
        self.assertEqual(out.concurrency, 6)
        self.assertEqual(out.history_keep, 5)
        due = next_due_at(self.db, ensure_policy(self.db))
        self.assertIsNotNone(due)

    def test_next_due_uses_hours_and_ignores_manual(self) -> None:
        policy = ensure_policy(self.db)
        policy.enabled = True
        policy.interval_hours = 6
        policy.interval_days = 1
        self.db.commit()
        now = datetime.utcnow()
        sched = TopoDiscoverJob(
            id=uuid4().hex,
            scope="all_inventory",
            trigger_mode="schedule",
            status="done",
            ended_at=now - timedelta(hours=1),
            created_at=now - timedelta(hours=2),
            updated_at=now - timedelta(hours=1),
        )
        manual = TopoDiscoverJob(
            id=uuid4().hex,
            scope="all_inventory",
            trigger_mode="manual",
            status="done",
            ended_at=now,
            created_at=now,
            updated_at=now,
        )
        self.db.add(sched)
        self.db.add(manual)
        self.db.commit()
        due = next_due_at(self.db, ensure_policy(self.db))
        self.assertIsNotNone(due)
        assert due is not None
        self.assertEqual(due, sched.ended_at + timedelta(hours=6))

    def test_start_discover_rejects_second_while_running(self) -> None:
        from netx_api.topology_schemas import FabricDiscoverRequest
        from netx_api.topology_service import start_discover_job

        now = datetime.utcnow()
        running = TopoDiscoverJob(
            id=uuid4().hex,
            scope="all_inventory",
            trigger_mode="manual",
            status="running",
            created_at=now,
            updated_at=now,
            started_at=now,
        )
        self.db.add(running)
        ensure_policy(self.db)
        self.db.commit()
        with self.assertRaises(Exception) as ctx:
            start_discover_job(self.db, FabricDiscoverRequest(scope="ne_ids", ne_ids=["x"]))
        detail = getattr(ctx.exception, "detail", str(ctx.exception))
        self.assertEqual(detail, "lldp_collect_already_running")

    def test_build_request_respects_source(self) -> None:
        suffix = uuid4().hex[:8]
        ne = ManagedNE(
            id=f"m-{suffix}",
            name=f"M-{suffix}",
            ip_address=f"203.0.113.{(int(suffix[:2], 16) % 200) + 1}",
            vendor="Cisco",
            device_type="cisco_ios",
        )
        self.db.add(ne)
        policy = ensure_policy(self.db)
        policy.scope_mode = "selected"
        # Same id string marked as ume should NOT resolve via managed path when building lists.
        policy.selected_targets = [
            {"source": "managed", "id": ne.id},
            {"source": "ume", "id": f"ume-{suffix}"},
        ]
        self.db.commit()
        req = build_discover_request(ensure_policy(self.db))
        self.assertEqual(req.managed_ne_ids, [ne.id])
        self.assertEqual(req.ume_ne_ids, [f"ume-{suffix}"])
        self.assertEqual(req.ne_ids, [])
        self.db.delete(ne)
        self.db.commit()

    def test_prune_discover_jobs_keeps_newest(self) -> None:
        now = datetime.utcnow()
        ids: list[str] = []
        for i in range(5):
            jid = uuid4().hex
            ids.append(jid)
            self.db.add(
                TopoDiscoverJob(
                    id=jid,
                    scope="all_inventory",
                    trigger_mode="manual",
                    status="done",
                    created_at=now - timedelta(minutes=5 - i),
                    updated_at=now - timedelta(minutes=5 - i),
                    ended_at=now - timedelta(minutes=5 - i),
                )
            )
            self.db.add(
                TopoDiscoverJobItem(
                    id=uuid4().hex,
                    job_id=jid,
                    ne_name=f"n{i}",
                    raw_preview="x" * 20,
                    created_at=now,
                )
            )
        # Keep one open job — must survive prune.
        open_id = uuid4().hex
        self.db.add(
            TopoDiscoverJob(
                id=open_id,
                scope="all_inventory",
                trigger_mode="manual",
                status="running",
                created_at=now,
                updated_at=now,
            )
        )
        self.db.commit()
        dropped = prune_discover_jobs(self.db, keep=2)
        self.assertEqual(dropped, 3)
        left = {r.id for r in self.db.query(TopoDiscoverJob).all()}
        self.assertIn(open_id, left)
        self.assertEqual(len(left), 3)  # 2 finished + 1 running

    def test_reclaim_force_all_open_on_startup(self) -> None:
        now = datetime.utcnow()
        job = TopoDiscoverJob(
            id=uuid4().hex,
            scope="all_inventory",
            trigger_mode="schedule",
            status="running",
            total=10,
            done=1,
            created_at=now,
            updated_at=now,
            started_at=now,
        )
        self.db.add(job)
        self.db.commit()
        closed = reclaim_stale_discover_jobs(self.db, force_all_open=True)
        self.assertEqual(closed, 1)
        self.db.refresh(job)
        self.assertEqual(job.status, "failed")
        self.assertIn("stale_running_reset_on_startup", job.error or "")
        self.assertIsNone(has_running_job(self.db))

    def test_reclaim_running_by_stale_updated_at(self) -> None:
        old = datetime.utcnow() - timedelta(hours=5)
        job = TopoDiscoverJob(
            id=uuid4().hex,
            scope="ne_ids",
            trigger_mode="manual",
            status="running",
            total=3,
            done=0,
            created_at=old,
            updated_at=old,
            started_at=old,
        )
        self.db.add(job)
        self.db.commit()
        closed = reclaim_stale_discover_jobs(self.db)
        self.assertEqual(closed, 1)
        self.db.refresh(job)
        self.assertEqual(job.status, "failed")
        self.assertIn("running_stale_timeout", job.error or "")

    def test_all_inventory_includes_ume(self) -> None:
        from netx_api.models import UmeInventoryNE
        from netx_api.topology_discover_common import _resolve_scan_targets
        from netx_api.topology_schemas import FabricDiscoverRequest

        suffix = uuid4().hex[:8]
        managed = ManagedNE(
            id=f"m-{suffix}",
            name=f"M-{suffix}",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address="10.20.30.1",
        )
        placeholder = ManagedNE(
            id=f"ph-{suffix}",
            name=f"PH-{suffix}",
            vendor="Other",
            device_type="generic",
            ip_address="",
            source=LLDP_DISCOVERED_NE_SOURCE,
        )
        ume_only = UmeInventoryNE(
            ne_id=f"u-{suffix}",
            ne_name=f"U-{suffix}",
            ip_address="10.20.30.2",
            vendor="ZTE",
            ne_type="ZXCTN",
        )
        # Same IP as managed — should be skipped to avoid double SSH.
        ume_dup = UmeInventoryNE(
            ne_id=f"udup-{suffix}",
            ne_name=f"UDup-{suffix}",
            ip_address="10.20.30.1",
            vendor="ZTE",
            ne_type="ZXCTN",
        )
        self.db.add(managed)
        self.db.add(placeholder)
        self.db.add(ume_only)
        self.db.add(ume_dup)
        self.db.commit()

        targets = _resolve_scan_targets(self.db, FabricDiscoverRequest(scope="all_inventory"))
        ids = {(t.get("ne_id"), t.get("ume_ne_id")) for t in targets}
        self.assertIn((managed.id, ""), ids)
        self.assertIn((ume_only.ne_id, ume_only.ne_id), ids)
        self.assertNotIn((ume_dup.ne_id, ume_dup.ne_id), ids)
        self.assertNotIn((placeholder.id, ""), ids)

        # Cleanup shared DB rows created by this test.
        self.db.delete(managed)
        self.db.delete(placeholder)
        self.db.delete(ume_only)
        self.db.delete(ume_dup)
        self.db.commit()

    def test_pause_resume_stop_job_control(self) -> None:
        from netx_api.topology_discover_jobs import (
            pause_discover_job,
            resume_discover_job,
            stop_discover_job,
        )
        from netx_api.topology_schemas import FabricDiscoverRequest
        from netx_api.topology_service import start_discover_job

        now = datetime.utcnow()
        job = TopoDiscoverJob(
            id=uuid4().hex,
            scope="ne_ids",
            trigger_mode="manual",
            ne_ids_json=["managed:m1"],
            status="running",
            total=10,
            done=3,
            created_at=now,
            updated_at=now,
            started_at=now,
        )
        self.db.add(job)
        ensure_policy(self.db)
        self.db.commit()

        paused = pause_discover_job(self.db, job.id)
        self.assertEqual(paused.status, "paused")
        self.assertIsNotNone(has_running_job(self.db))

        with self.assertRaises(Exception) as ctx:
            start_discover_job(self.db, FabricDiscoverRequest(scope="ne_ids", ne_ids=["x"]))
        self.assertEqual(getattr(ctx.exception, "detail", None), "lldp_collect_already_running")

        # Resume without live worker re-spawns thread; mark done so it has no work and
        # avoid racing real SSH — use stop path for terminal instead when remaining=0.
        job.done = 10
        self.db.commit()
        with self.assertRaises(Exception) as ctx2:
            resume_discover_job(self.db, job.id)
        self.assertEqual(getattr(ctx2.exception, "detail", None), "no_pending_targets")

        job.done = 3
        job.status = "paused"
        self.db.commit()
        stopped = stop_discover_job(self.db, job.id)
        self.assertEqual(stopped.status, "cancelled")
        self.assertEqual(stopped.error, "stopped_by_user")
        self.assertIsNone(has_running_job(self.db))
        last = last_finished_job(self.db)
        self.assertIsNotNone(last)
        assert last is not None
        self.assertEqual(last.id, job.id)
        self.assertEqual(last.status, "cancelled")

    def test_paused_survives_startup_reclaim(self) -> None:
        now = datetime.utcnow()
        job = TopoDiscoverJob(
            id=uuid4().hex,
            scope="all_inventory",
            trigger_mode="manual",
            status="paused",
            total=5,
            done=1,
            created_at=now,
            updated_at=now,
            started_at=now,
        )
        self.db.add(job)
        self.db.commit()
        closed = reclaim_stale_discover_jobs(self.db, force_all_open=True)
        self.assertEqual(closed, 0)
        self.db.refresh(job)
        self.assertEqual(job.status, "paused")

    def test_recover_resumes_interrupted_running_job(self) -> None:
        from unittest.mock import patch

        from netx_api.topology_discover_jobs import recover_lldp_discover_on_startup

        now = datetime.utcnow()
        older = TopoDiscoverJob(
            id=uuid4().hex,
            scope="all_inventory",
            trigger_mode="manual",
            status="running",
            total=10,
            done=1,
            created_at=now - timedelta(minutes=5),
            updated_at=now - timedelta(minutes=5),
            started_at=now - timedelta(minutes=5),
        )
        primary = TopoDiscoverJob(
            id=uuid4().hex,
            scope="ne_ids",
            trigger_mode="manual",
            ne_ids_json=["managed:m1"],
            status="running",
            total=10,
            done=3,
            created_at=now,
            updated_at=now,
            started_at=now,
        )
        self.db.add(older)
        self.db.add(primary)
        ensure_policy(self.db)
        self.db.commit()

        with patch("netx_api.topology_discover_jobs.threading.Thread") as thread_cls:
            thread_cls.return_value.start = lambda: None
            n = recover_lldp_discover_on_startup(self.db)
        self.assertEqual(n, 1)
        self.db.refresh(older)
        self.db.refresh(primary)
        self.assertEqual(older.status, "failed")
        self.assertIn("superseded_active_job", older.error or "")
        self.assertEqual(primary.status, "running")
        thread_cls.assert_called_once()
        kwargs = thread_cls.call_args.kwargs
        self.assertTrue(kwargs.get("kwargs", {}).get("resume"))

    def test_recover_keeps_paused_without_auto_resume(self) -> None:
        from netx_api.topology_discover_jobs import recover_lldp_discover_on_startup

        now = datetime.utcnow()
        job = TopoDiscoverJob(
            id=uuid4().hex,
            scope="all_inventory",
            trigger_mode="manual",
            status="paused",
            total=8,
            done=2,
            created_at=now,
            updated_at=now,
            started_at=now,
        )
        self.db.add(job)
        self.db.commit()
        n = recover_lldp_discover_on_startup(self.db)
        self.assertEqual(n, 0)
        self.db.refresh(job)
        self.assertEqual(job.status, "paused")
        self.assertIsNotNone(has_running_job(self.db))

    def test_item_needs_retry_covers_hard_and_weak(self) -> None:
        from netx_api.lldp_collect_service import item_needs_retry

        hard = TopoDiscoverJobItem(id=uuid4().hex, job_id="j", ok=False, error="exec_failed")
        weak_empty = TopoDiscoverJobItem(
            id=uuid4().hex, job_id="j", ok=True, error="empty_cli_output"
        )
        weak_stub = TopoDiscoverJobItem(
            id=uuid4().hex, job_id="j", ok=True, parser_stub=True, error="parser_stub"
        )
        ok = TopoDiscoverJobItem(id=uuid4().hex, job_id="j", ok=True, error="")
        self.assertTrue(item_needs_retry(hard))
        self.assertTrue(item_needs_retry(weak_empty))
        self.assertTrue(item_needs_retry(weak_stub))
        self.assertFalse(item_needs_retry(ok))

    def test_collect_retry_targets_and_start_retry_failed(self) -> None:
        from unittest.mock import patch

        from netx_api.lldp_collect_service import (
            collect_retry_targets,
            find_retry_source_job,
            start_collect,
        )
        from netx_api.topology_schemas import FabricDiscoverJobOut

        now = datetime.utcnow()
        job_id = uuid4().hex
        older_ok = uuid4().hex
        # Older finished job with a failure — should not be picked when a newer one has fails.
        self.db.add(
            TopoDiscoverJob(
                id=older_ok,
                scope="all_inventory",
                trigger_mode="manual",
                status="done",
                total=1,
                done=1,
                created_at=now - timedelta(hours=2),
                updated_at=now - timedelta(hours=2),
                ended_at=now - timedelta(hours=2),
            )
        )
        self.db.add(
            TopoDiscoverJobItem(
                id=uuid4().hex,
                job_id=older_ok,
                ne_id="old-fail",
                ok=False,
                error="timeout",
                created_at=now - timedelta(hours=2),
            )
        )
        self.db.add(
            TopoDiscoverJob(
                id=job_id,
                scope="all_inventory",
                trigger_mode="manual",
                status="done",
                total=3,
                done=3,
                created_at=now - timedelta(hours=1),
                updated_at=now - timedelta(hours=1),
                ended_at=now - timedelta(hours=1),
            )
        )
        self.db.add(
            TopoDiscoverJobItem(
                id=uuid4().hex,
                job_id=job_id,
                ne_id="ok-ne",
                ok=True,
                error="",
                created_at=now,
            )
        )
        self.db.add(
            TopoDiscoverJobItem(
                id=uuid4().hex,
                job_id=job_id,
                ne_id="fail-ne",
                ok=False,
                error="cli_budget_unavailable",
                created_at=now,
            )
        )
        self.db.add(
            TopoDiscoverJobItem(
                id=uuid4().hex,
                job_id=job_id,
                ume_ne_id="ume-weak",
                ok=True,
                error="empty_cli_output",
                created_at=now,
            )
        )
        ensure_policy(self.db)
        self.db.commit()

        src = find_retry_source_job(self.db)
        self.assertEqual(src.id, job_id)
        managed, ume = collect_retry_targets(self.db, src)
        self.assertEqual(managed, ["fail-ne"])
        self.assertEqual(ume, ["ume-weak"])

        dash = get_dashboard(self.db)
        self.assertIsNotNone(dash.last_job)
        assert dash.last_job is not None
        self.assertEqual(dash.last_job.fail_count, 2)
        self.assertEqual(dash.last_job.success_count, 1)

        fake_out = FabricDiscoverJobOut(
            id=uuid4().hex,
            scope="ne_ids",
            trigger_mode="retry_failed",
            status="pending",
            total=2,
            done=0,
        )
        with patch(
            "netx_api.lldp_collect_service.start_discover_job", return_value=fake_out
        ) as start_mock:
            out = start_collect(self.db, mode="retry_failed")
        self.assertTrue(out["ok"])
        self.assertEqual(out["job"]["trigger_mode"], "retry_failed")
        req = start_mock.call_args.args[1]
        self.assertEqual(req.scope, "ne_ids")
        self.assertEqual(req.managed_ne_ids, ["fail-ne"])
        self.assertEqual(req.ume_ne_ids, ["ume-weak"])
        self.assertEqual(start_mock.call_args.kwargs.get("trigger_mode"), "retry_failed")

    def test_start_retry_failed_no_targets(self) -> None:
        from fastapi import HTTPException

        from netx_api.lldp_collect_service import start_collect

        now = datetime.utcnow()
        job_id = uuid4().hex
        self.db.add(
            TopoDiscoverJob(
                id=job_id,
                scope="all_inventory",
                trigger_mode="manual",
                status="done",
                total=1,
                done=1,
                created_at=now,
                updated_at=now,
                ended_at=now,
            )
        )
        self.db.add(
            TopoDiscoverJobItem(
                id=uuid4().hex,
                job_id=job_id,
                ne_id="ok-only",
                ok=True,
                error="",
                created_at=now,
            )
        )
        ensure_policy(self.db)
        self.db.commit()
        with self.assertRaises(HTTPException) as ctx:
            start_collect(self.db, mode="retry_failed")
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.detail, "no_failed_job")


if __name__ == "__main__":
    unittest.main()