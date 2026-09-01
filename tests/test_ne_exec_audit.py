"""Device exec audit coverage for managed-ne /exec endpoints."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from netx_api.auth_deps import AuthContext, require_user
from netx_api.db import get_db
from netx_api.managed_ne_router import router as managed_ne_router
from netx_api.models import AppUser


class ManagedNeExecAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = FastAPI()
        self.app.include_router(managed_ne_router)

        fake = AppUser(id="u1", username="alice", role="admin", password_hash="x")
        fake.is_active = True

        def _user() -> AuthContext:
            return AuthContext(user=fake, auth_via="disabled", scopes=frozenset({"ne:exec"}))

        def _db():
            yield MagicMock()

        self.app.dependency_overrides[require_user] = _user
        self.app.dependency_overrides[get_db] = _db
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.app.dependency_overrides.clear()

    @patch("netx_api.managed_ne_router.write_audit")
    @patch("netx_api.managed_ne_router.execute_managed_ne_commands")
    def test_exec_writes_audit(self, mock_exec: MagicMock, mock_audit: MagicMock) -> None:
        mock_exec.return_value = {
            "ok": True,
            "device": {"name": "core-sw", "ip_address": "10.0.0.1"},
            "commands": ["display version"],
            "output": "VRP",
        }
        r = self.client.post(
            "/v1/managed-ne/exec",
            json={"ne_id": "ne1", "commands": ["display version"]},
        )
        self.assertEqual(r.status_code, 200)
        mock_audit.assert_called_once()
        kwargs = mock_audit.call_args.kwargs
        self.assertEqual(kwargs["action"], "ne.exec")
        self.assertEqual(kwargs["actor_username"], "alice")
        self.assertEqual(kwargs["detail"]["ne_name"], "core-sw")
        self.assertEqual(kwargs["detail"]["commands"], ["display version"])

    @patch("netx_api.managed_ne_router.write_audit")
    @patch("netx_api.managed_ne_router.execute_managed_ne_commands_batch")
    def test_exec_batch_writes_audit(self, mock_batch: MagicMock, mock_audit: MagicMock) -> None:
        mock_batch.return_value = {
            "items": [{"ok": True}, {"ok": False}],
        }
        r = self.client.post(
            "/v1/managed-ne/exec-batch",
            json={"ne_ids": ["a", "b"], "commands": ["display clock"]},
        )
        self.assertEqual(r.status_code, 200)
        mock_audit.assert_called_once()
        kwargs = mock_audit.call_args.kwargs
        self.assertEqual(kwargs["action"], "ne.exec_batch")
        self.assertEqual(kwargs["detail"]["ok_count"], 1)
        self.assertEqual(kwargs["detail"]["fail_count"], 1)


if __name__ == "__main__":
    unittest.main()
