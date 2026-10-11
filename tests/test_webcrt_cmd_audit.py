"""Device-echo command audit: LineScreen / CommandTracker and the live session wiring."""

from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock, patch

from netx_api.webcrt_cmd_audit import CommandTracker, LineScreen, split_prompt
from netx_api.webcrt_session_model import WebcrtSession


def _run(steps, *, linux=False, settle=False):
    t = CommandTracker(linux_like=linux)
    res = []
    for kind, val in steps:
        if kind == "out":
            res += t.feed(val)
        else:
            lines, saw, enter = val
            res += t.on_stdin(lines, saw_input=saw, had_enter=enter)
    if settle:
        res += t.expire(time.monotonic() + 5.0)
    return [r.command for r in res]


class LineScreenTests(unittest.TestCase):
    def _row(self, text: str) -> str:
        s = LineScreen()
        s.feed_row(text)
        return s.text()

    def test_plain_and_cr(self) -> None:
        self.assertEqual(self._row("abc\rX"), "Xbc")

    def test_backspace_overwrite(self) -> None:
        self.assertEqual(self._row("disX\x08 \x08play"), "display")

    def test_csi_left_erase_and_delete(self) -> None:
        self.assertEqual(self._row("<r1>display clock\x1b[13D\x1b[K"), "<r1>")
        self.assertEqual(self._row("abcdef\x1b[3D\x1b[2P"), "abcf")

    def test_huawei_history_redraw(self) -> None:
        raw = "<r1>display clock\x1b[13D             \x1b[13Ddis ip int brief \x1b[17D                 \x1b[17Ddisplay clock"
        self.assertEqual(self._row(raw), "<r1>display clock")

    def test_more_pager_wipe_leaves_blank_row(self) -> None:
        self.assertEqual(self._row("  ---- More ----\x1b[16D                \x1b[16D"), "")

    def test_partial_escape_is_carried_between_chunks(self) -> None:
        t = CommandTracker()
        t.feed("<r1>a\x1b[")
        t.feed("1Db")
        self.assertEqual(t.current_row(), "<r1>b")


class SplitPromptTests(unittest.TestCase):
    def test_vendors(self) -> None:
        self.assertEqual(split_prompt("<r1>display clock"), ("<r1>", "display clock"))
        self.assertEqual(split_prompt("[~r1-GigabitEthernet0/0/0]ip address 1.1.1.1 24")[1], "ip address 1.1.1.1 24")
        self.assertEqual(split_prompt("R2(config-if)#no shut"), ("R2(config-if)#", "no shut"))
        self.assertEqual(split_prompt("[admin@MikroTik] /ip> print")[1], "print")
        self.assertEqual(split_prompt("[user@host ~]$ ls -l")[1], "ls -l")

    def test_not_a_prompt(self) -> None:
        self.assertIsNone(split_prompt("[Y/N]:"))
        self.assertIsNone(split_prompt("  ---- More ----"))
        self.assertIsNone(split_prompt("Username:admin"))


