"""PostgreSQL storage metrics (database used size only)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import netx_api.db_storage_metrics as db_storage
from netx_api.db_storage_metrics import collect_db_storage_metrics


class DbStorageMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        db_storage._CACHED = None
        db_storage._CACHED_AT = 0.0

    def test_pg_used_bytes(self) -> None:
        row = {"db_name": "netx", "used_bytes": 12_345_678}
        session = MagicMock()
        session.__enter__.return_value = session
        session.__exit__.return_value = False
        session.execute.return_value.mappings.return_value.one.return_value = row

        with (
            patch.object(db_storage.settings, "database_url", "postgresql://u:p@10.0.0.8:5432/netx"),
            patch.object(db_storage, "_open_session", return_value=session),
        ):
            out = collect_db_storage_metrics()

        self.assertEqual(out["used_bytes"], 12_345_678)
        self.assertEqual(out["db_name"], "netx")
        self.assertEqual(out["source"], "pg_database_size")
        self.assertNotIn("total_bytes", out)
        self.assertNotIn("percent", out)

    def test_sqlite_not_applicable(self) -> None:
        with patch.object(db_storage.settings, "database_url", "sqlite:///tmp.db"):
            out = collect_db_storage_metrics()
        self.assertEqual(out["source"], "sqlite")
        self.assertEqual(out["error"], "not_applicable")
        self.assertEqual(out["used_bytes"], 0)

    def test_query_error(self) -> None:
        session = MagicMock()
        session.__enter__.return_value = session
        session.__exit__.return_value = False
        session.execute.side_effect = RuntimeError("boom")

        with (
            patch.object(db_storage.settings, "database_url", "postgresql://u:p@127.0.0.1:5432/netx"),
            patch.object(db_storage, "_open_session", return_value=session),
        ):
            out = collect_db_storage_metrics()

        self.assertEqual(out["source"], "error")
        self.assertIn("boom", str(out.get("error") or ""))


if __name__ == "__main__":
    unittest.main()
