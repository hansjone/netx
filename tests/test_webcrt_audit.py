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
    finalize_audit_line,
    is_auditable_command_line,
    _is_device_output_line,
    _is_prompt_only_line,
    resolve_audit_commands,
    pick_audit_command,
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


class FinalizeAuditLineTests(unittest.TestCase):
    def test_applies_echoed_backspaces(self) -> None:
        raw = "6150#disX\bplay version"
        self.assertEqual(finalize_audit_line(raw), "6150#display version")


class AuditableCommandLineTests(unittest.TestCase):
    def test_prompt_only_not_auditable(self) -> None:
        line = "AL5458-ACC-6120HS(config-if-loopback127)#"
        self.assertTrue(_is_prompt_only_line(line))
        self.assertFalse(is_auditable_command_line(line))

    def test_command_with_prompt_is_auditable(self) -> None:
        line = "AL5458-ACC-6120HS(config-if-loopback127)#ip address 1.1.1.11 255.255.255.255"
        self.assertFalse(_is_prompt_only_line(line))
        self.assertTrue(is_auditable_command_line(line))

    def test_device_error_not_auditable(self) -> None:
        line = "%Error 140303: Invalid input detected at '^' marker."
        self.assertTrue(_is_device_output_line(line))
        self.assertFalse(is_auditable_command_line(line))

    def test_plain_stdin_without_prompt_not_auditable(self) -> None:
        self.assertFalse(is_auditable_command_line("display version"))
        out = resolve_audit_commands(
            ["display version"],
            prompt_hint="6150#",
            source="stdin",
        )
        self.assertEqual(out, ["6150#display version"])

    def test_plain_stdin_records_actual_without_hint(self) -> None:
        out = resolve_audit_commands(["display version"], source="stdin")
        self.assertEqual(out, ["display version"])


