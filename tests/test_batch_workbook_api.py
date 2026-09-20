"""Batch workbook summary + paginated metric rows."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from netx_api.biz_state.service import get_batch, list_batch_metric_rows
from netx_api.models import BizStateBatch, BizStateBatchCommand


class BatchWorkbookApiTests(unittest.TestCase):
    def test_get_batch_summary_has_sheets_not_metrics(self) -> None:
        batch = BizStateBatch(
            id="b1",
            task_id="t1",
            status="ok",
            command_count=1,
            row_count=2,
        )
        cmd = BizStateBatchCommand(
            id="c1",
            batch_id="b1",
            profile_id="zte.arp",
            parser_id="arp",
            metric_id="arp",
            raw_command="show arp | one-line",
            parse_status="ok",
            row_count=2,
            raw_text="RAW",
        )
        db = MagicMock()
        db.get.side_effect = lambda model, pk: batch if pk == "b1" else None

        cmd_q = MagicMock()
        cmd_q.filter.return_value.order_by.return_value.all.return_value = [cmd]
        metric_count_q = MagicMock()
        metric_count_q.filter.return_value.group_by.return_value.all.return_value = [("arp", 2)]
        lldp_count_q = MagicMock()
        lldp_count_q.filter.return_value.scalar.return_value = 0

        def query(*_args, **_kwargs):
            # First call in get_batch after protect: BatchCommand; then metric count; then lldp
            # Distinguish by call count
            n = query.n
            query.n += 1
            if n == 0:
                return cmd_q
            if n == 1:
                return metric_count_q
            return lldp_count_q

        query.n = 0
        db.query.side_effect = query

        with patch(
            "netx_api.biz_state.service.batch_protect_info",
            return_value={"protected": False, "reasons": []},
        ):
            out = get_batch(db, "b1")

        self.assertIn("sheets", out)
        self.assertNotIn("metrics", out)
        self.assertNotIn("lldp_neighbors", out)
        self.assertEqual(out["commands"][0]["has_raw"], True)
        self.assertEqual(out["sheets"][0]["metric_id"], "arp")
        self.assertEqual(out["sheets"][0]["row_count"], 2)
        self.assertEqual(out["sheets"][0]["commands"][0]["raw_command"], "show arp | one-line")
        self.assertTrue(out["sheets"][0].get("title"))

    def test_list_metric_rows_rejects_commands_sheet(self) -> None:
        db = MagicMock()
        db.get.return_value = BizStateBatch(id="b1", task_id="t1")
        with self.assertRaises(HTTPException) as ctx:
            list_batch_metric_rows(db, "b1", "commands")
        self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
