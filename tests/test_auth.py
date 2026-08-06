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
            tok, jti, ttl = issue_access_token(user_id="u1", username="admin", role="admin")
            payload = decode_access_token(tok)
            self.assertEqual(payload["sub"], "u1")
            self.assertEqual(payload["username"], "admin")
            self.assertEqual(payload["role"], "admin")
            self.assertEqual(payload["jti"], jti)
            self.assertEqual(ttl, 3600)


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
            # Most tests exercise normal APIs; password-change gate is covered separately.
            admin = db.query(AppUser).filter(AppUser.username == "admin").one()
            admin.must_change_password = False
            db.commit()
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
            admin.must_change_password = True
            db.commit()
            self.assertTrue(admin.must_change_password)
        finally:
            db.close()
        token = self._login()
        me = self.client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertTrue(me.json()["user"]["must_change_password"])
        blocked = self.client.get("/v1/probe", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.json()["detail"], "password_change_required")
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
        probe = self.client.get("/v1/probe", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(probe.status_code, 200)

    def test_logout_revokes_jwt(self) -> None:
        token = self._login()
        r = self.client.get("/v1/probe", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r.status_code, 200)
        out = self.client.post("/v1/auth/logout", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(out.status_code, 200, out.text)
        self.assertGreaterEqual(int(out.json().get("revoked") or 0), 1)
        r2 = self.client.get("/v1/probe", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r2.status_code, 401)

    def test_refresh_rotates_tokens(self) -> None:
        login = self.client.post("/v1/auth/login", json={"username": "admin", "password": "adminpass"})
        self.assertEqual(login.status_code, 200, login.text)
        body = login.json()
        access = body["access_token"]
        refresh = body["refresh_token"]
        self.assertTrue(str(refresh).startswith("nxr_"))
        # Access works
        self.assertEqual(
            self.client.get("/v1/probe", headers={"Authorization": f"Bearer {access}"}).status_code,
            200,
        )
        rotated = self.client.post("/v1/auth/refresh", json={"refresh_token": refresh})
        self.assertEqual(rotated.status_code, 200, rotated.text)
        new_access = rotated.json()["access_token"]
        new_refresh = rotated.json()["refresh_token"]
        self.assertNotEqual(access, new_access)
        self.assertNotEqual(refresh, new_refresh)
        # Old access revoked
        self.assertEqual(
            self.client.get("/v1/probe", headers={"Authorization": f"Bearer {access}"}).status_code,
            401,
        )
        # New access works
        self.assertEqual(
            self.client.get("/v1/probe", headers={"Authorization": f"Bearer {new_access}"}).status_code,
            200,
        )
        # Old refresh cannot be reused
        reuse = self.client.post("/v1/auth/refresh", json={"refresh_token": refresh})
        self.assertEqual(reuse.status_code, 401)

    def test_single_session_login_revokes_others(self) -> None:
        token = self._login()
        token2 = self._login()
        # Default auth_single_session=True: first login is kicked.
        self.assertEqual(
            self.client.get("/v1/probe", headers={"Authorization": f"Bearer {token}"}).status_code,
            401,
        )
        self.assertEqual(
            self.client.get("/v1/probe", headers={"Authorization": f"Bearer {token2}"}).status_code,
            200,
        )
        listed = self.client.get("/v1/auth/sessions", headers={"Authorization": f"Bearer {token2}"})
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()["total"], 1)
        self.assertTrue(listed.json()["items"][0].get("current"))

    def test_list_and_revoke_sessions(self) -> None:
        with patch("netx_api.auth_service.settings.auth_single_session", False):
            token = self._login()
            listed = self.client.get("/v1/auth/sessions", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(listed.status_code, 200, listed.text)
            items = listed.json()["items"]
            self.assertGreaterEqual(len(items), 1)
            self.assertTrue(any(i.get("current") for i in items))
            # Multi-session mode: second login keeps the first alive.
            token2 = self._login()
            listed2 = self.client.get("/v1/auth/sessions", headers={"Authorization": f"Bearer {token2}"})
            self.assertGreaterEqual(listed2.json()["total"], 2)
            revoked = self.client.post(
                "/v1/auth/sessions/revoke-others",
                headers={"Authorization": f"Bearer {token2}"},
            )
            self.assertEqual(revoked.status_code, 200, revoked.text)
            self.assertGreaterEqual(int(revoked.json().get("revoked") or 0), 1)
            self.assertEqual(
                self.client.get("/v1/probe", headers={"Authorization": f"Bearer {token}"}).status_code,
                401,
            )
            self.assertEqual(
                self.client.get("/v1/probe", headers={"Authorization": f"Bearer {token2}"}).status_code,
                200,
            )

    def test_idle_timeout_revokes(self) -> None:
        from datetime import timedelta

        from netx_api.models import AuthSession
        from netx_api.timeutil import utcnow_naive

        token = self._login()
        with patch("netx_api.auth_service.settings.auth_idle_timeout_sec", 60):
            db = self.Session()
            try:
                row = db.query(AuthSession).filter(AuthSession.revoked_at.is_(None)).first()
                self.assertIsNotNone(row)
                row.last_seen_at = utcnow_naive() - timedelta(seconds=120)
                db.commit()
            finally:
                db.close()
            self.assertEqual(
                self.client.get("/v1/probe", headers={"Authorization": f"Bearer {token}"}).status_code,
                401,
            )

    def test_login_sets_auth_cookies(self) -> None:
        r = self.client.post("/v1/auth/login", json={"username": "admin", "password": "adminpass"})
        self.assertEqual(r.status_code, 200, r.text)
        # Starlette TestClient exposes set cookies
        self.assertIn("netx_at", r.cookies)
        self.assertIn("netx_rt", r.cookies)
        me = self.client.get("/v1/auth/me")  # cookie auth
        self.assertEqual(me.status_code, 200, me.text)
        self.assertEqual(me.json()["user"]["username"], "admin")

    def test_query_access_token_rejected(self) -> None:
        token = self._login()
        # Drop HttpOnly session cookies so only the deprecated query param remains.
        self.client.cookies.clear()
        r = self.client.get(f"/v1/probe?access_token={token}")
        self.assertEqual(r.status_code, 401)
        # Same token still works via Bearer header.
        r2 = self.client.get("/v1/probe", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r2.status_code, 200)

    def test_login_lockout(self) -> None:
        from netx_api.auth_rate_limit import reset_login_rate_limit_for_tests

        reset_login_rate_limit_for_tests()
        with patch("netx_api.auth_rate_limit.settings.auth_login_max_failures", 3):
            with patch("netx_api.auth_rate_limit.settings.auth_login_lockout_sec", 120):
                for _ in range(3):
                    r = self.client.post(
                        "/v1/auth/login", json={"username": "admin", "password": "wrong"}
                    )
                self.assertIn(r.status_code, (401, 429))
                locked = self.client.post(
                    "/v1/auth/login", json={"username": "admin", "password": "wrong"}
                )
                self.assertEqual(locked.status_code, 429)
                detail = locked.json()["detail"]
                self.assertEqual(detail["error"], "login_locked")
        reset_login_rate_limit_for_tests()

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
            create_user(db, username="alice", password="alice123", role="user", actor=admin)
        finally:
            db.close()
        token = self._login("alice", "alice123")
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
            json={"username": "carol", "password": "carol123", "role": "user"},
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

    def test_api_token_update_scopes(self) -> None:
        token = self._login()
        created = self.client.post(
            "/v1/api-tokens",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "edit-me", "scopes": ["ne:read", "alarms:read"]},
        )
        self.assertEqual(created.status_code, 200, created.text)
        tid = created.json()["token"]["id"]
        patched = self.client.patch(
            f"/v1/api-tokens/{tid}",
            headers={"Authorization": f"Bearer {token}"},
            json={"scopes": ["ne:read", "ne:write", "alarms:read", "ne:exec"]},
        )
        self.assertEqual(patched.status_code, 200, patched.text)
        scopes = sorted(patched.json()["token"].get("scopes") or [])
        self.assertEqual(scopes, ["alarms:read", "ne:exec", "ne:read", "ne:write"])
        empty = self.client.patch(
            f"/v1/api-tokens/{tid}",
            headers={"Authorization": f"Bearer {token}"},
            json={"scopes": []},
        )
        self.assertEqual(empty.status_code, 400)


if __name__ == "__main__":
    unittest.main()