class ResolveAuditCommandsTests(unittest.TestCase):
    def test_merged_flush_audits_all_buf_lines(self) -> None:
        """Interleave flush: multiple Enter in one write_stdin must not drop middle commands."""
        out = resolve_audit_commands(
            ["show version", "show ll n b", "show intf"],
            audit_lines=["AL5458#show intf"],
            prompt_hint="AL5458#show version",
            source="stdin",
        )
        self.assertEqual(len(out), 3)
        self.assertIn("show version", out[0])
        self.assertIn("show ll n b", out[1])
        self.assertIn("show intf", out[2])

    def test_hint_wins_over_corrupt_stdin(self) -> None:
        cmd = pick_audit_command(
            "how ll n b",
            "AL5458#show ll n b",
            prompt_hint="AL5458#",
            source="stdin",
        )
        self.assertEqual(cmd, "AL5458#show ll n b")

    def test_tab_in_stdin_uses_prompt_hint(self) -> None:
        cmd = pick_audit_command(
            "dis ip int\tbr",
            None,
            prompt_hint="[~r1]display ip interface brief",
            source="stdin",
        )
        self.assertEqual(cmd, "[~r1]display ip interface brief")

    def test_tab_completion_hint_beats_stdin(self) -> None:
        out = resolve_audit_commands(
            ["dis ip int\tbr"],
            audit_lines=["[~r1]display ip interface brief"],
            source="stdin",
        )
        self.assertEqual(out, ["[~r1]display ip interface brief"])

    def test_insert_edit_hint_beats_stdin(self) -> None:
        cmd = pick_audit_command(
            "dislay version",
            "[~r1]display version",
            source="stdin",
        )
        self.assertEqual(cmd, "[~r1]display version")

    def test_device_echo_expands_stale_tab_hint(self) -> None:
        """xterm may still show mid-Tab text while device already redrew the full command."""
        cmd = pick_audit_command(
            "dis inter\t",
            "[~r1]dis interface br",
            prompt_hint="[~r1]dis interface brief",
            source="stdin",
        )
        self.assertEqual(cmd, "[~r1]dis interface brief")

    def test_is_cli_expansion_abbrev_tokens(self) -> None:
        from netx_api.webcrt_channel import _is_cli_expansion

        self.assertTrue(_is_cli_expansion("[~r1]dis interface br", "[~r1]dis interface brief"))
        self.assertTrue(_is_cli_expansion("<r1>dis ip int", "<r1>display ip interface brief"))
        self.assertFalse(_is_cli_expansion("[~r1]dis arp all", "[~r1]dis interface brief"))

    def test_rejects_glued_interface_legend(self) -> None:
        from netx_api.webcrt_channel import _is_cli_expansion, sanitize_audit_command

        dirty = (
            "[~r1]display ip interface brief"
            "*down: administratively down"
            "!down: FIB overload down"
            "^down: standby"
            "(l): loopback"
        )
        self.assertEqual(sanitize_audit_command(dirty), "[~r1]display ip interface brief")
        cmd = pick_audit_command(
            "dis ip int\t",
            dirty,
            prompt_hint=dirty,
            stdout_tail=dirty + "\nInterface PHY\n",
            source="stdin",
        )
        self.assertEqual(cmd, "[~r1]display ip interface brief")
        self.assertFalse(_is_cli_expansion("[~r1]display ip interface brief", dirty))

    def test_history_recall_csi_render(self) -> None:
        from netx_api.webcrt_channel import extract_last_prompt_command, render_pty_line

        raw = "[~r1-LoopBack1]commit\x1b[6D      \x1b[6Dip address 10.1.1.1 33"
        self.assertEqual(render_pty_line(raw), "[~r1-LoopBack1]ip address 10.1.1.1 33")
        self.assertEqual(
            extract_last_prompt_command(raw + "\n"),
            "[~r1-LoopBack1]ip address 10.1.1.1 33",
        )

    def test_history_edit_fragment_not_glued_to_prompt(self) -> None:
        """Up-arrow edit only types '33' — must not audit as '[*r1-LoopBack1]33'."""
        cmd = pick_audit_command(
            "33",
            None,
            prompt_hint="[*r1-LoopBack1]ip address 10.1.1.1 25",
            stdout_tail="[*r1-LoopBack1]ip address 10.1.1.1 25\x1b[2D33\n",
            source="stdin",
        )
        self.assertEqual(cmd, "[*r1-LoopBack1]ip address 10.1.1.1 33")
        # Even with no usable stdout, never publish bare prompt+fragment.
        cmd2 = pick_audit_command(
            "33",
            None,
            prompt_hint="[*r1-LoopBack1]ip address 10.1.1.1 25",
            source="stdin",
        )
        self.assertEqual(cmd2, "[*r1-LoopBack1]ip address 10.1.1.1 25")

    def test_history_insert_fragment_ip(self) -> None:
        # Real Huawei redraws include a trailing space before CSI left.
        stdout = (
            "[~r1]display interface brief "
            "\x1b[24D                        \x1b[24D"
            "display ip interface brief"
        )
        cmd = pick_audit_command(
            "ip",
            None,
            prompt_hint="[~r1]display interface brief",
            stdout_tail=stdout,
            source="stdin",
        )
        self.assertEqual(cmd, "[~r1]display ip interface brief")

    def test_history_midline_delete_prefers_device_echo(self) -> None:
        """Up-arrow then delete middle ``ip`` — must not keep stale longer audit_line."""
        frag = (
            "<r1>dis ip interface brief "
            + ("\x1b[1D" * 18)
            + " interface brief  \x1b[18D\x1b[1D interface brief  \x1b[18D\x1b[17C"
        )
        # Stale xterm snapshot still has ``ip``; device echo already deleted it.
        cmd = pick_audit_command(
            "",
            "<r1>dis ip interface brief",
            prompt_hint="<r1>dis ip interface brief",
            stdout_tail=frag + "\n",
            source="stdin",
        )
        self.assertEqual(cmd, "<r1>dis interface brief")
        out = resolve_audit_commands(
            [],
            audit_line="<r1>dis ip interface brief",
            prompt_hint="<r1>dis ip interface brief",
            stdout_tail=frag + "\n",
            source="stdin",
        )
        self.assertEqual(out, ["<r1>dis interface brief"])

    def test_bare_enter_does_not_reaudit_previous_command(self) -> None:
        """Empty Enter after a prior command must not invent another audit from stdout."""
        out = resolve_audit_commands(
            [],
            audit_line=None,
            prompt_hint="<r1>dis interface",
            stdout_tail="<r1>dis interface \nEthernet1/0/0 current state : UP\n<r1>",
            source="stdin",
        )
        self.assertEqual(out, [])
        out2 = resolve_audit_commands(
            [],
            audit_line="<r1>",  # prompt-only from xterm
            prompt_hint="<r1>dis interface",
            stdout_tail="<r1>dis interface \n",
            source="stdin",
        )
        self.assertEqual(out2, [])
        # Stale audit_line harvested from previous command row on screen.
        out3 = resolve_audit_commands(
            [],
            audit_line="<r1>dis interface brief",
            prompt_hint="<r1>dis interface brief",
            stdout_tail=(
                "<r1>dis interface brief \n"
                "PHY: Physical\n"
                "*down: administratively down\n"
                "<r1>"
            ),
            source="stdin",
        )
        self.assertEqual(out3, [])
        # Prompt redrawn with CR onto the previous output row (common on Huawei).
        out4 = resolve_audit_commands(
            [],
            audit_line="<r1>dis interface brief",
            prompt_hint="<r1>dis interface brief",
            stdout_tail="Ethernet1/0/0 UP\r<r1>",
            source="stdin",
        )
        self.assertEqual(out4, [])
        # Leftover glyphs after CR must not look like a live command row.
        out5 = resolve_audit_commands(
            [],
            audit_line="<r1>dis interface brief",
            stdout_tail="Interface PHY Protocol\r<r1>",
            source="stdin",
        )
        self.assertEqual(out5, [])
        # Trailing CR after bare prompt must still look idle (not empty → stale hint).
        out6 = resolve_audit_commands(
            [],
            audit_line="<r1>sys",
            stdout_tail=(
                "---- More ----\x1b[16D                \x1b[16D<r1>sys\r\r\n"
                "Enter system view, return user view with return command.\r\r\n"
                "[~r1]\r\r\n"
            ),
            source="stdin",
        )
        self.assertEqual(out6, [])
        # pick() itself must also refuse stale hint on bare prompt.
        self.assertIsNone(
            pick_audit_command(
                "",
                "<r1>sys",
                stdout_tail="[~r1]\r\r\n",
                source="stdin",
            )
        )

    def test_tab_interface_loopback_from_session_transcript(self) -> None:
        """Tab ``inter``→``interface LoopBack 1`` must audit the expanded line."""
        stdout = (
            "[~r1]inter\t\r\r\n"
            "[~r1]interface lo\t\r\r\n"
            "[~r1]interface LoopBack 1\r\r\n"
            "[~r1-LoopBack1]\r\r\n"
        )
        # Incomplete xterm snapshot must not win over device expansion.
        out = resolve_audit_commands(
            ["inter\tlo\t1"],
            audit_line="[~r1]interface lo",
            stdout_tail=stdout,
            source="stdin",
        )
        self.assertEqual(out, ["[~r1]interface LoopBack 1"])
        # Extra audit_line from duplicate Enter must not IndexError / drop the line.
        out2 = resolve_audit_commands(
            ["inter\tlo\t1"],
            audit_lines=["[~r1]interface lo", "[~r1]interface LoopBack 1"],
            stdout_tail=stdout,
            source="stdin",
        )
        self.assertEqual(out2, ["[~r1]interface LoopBack 1"])

    def test_history_trailing_delete_without_audit_line(self) -> None:
        """Delete trailing ``brief`` via CSI — audit even if xterm snapshot is missing."""
        frag = (
            "<r1>dis ip interface brief "
            + "".join("\x1b[1D \x1b[1D" for _ in range(6))
        )
        out = resolve_audit_commands(
            [],
            audit_line=None,
            stdout_tail=frag + "\n",
            source="stdin",
        )
        self.assertEqual(out, ["<r1>dis ip interface"])
        # Stale longer audit_line must not win over CSI-shortened echo.
        out2 = resolve_audit_commands(
            [],
            audit_line="<r1>dis ip interface brief",
            stdout_tail=frag + "\n",
            source="stdin",
        )
        self.assertEqual(out2, ["<r1>dis ip interface"])

    def test_early_stdin_plain_command(self) -> None:
        out = resolve_audit_commands(
            ["show version"],
            source="early_stdin",
            prompt_hint="AL5458#",
        )
        self.assertEqual(out, ["show version"])


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
        sess.write_stdin("\r", audit_line="6150#display version")
        mock_audit.assert_called()
        event = mock_audit.call_args[0][0]
        self.assertEqual(event, "command")
        kwargs = mock_audit.call_args.kwargs
        self.assertEqual(kwargs["command"], "6150#display version")
        self.assertEqual(kwargs["owner_username"], "bob")
        self.assertEqual(kwargs["ne_name"], "lab")
        self.assertFalse(kwargs["redacted"])

    @patch("netx_api.webcrt_session_model._audit")
    def test_audit_line_wins_over_stdout_backspaces(self, mock_audit: MagicMock) -> None:
        """Frontend xterm row at Enter is authoritative; PTY stdout has edit noise."""
        conn = MagicMock()
        conn.RETURN = "\n"
        conn.remote_conn = MagicMock(spec=["recv_ready", "recv", "exit_status_ready", "resize_pty"])
        del conn.remote_conn.send
        conn.write_channel = MagicMock()

        sess = WebcrtSession(
            session_id="s-xterm",
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
            "AL5458-ACC-6120HS(config-if-loopback127)#ip address 11\b\b\bip ad\b\bip address 1.1.1.11 32"
        )
        final = "AL5458-ACC-6120HS(config-if-loopback127)#ip address 1.1.1.11 255.255.255.255"
        sess.write_stdin("\r", audit_line=final)
        kwargs = mock_audit.call_args.kwargs
        self.assertEqual(kwargs["command"], final)

    @patch("netx_api.webcrt_session_model._audit")
    def test_empty_enter_not_audited(self, mock_audit: MagicMock) -> None:
        conn = MagicMock()
        conn.RETURN = "\n"
        conn.remote_conn = MagicMock(spec=["recv_ready", "recv", "exit_status_ready", "resize_pty"])
        del conn.remote_conn.send
        conn.write_channel = MagicMock()

        sess = WebcrtSession(
            session_id="s-empty",
            ne_id="ne1",
            ne_name="lab",
            ne_ip="1.2.3.4",
            protocol="ssh",
            cols=80,
            rows=24,
            cli_keymap=False,
            conn=conn,
        )
        sess._last_prompt_line = "AL5458-ACC-6120HS(config-if-loopback127)#ip address 1.1.1.11 255.255.255.255"
        sess._note_stdout_for_audit("AL5458-ACC-6120HS(config-if-loopback127)#\r\n")
        sess.write_stdin("\r", audit_line="AL5458-ACC-6120HS(config-if-loopback127)#")
        mock_audit.assert_not_called()

    @patch("netx_api.webcrt_session_model._audit")
    def test_duplicate_enter_same_command_deduped(self, mock_audit: MagicMock) -> None:
        conn = MagicMock()
        conn.RETURN = "\n"
        conn.remote_conn = MagicMock(spec=["recv_ready", "recv", "exit_status_ready", "resize_pty"])
        del conn.remote_conn.send
        conn.write_channel = MagicMock()

        sess = WebcrtSession(
            session_id="s-dedup",
            ne_id="ne1",
            ne_name="lab",
            ne_ip="1.2.3.4",
            protocol="ssh",
            cols=80,
            rows=24,
            cli_keymap=False,
            conn=conn,
        )
        line = "AL5458-ACC-6120HS(config)#intt"
        sess.write_stdin("\r", audit_line=line)
        sess.write_stdin("\r", audit_line=line)
        self.assertEqual(mock_audit.call_count, 1)

    @patch("netx_api.webcrt_session_model._audit")
    def test_prompt_sync_enter_not_audited(self, mock_audit: MagicMock) -> None:
        conn = MagicMock()
        conn.RETURN = "\n"
        conn.remote_conn = MagicMock(spec=["recv_ready", "recv", "exit_status_ready", "resize_pty"])
        del conn.remote_conn.send
        conn.write_channel = MagicMock()

        sess = WebcrtSession(
            session_id="s-sync",
            ne_id="ne1",
            ne_name="lab",
            ne_ip="1.2.3.4",
            protocol="ssh",
            cols=80,
            rows=24,
            cli_keymap=False,
            conn=conn,
        )
        sess._last_prompt_line = "AL5458-ACC-6120HS#configure terminal"
        sess._note_stdout_for_audit("AL5458-ACC-6120HS#configure terminal\r\n")
        sess.write_stdin("\r", audit_source="prompt_sync")
        mock_audit.assert_not_called()

    @patch("netx_api.webcrt_session_model._audit")
    def test_merged_write_stdin_audits_every_command(self, mock_audit: MagicMock) -> None:
        conn = MagicMock()
        conn.RETURN = "\n"
        conn.remote_conn = MagicMock(spec=["recv_ready", "recv", "exit_status_ready", "resize_pty"])
        del conn.remote_conn.send
        conn.write_channel = MagicMock()

        sess = WebcrtSession(
            session_id="s-merge",
            ne_id="ne1",
            ne_name="lab",
            ne_ip="1.2.3.4",
            protocol="ssh",
            cols=80,
            rows=24,
            cli_keymap=False,
            conn=conn,
        )
        sess._last_prompt_line = "AL5458#"
        sess.write_stdin(
            "show version\rshow ll n b\rshow intf\r",
            audit_lines=["AL5458#show intf"],
        )
        self.assertEqual(mock_audit.call_count, 3)
        recorded = [c.kwargs["command"] for c in mock_audit.call_args_list]
        self.assertTrue(any("show version" in x for x in recorded))
        self.assertTrue(any("show ll n b" in x for x in recorded))
        self.assertTrue(any("show intf" in x for x in recorded))

    @patch("netx_api.webcrt_session_model._audit")
    def test_device_error_audit_line_rejected(self, mock_audit: MagicMock) -> None:
        conn = MagicMock()
        conn.RETURN = "\n"
        conn.remote_conn = MagicMock(spec=["recv_ready", "recv", "exit_status_ready", "resize_pty"])
        del conn.remote_conn.send
        conn.write_channel = MagicMock()

        sess = WebcrtSession(
            session_id="s-err",
            ne_id="ne1",
            ne_name="lab",
            ne_ip="1.2.3.4",
            protocol="ssh",
            cols=80,
            rows=24,
            cli_keymap=False,
            conn=conn,
        )
        sess.write_stdin(
            "\r",
            audit_line="%Error 140303: Invalid input detected at '^' marker.",
        )
        mock_audit.assert_not_called()

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
