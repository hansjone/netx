"""biz_state collect spool: disk collect + batched DB flush."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from netx_api.biz_state import collect_runner as runner
from netx_api.biz_state import spool as spool_mod
from netx_api.biz_state.spool import (
    SpooledCommand,
    clear_batch_spool,
    iter_record_chunks,
    read_raw_text,
    read_records,
    write_raw_text,
    write_records,
)
from netx_api.db import Base
from netx_api.models import BizStateBatch, BizStateBatchCommand, BizStateMetricRow, BizStateTask


class BizStateSpoolIoTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self._patcher = patch.object(spool_mod.settings, "biz_state_spool_dir", str(self.root))
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()
        self._tmpdir.cleanup()

    def test_write_read_raw_and_records(self) -> None:
        bid = "batch1"
        cid = "cmd1"
        rel = write_raw_text(bid, cid, "show arp\nA B C")
        self.assertTrue(rel.endswith("cmd1.raw.txt"))
        self.assertEqual(read_raw_text(rel), "show arp\nA B C")
        rrel, n = write_records(bid, cid, [{"ip": "1.1.1.1"}, {"ip": "2.2.2.2"}])
        self.assertEqual(n, 2)
        recs = read_records(rrel)
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0]["ip"], "1.1.1.1")

    def test_write_records_streams_generator(self) -> None:
        def _gen():
            for i in range(5):
                yield {"i": i}

        rrel, n = write_records("batch-g", "cmd-g", _gen())
        self.assertEqual(n, 5)
        chunks = list(iter_record_chunks(rrel, chunk_size=2))
        self.assertEqual([len(c) for c in chunks], [2, 2, 1])
        self.assertEqual(chunks[0][0]["i"], 0)

    def test_raw_max_bytes_truncate(self) -> None:
        bid = "b2"
        cid = "c2"
        rel = write_raw_text(bid, cid, "x" * 100)
        text = read_raw_text(rel, max_bytes=20)
        self.assertIn("truncated", text)
        self.assertLess(len(text), 80)

    def test_clear_batch_spool(self) -> None:
        bid = "b3"
        write_raw_text(bid, "c", "hi")
        self.assertTrue((self.root / "b3").is_dir())
        clear_batch_spool(bid)
        self.assertFalse((self.root / "b3").exists())


class BizStateFlushSpoolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self._spool_patch = patch.object(
            spool_mod.settings, "biz_state_spool_dir", str(self.root)
        )
        self._spool_patch.start()

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
            id="t-spool",
            source="managed",
            ne_id="ne1",
            ne_name="PE1",
            status="running",
            collect_running=True,
            interval_sec=300,
        )
        self.db.add(self.task)
        self.batch = BizStateBatch(
            id="b-spool",
            task_id="t-spool",
            status="running",
            command_count=0,
            row_count=0,
        )
        self.db.add(self.batch)
        self.db.commit()
        self._session_patch = patch.object(runner, "SessionLocal", TestingSession)
        self._session_patch.start()

    def tearDown(self) -> None:
        self._session_patch.stop()
        self._spool_patch.stop()
        self.db.close()
        self._tmpdir.cleanup()

    def test_flush_inserts_command_and_metric_rows(self) -> None:
        cid = uuid4().hex
        raw_rel = write_raw_text("b-spool", cid, "ARP OUTPUT")
        rec_rel, rec_n = write_records(
            "b-spool",
            cid,
            [{"ip": "10.0.0.1", "mac": "aaaa"}, {"ip": "10.0.0.2", "mac": "bbbb"}],
        )
        self.assertEqual(rec_n, 2)
        pending = [
            SpooledCommand(
                id=cid,
                batch_id="b-spool",
                task_item_id="item1",
                profile_id="zte.arp",
                parser_id="zte_arp",
                metric_id="arp",
                raw_command="show arp",
                parse_status="ok",
                message="spooled",
                raw_rel_path=raw_rel,
                records_rel_path=rec_rel,
                row_count=rec_n,
                persist_kind="metric",
            )
        ]
        cmds, rows = runner._flush_spooled_commands("b-spool", pending)
        self.assertEqual(cmds, 1)
        self.assertEqual(rows, 2)
        self.assertEqual(pending, [])
        self.db.expire_all()
        cmd = self.db.get(BizStateBatchCommand, cid)
        assert cmd is not None
        self.assertEqual(cmd.parse_status, "ok")
        self.assertEqual(cmd.raw_text, "ARP OUTPUT")
        self.assertEqual(cmd.row_count, 2)
        n = (
            self.db.query(BizStateMetricRow)
            .filter(BizStateMetricRow.batch_command_id == cid)
            .count()
        )
        self.assertEqual(n, 2)
        batch = self.db.get(BizStateBatch, "b-spool")
        assert batch is not None
        self.assertEqual(batch.command_count, 1)
        self.assertEqual(batch.row_count, 2)

    def test_flush_skips_mega_raw_into_db(self) -> None:
        cid = uuid4().hex
        big = "X" * (9 * 1024 * 1024)
        raw_rel = write_raw_text("b-spool", cid, big)
        rec_rel, rec_n = write_records("b-spool", cid, [{"k": 1}])
        pending = [
            SpooledCommand(
                id=cid,
                batch_id="b-spool",
                metric_id="arp",
                raw_command="show arp",
                parse_status="ok",
                message="ok",
                raw_rel_path=raw_rel,
                records_rel_path=rec_rel,
                row_count=rec_n,
                persist_kind="metric",
            )
        ]
        with patch.object(spool_mod.settings, "biz_state_raw_max_bytes", 8 * 1024 * 1024):
            cmds, rows = runner._flush_spooled_commands("b-spool", pending)
        self.assertEqual(cmds, 1)
        self.assertEqual(rows, 1)
        self.db.expire_all()
        cmd = self.db.get(BizStateBatchCommand, cid)
        assert cmd is not None
        self.assertIn("raw_on_spool", cmd.raw_text)
        self.assertNotIn("XXXX", cmd.raw_text)
        self.assertLess(len(cmd.raw_text or ""), 500)

    def test_flush_batches_multiple_without_per_cmd_sessions(self) -> None:
        pending: list[SpooledCommand] = []
        for i in range(5):
            cid = uuid4().hex
            raw_rel = write_raw_text("b-spool", cid, f"out-{i}")
            pending.append(
                SpooledCommand(
                    id=cid,
                    batch_id="b-spool",
                    raw_command=f"show x {i}",
                    parse_status="skipped_custom",
                    message="custom_raw",
                    raw_rel_path=raw_rel,
                )
            )
        cmds, rows = runner._flush_spooled_commands("b-spool", pending)
        self.assertEqual(cmds, 5)
        self.assertEqual(rows, 0)
        self.db.expire_all()
        n = (
            self.db.query(BizStateBatchCommand)
            .filter(BizStateBatchCommand.batch_id == "b-spool")
            .count()
        )
        self.assertEqual(n, 5)


if __name__ == "__main__":
    unittest.main()
