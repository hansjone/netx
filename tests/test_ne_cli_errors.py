"""Unit tests for CLI auth-failure classification."""

from __future__ import annotations

import unittest

from netx_api.ne_cli_errors import find_auth_failure_snippet, format_cli_failure


class CliAuthClassifyTests(unittest.TestCase):
    def test_permission_denied_password(self):
        text = "banner\nca-oper@114.1.105.3: Permission denied (password).\n"
        self.assertIn("Permission denied", find_auth_failure_snippet(text) or "")

    def test_prompt_timeout_promoted_to_auth(self):
        exc = "ReadTimeout: Pattern not detected: '[>#]' in output."
        transcript = "Warning\nca-oper@114.1.105.3: Permission denied (password).\n"
        msg = format_cli_failure(exc, transcript)
        self.assertTrue(msg.startswith("auth_rejected:"))
        self.assertIn("Permission denied", msg)
        self.assertIn("prompt_timeout", msg)

    def test_plain_timeout_unchanged(self):
        exc = RuntimeError("ReadTimeout: Pattern not detected: '[>#]' in output.")
        msg = format_cli_failure(exc, "show running-config\n...still dumping...\n")
        self.assertIn("ReadTimeout", msg)
        self.assertFalse(msg.startswith("auth_rejected:"))

    def test_authentication_exception(self):
        class AuthenticationException(Exception):
            pass

        msg = format_cli_failure(AuthenticationException("target_auth_rejected: Permission denied"))
        self.assertTrue(msg.startswith("auth_rejected:"))


if __name__ == "__main__":
    unittest.main()
