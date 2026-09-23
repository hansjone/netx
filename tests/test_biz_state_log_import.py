"""Tests for biz_state manual log import (split + unpack + match path)."""

from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from netx_api.biz_state import import_runner as imp
from netx_api.biz_state import spool as spool_mod
from netx_api.biz_state.command_match import match_command
from netx_api.biz_state.log_split import (
    split_log_text,
    unpack_upload,
)
from netx_api.db import Base
from netx_api.models import BizStateBatch, BizStateBatchCommand, BizStateTask


_ZTE_SAMPLE = """
PE1#show arp
IP Address       MAC Address
10.0.0.1         aaaa.bbbb.cccc

show\u00a0bgp\u00a0ipv4\u00a0unicast\u00a0neighbor\u00a0out\u00a01.1.1.1\u00a0| one-line
Routes Sent To This Neighbor:
Total number of routes: 1
Network          Next Hop
10.1.0.0/24      1.1.1.2

show weird-unknown-command
garbage line

show bgp vpnv4 unicast neighbor in 2.2.2.2 | one-line
Routes Learned From This Neighbor:
Total number of routes: 0
"""

_HW_SAMPLE = """
<HUAWEI>display ip routing-table
Routing Table
Destinations : 1

display bgp peer
BGP peer table
"""


class LogSplitTests(unittest.TestCase):
    def test_zte_split_nbsp_and_prompt(self) -> None:
        segs = split_log_text(_ZTE_SAMPLE, vendor_key="zte", source_file="a.ini")
        cmds = [s.command for s in segs]
        self.assertIn("show arp", cmds)
        self.assertTrue(any(c.startswith("show bgp ipv4") for c in cmds))
        self.assertTrue(any("\u00a0" not in c for c in cmds))
        bgp = next(s for s in segs if "bgp ipv4" in s.command)
        self.assertIn("10.1.0.0/24", bgp.body)
        self.assertEqual(bgp.source_file, "a.ini")

    def test_preamble_before_first_show_discarded(self) -> None:
        """Leading noise / prompts without show are dropped; no-show → empty."""
        raw = """Some banner
login ok
MDN-BCP-CN1-ZM8SP#
08:38:53 clock
garbage

MDN-BCP-CN1-ZM8SP#show arp | one-line
The count is 1
1.1.1.1 aaaa

PE1#show interface brief
gei-0/0/0/1 up
"""
        segs = split_log_text(raw, vendor_key="zte")
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0].command, "show arp | one-line")
        self.assertNotIn("banner", segs[0].body.lower())
        self.assertNotIn("garbage", segs[0].body)
        self.assertIn("1.1.1.1", segs[0].body)
        self.assertEqual(segs[1].command, "show interface brief")
        self.assertEqual(split_log_text("hello\nworld\n", vendor_key="zte"), [])

    def test_hostname_hash_show_multi_segment(self) -> None:
        """Real ZTE paste: MDN-...#show ... then another #show — auto split."""
        raw = """MDN-BCP-CN1-ZM8SP#show arp | one-line
08:38:53 Indonesia Sat Sep 19 2026
The count is 2
100.67.5.215    H        d4c1.c893.1a90 gei-0/0/0/1.1409

MDN-BCP-CN1-ZM8SP#show interface brief
gei-0/0/0/1                up       up

PE2#show bgp vpnv4 unicast neighbor in 10.0.0.1 | one-line
Total number of routes: 0
"""
        segs = split_log_text(raw, vendor_key="zte", source_file="show-arp")
        self.assertEqual(len(segs), 3)
        self.assertEqual(segs[0].command, "show arp | one-line")
        self.assertEqual(segs[1].command, "show interface brief")
        self.assertTrue(segs[2].command.startswith("show bgp vpnv4"))
        self.assertIn("100.67.5.215", segs[0].body)
        self.assertIn("gei-0/0/0/1", segs[1].body)
        hit0 = match_command(vendor_key="zte", command=segs[0].command)
        self.assertIsNotNone(hit0)
        assert hit0 is not None
        self.assertEqual(hit0.profile.metric_id, "arp")

    def test_huawei_prefers_display(self) -> None:
        segs = split_log_text(_HW_SAMPLE, vendor_key="huawei")
        self.assertGreaterEqual(len(segs), 2)
        self.assertTrue(all(s.command.lower().startswith("display") for s in segs))

    def test_zip_multiple_files(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("one.txt", "show arp\n1.1.1.1 aaaa\n")
            zf.writestr("two.log", "show interface brief\ngei-0/0/0/1 up\n")
            zf.writestr("skip.bin", b"\x00\x01\x02")
        segs, stats = unpack_upload(
            filename="dump.zip", data=buf.getvalue(), vendor_key="zte"
        )
        self.assertEqual(stats["files"], 2)
        self.assertEqual(stats["segments"], 2)
        files = {s.source_file for s in segs}
        self.assertEqual(files, {"one.txt", "two.log"})

    def test_upload_size_limit(self) -> None:
        with patch(
            "netx_api.biz_state.log_split.settings"
        ) as st:
            st.biz_state_import_max_bytes = 100
            st.biz_state_import_max_files = 200
            with self.assertRaises(ValueError):
                unpack_upload(
                    filename="big.txt",
                    data=b"x" * 200,
                    vendor_key="zte",
                )

    def test_match_known_zte_bgp(self) -> None:
        segs = split_log_text(_ZTE_SAMPLE, vendor_key="zte")
        hits = []
        misses = []
        for s in segs:
            hit = match_command(vendor_key="zte", command=s.command)
            if hit:
                hits.append(hit.profile.metric_id)
            else:
                misses.append(s.command)
        self.assertIn("bgp_route", hits)
        self.assertTrue(any("weird-unknown" in c for c in misses))


class ImportRunnerEnqueueTests(unittest.TestCase):
    def setUp(self) -> None:
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
            id="t-imp",
            source="managed",
            ne_id="ne1",
            ne_name="PE1",
            vendor="ZTE",
            device_type="zte_zxros",
            status="paused",
            collect_running=False,
            interval_sec=300,
        )
        self.db.add(self.task)
        self.db.commit()
        self._sess_patch = patch.object(imp, "SessionLocal", TestingSession)
        self._sess_patch.start()

    def tearDown(self) -> None:
        self._sess_patch.stop()
        self.db.close()

    def test_enqueue_mutex(self) -> None:
        r1 = imp.enqueue_import("t-imp", filename="a.ini", vendor_key="zte")
        self.assertTrue(r1.get("queued"))
        self.assertTrue(r1.get("batch_id"))
        r2 = imp.enqueue_import("t-imp", filename="b.ini", vendor_key="zte")
        self.assertEqual(r2.get("reason"), "already_collecting")
        self.db.expire_all()
        task = self.db.get(BizStateTask, "t-imp")
        assert task is not None
        self.assertTrue(task.collect_running)
        batch = self.db.get(BizStateBatch, r1["batch_id"])
        assert batch is not None
        self.assertEqual(batch.status, "running")
        self.assertIn("a.ini", batch.alias or "")


class ImportRunnerParseTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
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
            id="t-imp2",
            source="managed",
            ne_id="ne1",
            ne_name="PE1",
            vendor="ZTE",
            device_type="zte_zxros",
            status="paused",
            collect_running=True,
            interval_sec=300,
        )
        self.batch = BizStateBatch(
            id="b-imp2",
            task_id="t-imp2",
            source="managed",
            ne_id="ne1",
            ne_name="PE1",
            vendor="ZTE",
            status="running",
            message="importing:sample.ini",
            alias="sample.ini",
        )
        self.db.add(self.task)
        self.db.add(self.batch)
        self.db.commit()

        self._patches = [
            patch.object(imp, "SessionLocal", TestingSession),
            patch.object(spool_mod.settings, "biz_state_spool_dir", str(self.root)),
        ]
        # collect_runner uses SessionLocal too for flush/finalize/finish
        from netx_api.biz_state import collect_runner as runner
        from netx_api.biz_state import persist_pool as pp

        self._patches.append(patch.object(runner, "SessionLocal", TestingSession))

        class _SyncPersist:
            def submit(self, batch_id, items):
                runner._flush_spooled_commands(batch_id, list(items))

            def wait_idle(self, *, timeout=None):
                return True

        self._sync = _SyncPersist()
        self._patches.append(patch.object(imp, "get_persist_pool", return_value=self._sync))
        self._patches.append(patch.object(pp, "get_persist_pool", return_value=self._sync))
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        self.db.close()
        self._tmpdir.cleanup()

    def test_run_import_marks_unmatched_and_ok(self) -> None:
        segs = split_log_text(_ZTE_SAMPLE, vendor_key="zte", source_file="sample.ini")
        out = imp.run_import_batch(
            batch_id="b-imp2",
            task_id="t-imp2",
            segments=segs,
            vendor_key="zte",
            vendor="ZTE",
            device_type="zte_zxros",
            filename="sample.ini",
        )
        self.assertIn(out.get("status"), ("success", "partial", "failed"))
        self.assertGreaterEqual(int(out.get("matched") or 0), 1)
        self.assertGreaterEqual(int(out.get("unmatched") or 0), 1)
        self.db.expire_all()
        cmds = (
            self.db.query(BizStateBatchCommand)
            .filter(BizStateBatchCommand.batch_id == "b-imp2")
            .all()
        )
        statuses = {str(c.parse_status or "") for c in cmds}
        self.assertIn("unmatched", statuses)
        batch = self.db.get(BizStateBatch, "b-imp2")
        assert batch is not None
        self.assertIn(batch.status, ("partial", "success", "failed"))
        self.assertTrue(str(batch.message or "").strip())
        self.assertIn("imported:", (batch.message or "").lower())
        # Missing aux must not claim enrich in ok command messages
        for c in cmds:
            if c.parse_status == "ok":
                self.assertNotIn("enrich=", (c.message or ""))
        task = self.db.get(BizStateTask, "t-imp2")
        assert task is not None
        self.assertFalse(task.collect_running)


class StandaloneImportTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        TestingSession = sessionmaker(bind=self.engine)
        self.Session = TestingSession
        self.db = TestingSession()
        self._patch = patch.object(imp, "SessionLocal", TestingSession)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        self.db.close()
        self.engine.dispose()

    def test_create_standalone_import_task(self) -> None:
        out = imp.create_standalone_import_task(
            vendor_key="zte",
            ne_name="PE1-lab",
            filename="show-arp.ini",
        )
        self.assertTrue(out.get("ok"))
        tid = str(out["task_id"])
        task = self.db.get(BizStateTask, tid)
        assert task is not None
        self.assertEqual(task.source, "import")
        self.assertEqual(task.status, "paused")
        self.assertEqual(task.ne_name, "PE1-lab")
        self.assertEqual(task.vendor, "ZTE")
        self.assertTrue(str(task.ne_id or "").startswith("import-"))
        self.assertFalse(bool(task.ne_ip))

    def test_create_defaults_name_from_filename(self) -> None:
        out = imp.create_standalone_import_task(
            vendor_key="huawei",
            filename="C:/logs/display-ip.zip",
        )
        task = self.db.get(BizStateTask, out["task_id"])
        assert task is not None
        self.assertEqual(task.ne_name, "import:display-ip.zip")
        self.assertEqual(task.vendor, "Huawei")


if __name__ == "__main__":
    unittest.main()
