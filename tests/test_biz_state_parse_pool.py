"""Collect/parse decoupling: fetch_raw + parse pool job."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from netx_api.biz_state.collect_session import CollectSession
from netx_api.biz_state.parse_pool import AuxRawCapture, PrimaryParseJob, parse_async_enabled


class FetchRawTests(unittest.TestCase):
    def test_fetch_raw_skips_parser(self) -> None:
        conn = object()
        sends: list[str] = []

        def _send(_c, command, read_timeout=0):
            sends.append(command)
            return f"RAW:{command}"

        session = CollectSession(conn, send_fn=_send, read_timeout=10)
        with patch("netx_api.biz_state.collect_session.run_parser") as rp:
            entry, hit = session.fetch_raw("show ip route")
            rp.assert_not_called()
        self.assertFalse(hit)
        self.assertTrue(entry.ok)
        self.assertEqual(entry.raw, "RAW:show ip route")
        self.assertEqual(entry.records, [])
        # Second call hits cache without re-CLI.
        entry2, hit2 = session.fetch_raw("show ip route")
        self.assertTrue(hit2)
        self.assertEqual(len(sends), 1)
        self.assertEqual(entry2.raw, entry.raw)


class ParseJobTests(unittest.TestCase):
    def test_run_primary_parse_job_persists(self) -> None:
        from netx_api.biz_state import collect_runner as cr

        with tempfile.TemporaryDirectory() as tmp:
            with patch("netx_api.biz_state.spool.settings") as st:
                st.biz_state_spool_dir = tmp
                st.biz_state_persist_every_cmds = 8
                st.biz_state_raw_max_bytes = 0
                submitted: list = []

                mock_pool = MagicMock()
                mock_pool.submit.side_effect = lambda bid, items: submitted.append(
                    (bid, list(items))
                )

                with patch.object(cr, "run_primary_with_bundle") as rpb:
                    rpb.return_value = (
                        [{"prefix": "1.1.1.1/32"}],
                        {"rule_a": [{"PREFIX": "1.1.1.1/32"}]},
                        ["rule_a"],
                    )
                    with patch(
                        "netx_api.biz_state.persist_pool.get_persist_pool",
                        return_value=mock_pool,
                    ):
                        job = PrimaryParseJob(
                            batch_id="batch1",
                            cmd_id="cmd1",
                            task_item_id="item1",
                            profile_id="zte.ip_route",
                            parser_id="ip_route",
                            metric_id="ip_route",
                            concrete="show ip forwarding route",
                            merged_params={},
                            raw_text="DESTINATION\n1.1.1.1/32",
                            raw_rel_path="",
                            textfsm_command="show ip forwarding route",
                            vendor="ZTE",
                            device_type="zte_zxros",
                            enrich_joins=[],
                            aux_captures=[],
                            persisted=set(),
                        )
                        ok, fail = cr._run_primary_parse_job(job)

                self.assertTrue(ok)
                self.assertFalse(fail)
                self.assertEqual(len(submitted), 1)
                _bid, items = submitted[0]
                self.assertEqual(_bid, "batch1")
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0].parse_status, "ok")
                self.assertEqual(items[0].row_count, 1)
                rec_path = Path(tmp) / "batch1" / "cmd1.records.jsonl"
                self.assertTrue(rec_path.is_file())

    def test_parse_async_default_on(self) -> None:
        self.assertTrue(parse_async_enabled())


if __name__ == "__main__":
    unittest.main()
