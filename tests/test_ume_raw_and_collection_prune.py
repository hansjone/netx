"""Tests for UME raw_json capping and NE collection dir prune."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from netx_api.config import Settings
from netx_api.ne_collection_paths import prune_old_collection_dirs
from netx_api.ume_raw import dumps_ume_raw


class UmeRawAndCollectionPruneTests(unittest.TestCase):
    def test_dumps_ume_raw_truncates(self) -> None:
        payload = {"x": "a" * 10000}
        out = dumps_ume_raw(payload, max_bytes=200)
        self.assertLessEqual(len(out.encode("utf-8")), 200 + 32)
        self.assertIn("truncated raw_json", out)

    def test_dumps_ume_raw_unlimited(self) -> None:
        payload = {"ok": True, "n": 1}
        out = dumps_ume_raw(payload, max_bytes=0)
        self.assertEqual(json.loads(out)["ok"], True)

    def test_production_defaults_include_budgets(self) -> None:
        s = Settings(_env_file=None)
        self.assertEqual(s.ume_raw_json_max_bytes, 64 * 1024)
        self.assertEqual(s.ne_collection_keep_days, 14)
        self.assertTrue(s.run_inline_schedulers)

    def test_prune_old_collection_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "job_old"
            new = root / "job_new"
            old.mkdir()
            new.mkdir()
            (old / "a.txt").write_text("x", encoding="utf-8")
            (new / "b.txt").write_text("y", encoding="utf-8")
            # Make old directory appear stale.
            old_mtime = time.time() - (20 * 86400)
            import os

            os.utime(old, (old_mtime, old_mtime))
            with patch("netx_api.ne_collection_paths.collection_data_root", return_value=root):
                removed = prune_old_collection_dirs(keep_days=14)
            self.assertEqual(removed, 1)
            self.assertFalse(old.exists())
            self.assertTrue(new.exists())


if __name__ == "__main__":
    unittest.main()
