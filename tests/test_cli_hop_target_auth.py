"""Unit tests for CLI-hop secondary auth (Huawei stelnet host-key + login)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from netx_api.ne_session_connect import (
    _interactive_target_auth,
    _looks_like_target_cli_prompt,
    _prompt_needs_auth,
    _prompt_needs_host_key_confirm,
)


class PromptDetectTests(unittest.TestCase):
    def test_progress_bytesio_is_netmiko_compatible(self) -> None:
        import io

        from netx_api.ne_session_connect import _ProgressBytesIO

        buf = _ProgressBytesIO(lambda _t: None)
        self.assertIsInstance(buf, io.BufferedIOBase)
        # Plain custom log objects are what broke every WebCRT connect (ValueError).
        self.assertFalse(isinstance(object(), io.BufferedIOBase))

    def test_huawei_username_and_password(self) -> None:
        self.assertEqual(
            _prompt_needs_auth("Please input the username:"),
            (True, False),
        )
        self.assertEqual(_prompt_needs_auth("Enter password:"), (False, True))
        self.assertEqual(_prompt_needs_auth("Password:"), (False, True))

    def test_host_key_prompts(self) -> None:
        cont, save = _prompt_needs_host_key_confirm(
            "The server is not authenticated. Continue to access it? [Y/N]:"
        )
        self.assertTrue(cont)
        self.assertFalse(save)
        cont, save = _prompt_needs_host_key_confirm("Save the server's public key? [Y/N]:")
        self.assertFalse(cont)
        self.assertTrue(save)

    def test_host_key_wrapped_yn_line(self) -> None:
        cont, save = _prompt_needs_host_key_confirm(
            "The server is not authenticated. Continue to access it?\n[Y/N]:"
        )
        self.assertTrue(cont)
        self.assertFalse(save)

    def test_password_change_not_host_key(self) -> None:
        cont, save = _prompt_needs_host_key_confirm("Change now? [Y/N]:")
        self.assertFalse(cont)
        self.assertFalse(save)

    def test_cli_prompt_rejects_yn(self) -> None:
        self.assertFalse(_looks_like_target_cli_prompt("Continue? [Y/N]:"))
        self.assertTrue(_looks_like_target_cli_prompt("<HW-TARGET>"))
        self.assertTrue(_looks_like_target_cli_prompt("[HW-VM-AR1000V-1]"))


class HuaweiStelnetAuthTests(unittest.TestCase):
    @patch("netx_api.ne_session_connect._read_channel")
    @patch("netx_api.ne_session_connect._send_line")
    def test_answers_host_key_then_login(
        self,
        mock_send: MagicMock,
        mock_read: MagicMock,
    ) -> None:
        """Transcript from Huawei AR stelnet first connect to untrusted target."""
        mock_read.side_effect = [
            "Please input the username:",
            (
                "Trying 1.1.1.2 ...\n"
                "Press CTRL+K to abort\n"
                "Connected to 1.1.1.2 ...\n"
                "The server is not authenticated. Continue to access it? [Y/N]:"
            ),
            "Save the server's public key? [Y/N]:",
            (
                "Jan  1 2017 01:16:39+00:00 HW-VM-AR1000V-1 "
                "%%01SSH/4/SAVE_PUBLICKEY(l)[3]:When deciding whether to save "
                "the server's public key 1.1.1.2, the user chose N.\n"
                "[HW-VM-AR1000V-1]\n"
                "Enter password:"
            ),
            (
                "  -----------------------------------------------------------------------------\n"
                "  User last login information:\n"
                "  -----------------------------------------------------------------------------\n"
                "  Access Type: SSH\n"
                "  IP-Address : 172.16.0.6\n"
                "  Time       : 2017-01-05 00:11:36+00:00\n"
                "  ----------------\n"
                "<HW-TARGET>"
            ),
        ]
        conn = MagicMock()
        seen: list[str] = []
        _interactive_target_auth(conn, "ipran", "secret", progress_cb=seen.append)
        self.assertEqual(
            [c.args[1] for c in mock_send.call_args_list],
            ["ipran", "Y", "N", "secret"],
        )
        self.assertTrue(any("host-key continue" in p for p in seen))
        self.assertTrue(any("Please input the username" in p for p in seen))

    @patch("netx_api.ne_session_connect._read_channel")
    @patch("netx_api.ne_session_connect._send_line")
    def test_hop_prompt_before_password_not_success(
        self,
        mock_send: MagicMock,
        mock_read: MagicMock,
    ) -> None:
        """``[hop]`` between host-key and password must not end auth early."""
        mock_read.side_effect = [
            "Please input the username:",
            "The server is not authenticated. Continue to access it? [Y/N]:",
            "Save the server's public key? [Y/N]:",
            "[HW-VM-AR1000V-1]\n",  # hop reprint — must keep waiting
            "Enter password:",
            "<TARGET>\n",
            "",
            "",
            "",
            "",
        ]
        conn = MagicMock()
        _interactive_target_auth(conn, "ipran", "secret")
        sent = [c.args[1] for c in mock_send.call_args_list]
        self.assertEqual(sent, ["ipran", "Y", "N", "secret"])

    @patch("netx_api.ne_session_connect._read_channel")
    @patch("netx_api.ne_session_connect._send_line")
    def test_answers_password_change_after_login(
        self,
        mock_send: MagicMock,
        mock_read: MagicMock,
    ) -> None:
        """Huawei ``Change now? [Y/N]:`` after password must not hang auth until timeout."""
        chunks = [
            "Please input the username:",
            "Enter password:",
            "Change now? [Y/N]:",
            "<HW-TARGET>\n",
        ]
        mock_read.side_effect = lambda *_a, **_k: chunks.pop(0) if chunks else ""
        conn = MagicMock()
        seen: list[str] = []
        _interactive_target_auth(conn, "admin", "secret", progress_cb=seen.append)
        self.assertEqual(
            [c.args[1] for c in mock_send.call_args_list],
            ["admin", "secret", "N"],
        )
        self.assertTrue(any("password-change" in p for p in seen))


if __name__ == "__main__":
    unittest.main()
