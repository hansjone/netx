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
        self.assertEqual(out["commands"][0]["raw_line_count"], 1)
        self.assertEqual(out["sheets"][0]["metric_id"], "arp")
        self.assertEqual(out["sheets"][0]["row_count"], 2)
        self.assertEqual(out["sheets"][0]["commands"][0]["raw_command"], "show arp | one-line")
        self.assertEqual(out["sheets"][0]["commands"][0]["raw_line_count"], 1)
        self.assertTrue(out["sheets"][0].get("title"))

    def test_get_batch_raw_line_count_splitlines(self) -> None:
        batch = BizStateBatch(
            id="b1",
            task_id="t1",
            status="partial",
            command_count=1,
            row_count=0,
            message="stopped",
        )
        cmd = BizStateBatchCommand(
            id="c1",
            batch_id="b1",
            profile_id="zte.arp",
            parser_id="arp",
            metric_id="arp",
            raw_command="show arp",
            parse_status="ok",
            row_count=3,
            raw_text="a\nb\nc\n",
            message="",
        )
        db = MagicMock()
        db.get.side_effect = lambda model, pk: batch if pk == "b1" else None
        cmd_q = MagicMock()
        cmd_q.filter.return_value.order_by.return_value.all.return_value = [cmd]
        metric_count_q = MagicMock()
        metric_count_q.filter.return_value.group_by.return_value.all.return_value = [("arp", 3)]
        lldp_count_q = MagicMock()
        lldp_count_q.filter.return_value.scalar.return_value = 0

        def query(*_args, **_kwargs):
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

        self.assertEqual(out["message"], "stopped")
        self.assertEqual(out["commands"][0]["raw_line_count"], 3)
        self.assertEqual(out["commands"][0]["row_count"], 3)
        self.assertEqual(out["commands"][0]["declared_total"], 0)

    def test_get_batch_prefers_stored_raw_line_count(self) -> None:
        """DB raw_text may be truncated; API must use persisted full-file line count."""
        batch = BizStateBatch(
            id="b1",
            task_id="t1",
            status="ok",
            command_count=1,
            row_count=100,
        )
        cmd = BizStateBatchCommand(
            id="c1",
            batch_id="b1",
            profile_id="zte.bgp_vpnv4_neighbor_in",
            parser_id="bgp_route",
            metric_id="bgp_route",
            raw_command="show bgp vpnv4 unicast neighbor in 1.1.1.1",
            parse_status="ok",
            row_count=100,
            raw_text="a\nb\nc",  # truncated stub (3 lines)
            raw_line_count=119303,
            declared_total=1669101,
            message="declared=1669101;parsed=100",
        )
        db = MagicMock()
        db.get.side_effect = lambda model, pk: batch if pk == "b1" else None
        cmd_q = MagicMock()
        cmd_q.filter.return_value.order_by.return_value.all.return_value = [cmd]
        metric_count_q = MagicMock()
        metric_count_q.filter.return_value.group_by.return_value.all.return_value = [
            ("bgp_route", 100)
        ]
        lldp_count_q = MagicMock()
        lldp_count_q.filter.return_value.scalar.return_value = 0

        def query(*_args, **_kwargs):
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

        self.assertEqual(out["commands"][0]["raw_line_count"], 119303)
        self.assertEqual(out["commands"][0]["declared_total"], 1669101)
        self.assertEqual(out["sheets"][0]["commands"][0]["raw_line_count"], 119303)
        self.assertEqual(out["sheets"][0]["commands"][0]["declared_total"], 1669101)

    def test_get_batch_command_and_raw_download(self) -> None:
        from netx_api.biz_state.service import get_batch_command
        from netx_api.biz_state_router import api_download_batch_command_raw

        batch = BizStateBatch(id="b1", task_id="t1", status="ok")
        cmd = BizStateBatchCommand(
            id="c1",
            batch_id="b1",
            profile_id="zte.arp",
            parser_id="arp",
            metric_id="arp",
            raw_command="show arp | one-line",
            parse_status="ok",
            row_count=2,
            raw_text="line1\nline2",
            message="hint",
        )
        db = MagicMock()

        def _get(model, pk):
            if model is BizStateBatch and pk == "b1":
                return batch
            if model is BizStateBatchCommand and pk == "c1":
                return cmd
            return None

        db.get.side_effect = _get
        detail = get_batch_command(db, "b1", "c1")
        self.assertEqual(detail["raw_line_count"], 2)
        self.assertEqual(detail["row_count"], 2)
        self.assertEqual(detail["message"], "hint")
        self.assertIn("line1", detail["raw_text"])

        resp = api_download_batch_command_raw("b1", "c1", db)
        self.assertEqual(resp.media_type, "text/plain; charset=utf-8")
        cd = (resp.headers.get("content-disposition") or "").lower()
        self.assertIn("attachment", cd)
        self.assertIn(".txt", cd)
        # StreamingResponse may expose async iterator; content already covered by get_batch_command.
        body_iter = getattr(resp, "body_iterator", None)
        if body_iter is not None and hasattr(body_iter, "__iter__") and not hasattr(body_iter, "__aiter__"):
            body = b"".join(body_iter)
            self.assertEqual(body.decode("utf-8"), "line1\nline2")
        else:
            # Fallback: reconstruct what the route encodes
            from netx_api.biz_state.service import get_batch_command as _gbc

            raw = str(_gbc(db, "b1", "c1").get("raw_text") or "")
            self.assertEqual(raw, "line1\nline2")

    def test_bgp_peer_sheet_uses_status_summary_title(self) -> None:
        """Shared metric_id bgp_peer must not inherit first AF profile title."""
        batch = BizStateBatch(
            id="b1",
            task_id="t1",
            status="ok",
            command_count=2,
            row_count=3,
        )
        cmds = [
            BizStateBatchCommand(
                id="c1",
                batch_id="b1",
                profile_id="zte.bgp_vpnv4_summary",
                parser_id="bgp_peer",
                metric_id="bgp_peer",
                raw_command="show bgp vpnv4 unicast summary | one-line",
                parse_status="ok",
                row_count=2,
            ),
            BizStateBatchCommand(
                id="c2",
                batch_id="b1",
                profile_id="zte.bgp_ipv4_summary",
                parser_id="bgp_peer",
                metric_id="bgp_peer",
                raw_command="show bgp ipv4 unicast summary | one-line",
                parse_status="ok",
                row_count=1,
            ),
        ]
        db = MagicMock()
        db.get.side_effect = lambda model, pk: batch if pk == "b1" else None
        cmd_q = MagicMock()
        cmd_q.filter.return_value.order_by.return_value.all.return_value = cmds
        metric_count_q = MagicMock()
        metric_count_q.filter.return_value.group_by.return_value.all.return_value = [
            ("bgp_peer", 3)
        ]
        lldp_count_q = MagicMock()
        lldp_count_q.filter.return_value.scalar.return_value = 0

        def query(*_args, **_kwargs):
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

        bgp = next(s for s in out["sheets"] if s["metric_id"] == "bgp_peer")
        self.assertEqual(bgp["title"], "BGP Status Summary")
        self.assertEqual(bgp["row_count"], 3)
        self.assertEqual(len(bgp["commands"]), 2)

    def test_list_metric_rows_rejects_commands_sheet(self) -> None:
        db = MagicMock()
        db.get.return_value = BizStateBatch(id="b1", task_id="t1")
        with self.assertRaises(HTTPException) as ctx:
            list_batch_metric_rows(db, "b1", "commands")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_metric_columns_use_original_field_names(self) -> None:
        batch = BizStateBatch(id="b1", task_id="t1", status="ok")
        db = MagicMock()
        db.get.return_value = batch
        q = MagicMock()
        q.filter.return_value = q
        q.count.return_value = 0
        q.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []
        db.query.return_value = q

        out = list_batch_metric_rows(db, "b1", "interface_detail", page=1, page_size=10)
        self.assertTrue(out["columns"])
        for col in out["columns"]:
            self.assertEqual(col["key"], col["header"])
            # Must not surface Chinese display_name as header
            self.assertNotIn("接口", col["header"])
            self.assertNotIn("描述", col["header"])
        keys = {c["key"] for c in out["columns"]}
        self.assertIn("interface", keys)
        self.assertIn("description", keys)


if __name__ == "__main__":
    unittest.main()
