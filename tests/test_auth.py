"""Auth login, bootstrap, gate, and admin user management tests."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from netx_api.auth_middleware import AuthAuditMiddleware
from netx_api.auth_passwords import hash_password, verify_password
from netx_api.auth_router import router as auth_router
from netx_api.auth_service import bootstrap_admin_if_needed, create_user
from netx_api.auth_tokens import decode_access_token, issue_access_token
from netx_api.db import Base, get_db
from netx_api.models import AppUser, AuditLog


class AuthUnitTests(unittest.TestCase):
    def test_password_hash_roundtrip(self) -> None:
        h = hash_password("secret123")
        self.assertTrue(verify_password("secret123", h))
        self.assertFalse(verify_password("wrong", h))

    def test_jwt_roundtrip(self) -> None:
        with patch("netx_api.auth_tokens.settings") as st:
            st.auth_secret = "test-secret-key-for-jwt"
            st.auth_token_ttl_sec = 3600
            tok = issue_access_token(user_id="u1", username="admin", role="admin")
            payload = decode_access_token(tok)
            self.assertEqual(payload["sub"], "u1")
            self.assertEqual(payload["username"], "admin")
            self.assertEqual(payload["role"], "admin")


class AuthApiTests(unittest.TestCase):
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

        @self.app.get("/v1/probe")
        def probe() -> dict[str, str]:
            return {"ok": "1"}

        def _override_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        self.app.dependency_overrides[get_db] = _override_db

        self._sess_patch = patch("netx_api.auth_middleware.SessionLocal", self.Session)
        self._sess_patch.start()
        self._settings_patches = [
            patch("netx_api.auth_middleware.settings.auth_enabled", True),
            patch("netx_api.audit_async.settings.audit_async", False),
            patch("netx_api.auth_tokens.settings.auth_secret", "unit-test-auth-secret-32bytes!!"),
            patch("netx_api.auth_tokens.settings.auth_token_ttl_sec", 3600),
            patch("netx_api.auth_service.settings.bootstrap_admin_username", "admin"),
            patch("netx_api.auth_service.settings.bootstrap_admin_password", "adminpass"),
            patch("netx_api.auth_deps.settings.auth_enabled", True),
        ]
        for p in self._settings_patches:
            p.start()

        db = self.Session()
        try:
            bootstrap_admin_if_needed(db)
        finally:
            db.close()

        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self._sess_patch.stop()
        for p in self._settings_patches:
            p.stop()
        self.app.dependency_overrides.clear()
        self.engine.dispose()

    def _login(self, username: str = "admin", password: str = "adminpass") -> str:
        r = self.client.post("/v1/auth/login", json={"username": username, "password": password})
        self.assertEqual(r.status_code, 200, r.text)
        return str(r.json()["access_token"])

    def test_bootstrap_creates_admin_once(self) -> None:
        db = self.Session()
        try:
            users = db.query(AppUser).all()
            self.assertEqual(len(users), 1)
            self.assertEqual(users[0].username, "admin")
            self.assertEqual(users[0].role, "admin")
            bootstrap_admin_if_needed(db)
            self.assertEqual(db.query(AppUser).count(), 1)
        finally:
            db.close()

    def test_bootstrap_requires_password_change(self) -> None:
        db = self.Session()
        try:
            admin = db.query(AppUser).filter(AppUser.username == "admin").one()
            self.assertTrue(admin.must_change_password)
        finally:
            db.close()
        token = self._login()
        me = self.client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertTrue(me.json()["user"]["must_change_password"])
        bad = self.client.post(
            "/v1/auth/change-password",
            headers={"Authorization": f"Bearer {token}"},
            json={"old_password": "adminpass", "new_password": "adminpass"},
        )
        self.assertEqual(bad.status_code, 400)
        ok = self.client.post(
            "/v1/auth/change-password",
            headers={"Authorization": f"Bearer {token}"},
            json={"old_password": "adminpass", "new_password": "newpass99"},
        )
        self.assertEqual(ok.status_code, 200, ok.text)
        me2 = self.client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertFalse(me2.json()["user"]["must_change_password"])

    def test_login_and_me(self) -> None:
        token = self._login()
        r = self.client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["user"]["username"], "admin")

    def test_probe_requires_auth(self) -> None:
        r = self.client.get("/v1/probe")
        self.assertEqual(r.status_code, 401)
        token = self._login()
        r2 = self.client.get("/v1/probe", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r2.status_code, 200)

    def test_login_failed_audited(self) -> None:
        r = self.client.post("/v1/auth/login", json={"username": "admin", "password": "bad"})
        self.assertEqual(r.status_code, 401)
        db = self.Session()
        try:
            row = (
                db.query(AuditLog)
                .filter(AuditLog.action == "auth.login_failed")
                .order_by(AuditLog.ts.desc())
                .first()
            )
            self.assertIsNotNone(row)
        finally:
            db.close()

    def test_non_admin_cannot_create_user(self) -> None:
        db = self.Session()
        try:
            admin = db.query(AppUser).filter(AppUser.username == "admin").one()
            create_user(db, username="alice", password="alice12", role="user", actor=admin)
        finally:
            db.close()
        token = self._login("alice", "alice12")
        r = self.client.post(
            "/v1/users",
            headers={"Authorization": f"Bearer {token}"},
            json={"username": "bob", "password": "bob12345", "role": "user"},
        )
        self.assertEqual(r.status_code, 403)

    def test_admin_create_user_and_list_audit(self) -> None:
        token = self._login()
        r = self.client.post(
            "/v1/users",
            headers={"Authorization": f"Bearer {token}"},
            json={"username": "bob", "password": "bob12345", "role": "user"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["user"]["username"], "bob")
        audit = self.client.get("/v1/audit-logs", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(audit.status_code, 200)
        self.assertGreaterEqual(audit.json()["total"], 1)

    def test_api_token_with_expiry(self) -> None:
        token = self._login()
        created = self.client.post(
            "/v1/api-tokens",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "short", "expires_in_days": 7},
        )
        self.assertEqual(created.status_code, 200, created.text)
        body = created.json()["token"]
        self.assertTrue(body.get("expires_at"))
        api_tok = body["token"]
        r = self.client.get("/v1/probe", headers={"Authorization": f"Bearer {api_tok}"})
        self.assertEqual(r.status_code, 200)

        # Admin creates for another user
        self.client.post(
            "/v1/users",
            headers={"Authorization": f"Bearer {token}"},
            json={"username": "carol", "password": "carol12", "role": "user"},
        )
        users = self.client.get("/v1/users", headers={"Authorization": f"Bearer {token}"})
        carol_id = next(u["id"] for u in users.json()["items"] if u["username"] == "carol")
        for_user = self.client.post(
            "/v1/api-tokens",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "for-carol", "expires_in_days": 30, "user_id": carol_id},
        )
        self.assertEqual(for_user.status_code, 200, for_user.text)
        self.assertEqual(for_user.json()["token"]["username"], "carol")

    def test_api_token_auth(self) -> None:
        token = self._login()
        created = self.client.post(
            "/v1/api-tokens",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "mcp"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        api_tok = created.json()["token"]["token"]
        self.assertTrue(str(api_tok).startswith("nxt_"))
        r = self.client.get("/v1/probe", headers={"Authorization": f"Bearer {api_tok}"})
        self.assertEqual(r.status_code, 200)

    def test_api_token_with_scopes(self) -> None:
        token = self._login()
        created = self.client.post(
            "/v1/api-tokens",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "topo-write",
                "expires_in_days": 30,
                "scopes": ["ne:read", "ne:write", "alarms:read"],
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        body = created.json()["token"]
        self.assertEqual(sorted(body.get("scopes") or []), ["alarms:read", "ne:read", "ne:write"])
        api_tok = body["token"]
        me = self.client.get("/v1/auth/me", headers={"Authorization": f"Bearer {api_tok}"})
        self.assertEqual(me.status_code, 200, me.text)
        granted = sorted(me.json().get("scopes") or [])
        self.assertIn("ne:write", granted)
        self.assertIn("ne:read", granted)
        # Token cannot escalate beyond listed scopes (admin owner still capped by token list).
        self.assertNotIn("admin:users", granted)


if __name__ == "__main__":
    unittest.main()