class TrackerScenarioTests(unittest.TestCase):
    def test_echo_row_wins_over_lagging_typed_text(self) -> None:
        got = _run([("out", "<r1>"), ("in", (["show clock"], True, True)), ("out", "sh"), ("out", "ow clock\r\nT\r\n<r1>")])
        self.assertEqual(got, ["<r1>show clock"])

    def test_backspace_edit(self) -> None:
        got = _run([("out", "R2#"), ("in", (["shoX"], True, False)), ("out", "shoX\b \bw"),
                    ("in", (["show clock"], True, True)), ("out", " clock\r\nT\r\nR2#")])
        self.assertEqual(got, ["R2#show clock"])

    def test_bare_enter_not_audited(self) -> None:
        self.assertEqual(_run([("out", "<r1>"), ("in", ([], False, True)), ("out", "\r\n<r1>")]), [])

    def test_history_recall_same_frame_and_separate(self) -> None:
        a = _run([("out", "<r1>"), ("in", ([], True, True)), ("out", "display clock\r\nx\r\n<r1>")])
        b = _run([("out", "<r1>"), ("in", ([], True, False)), ("out", "display clock"),
                  ("in", ([], False, True)), ("out", "\r\nx\r\n<r1>")])
        self.assertEqual(a, ["<r1>display clock"])
        self.assertEqual(b, ["<r1>display clock"])

    def test_tab_redraw_before_enter_echo(self) -> None:
        # Huawei redraws the line after CRLF on Tab; the executed line is the later row.
        got = _run([("out", "<r1>"), ("in", (["display ver\t"], True, True)),
                    ("out", "\tdisX\x1b[1D \x1b[1Dplay ver\r\n<r1>display version \r\nVRP\r\n<r1>")])
        self.assertEqual(got, ["<r1>display version"])

    def test_password_redacted(self) -> None:
        self.assertEqual(_run([("out", "Password:"), ("in", (["secret"], True, True))]), ["***"])

    def test_login_dialog_not_audited(self) -> None:
        got = _run([("out", "Username:"), ("in", (["admin"], True, True)), ("out", "admin\r\nPassword:")], settle=True)
        self.assertEqual(got, [])

    def test_multiline_paste_keeps_order_and_skips_empty_echo(self) -> None:
        got = _run([("out", "R2#"), ("in", (["a", "b"], True, True)), ("out", "a\r\nR2#\r\nR2#b\r\nR2#")])
        self.assertEqual(got, ["R2#a", "R2#b"])

    def test_pager_enter_is_not_a_command(self) -> None:
        got = _run([("out", "<r1>"), ("in", (["display cur"], True, True)),
                    ("out", "display cur\r\nl1\r\n  ---- More ----"), ("in", ([], False, True)),
                    ("out", "\x1b[16D                \x1b[16D\r\nl2\r\n<r1>")])
        self.assertEqual(got, ["<r1>display cur"])

    def test_no_echo_falls_back_to_snapshot(self) -> None:
        self.assertEqual(_run([("out", "R2#show clock"), ("in", ([], False, True))], settle=True), ["R2#show clock"])

    def test_linux_prompt_keeps_single_space(self) -> None:
        got = _run([("out", "[u@h ~]$ "), ("in", (["ls"], True, True)), ("out", "ls\r\nf\r\n[u@h ~]$ ")], linux=True)
        self.assertEqual(got, ["[u@h ~]$ ls"])

    def test_enter_typed_ahead_during_output_then_recall(self) -> None:
        # Up+Enter pressed while the previous command is still printing: output rows must
        # not swallow the bare Enter.
        got = _run([("out", "<r1>"), ("in", (["display clock"], True, True)),
                    ("in", ([], True, False)), ("in", ([], False, True)),
                    ("out", "display clock\r\n2026\r\nSunday\r\n<r1>"),
                    ("out", "display clock\r\n2026\r\nSunday\r\n<r1>")])
        self.assertEqual(got, ["<r1>display clock", "<r1>display clock"])

    def test_pager_enter_then_typed_line_recorded_once(self) -> None:
        got = _run([("out", "<r1>"), ("in", (["display cur"], True, True)),
                    ("out", "display cur\r\nl1\r\n  ---- More ----"), ("in", ([], False, True)),
                    ("out", "\x1b[16D                \x1b[16D\r\nl2\r\n<r1>"),
                    ("in", (["display clock"], True, True)), ("out", "display clock\r\nt\r\n<r1>")])
        self.assertEqual(got, ["<r1>display cur", "<r1>display clock"])

    def test_ctrl_c_echo_does_not_steal_next_enter(self) -> None:
        # IOS/bash echo the aborted row + CRLF; keys typed meanwhile must still be audited.
        t = CommandTracker()
        t.feed("R2#garbage")
        res = t.on_stdin([], saw_input=True, had_enter=False, abort=True)
        res += t.on_stdin(["show clock"], saw_input=True, had_enter=True)
        res += t.feed("\r\nR2#show clock\r\n*22:57:17 UTC\r\nR2#")
        self.assertEqual([r.command for r in res], ["R2#show clock"])

    def test_ctrl_c_with_slow_echo_still_skips_abort_linefeed(self) -> None:
        # Only "R2#garb" was echoed when Ctrl-C was typed; the device later prints the
        # whole aborted row + CRLF, then echoes the line typed meanwhile.
        t = CommandTracker()
        t.feed("R2#")
        t.on_stdin(["garbage"], saw_input=True, had_enter=False)
        t.feed("garb")
        res = t.on_stdin([], saw_input=True, had_enter=False, abort=True, abort_text="garbage")
        res += t.on_stdin(["show clock"], saw_input=True, had_enter=True)
        res += t.feed("age\r\nR2#show clock\r\n*22:57 UTC\r\nR2#")
        self.assertEqual([r.command for r in res], ["R2#show clock"])

    def test_ctrl_c_that_keeps_text_is_audited_as_displayed(self) -> None:
        # VRP ignores Ctrl-C at the prompt: the executed line really is garbage+command.
        t = CommandTracker()
        t.feed("<r1>garbage")
        res = t.on_stdin([], saw_input=True, had_enter=False, abort=True)
        res += t.on_stdin(["display clock"], saw_input=True, had_enter=True)
        res += t.feed("display clock\r\nError\r\n<r1>")
        self.assertEqual([r.command for r in res], ["<r1>garbagedisplay clock"])

    def test_network_prompt_classifier(self) -> None:
        from netx_api.webcrt_channel import looks_like_network_prompt as f

        for ok in ("R2#", "<r1>", "[~r1]", "R2(config)#"):
            self.assertTrue(f(ok), ok)
        for bad in ("[u@h ~]$", "root@h:~#", "~ #", "user@host>"):
            self.assertFalse(f(bad), bad)

    def test_bulk_output_skips_to_last_row_when_idle(self) -> None:
        t = CommandTracker()
        t.feed("".join(f"line {i}\r\n" for i in range(5000)) + "<r1>")
        self.assertEqual(t.current_row(), "<r1>")


