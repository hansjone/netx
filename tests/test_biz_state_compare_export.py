"""Export must page lazily, keep tied sequence rows, and produce valid UTF-8 CSV."""

from __future__ import annotations

import csv
import io
import unittest
import zipfile
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from netx_api.biz_state.compare_service import _iter_sheet_diffs, export_run_zip
from netx_api.db import Base
from netx_api.models import BizCompareDiff, BizCompareRun


class CompareExportTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.sheet = {"sheet_id": "sheet", "metric_id": "test", "key_fields": ["id"],
                      "compare_fields": ["note"], "display_fields": ["id", "note"],
                      "summary": {"unchanged": 7}, "status": "done"}
        self.run = BizCompareRun(id="run", metric_id="test", status="success",
                                 summary_json={"sheets": [self.sheet]})
        self.db.add(self.run)
        for seq, ids in ((0, "cba"), (1, "ed"), (2, "gf")):
            for rid in ids:
                note = '中文, "quoted"\nsecond line'
                self.db.add(BizCompareDiff(id=rid, run_id="run", metric_id="sheet", seq=seq,
                                           kind="unchanged", key_json={"id": rid},
                                           before_json={"id": rid, "note": note},
                                           after_json={"id": rid, "note": note}))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_cursor_handles_tied_sequence_numbers_across_chunks(self):
        statements = []
        def record(_conn, _cursor, statement, _parameters, _context, _executemany):
            if "ORDER BY biz_compare_diff.seq" in statement:
                statements.append(statement)
        event.listen(self.engine, "before_cursor_execute", record)
        with patch("netx_api.biz_state.compare_service._DIFF_CHUNK", 2):
            iterator = _iter_sheet_diffs(self.db, "run", "sheet")
            self.assertEqual(statements, [])
            self.assertEqual(next(iterator)["key"]["id"], "a")
            self.assertEqual(next(iterator)["key"]["id"], "b")
            self.assertEqual(len(statements), 1, "do not load the next chunk early")
            self.assertEqual([d["key"]["id"] for d in iterator], list("cdefg"))
        self.assertEqual(len(statements), 4)
        self.assertTrue(all("biz_compare_diff.seq >" in q and "biz_compare_diff.id >" in q
                            for q in statements[1:]))

    def test_export_csv_escapes_unicode_quotes_commas_and_newlines(self):
        with patch("netx_api.biz_state.compare_service._DIFF_CHUNK", 2):
            archive_bytes = export_run_zip(self.db, "run")
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            payload = archive.read("tables/sheet.csv")
            self.assertTrue(payload.startswith(b"\xef\xbb\xbf"))
            rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
            self.assertEqual([r["id"] for r in rows], list("abcdefg"))
            self.assertTrue(all(r["note__pre"] == '中文, "quoted"\nsecond line' for r in rows))
            self.assertTrue(all(r["note__pre"] == r["note__post"] for r in rows))

    def test_other_sheets_do_not_leak_into_export(self):
        self.db.add(BizCompareDiff(id="other", run_id="run", metric_id="another", seq=0,
                                   kind="added", key_json={"id": "other"}, after_json={"note": "other"}))
        self.db.commit()
        with patch("netx_api.biz_state.compare_service._DIFF_CHUNK", 2):
            self.assertEqual([d["key"]["id"] for d in _iter_sheet_diffs(self.db, "run", "sheet")], list("abcdefg"))

    def test_legacy_inline_and_empty_exports(self):
        self.db.query(BizCompareDiff).delete()
        inline = {"kind": "added", "key": {"id": "legacy"}, "after": {"note": "value"}}
        self.run.summary_json = {"sheets": [{**self.sheet, "diffs": [inline]}]}
        self.db.commit()
        self.assertEqual(list(_iter_sheet_diffs(self.db, "run", "sheet")), [inline])
        self.run.summary_json = {"sheets": [self.sheet]}
        self.db.commit()
        with zipfile.ZipFile(io.BytesIO(export_run_zip(self.db, "run"))) as archive:
            self.assertEqual(archive.read("tables/sheet.csv").decode("utf-8-sig"), "kind,id,note__pre,note__post\n")
