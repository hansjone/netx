"""Schema patch / Alembic wiring smoke tests."""

from __future__ import annotations

import unittest
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from netx_api.db import Base
import netx_api.models  # noqa: F401
from netx_api.schema_patches import apply_auth_schema_patches, apply_domain_schema_patches


class SchemaPatchesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_auth_patches_idempotent(self) -> None:
        with self.engine.begin() as conn:
            apply_auth_schema_patches(conn)
            apply_auth_schema_patches(conn)
        insp = inspect(self.engine)
        user_cols = {c["name"] for c in insp.get_columns("app_user")}
        token_cols = {c["name"] for c in insp.get_columns("api_token")}
        self.assertIn("scopes", user_cols)
        self.assertIn("must_change_password", user_cols)
        self.assertIn("scopes", token_cols)
        self.assertIn("expires_at", token_cols)
        self.assertIn("auth_session", insp.get_table_names())

    def test_domain_patches_do_not_raise(self) -> None:
        with self.engine.begin() as conn:
            apply_domain_schema_patches(conn)
            apply_domain_schema_patches(conn)

    def test_worker_default_off_inline(self) -> None:
        from netx_api.config import Settings

        s = Settings(_env_file=None)
        self.assertTrue(s.run_inline_schedulers)
        self.assertTrue(s.alembic_upgrade_on_start)

        versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
        files = sorted(p.name for p in versions.glob("*.py") if p.name != "__init__.py")
        self.assertIn("20260802_scopes.py", files)
        self.assertIn("20260802_legacy_schema.py", files)
        self.assertIn("20260806_auth_session.py", files)
        self.assertIn("20260806_auth_refresh.py", files)
        text_legacy = (versions / "20260802_legacy_schema.py").read_text(encoding="utf-8")
        self.assertIn('down_revision', text_legacy)
        self.assertIn("20260802_scopes", text_legacy)


if __name__ == "__main__":
    unittest.main()
