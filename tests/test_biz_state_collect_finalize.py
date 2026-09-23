"""Finalize / reconnect after long-collect DB disconnect."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from netx_api.biz_state import collect_runner as runner
from netx_api.db import Base
from netx_api.models import BizStateBatch, BizStateTask


class BizStateCollectFinalizeTests(unittest.TestCase):
    def setUp(self) -> None:
        # StaticPool: all sessions share one :memory: SQLite DB.
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        TestingSession = sessionmaker(
            bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
        )
        Base.metadata.create_all(bind=engine)
        self.Session = TestingSession
        self.db = TestingSession()
        self.task = BizStateTask(
            id="t-finalize",
            source="managed",
            ne_id="ne1",
            ne_name="PE1",
            status="running",
            collect_running=True,
            interval_sec=300,
        )
        self.db.add(self.task)
        self.batch = BizStateBatch(
            id="b-finalize",
            task_id="t-finalize",
            status="running",
            command_count=55,
            row_count=6606,
            message="",
        )
        self.db.add(self.batch)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_finalize_partial_after_heavy_timeout(self) -> None:
        with patch.object(runner, "SessionLocal", self.Session):
            with patch(
                "netx_api.biz_state.compare_service.schedule_auto_compare_for_task",
            ) as sched:
                status = runner._finalize_batch_status(
                    batch_id="b-finalize",
                    task_id="t-finalize",
                    cmd_count=40,
                    total_rows=1000,
                    any_fail=True,
                    any_ok=True,
                    lane_errors=["RuntimeError: biz_state_heavy_timeout (2400s)"],
                )
        self.assertEqual(status, "partial")
        sched.assert_not_called()
        self.db.expire_all()
        batch = self.db.get(BizStateBatch, "b-finalize")
        assert batch is not None
        self.assertEqual(batch.status, "partial")
        self.assertEqual(batch.command_count, 55)
        self.assertEqual(batch.row_count, 6606)
        self.assertIn("biz_state_heavy_timeout", batch.message or "")
        self.assertIsNotNone(batch.ended_at)

    def test_finalize_retries_once_on_operational_error(self) -> None:
        calls = {"n": 0}
        real_session = self.Session

        class FlakySession:
            """First commit raises OperationalError; subsequent sessions work."""

            def __init__(self) -> None:
                self._inner = real_session()
                self._failed = False

            def get(self, *args, **kwargs):
                return self._inner.get(*args, **kwargs)

            def commit(self) -> None:
                calls["n"] += 1
                if calls["n"] == 1:
                    self._failed = True
                    raise OperationalError(
                        "UPDATE",
                        {},
                        Exception(
                            "consuming input failed: server closed the connection unexpectedly"
                        ),
                    )
                self._inner.commit()

            def rollback(self) -> None:
                try:
                    self._inner.rollback()
                except Exception:
                    pass

            def close(self) -> None:
                self._inner.close()

            def connection(self):
                # Avoid invalidate() wiping StaticPool's only connection in tests.
                raise RuntimeError("skip invalidate in test")

            def add(self, *args, **kwargs):
                return self._inner.add(*args, **kwargs)

        def session_factory():
            return FlakySession()

        with patch.object(runner, "SessionLocal", session_factory):
            with patch(
                "netx_api.biz_state.compare_service.schedule_auto_compare_for_task",
            ):
                status = runner._finalize_batch_status(
                    batch_id="b-finalize",
                    task_id="t-finalize",
                    cmd_count=55,
                    total_rows=6606,
                    any_fail=True,
                    any_ok=True,
                    lane_errors=["RuntimeError: biz_state_heavy_timeout (2400s)"],
                )
        self.assertEqual(status, "partial")
        self.assertEqual(calls["n"], 2)
        self.db.expire_all()
        batch = self.db.get(BizStateBatch, "b-finalize")
        assert batch is not None
        self.assertEqual(batch.status, "partial")
        self.assertIn("biz_state_heavy_timeout", batch.message or "")

    def test_finalize_success_schedules_auto_compare(self) -> None:
        with patch.object(runner, "SessionLocal", self.Session):
            with patch(
                "netx_api.biz_state.compare_service.schedule_auto_compare_for_task",
            ) as sched:
                status = runner._finalize_batch_status(
                    batch_id="b-finalize",
                    task_id="t-finalize",
                    cmd_count=55,
                    total_rows=6606,
                    any_fail=False,
                    any_ok=True,
                    lane_errors=[],
                )
        self.assertEqual(status, "success")
        sched.assert_called_once_with("t-finalize", "b-finalize")

    def test_fail_batch_retries_on_stale_connection(self) -> None:
        calls = {"n": 0}
        real_session = self.Session

        class FlakySession:
            def __init__(self) -> None:
                self._inner = real_session()

            def get(self, *args, **kwargs):
                return self._inner.get(*args, **kwargs)

            def commit(self) -> None:
                calls["n"] += 1
                if calls["n"] == 1:
                    raise OperationalError(
                        "UPDATE",
                        {},
                        Exception("server closed the connection unexpectedly"),
                    )
                self._inner.commit()

            def rollback(self) -> None:
                try:
                    self._inner.rollback()
                except Exception:
                    pass

            def close(self) -> None:
                self._inner.close()

            def connection(self):
                raise RuntimeError("skip invalidate in test")

        with patch.object(runner, "SessionLocal", FlakySession):
            runner._fail_batch_status("b-finalize", "RuntimeError: boom")
        self.assertEqual(calls["n"], 2)
        self.db.expire_all()
        batch = self.db.get(BizStateBatch, "b-finalize")
        assert batch is not None
        self.assertEqual(batch.status, "failed")
        self.assertIn("boom", batch.message or "")

    def test_run_db_with_reconnect_reraises_non_stale(self) -> None:
        calls = {"n": 0}

        class BoomSession:
            def commit(self) -> None:
                pass

            def rollback(self) -> None:
                pass

            def close(self) -> None:
                pass

            def connection(self):
                raise RuntimeError("no conn")

        def fn(db) -> None:
            calls["n"] += 1
            raise ValueError("not a disconnect")

        with patch.object(runner, "SessionLocal", BoomSession):
            with self.assertRaises(ValueError):
                runner._run_db_with_reconnect(fn, label="test")
        self.assertEqual(calls["n"], 1)

    def test_is_stale_db_connection(self) -> None:
        self.assertTrue(
            runner._is_stale_db_connection(
                OperationalError("x", {}, Exception("server closed the connection"))
            )
        )
        self.assertTrue(
            runner._is_stale_db_connection(
                RuntimeError("consuming input failed: server closed the connection unexpectedly")
            )
        )
        self.assertFalse(runner._is_stale_db_connection(ValueError("nope")))


if __name__ == "__main__":
    unittest.main()
