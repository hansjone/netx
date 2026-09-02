"""Tests for WebCRT / device operation audit (command lines + audit_log dual-write)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from netx_api.webcrt_channel import (
    _DB_AUDIT_EVENTS,
    _audit,
    feed_command_line_buffer,
    looks_like_password_prompt,
    normalize_audit_line,
    extract_last_prompt_command,
)
from netx_api.webcrt_session_model import WebcrtSession


class FeedCommandLineBufferTests(unittest.TestCase):
    def test_enter_emits_line(self) -> None:
        buf, lines = feed_command_line_buffer("", "display version\r")
        self.assertEqual(buf, "")
        self.assertEqual(lines, ["display version"])

    def test_backspace(self) -> None:
        buf, lines = feed_command_line_buffer("", "disX\x08play\n")
        self.assertEqual(buf, "")
        self.assertEqual(lines, ["display"])

    def test_multiline_paste(self) -> None:
        buf, lines = feed_command_line_buffer("", "a\nb\nc\n")
        self.assertEqual(buf, "")
        self.assertEqual(lines, ["a", "b", "c"])

    def test_partial_stays_in_buffer(self) -> None:
        buf, lines = feed_command_line_buffer("", "sho")
        self.assertEqual(buf, "sho")
        self.assertEqual(lines, [])
        buf, lines = feed_command_line_buffer(buf, "w ver\n")
        self.assertEqual(buf, "")
        self.assertEqual(lines, ["show ver"])

    def test_empty_line_skipped(self) -> None:
        buf, lines = feed_command_line_buffer("", "\r\n")
        self.assertEqual(buf, "")
        self.assertEqual(lines, [])

    def test_ctrl_c_clears(self) -> None:
        buf, lines = feed_command_line_buffer("half", "\x03show\n")
        self.assertEqual(buf, "")
        self.assertEqual(lines, ["show"])

    def test_truncates_long_line(self) -> None:
        long = "x" * 600
        buf, lines = feed_command_line_buffer("", long + "\n", max_line=512)
        self.assertEqual(buf, "")
        self.assertEqual(len(lines), 1)
        self.assertEqual(len(lines[0]), 512)

    def test_ignores_delete_key_sequence(self) -> None:
        buf, lines = feed_command_line_buffer("", "dis\x1b[3~play\n")
        self.assertEqual(lines, ["display"])


class NormalizeAuditLineTests(unittest.TestCase):
    def test_keeps_zte_hash_prompt(self) -> None:
        self.assertEqual(
            normalize_audit_line("AL5458-ACC-6120HS#display version"),
            "AL5458-ACC-6120HS#display version",
        )

    def test_keeps_huawei_angle_prompt(self) -> None:
        self.assertEqual(
            normalize_audit_line("<HW-TARGET>display version"),
            "<HW-TARGET>display version",
        )

    def test_keeps_bracket_prompt(self) -> None:
        self.assertEqual(normalize_audit_line("[6150]show run"), "[6150]show run")

    def test_strips_ansi(self) -> None:
        self.assertEqual(
            normalize_audit_line("\x1b[31m6150#show run\x1b[0m"),
            "6150#show run",
        )


class ExtractPromptCommandTests(unittest.TestCase):
    def test_tab_redraw_after_carriage_return(self) -> None:
        """ZTE tab completion redraws the line with \\r — audit must use that tail."""
        transcript = (
            "AL5458-ACC-6120HS(config-if-loopback127)#ip ad"
            "\rAL5458-ACC-6120HS(config-if-loopback127)#ip address "
            "1.1.1.11 32"
        )
        self.assertEqual(
            extract_last_prompt_command(transcript),
            "AL5458-ACC-6120HS(config-if-loopback127)#ip address 1.1.1.11 32",
        )

    def test_config_mode_interface(self) -> None:
        transcript = "AL5458-ACC-6120HS(config)#interface loopback127"
        self.assertEqual(
            extract_last_prompt_command(transcript),
            "AL5458-ACC-6120HS(config)#interface loopback127",
        )

    def test_show_partial_command(self) -> None:
        transcript = "AL5458-ACC-6120HS(config-if-loopback127)#show th"
        self.assertEqual(
            extract_last_prompt_command(transcript),
            "AL5458-ACC-6120HS(config-if-loopback127)#show th",
        )


class PasswordPromptTests(unittest.TestCase):
    def test_detects_password_prompt(self) -> None:
        self.assertTrue(looks_like_password_prompt("Password:"))
        self.assertTrue(looks_like_password_prompt("Please enter password:"))
        self.assertTrue(looks_like_password_prompt("请输入密码:"))
        self.assertFalse(looks_like_password_prompt("<SW>"))
        self.assertFalse(looks_like_password_prompt("Username:"))


class AuditDualWriteTests(unittest.TestCase):
    def test_db_events_set(self) -> None:
        self.assertIn("session_created", _DB_AUDIT_EVENTS)
        self.assertIn("command", _DB_AUDIT_EVENTS)
        self.assertNotIn("session_attached", _DB_AUDIT_EVENTS)

    @patch("netx_api.webcrt_channel.webcrt_data_root")
    @patch("netx_api.audit_async.enqueue_audit")
    def test_lifecycle_enqueues_audit_log(self, mock_enq: MagicMock, mock_root: MagicMock) -> None:
        root = MagicMock()
        path = MagicMock()
        mock_root.return_value = root
        root.__truediv__ = MagicMock(return_value=path)
        path.open = MagicMock()
        fh = MagicMock()
        path.open.return_value.__enter__ = MagicMock(return_value=fh)
        path.open.return_value.__exit__ = MagicMock(return_value=False)

        _audit(
            "session_created",
            session_id="sid1",
            ne_id="ne1",
            ne_name="core-sw",
            ne_ip="10.0.0.1",
            protocol="ssh",
            owner_user_id="u1",
            owner_username="alice",
        )
        mock_enq.assert_called_once()
        kwargs = mock_enq.call_args.kwargs
        self.assertEqual(kwargs["action"], "webcrt.session_created")
        self.assertEqual(kwargs["actor_username"], "alice")
        self.assertEqual(kwargs["actor_user_id"], "u1")
        self.assertEqual(kwargs["detail"]["ne_name"], "core-sw")
        self.assertEqual(kwargs["detail"]["ne_ip"], "10.0.0.1")

    @patch("netx_api.webcrt_channel.webcrt_data_root")
    @patch("netx_api.audit_async.enqueue_audit")
    def test_attach_does_not_enqueue(self, mock_enq: MagicMock, mock_root: MagicMock) -> None:
        root = MagicMock()
        path = MagicMock()
        mock_root.return_value = root
        root.__truediv__ = MagicMock(return_value=path)
        path.open = MagicMock()
        fh = MagicMock()
        path.open.return_value.__enter__ = MagicMock(return_value=fh)
        path.open.return_value.__exit__ = MagicMock(return_value=False)

        _audit("session_attached", session_id="sid1", ne_id="ne1")
        mock_enq.assert_not_called()


class SessionCommandAuditTests(unittest.TestCase):
    @patch("netx_api.webcrt_session_model._audit")
    def test_write_stdin_audits_completed_command(self, mock_audit: MagicMock) -> None:
        conn = MagicMock()
        conn.RETURN = "\n"
        conn.remote_conn = MagicMock(spec=["recv_ready", "recv", "exit_status_ready", "resize_pty"])
        # Force write_channel path (no send).
        del conn.remote_conn.send
        conn.write_channel = MagicMock()

        sess = WebcrtSession(
            session_id="s-cmd",
            ne_id="ne1",
            ne_name="lab",
            ne_ip="1.2.3.4",
            protocol="ssh",
            cols=80,
            rows=24,
            cli_keymap=False,
            owner_user_id="u1",
            owner_username="bob",
            conn=conn,
        )
        sess.write_stdin("display version\r")
        mock_audit.assert_called()
        event = mock_audit.call_args[0][0]
        self.assertEqual(event, "command")
        kwargs = mock_audit.call_args.kwargs
        self.assertEqual(kwargs["command"], "display version")
        self.assertEqual(kwargs["owner_username"], "bob")
        self.assertEqual(kwargs["ne_name"], "lab")
        self.assertFalse(kwargs["redacted"])

    @patch("netx_api.webcrt_session_model._audit")
    def test_stdout_prompt_line_wins_over_stdin_on_enter(self, mock_audit: MagicMock) -> None:
        """Tab-completed command is in device stdout, not stdin keystrokes."""
        conn = MagicMock()
        conn.RETURN = "\n"
        conn.remote_conn = MagicMock(spec=["recv_ready", "recv", "exit_status_ready", "resize_pty"])
        del conn.remote_conn.send
        conn.write_channel = MagicMock()

        sess = WebcrtSession(
            session_id="s-stdout",
            ne_id="ne1",
            ne_name="lab",
            ne_ip="1.2.3.4",
            protocol="ssh",
            cols=80,
            rows=24,
            cli_keymap=False,
            owner_user_id="u1",
            owner_username="bob",
            conn=conn,
        )
        sess._note_stdout_for_audit(
            "AL5458-ACC-6120HS(config-if-loopback127)#ip ad"
            "\rAL5458-ACC-6120HS(config-if-loopback127)#ip address 1.1.1.11 32"
        )
        sess.write_stdin("\r", audit_line="AL5458-ACC-6120HS(config-if-loopback127)#ip ad")
        kwargs = mock_audit.call_args.kwargs
        self.assertEqual(
            kwargs["command"],
            "AL5458-ACC-6120HS(config-if-loopback127)#ip address 1.1.1.11 32",
        )

    @patch("netx_api.webcrt_session_model._audit")
    def test_password_mode_redacts_command(self, mock_audit: MagicMock) -> None:
        conn = MagicMock()
        conn.RETURN = "\n"
        conn.remote_conn = MagicMock(spec=["recv_ready", "recv", "exit_status_ready", "resize_pty"])
        del conn.remote_conn.send
        conn.write_channel = MagicMock()

        sess = WebcrtSession(
            session_id="s-pw",
            ne_id="ne1",
            ne_name="lab",
            ne_ip="1.2.3.4",
            protocol="ssh",
            cols=80,
            rows=24,
            cli_keymap=False,
            conn=conn,
        )
        sess._note_stdout_for_audit("Password:")
        self.assertTrue(sess._password_mode)
        sess.write_stdin("secret-pass\r")
        kwargs = mock_audit.call_args.kwargs
        self.assertEqual(kwargs["command"], "***")
        self.assertTrue(kwargs["redacted"])
        self.assertFalse(sess._password_mode)

    @patch("netx_api.webcrt_session_model._audit")
    def test_post_login_source(self, mock_audit: MagicMock) -> None:
        conn = MagicMock()
        conn.RETURN = "\n"
        conn.remote_conn = MagicMock(spec=["recv_ready", "recv", "exit_status_ready", "resize_pty"])
        del conn.remote_conn.send
        conn.write_channel = MagicMock()

        sess = WebcrtSession(
            session_id="s-pl",
            ne_id="ne1",
            ne_name="lab",
            ne_ip="1.2.3.4",
            protocol="ssh",
            cols=80,
            rows=24,
            cli_keymap=False,
            conn=conn,
            post_login_commands=["screen-length 0 temporary"],
        )
        with patch("netx_api.webcrt_session_model.time.sleep"):
            sess.run_post_login_commands()
        kwargs = mock_audit.call_args.kwargs
        self.assertEqual(kwargs["source"], "post_login")
        self.assertEqual(kwargs["command"], "screen-length 0 temporary")


if __name__ == "__main__":
    unittest.main()
