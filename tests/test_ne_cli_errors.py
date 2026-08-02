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

    def test_authentication_exception_empty_message(self):
        class AuthenticationException(Exception):
            pass

        msg = format_cli_failure(AuthenticationException())
        self.assertTrue(msg.startswith("auth_rejected:"))

    def test_huawei_post_login_banner_not_auth_failure(self):
        """Bastion/SSH hop success banner must not be classified as auth reject."""
        text = (
            "Info: The max number of VTY users is 21, "
            "the number of current VTY users online is 1.\n"
            "The last successful login was performed at 19:28:16 08-02-2026 "
            "from 10.229.147.122 through SSH. Afterwards, 0 authentication "
            "failure occurred.\n"
            "<HUAWEI>"
        )
        self.assertIsNone(find_auth_failure_snippet(text))
        msg = format_cli_failure("ReadTimeout: Pattern not detected", text)
        self.assertFalse(msg.startswith("auth_rejected:"))

    def test_real_auth_failure_still_detected_near_banner(self):
        text = (
            "The last successful login was performed at 19:28:16 08-02-2026 "
            "from 10.229.147.122 through SSH. Afterwards, 0 authentication "
            "failure occurred.\n"
            "Error: Username or password is wrong.\n"
        )
        self.assertIn("Username or password is wrong", find_auth_failure_snippet(text) or "")


if __name__ == "__main__":
    unittest.main()
