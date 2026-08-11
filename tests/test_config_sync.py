"""Unit tests for config sync commands, codec, snapshot overwrite rules, recovery."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

from netx_api.config_sync_codec import compress_text, decompress_text
from netx_api.config_sync_commands import command_list, commands_for_vendor, normalize_vendor_key
from netx_api.config_sync_recovery import recover_config_sync_on_startup
from netx_api.config_sync_runner import _claim_task, _save_success_snapshot
from netx_api.config_sync_service import prune_config_sync_cycles
from netx_api.db import Base, SessionLocal, engine
from netx_api.models import ConfigSyncCycle, ConfigSyncTask


class ConfigSyncCommandsTests(unittest.TestCase):
    def test_normalize_vendor_keys(self):
        self.assertEqual(normalize_vendor_key("Cisco", "ios"), "cisco")
        self.assertEqual(normalize_vendor_key("ZTE", "zxros"), "zte")
        self.assertEqual(normalize_vendor_key("Huawei", "vrp"), "huawei")
        self.assertEqual(normalize_vendor_key("H3C", "comware"), "h3c")
        self.assertEqual(normalize_vendor_key("Juniper", "junos"), "juniper")
        self.assertEqual(normalize_vendor_key("Nokia", "sros"), "nokia")
        self.assertEqual(normalize_vendor_key("Ericsson", ""), "ericsson")
        self.assertEqual(normalize_vendor_key("Acme", "router"), "other")

    def test_command_matrix(self):
        self.assertEqual(commands_for_vendor("Cisco", "").primary, "show running-config")
        self.assertEqual(commands_for_vendor("ZTE", "").primary, "show running-config")
        self.assertEqual(commands_for_vendor("Huawei", "").primary, "display current-configuration")
        self.assertEqual(commands_for_vendor("H3C", "").primary, "display current-configuration")
        self.assertEqual(commands_for_vendor("Nokia", "").primary, "admin display-config")
        self.assertEqual(commands_for_vendor("Ericsson", "").primary, "show configuration")
        self.assertIsNone(commands_for_vendor("UnknownVendor", ""))

    def test_juniper_dual_commands(self):
        cmds = commands_for_vendor("Juniper", "junos")
        assert cmds is not None
        self.assertEqual(cmds.primary, "show configuration | display set")
        self.assertEqual(cmds.alt, "show configuration | no-more")
        self.assertEqual(
            command_list(cmds),
            ["show configuration | display set", "show configuration | no-more"],
        )


class ConfigSyncCodecTests(unittest.TestCase):
    def test_zlib_roundtrip(self):
        text = "hostname router1\ninterface GigabitEthernet0/0\n"
        blob, digest, plain_size, zlib_size = compress_text(text)
        self.assertEqual(plain_size, len(text.encode("utf-8")))
        self.assertGreater(zlib_size, 0)
        self.assertEqual(len(digest), 64)
        self.assertEqual(decompress_text(blob), text)

    def test_empty_decompress(self):
        self.assertEqual(decompress_text(None), "")
        self.assertEqual(decompress_text(b""), "")


class ConfigSyncSnapshotOverwriteTests(unittest.TestCase):
    @patch("netx_api.config_sync_runner.SessionLocal")
    def test_success_updates_snapshot(self, session_local):
        db = MagicMock()
        session_local.return_value = db
        existing = MagicMock()
        existing.source = "managed"
        existing.target_id = "ne1"
        existing.config_sha256 = "old"
        existing.config_alt_sha256 = ""
        existing.config_zlib = b"old"
        existing.config_alt_zlib = None
        existing.plain_size = 1
        existing.plain_alt_size = 0
        existing.zlib_size = 1
        existing.zlib_alt_size = 0
        existing.commands_json = []
        existing.collected_at = datetime.now(timezone.utc)
        existing.last_cycle_id = "c0"
        existing.last_task_id = "t0"
        existing.vendor = "Cisco"
        existing.device_type = "ios"
        existing.ne_name = "r1"
        existing.ne_ip = "1.1.1.1"
        db.get.side_effect = lambda model, key: existing if "Snapshot" in str(model) else MagicMock(history_keep=3)

        _save_success_snapshot(
            source="managed",
            target_id="ne1",
            vendor="Cisco",
            device_type="ios",
            ne_name="r1",
            ne_ip="1.1.1.1",
            primary_text="hostname r1\n",
            alt_text=None,
            commands=["show running-config"],
            cycle_id="c1",
            task_id="t1",
        )
        self.assertEqual(existing.last_cycle_id, "c1")
        self.assertEqual(existing.last_task_id, "t1")
        self.assertTrue(existing.config_sha256)
        self.assertNotEqual(existing.config_sha256, "old")
        db.commit.assert_called()

    @patch("netx_api.config_sync_runner._save_success_snapshot")
    @patch("netx_api.config_sync_runner._collect_with_timeout", side_effect=TimeoutError("boom"))
    @patch("netx_api.config_sync_runner.resolve_cli_target")
    @patch("netx_api.config_sync_runner._claim_task", return_value=True)
    @patch("netx_api.config_sync_runner.SessionLocal")
    def test_fail_does_not_overwrite_snapshot(self, session_local, _claim, resolve, _collect, save_snap):
        from netx_api.config_sync_runner import _run_single

        db = MagicMock()
        session_local.return_value = db
        task = MagicMock()
        task.source = "managed"
        task.target_id = "ne1"
        task.vendor = "Cisco"
        task.ne_name = "r1"
        task.ne_ip = "1.1.1.1"
        db.get.return_value = task
        resolve.return_value = (
            {
                "ip_address": "1.1.1.1",
                "protocol": "ssh",
                "username": "admin",
                "password": "secret",
                "hop_enabled": False,
            },
            {"vendor": "Cisco", "device_type": "ios", "name": "r1", "ip_address": "1.1.1.1"},
        )

        with patch("netx_api.config_sync_runner._update_task") as update_task, patch(
            "netx_api.config_sync_runner.sync_cycle_progress"
        ), patch("netx_api.config_sync_runner.finalize_cycle"):
            _run_single("c1", "t1")
            save_snap.assert_not_called()
            args = update_task.call_args
            self.assertEqual(args[0][0], "t1")
            self.assertEqual(args[1]["status"], "fail")

    @patch("netx_api.config_sync_runner._save_success_snapshot")
    @patch(
        "netx_api.config_sync_runner._collect_with_timeout",
        return_value=["set system host-name r1", "system {\n  host-name r1;\n}"],
    )
    @patch("netx_api.config_sync_runner.resolve_cli_target")
    @patch("netx_api.config_sync_runner._claim_task", return_value=True)
    @patch("netx_api.config_sync_runner.SessionLocal")
    def test_juniper_dual_fields_passed_to_save(self, session_local, _claim, resolve, _collect, save_snap):
        from netx_api.config_sync_runner import _run_single

        db = MagicMock()
        session_local.return_value = db
        task = MagicMock()
        task.source = "managed"
        task.target_id = "ne1"
        task.vendor = "Juniper"
        task.ne_name = "r1"
        task.ne_ip = "1.1.1.1"
        db.get.return_value = task
        resolve.return_value = (
            {
                "ip_address": "1.1.1.1",
                "protocol": "ssh",
                "username": "admin",
                "password": "secret",
                "hop_enabled": False,
            },
            {"vendor": "Juniper", "device_type": "junos", "name": "r1", "ip_address": "1.1.1.1"},
        )

        with patch("netx_api.config_sync_runner._update_task"), patch(
            "netx_api.config_sync_runner.sync_cycle_progress"
        ), patch("netx_api.config_sync_runner.finalize_cycle"):
            _run_single("c1", "t1")
            save_snap.assert_called_once()
            kwargs = save_snap.call_args.kwargs
            self.assertEqual(kwargs["primary_text"], "set system host-name r1")
            self.assertIn("host-name r1", kwargs["alt_text"])
            self.assertEqual(
                kwargs["commands"],
                ["show configuration | display set", "show configuration | no-more"],
            )


class ConfigSyncClaimTests(unittest.TestCase):
    @patch("netx_api.config_sync_runner.SessionLocal")
    def test_claim_pending_to_running(self, session_local):
        db = MagicMock()
        session_local.return_value = db
        task = MagicMock()
        task.status = "pending"
        cycle = MagicMock()
        cycle.status = "running"

        def get_side(model, _id):
            name = getattr(model, "__name__", str(model))
            if "Task" in name:
                return task
            return cycle

        db.get.side_effect = get_side
        ok = _claim_task("c1", "t1")
        self.assertTrue(ok)
        self.assertEqual(task.status, "running")
        db.commit.assert_called()

    @patch("netx_api.config_sync_runner.SessionLocal")
    def test_claim_skipped_when_paused(self, session_local):
        db = MagicMock()
        session_local.return_value = db
        task = MagicMock()
        task.status = "pending"
        cycle = MagicMock()
        cycle.status = "paused"
        db.get.side_effect = lambda model, _id: task if "Task" in getattr(model, "__name__", "") else cycle
        self.assertFalse(_claim_task("c1", "t1"))
        self.assertEqual(task.status, "pending")


class ConfigSyncRecoveryTests(unittest.TestCase):
    @patch("netx_api.config_sync_recovery.prune_config_sync_cycles")
    @patch("netx_api.config_sync_recovery.dispatch_cycle", return_value=2)
    @patch("netx_api.config_sync_recovery.finalize_cycle")
    @patch("netx_api.config_sync_recovery.sync_cycle_progress")
    def test_requeues_orphans_and_resumes(self, _sync, _fin, dispatch, _prune):
        cycle = MagicMock()
        cycle.id = "c1"
        cycle.status = "running"
        cycle.created_at = datetime.now(timezone.utc)
        cycle.started_at = datetime.now(timezone.utc)
        cycle.error_message = ""
        cycle.ended_at = None

        orphan = MagicMock()
        orphan.status = "running"
        orphan.message = ""
        orphan.started_at = datetime.now(timezone.utc)
        orphan.ended_at = None

        pending = MagicMock()
        pending.status = "pending"
        pending.id = "t2"

        db = MagicMock()
        db.query.side_effect = [
            MagicMock(filter=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[cycle])))),
            MagicMock(filter=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[orphan])))),
            MagicMock(
                filter=MagicMock(
                    return_value=MagicMock(all=MagicMock(return_value=[orphan, pending]))
                )
            ),
        ]
        db.refresh = MagicMock()

        resumed = recover_config_sync_on_startup(db)

        self.assertEqual(orphan.status, "pending")
        self.assertEqual(orphan.message, "requeued_after_restart")
        self.assertIsNone(orphan.started_at)
        self.assertEqual(resumed, 2)
        dispatch.assert_called_once_with("c1")

    @patch("netx_api.config_sync_recovery.prune_config_sync_cycles")
    @patch("netx_api.config_sync_recovery.dispatch_cycle")
    @patch("netx_api.config_sync_recovery.finalize_cycle")
    @patch("netx_api.config_sync_recovery.sync_cycle_progress")
    def test_closes_older_active_keeps_newest(self, _sync, _fin, dispatch, _prune):
        old = MagicMock()
        old.id = "old"
        old.status = "running"
        old.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        old.error_message = ""
        old.ended_at = None

        new = MagicMock()
        new.id = "new"
        new.status = "running"
        new.created_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
        new.started_at = datetime.now(timezone.utc)
        new.error_message = ""
        new.ended_at = None

        db = MagicMock()
        db.query.side_effect = [
            MagicMock(filter=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[old, new])))),
            MagicMock(filter=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
            MagicMock(filter=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
            MagicMock(filter=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
        ]
        db.refresh = MagicMock()
        dispatch.return_value = 0

        recover_config_sync_on_startup(db)

        self.assertEqual(old.status, "fail")
        self.assertEqual(old.error_message, "superseded_active_cycle")
        _fin.assert_called()
        dispatch.assert_not_called()


class ConfigSyncCyclePruneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        Base.metadata.create_all(bind=engine)
        with engine.begin() as conn:
            try:
                conn.exec_driver_sql(
                    "ALTER TABLE config_sync_policy ADD COLUMN IF NOT EXISTS cycle_keep INTEGER DEFAULT 30"
                )
            except Exception:
                pass

    def setUp(self) -> None:
        self.db = SessionLocal()
        self.db.query(ConfigSyncTask).delete()
        self.db.query(ConfigSyncCycle).delete()
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_prune_cycles_keeps_newest_and_active(self) -> None:
        now = datetime.utcnow()
        for i in range(5):
            cid = uuid4().hex
            self.db.add(
                ConfigSyncCycle(
                    id=cid,
                    trigger_mode="manual",
                    status="success",
                    created_at=now - timedelta(minutes=5 - i),
                    ended_at=now - timedelta(minutes=5 - i),
                )
            )
            self.db.add(
                ConfigSyncTask(
                    id=uuid4().hex,
                    cycle_id=cid,
                    source="managed",
                    target_id=f"ne-{i}",
                    status="success",
                )
            )
        open_id = uuid4().hex
        self.db.add(
            ConfigSyncCycle(
                id=open_id,
                trigger_mode="manual",
                status="running",
                created_at=now,
            )
        )
        self.db.commit()
        dropped = prune_config_sync_cycles(self.db, keep=2)
        self.assertEqual(dropped, 3)
        left = {r.id for r in self.db.query(ConfigSyncCycle).all()}
        self.assertIn(open_id, left)
        self.assertEqual(len(left), 3)
        self.assertEqual(self.db.query(ConfigSyncTask).count(), 2)


if __name__ == "__main__":
    unittest.main()
