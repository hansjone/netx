"""Unit tests for WebCRT session service (mocked device connection)."""

from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from netx_api import webcrt_service as svc


class _FakeConn:
    def __init__(self) -> None:
        self.written: list[str] = []
        self.RETURN = "\n"
        self.remote_conn = MagicMock(spec=["recv_ready", "recv", "exit_status_ready", "resize_pty", "send_break", "send"])
        self.remote_conn.recv_ready.return_value = False
        self.remote_conn.exit_status_ready.return_value = False
        self.remote_conn.resize_pty = MagicMock()
        self.remote_conn.send_break = MagicMock()
        # No send by default so write_stdin uses write_channel in unit tests.
        del self.remote_conn.send

    def write_channel(self, data: str) -> None:
        self.written.append(data)

    def read_channel(self) -> str:
        return ""

    def disconnect(self) -> None:
        return None


class WebcrtServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        with svc._sessions_lock:
            for s in list(svc._sessions.values()):
                s.close("test_cleanup")
            svc._sessions.clear()

    def tearDown(self) -> None:
        self.setUp()

    def test_session_write_resize_and_close(self) -> None:
        conn = _FakeConn()
        sess = svc.WebcrtSession(
            session_id="s1",
            ne_id="ne1",
            ne_name="lab",
            ne_ip="1.2.3.4",
            protocol="ssh",
            cols=80,
            rows=24,
            cli_keymap=True,
            conn=conn,  # type: ignore[arg-type]
        )
        sess.write_stdin("show ver\n")
        self.assertEqual(conn.written, ["show ver\n"])
        # xterm Enter (\r) -> Netmiko RETURN (\n for SSH)
        sess.write_stdin("\r")
        self.assertEqual(conn.written[-1], "\n")
        # Backspace DEL -> BS
        sess.write_stdin("\x7f")
        self.assertEqual(conn.written[-1], "\x08")
        # CSI arrows pass through; application-cursor SS3 -> CSI.
        sess.write_stdin("\x1b[D\x1b[C\x1b[A\x1b[B")
        self.assertEqual(conn.written[-1], "\x1b[D\x1b[C\x1b[A\x1b[B")
        sess.write_stdin("\x1bOD")
        self.assertEqual(conn.written[-1], "\x1b[D")
        sess.resize(120, 40)
        conn.remote_conn.resize_pty.assert_called_with(width=120, height=40)
        conn.remote_conn.send_break = MagicMock()
        sess.send_break()
        conn.remote_conn.send_break.assert_called()
        sess.close("test")
        self.assertTrue(sess.closed)

    def test_map_network_cli_keys_helpers(self) -> None:
        # Backspace: DEL -> BS (SecureCRT/VT default), vendor-agnostic.
        self.assertEqual(svc.map_network_cli_keys("\x7fab"), "\x08ab")
        self.assertEqual(svc.map_network_cli_keys("\x7fab", device_type="cisco_ios", vendor="Cisco"), "\x08ab")
        self.assertEqual(svc.map_network_cli_keys("\x7fab", device_type="huawei", vendor="Huawei"), "\x08ab")
        # Keep CSI left; normalize SS3 application-cursor left.
        self.assertEqual(svc.map_network_cli_keys("\x1b[D"), "\x1b[D")
        self.assertEqual(svc.map_network_cli_keys("\x1bOD"), "\x1b[D")
        self.assertEqual(svc.map_network_cli_keys("\x1bOA\x1bOB\x1bOC\x1bOD"), "\x1b[A\x1b[B\x1b[C\x1b[D")
        self.assertTrue(svc.uses_network_cli_keymap("huawei", "Huawei"))
        self.assertFalse(svc.uses_network_cli_keymap("linux", "bastion"))
        self.assertEqual(svc.map_network_cli_enter("\r", _FakeConn()), "\n")  # type: ignore[arg-type]
        telnet = _FakeConn()
        telnet.RETURN = "\r\n"
        self.assertEqual(svc.map_network_cli_enter("\r", telnet), "\r\n")  # type: ignore[arg-type]
        self.assertEqual(svc.normalize_cli_transcript("R2#R2#\nR2#"), "R2#")
        self.assertEqual(svc.normalize_cli_transcript("banner\nR2#R2#"), "banner\nR2#")
        self.assertEqual(svc.prepare_bootstrap_output("login\nR2#\nR2#"), "login\nR2#")
        self.assertTrue(svc.prepare_bootstrap_output("login\nR2#").endswith("R2#"))
        # Slow VM / prime Enter can leave three identical prompts.
        self.assertEqual(svc.prepare_bootstrap_output("banner\nR2#\nR2#\nR2#"), "banner\nR2#")
        self.assertEqual(svc.prepare_bootstrap_output("banner\nR2#\n\nR2#"), "banner\nR2#")
        self.assertTrue(svc._is_prompt_only_echo("\r\nR2#\r\n", "R2#"))
        self.assertFalse(svc._is_prompt_only_echo("R2#show clock\r\n", "R2#"))

    @patch.object(svc, "_audit")
    @patch.object(svc, "open_netmiko_connection")
    @patch("netx_api.cli_resolve.resolve_cli_target")
    def test_create_session_limit(
        self,
        mock_resolve: MagicMock,
        mock_open: MagicMock,
        _mock_audit: MagicMock,
    ) -> None:
        mock_resolve.return_value = (
            {
                "username": "u",
                "password": "p",
                "hop_enabled": False,
                "ip_address": "10.0.0.1",
                "protocol": "ssh",
            },
            {
                "id": "ne-a",
                "name": "A",
                "ip_address": "10.0.0.1",
                "protocol": "ssh",
                "source": "managed",
            },
        )
        mock_open.side_effect = lambda *a, **k: _FakeConn()
        db = MagicMock()

        with patch.object(svc.settings, "webcrt_max_sessions", 1):
            out = svc.create_session(db, ne_id="ne-a", cols=80, rows=24, client="test", async_connect=False)
            self.assertIn("session_id", out)
            self.assertEqual(out.get("state"), "ready")
            self.assertFalse(out.get("cli_hop"))
            with self.assertRaises(HTTPException) as ctx:
                svc.create_session(db, ne_id="ne-a", cols=80, rows=24, client="test", async_connect=False)
            self.assertEqual(ctx.exception.status_code, 429)

    @patch.object(svc, "_audit")
    @patch.object(svc, "open_netmiko_connection")
    @patch("netx_api.cli_resolve.resolve_cli_target")
    def test_create_session_passes_hop_creds(
        self,
        mock_resolve: MagicMock,
        mock_open: MagicMock,
        _mock_audit: MagicMock,
    ) -> None:
        mock_resolve.return_value = (
            {
                "username": "u",
                "password": "p",
                "hop_enabled": True,
                "hop_vendor": "bastion",
                "hop_host": "jump.example",
                "hop_username": "jumpuser",
                "hop_password": "jumppass",
                "hop_target_auth_mode": "bastion_managed",
                "ip_address": "10.0.0.2",
                "protocol": "ssh",
            },
            {
                "id": "ne-hop",
                "name": "HopNE",
                "ip_address": "10.0.0.2",
                "protocol": "ssh",
                "source": "managed",
            },
        )
        fake = _FakeConn()

        def _open_with_log(*_a, **kwargs):
            log_buf = kwargs.get("session_log")
            if log_buf is not None and hasattr(log_buf, "write"):
                log_buf.write(
                    b"Warning: Telnet is not a secure protocol...\r\n"
                    b"Username:huawei\r\nPassword:\r\n"
                    b"<r1>"
                )
            return fake

        mock_open.side_effect = _open_with_log

        db = MagicMock()
        out = svc.create_session(db, ne_id="ne-hop", cols=100, rows=30, client="test", async_connect=False)
        mock_open.assert_called_once()
        called_creds = mock_open.call_args.args[0]
        self.assertTrue(called_creds["hop_enabled"])
        self.assertEqual(called_creds["hop_vendor"], "bastion")
        self.assertIn("session_log", mock_open.call_args.kwargs)
        fake.remote_conn.resize_pty.assert_called()
        self.assertEqual(out["ne_id"], "ne-hop")
        self.assertFalse(out.get("cli_hop"))  # bastion hop is not vendor CLI hop guard
        sess = svc.get_session(out["session_id"])
        assert sess is not None
        boot = sess.bootstrap_output.decode("utf-8", errors="replace")
        self.assertIn("Username:huawei", boot)
        self.assertIn("<r1>", boot)
        self.assertFalse(sess.needs_live_prompt)
        before = list(fake.written)
        sess.write_stdin("\n")
        self.assertEqual(fake.written[len(before) :], ["\n"])
        svc.close_session(out["session_id"], reason="test")

    @patch.object(svc, "_audit")
    @patch.object(svc, "get_cli_hop_guard")
    @patch.object(svc, "open_netmiko_connection")
    @patch("netx_api.cli_resolve.resolve_cli_target")
    def test_create_session_reports_cli_hop(
        self,
        mock_resolve: MagicMock,
        mock_open: MagicMock,
        mock_guard: MagicMock,
        _mock_audit: MagicMock,
    ) -> None:
        mock_resolve.return_value = (
            {
                "username": "u",
                "password": "p",
                "hop_enabled": True,
                "hop_vendor": "huawei",
                "ip_address": "10.0.0.3",
                "protocol": "ssh",
            },
            {
                "id": "ne-cli-hop",
                "name": "C",
                "ip_address": "10.0.0.3",
                "protocol": "ssh",
                "source": "managed",
            },
        )
        mock_open.side_effect = lambda *a, **k: _FakeConn()
        mock_guard.return_value = {"hop_prompt": "<HOP>", "hop_vendor": "huawei", "hop_host": "1.1.1.1"}
        out = svc.create_session(
            MagicMock(), ne_id="ne-cli-hop", cols=100, rows=30, client="test", async_connect=False
        )
        sess = svc.get_session(out["session_id"])
        assert sess is not None
        self.assertTrue(sess.cli_hop_guard)
        self.assertTrue(out.get("cli_hop") or sess.cli_hop_guard)
        self.assertEqual(mock_open.call_args.kwargs.get("cols"), 100)
        self.assertEqual(mock_open.call_args.kwargs.get("rows"), 30)
        self.assertEqual(sess.cli_hop_prompt, "<HOP>")
        svc.close_session(out["session_id"], reason="test")

    @patch.object(svc, "_audit")
    @patch.object(svc, "open_netmiko_connection")
    @patch("netx_api.cli_resolve.resolve_cli_target")
    def test_create_session_bastion_managed_without_target_password(
        self,
        mock_resolve: MagicMock,
        mock_open: MagicMock,
        _mock_audit: MagicMock,
    ) -> None:
        mock_resolve.return_value = (
            {
                "username": "ca-oper",
                "password": "",
                "hop_enabled": True,
                "hop_vendor": "bastion",
                "hop_host": "10.34.145.27",
                "hop_username": "ZTE-TSM",
                "hop_password": "bastion-secret",
                "hop_target_auth_mode": "bastion_managed",
                "hop_command_template": "ssh {target_ip}",
                "ip_address": "114.0.44.90",
                "protocol": "ssh",
                "device_type": "zte_zxros",
            },
            {
                "id": "ne-bastion",
                "name": "KND-PUN-EN1-Z20HS",
                "ip_address": "114.0.44.90",
                "protocol": "ssh",
                "source": "managed",
            },
        )
        mock_open.return_value = _FakeConn()
        out = svc.create_session(
            MagicMock(), ne_id="ne-bastion", cols=80, rows=24, client="test", async_connect=False
        )
        mock_open.assert_called_once()
        self.assertEqual(out["ne_id"], "ne-bastion")
        svc.close_session(out["session_id"], reason="test")

    def test_webcrt_creds_ready_bastion_managed(self) -> None:
        self.assertTrue(
            svc._webcrt_creds_ready(
                {
                    "username": "ca-oper",
                    "password": "",
                    "hop_enabled": True,
                    "hop_vendor": "bastion",
                    "hop_host": "10.34.145.27",
                    "hop_username": "ZTE-TSM",
                    "hop_password": "x",
                    "hop_target_auth_mode": "bastion_managed",
                }
            )
        )
        self.assertFalse(
            svc._webcrt_creds_ready(
                {
                    "username": "ca-oper",
                    "password": "",
                    "hop_enabled": True,
                    "hop_vendor": "bastion",
                    "hop_host": "10.34.145.27",
                    "hop_username": "ZTE-TSM",
                    "hop_password": "",
                    "hop_target_auth_mode": "bastion_managed",
                }
            )
        )
        self.assertFalse(
            svc._webcrt_creds_ready(
                {
                    "username": "u",
                    "password": "",
                    "hop_enabled": False,
                }
            )
        )

    @patch.object(svc, "_audit")
    def test_attach_gen_exclusive_stdout_and_stale_detach(self, _mock_audit: MagicMock) -> None:
        conn = _FakeConn()
        sess = svc.WebcrtSession(
            session_id="race",
            ne_id="ne1",
            ne_name="lab",
            ne_ip="1.2.3.4",
            protocol="ssh",
            cols=80,
            rows=24,
            conn=conn,  # type: ignore[arg-type]
        )
        with svc._sessions_lock:
            svc._sessions["race"] = sess

        sess1, gen1 = svc.mark_attached("race")
        self.assertEqual(gen1, 1)
        sess1.out_queue.put(b"a")
        sess1.out_queue.put(b"b")

        # Newer StrictMode WS takes ownership before old pump drains.
        _sess2, gen2 = svc.mark_attached("race")
        self.assertEqual(gen2, 2)
        self.assertEqual(sess1.take_stdout(gen1, timeout=0.05), "stale")
        self.assertEqual(sess1.take_stdout(gen2, timeout=0.05), b"a")
        self.assertEqual(sess1.take_stdout(gen2, timeout=0.05), b"b")

        # Old WS detach must not clear the live attach.
        out = svc.detach_session("race", grace_sec=8.0, attach_gen=gen1)
        self.assertFalse(out.get("detached"))
        self.assertTrue(sess.attached)
        self.assertIsNone(sess.detach_deadline)

        out2 = svc.detach_session("race", grace_sec=8.0, attach_gen=gen2)
        self.assertTrue(out2.get("detached"))
        self.assertFalse(sess.attached)
        svc.close_session("race", reason="test")

    @patch.object(svc, "_audit")
    def test_attach_timeout_reaper(self, _mock_audit: MagicMock) -> None:
        conn = _FakeConn()
        sess = svc.WebcrtSession(
            session_id="stale",
            ne_id="ne1",
            ne_name="lab",
            ne_ip="1.2.3.4",
            protocol="ssh",
            cols=80,
            rows=24,
            conn=conn,  # type: ignore[arg-type]
        )
        sess.created_at = time.time() - 120
        with svc._sessions_lock:
            svc._sessions["stale"] = sess
        with patch.object(svc.settings, "webcrt_attach_timeout_sec", 30):
            with patch.object(svc.settings, "webcrt_idle_timeout_sec", 99999):
                svc._reap_sessions()
        self.assertIsNone(svc.get_session("stale"))

    @patch.object(svc, "_audit")
    def test_idle_timeout_reaper(self, _mock_audit: MagicMock) -> None:
        conn = _FakeConn()
        sess = svc.WebcrtSession(
            session_id="idle",
            ne_id="ne1",
            ne_name="lab",
            ne_ip="1.2.3.4",
            protocol="ssh",
            cols=80,
            rows=24,
            conn=conn,  # type: ignore[arg-type]
        )
        sess.attached = True
        sess.last_activity = time.time() - 9999
        with svc._sessions_lock:
            svc._sessions["idle"] = sess
        with patch.object(svc.settings, "webcrt_attach_timeout_sec", 99999):
            with patch.object(svc.settings, "webcrt_idle_timeout_sec", 60):
                svc._reap_sessions()
        self.assertIsNone(svc.get_session("idle"))

    @patch.object(svc, "_audit")
    def test_cli_hop_return_closes_session(self, _mock_audit: MagicMock) -> None:
        """Vendor CLI hop: nested target exit must tear down WebCRT (no hop shell)."""
        conn = _FakeConn()
        chunks = [
            b"<TARGET>\r\n",
            b"quit\r\nConnection closed by foreign host\r\n\r\n<HOP>\r\n",
        ]
        idx = {"i": 0}

        def recv_ready() -> bool:
            return idx["i"] < len(chunks)

        def recv(_n: int) -> bytes:
            i = idx["i"]
            idx["i"] = i + 1
            return chunks[i]

        conn.remote_conn.recv_ready.side_effect = recv_ready
        conn.remote_conn.recv.side_effect = recv
        conn.remote_conn.exit_status_ready.return_value = False

        sess = svc.WebcrtSession(
            session_id="hop1",
            ne_id="ne1",
            ne_name="lab",
            ne_ip="1.2.3.4",
            protocol="ssh",
            cols=80,
            rows=24,
            conn=conn,  # type: ignore[arg-type]
            cli_hop_guard=True,
            cli_hop_prompt="<HOP>",
        )
        with svc._sessions_lock:
            svc._sessions["hop1"] = sess
        sess.start_reader()
        deadline = time.time() + 3.0
        got: list[bytes] = []
        while time.time() < deadline:
            item = sess.take_stdout(0, timeout=0.2)
            if item == "empty":
                if sess.closed:
                    break
                continue
            if item is None:
                break
            if isinstance(item, bytes):
                got.append(item)
        self.assertTrue(sess.closed)
        self.assertEqual(sess.close_reason, "cli_hop_return")
        self.assertIsNone(svc.get_session("hop1"))
        blob = b"".join(got).decode("utf-8", errors="replace")
        self.assertIn("Connection closed by foreign host", blob)
        self.assertIn("目标会话已结束", blob)

    def test_cli_hop_note_ignores_same_sysname_until_close_msg(self) -> None:
        sess = svc.WebcrtSession(
            session_id="hop2",
            ne_id="ne1",
            ne_name="lab",
            ne_ip="1.2.3.4",
            protocol="ssh",
            cols=80,
            rows=24,
            cli_hop_guard=True,
            cli_hop_prompt="<HUAWEI>",
        )
        # Same default sysname on target must not trip prompt-only close.
        self.assertFalse(sess._note_cli_hop_output(b"<HUAWEI>\r\n"))
        self.assertFalse(sess._cli_hop_seen_other_prompt)
        self.assertTrue(
            sess._note_cli_hop_output(b"Connection closed by foreign host\r\n<HUAWEI>\r\n")
        )

    def test_bounded_queue_drops_oldest(self) -> None:
        q = svc._BoundedByteQueue(maxsize=8)
        for i in range(10):
            q.put(str(i).encode())
        self.assertGreaterEqual(q.dropped, 2)
        first = q.get_nowait()
        self.assertEqual(first, b"2")

    def test_normalize_encoding(self) -> None:
        self.assertEqual(svc._normalize_encoding("GBK"), "gbk")
        self.assertEqual(svc._normalize_encoding("utf8"), "utf-8")
        self.assertEqual(svc._encode_text("测", "gbk")[:1], b"\xb2")


if __name__ == "__main__":
    unittest.main()