def _live_session(reader_alive: bool = True) -> tuple[WebcrtSession, MagicMock]:
    conn = MagicMock()
    conn.RETURN = "\n"
    conn.remote_conn = MagicMock(spec=["recv_ready", "recv", "exit_status_ready", "resize_pty", "send"])
    conn.remote_conn.send.side_effect = lambda b: len(b)
    sess = WebcrtSession(
        session_id="s-live", ne_id="ne1", ne_name="lab", ne_ip="1.2.3.4", protocol="ssh",
        cols=80, rows=24, cli_keymap=False, owner_user_id="u1", owner_username="bob", conn=conn,
    )
    alive = MagicMock()
    alive.is_alive.return_value = reader_alive
    sess._reader = alive
    return sess, conn


class LiveSessionAuditTests(unittest.TestCase):
    @patch("netx_api.webcrt_session_model._audit")
    def test_command_resolved_from_device_echo_not_client_hint(self, mock_audit: MagicMock) -> None:
        sess, _ = _live_session()
        sess._note_stdout_for_audit("<r1>")
        sess.write_stdin("display clock\r", audit_line="<r1>dis")  # stale xterm snapshot
        mock_audit.assert_not_called()  # nothing recorded until the device echoes
        sess._note_stdout_for_audit("display clock\r\n2026-10-12\r\n<r1>")
        kwargs = mock_audit.call_args.kwargs
        self.assertEqual(kwargs["command"], "<r1>display clock")
        self.assertEqual(kwargs["via"], "device_echo")
        self.assertEqual(kwargs["owner_username"], "bob")

    @patch("netx_api.webcrt_session_model._audit")
    def test_keystroke_sent_before_audit_work(self, mock_audit: MagicMock) -> None:
        sess, conn = _live_session()
        sess._note_stdout_for_audit("<r1>")
        sess.write_stdin("display clock\r")
        self.assertTrue(conn.remote_conn.send.called)

    @patch("netx_api.webcrt_session_model._audit")
    def test_password_prompt_redacted_immediately(self, mock_audit: MagicMock) -> None:
        sess, _ = _live_session()
        sess._note_stdout_for_audit("Password:")
        self.assertTrue(sess._password_mode)
        sess.write_stdin("hunter2\r")
        kwargs = mock_audit.call_args.kwargs
        self.assertEqual(kwargs["command"], "***")
        self.assertTrue(kwargs["redacted"])

    @patch("netx_api.webcrt_session_model._audit")
    def test_post_login_is_trusted(self, mock_audit: MagicMock) -> None:
        sess, _ = _live_session()
        sess.write_stdin("screen-length 0 temporary\r", audit_source="post_login")
        kwargs = mock_audit.call_args.kwargs
        self.assertEqual(kwargs["command"], "screen-length 0 temporary")
        self.assertEqual(kwargs["source"], "post_login")

    @patch("netx_api.webcrt_session_model._audit")
    def test_prompt_sync_enter_not_audited(self, mock_audit: MagicMock) -> None:
        sess, _ = _live_session()
        sess._note_stdout_for_audit("<r1>display clock")
        sess.write_stdin("\r", audit_source="prompt_sync")
        sess._note_stdout_for_audit("\r\n<r1>")
        mock_audit.assert_not_called()


class StdoutHandOffTests(unittest.TestCase):
    def test_drain_stdout_batches_and_reports_eof_and_stale(self) -> None:
        sess, _ = _live_session()
        sess.attach_gen = 3
        sess.out_queue.put(b"a")
        sess.out_queue.put(b"b")
        self.assertEqual(sess.drain_stdout(2), ("stale", b""))
        self.assertEqual(sess.drain_stdout(3), ("data", b"ab"))
        self.assertEqual(sess.drain_stdout(3), ("empty", b""))
        sess.out_queue.put(b"z")
        sess.out_queue.put(None)
        self.assertEqual(sess.drain_stdout(3), ("eof", b"z"))

    def test_wake_is_coalesced(self) -> None:
        sess, _ = _live_session()
        loop = MagicMock()
        sess.set_notifier(loop, MagicMock())
        sess._wake()
        sess._wake()
        sess._wake()
        self.assertEqual(loop.call_soon_threadsafe.call_count, 1)
        sess.drain_stdout(sess.attach_gen)  # pump consumed -> next put wakes again
        sess._wake()
        self.assertEqual(loop.call_soon_threadsafe.call_count, 2)

    def test_reader_keeps_split_utf8_intact(self) -> None:
        import threading

        sess, conn = _live_session(reader_alive=False)
        chunks = [b"<r1>\xe4\xbd", b"\xa0\xe5\xa5\xbd\r\n", None]
        ch = conn.remote_conn
        ch.recv_ready.return_value = True
        ch.exit_status_ready.return_value = False
        seen: list[str] = []
        it = iter(chunks)

        def _recv(_n: int) -> bytes:
            nxt = next(it)
            if nxt is None:
                sess.closed = True
                return b""
            return nxt

        ch.recv.side_effect = _recv
        sess._note_stdout_for_audit = lambda text: seen.append(text)  # type: ignore[method-assign]
        t = threading.Thread(target=sess._reader_loop, daemon=True)
        t.start()
        t.join(timeout=3)
        self.assertEqual("".join(seen), "<r1>你好\r\n")


if __name__ == "__main__":
    unittest.main()
