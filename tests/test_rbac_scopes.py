"""RBAC scopes, SQL guard, and insecure-default startup checks."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from netx_api.auth_middleware import AuthAuditMiddleware
from netx_api.auth_router import router as auth_router
from netx_api.auth_scopes import (
    MCP_DEFAULT_SCOPES,
    SCOPE_SQL,
    SCOPE_WEBCRT,
    effective_token_scopes,
    required_scope_for_request,
    scopes_for_role,
)
from netx_api.auth_service import bootstrap_admin_if_needed, create_api_token, create_user
from netx_api.db import Base, get_db
from netx_api.models import AppUser
from netx_api.security_bootstrap import assert_secure_defaults_or_exit
from netx_api.sql_guard import validate_select_sql
from netx_api.sql_router import router as sql_router


class ScopeUnitTests(unittest.TestCase):
    def test_role_defaults(self) -> None:
        self.assertIn(SCOPE_WEBCRT, scopes_for_role("admin"))
        self.assertNotIn(SCOPE_WEBCRT, scopes_for_role("user"))
        self.assertNotIn(SCOPE_SQL, scopes_for_role("user"))
        self.assertIn("alarms:read", scopes_for_role("user"))

    def test_mcp_default_excludes_webcrt_sql(self) -> None:
        self.assertNotIn(SCOPE_WEBCRT, MCP_DEFAULT_SCOPES)
        self.assertNotIn(SCOPE_SQL, MCP_DEFAULT_SCOPES)
        self.assertIn("ne:exec", MCP_DEFAULT_SCOPES)

    def test_token_intersection(self) -> None:
        user = {"alarms:read", "ne:read", "ne:exec", "sql:query"}
        tok = effective_token_scopes(user_scopes=user, token_scopes=["ne:exec", "sql:query", "webcrt:session"])
        self.assertEqual(tok, frozenset({"ne:exec", "sql:query"}))

    def test_path_scope_map(self) -> None:
        self.assertEqual(required_scope_for_request("POST", "/v1/sql/ume_query"), SCOPE_SQL)
        self.assertEqual(required_scope_for_request("GET", "/v1/webcrt/sessions"), SCOPE_WEBCRT)
        self.assertEqual(required_scope_for_request("POST", "/v1/managed-ne/exec"), "ne:exec")
        self.assertEqual(required_scope_for_request("POST", "/v1/managed-ne"), "ne:write")


class SqlGuardTests(unittest.TestCase):
    def test_rejects_cte(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            validate_select_sql(
                "WITH x AS (SELECT * FROM app_user) SELECT * FROM x",
                allowed_tables={"ume_alarms_current", "ume_inventory_ne"},
            )
        self.assertEqual(ctx.exception.detail, "with_cte_not_allowed")

    def test_rejects_disallowed_table(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            validate_select_sql(
                "select * from app_user",
                allowed_tables={"ume_alarms_current", "ume_inventory_ne"},
            )
        self.assertIn("ume_table_not_allowed", str(ctx.exception.detail))

    def test_rejects_catalog(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            validate_select_sql(
                "select * from ume_alarms_current where ne_id in (select relname from pg_catalog.pg_class)",
                allowed_tables={"ume_alarms_current", "ume_inventory_ne"},
            )
        detail = str(ctx.exception.detail)
        self.assertTrue(
            detail == "catalog_not_allowed" or detail.startswith("ume_table_not_allowed:"),
            detail,
        )


class SecurityBootstrapTests(unittest.TestCase):
    def test_loopback_allows_defaults(self) -> None:
        with patch("netx_api.security_bootstrap.settings") as st:
            st.allow_insecure_defaults = False
            st.host = "127.0.0.1"
            st.auth_secret = ""
            st.bootstrap_admin_password = "admin123"
            st.ume_verify_tls = False
            with patch("netx_api.security_bootstrap.ensure_auth_secret", return_value="x" * 48):
                assert_secure_defaults_or_exit()

    def test_non_loopback_rejects_legacy_secret(self) -> None:
        with patch("netx_api.security_bootstrap.settings") as st:
            st.allow_insecure_defaults = False
            st.host = "0.0.0.0"
            st.auth_secret = "netx-dev-auth-secret-change-me-in-production-32b"
            st.bootstrap_admin_password = "strong-pass-here"
            st.ume_verify_tls = True
            with patch("netx_api.security_bootstrap.ensure_auth_secret", return_value="x" * 48):
                with self.assertRaises(SystemExit) as ctx:
                    assert_secure_defaults_or_exit()
                self.assertEqual(ctx.exception.code, 2)

    def test_non_loopback_rejects_default_password(self) -> None:
        with patch("netx_api.security_bootstrap.settings") as st:
            st.allow_insecure_defaults = False
            st.host = "0.0.0.0"
            st.auth_secret = ""
            st.bootstrap_admin_password = "admin123"
            st.ume_verify_tls = True
            with patch("netx_api.security_bootstrap.ensure_auth_secret", return_value="x" * 48):
                with self.assertRaises(SystemExit) as ctx:
                    assert_secure_defaults_or_exit()
                self.assertEqual(ctx.exception.code, 2)


class RbacApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)

        self.app = FastAPI()
        self.app.add_middleware(AuthAuditMiddleware)
        self.app.include_router(auth_router)
        self.app.include_router(sql_router)

        @self.app.post("/v1/webcrt/sessions")
        def fake_webcrt() -> dict[str, str]:
            return {"ok": "1"}

        @self.app.post("/v1/managed-ne/exec")
        def fake_exec() -> dict[str, str]:
            return {"ok": "1"}

        def _override_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        self.app.dependency_overrides[get_db] = _override_db
        self._patches = [
            patch("netx_api.auth_middleware.SessionLocal", self.Session),
            patch("netx_api.audit_async.settings.audit_async", False),
            patch("netx_api.auth_middleware.settings.auth_enabled", True),
            patch("netx_api.auth_tokens.settings.auth_secret", "unit-test-auth-secret-32bytes!!"),
            patch("netx_api.auth_tokens.settings.auth_token_ttl_sec", 3600),
            patch("netx_api.auth_service.settings.bootstrap_admin_username", "admin"),
            patch("netx_api.auth_service.settings.bootstrap_admin_password", "adminpass"),
            patch("netx_api.auth_deps.settings.auth_enabled", True),
        ]
        for p in self._patches:
            p.start()

        db = self.Session()
        try:
            bootstrap_admin_if_needed(db)
            admin = db.query(AppUser).filter(AppUser.username == "admin").one()
            create_user(db, username="alice", password="alice12", role="user", actor=admin)
        finally:
            db.close()
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        self.app.dependency_overrides.clear()
        self.engine.dispose()

    def _login(self, username: str, password: str) -> str:
        r = self.client.post("/v1/auth/login", json={"username": username, "password": password})
        self.assertEqual(r.status_code, 200, r.text)
        return str(r.json()["access_token"])

    def test_user_denied_webcrt_and_sql(self) -> None:
        token = self._login("alice", "alice12")
        h = {"Authorization": f"Bearer {token}"}
        self.assertEqual(self.client.post("/v1/webcrt/sessions", headers=h).status_code, 403)
        self.assertEqual(
            self.client.post(
                "/v1/sql/ume_query",
                headers=h,
                json={"sql": "select * from ume_alarms_current", "limit": 1},
            ).status_code,
            403,
        )
        self.assertEqual(self.client.post("/v1/managed-ne/exec", headers=h, json={}).status_code, 403)

    def test_admin_allowed_sql_path_auth(self) -> None:
        token = self._login("admin", "adminpass")
        h = {"Authorization": f"Bearer {token}"}
        # Passes scope gate; may fail SQL execution without tables — not 403.
        r = self.client.post(
            "/v1/sql/ume_query",
            headers=h,
            json={"sql": "select 1 as n from ume_alarms_current", "limit": 1},
        )
        self.assertNotEqual(r.status_code, 403)

    def test_me_returns_scopes(self) -> None:
        token = self._login("alice", "alice12")
        me = self.client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(me.status_code, 200)
        scopes = me.json()["scopes"]
        self.assertIn("alarms:read", scopes)
        self.assertNotIn("webcrt:session", scopes)

    def test_mcp_token_scopes(self) -> None:
        token = self._login("admin", "adminpass")
        created = self.client.post(
            "/v1/api-tokens",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "mcp", "expires_in_days": 0, "scopes": list(MCP_DEFAULT_SCOPES)},
        )
        self.assertEqual(created.status_code, 200, created.text)
        plain = created.json()["token"]["token"]
        # MCP token cannot call webcrt
        r = self.client.post(
            "/v1/webcrt/sessions",
            headers={"Authorization": f"Bearer {plain}"},
        )
        self.assertEqual(r.status_code, 403)


if __name__ == "__main__":
    unittest.main()
