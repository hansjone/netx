"""Per-user prefs API: workbench layout read/write, whitelist, validation, isolation."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from netx_api.auth_middleware import AuthAuditMiddleware
from netx_api.auth_router import router as auth_router
from netx_api.auth_service import bootstrap_admin_if_needed, create_user
from netx_api.db import Base, get_db
from netx_api.models import AppUser


class UserPrefsApiTests(unittest.TestCase):
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
            admin = db.query(AppUser).filter(AppUser.username == "admin").one()
            admin.must_change_password = False
            create_user(db, username="alice", password="alice123", role="user", actor=admin)
            create_user(db, username="bob", password="bob12345", role="user", actor=admin)
            for name in ("alice", "bob"):
                u = db.query(AppUser).filter(AppUser.username == name).one()
                u.must_change_password = False
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

    def _login(self, username: str, password: str) -> str:
        r = self.client.post("/v1/auth/login", json={"username": username, "password": password})
        self.assertEqual(r.status_code, 200, r.text)
        return str(r.json()["access_token"])

    def test_me_includes_empty_prefs(self) -> None:
        token = self._login("alice", "alice123")
        me = self.client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(me.status_code, 200, me.text)
        self.assertEqual(me.json().get("prefs"), {})
        login = self.client.post("/v1/auth/login", json={"username": "alice", "password": "alice123"})
        self.assertEqual(login.json().get("prefs"), {})

    def test_get_and_put_workbench(self) -> None:
        token = self._login("alice", "alice123")
        empty = self.client.get("/v1/auth/me/prefs", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertEqual(empty.json(), {"prefs": {}})

        body = {"order": ["ne", "topology"], "hidden": ["webcrt"]}
        put = self.client.put(
            "/v1/auth/me/prefs/workbench",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )
        self.assertEqual(put.status_code, 200, put.text)
        self.assertEqual(put.json()["prefs"]["workbench"], body)

        got = self.client.get("/v1/auth/me/prefs", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(got.json()["prefs"]["workbench"], body)
        me = self.client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(me.json()["prefs"]["workbench"], body)

    def test_put_strips_unknown_fields(self) -> None:
        token = self._login("alice", "alice123")
        put = self.client.put(
            "/v1/auth/me/prefs/workbench",
            headers={"Authorization": f"Bearer {token}"},
            json={"order": ["ne"], "hidden": [], "extra": True, "panels": {}},
        )
        self.assertEqual(put.status_code, 200, put.text)
        self.assertEqual(put.json()["prefs"]["workbench"], {"order": ["ne"], "hidden": []})

    def test_unknown_key_404(self) -> None:
        token = self._login("alice", "alice123")
        r = self.client.put(
            "/v1/auth/me/prefs/panels",
            headers={"Authorization": f"Bearer {token}"},
            json={"order": [], "hidden": []},
        )
        self.assertEqual(r.status_code, 404)

    def test_validation_422(self) -> None:
        token = self._login("alice", "alice123")
        headers = {"Authorization": f"Bearer {token}"}

        bad_char = self.client.put(
            "/v1/auth/me/prefs/workbench",
            headers=headers,
            json={"order": ["NE_Admin"], "hidden": []},
        )
        self.assertEqual(bad_char.status_code, 422)

        too_long = self.client.put(
            "/v1/auth/me/prefs/workbench",
            headers=headers,
            json={"order": [f"m{i}" for i in range(65)], "hidden": []},
        )
        self.assertEqual(too_long.status_code, 422)

        bad_type = self.client.put(
            "/v1/auth/me/prefs/workbench",
            headers=headers,
            json={"order": "ne", "hidden": []},
        )
        self.assertEqual(bad_type.status_code, 422)

    def test_user_isolation(self) -> None:
        alice = self._login("alice", "alice123")
        bob = self._login("bob", "bob12345")
        layout = {"order": ["topology", "ne"], "hidden": ["ume"]}
        put = self.client.put(
            "/v1/auth/me/prefs/workbench",
            headers={"Authorization": f"Bearer {alice}"},
            json=layout,
        )
        self.assertEqual(put.status_code, 200, put.text)

        bob_me = self.client.get("/v1/auth/me", headers={"Authorization": f"Bearer {bob}"})
        self.assertEqual(bob_me.status_code, 200, bob_me.text)
        self.assertEqual(bob_me.json().get("prefs"), {})

        alice_me = self.client.get("/v1/auth/me", headers={"Authorization": f"Bearer {alice}"})
        self.assertEqual(alice_me.json()["prefs"]["workbench"], layout)

    def test_auth_disabled_does_not_insert_system_user(self) -> None:
        with patch("netx_api.auth_deps.settings.auth_enabled", False), patch(
            "netx_api.auth_middleware.settings.auth_enabled", False
        ):
            r = self.client.put(
                "/v1/auth/me/prefs/workbench",
                json={"order": ["ne"], "hidden": []},
            )
        self.assertEqual(r.status_code, 409, r.text)
        db = self.Session()
        try:
            self.assertIsNone(db.get(AppUser, "system"))
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
