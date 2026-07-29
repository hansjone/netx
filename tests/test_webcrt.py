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
        self.remote_conn = MagicMock(spec=["recv_ready", "recv", "exit_status_ready", "resize_pty"])
        self.remote_conn.recv_ready.return_value = False
        self.remote_conn.exit_status_ready.return_value = False
        self.remote_conn.resize_pty = MagicMock()

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
        # xterm DEL / arrows -> network CLI controls
        sess.write_stdin("\x7f")
        self.assertEqual(conn.written[-1], "\x08")
        sess.write_stdin("\x1b[D\x1b[C\x1b[A\x1b[B")
        self.assertEqual(conn.written[-1], "\x02\x06\x10\x0e")
        sess.resize(120, 40)
        conn.remote_conn.resize_pty.assert_called_with(width=120, height=40)
        sess.close("test")
        self.assertTrue(sess.closed)

    def test_map_network_cli_keys_helpers(self) -> None:
        self.assertEqual(svc.map_network_cli_keys("\x7fab"), "\x08ab")
        self.assertEqual(svc.map_network_cli_keys("\x1b[D"), "\x02")
        self.assertTrue(svc.uses_network_cli_keymap("huawei", "Huawei"))
        self.assertFalse(svc.uses_network_cli_keymap("linux", "bastion"))

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
            out = svc.create_session(db, ne_id="ne-a", cols=80, rows=24, client="test")
            self.assertIn("session_id", out)
            with self.assertRaises(HTTPException) as ctx:
                svc.create_session(db, ne_id="ne-a", cols=80, rows=24, client="test")
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
        mock_open.return_value = fake

        db = MagicMock()
        out = svc.create_session(db, ne_id="ne-hop", cols=100, rows=30, client="test")
        mock_open.assert_called_once()
        called_creds = mock_open.call_args.args[0]
        self.assertTrue(called_creds["hop_enabled"])
        self.assertEqual(called_creds["hop_vendor"], "bastion")
        fake.remote_conn.resize_pty.assert_called()
        self.assertEqual(out["ne_id"], "ne-hop")
        sess = svc.get_session(out["session_id"])
        assert sess is not None
        # create_session may nudge Enter when no prompt was drained.
        before = list(fake.written)
        sess.write_stdin("\n")
        self.assertEqual(fake.written[len(before) :], ["\n"])
        self.assertTrue(isinstance(sess.bootstrap_output, (bytes, bytearray)))
        svc.close_session(out["session_id"], reason="test")

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


if __name__ == "__main__":
    unittest.main()
