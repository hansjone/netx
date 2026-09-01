"""Tests for audit noise filtering (persist policy + list exclude_noise)."""

from __future__ import annotations

import unittest

from netx_api.audit_async import audit_should_persist


class AuditShouldPersistTests(unittest.TestCase):
    def test_drop_successful_http_get(self) -> None:
        self.assertFalse(
            audit_should_persist(action="http.get", method="GET", status_code=200, path="/v1/topology")
        )

    def test_keep_failed_http_get(self) -> None:
        self.assertTrue(
            audit_should_persist(action="http.get", method="GET", status_code=500, path="/v1/topology")
        )

    def test_keep_http_mutations_without_sampling(self) -> None:
        for method, action in (
            ("POST", "http.post"),
            ("PUT", "http.put"),
            ("PATCH", "http.patch"),
            ("DELETE", "http.delete"),
        ):
            self.assertTrue(
                audit_should_persist(action=action, method=method, status_code=200, path="/v1/x"),
                msg=action,
            )

    def test_drop_middleware_noise(self) -> None:
        for action in (
            "audit.list",
            "webcrt.get",
            "webcrt.post",
            "users.get",
            "api_tokens.get",
            "auth.unauthorized",
            "auth.password_change_required",
            "auth.forbidden_scope",
        ):
            self.assertFalse(
                audit_should_persist(action=action, method="GET", status_code=200),
                msg=action,
            )

    def test_keep_semantic_webcrt(self) -> None:
        self.assertTrue(audit_should_persist(action="webcrt.session_created", status_code=0))
        self.assertTrue(audit_should_persist(action="webcrt.command", status_code=0))
        self.assertTrue(audit_should_persist(action="webcrt.session_closed", status_code=0))

    def test_drop_auth_polls(self) -> None:
        self.assertFalse(audit_should_persist(action="auth.me", method="GET", status_code=200))
        self.assertFalse(audit_should_persist(action="auth.sessions", method="GET", status_code=200))
        self.assertFalse(audit_should_persist(action="auth.refresh", method="POST", status_code=200))

    def test_keep_intentional_auth_ops(self) -> None:
        self.assertTrue(audit_should_persist(action="auth.login", status_code=200))
        self.assertTrue(audit_should_persist(action="auth.login_failed", status_code=401))
        self.assertTrue(audit_should_persist(action="auth.logout", status_code=200))
        self.assertTrue(audit_should_persist(action="auth.change_password", status_code=200))

    def test_keep_business_prefixes(self) -> None:
        self.assertTrue(audit_should_persist(action="ne.exec", status_code=200))
        self.assertTrue(audit_should_persist(action="port_traffic.device.start", status_code=200))
        self.assertTrue(audit_should_persist(action="config_sync.start", status_code=200))
        self.assertTrue(audit_should_persist(action="users.create", status_code=200))

    def test_drop_ume_token_get(self) -> None:
        self.assertFalse(audit_should_persist(action="ume.token.get", method="GET", status_code=200))


if __name__ == "__main__":
    unittest.main()
