#!/usr/bin/env python3
"""Local fake network-device SSH server for WebCRT pipeline benchmarks.

Zero device latency, so a benchmark against it measures netx overhead only.

    python scripts/fake_ssh_device.py [--port 2222] [--user lab] [--password lab]

Behaviour: Cisco-like ``test#`` prompt, server-side echo with Tab completion
(``sh<Tab>`` -> ``show ``), history on Up arrow, ``show clock`` (short output),
``bulk <MB>`` (large output) and ``quit``.
"""

from __future__ import annotations

import argparse
import socket
import threading
import time

import paramiko

HOST_KEY = paramiko.RSAKey.generate(2048)
PROMPT = b"test#"


class _Server(paramiko.ServerInterface):
    def __init__(self, user: str, password: str) -> None:
        self.user, self.password = user, password
        self.event = threading.Event()

    def check_auth_password(self, username, password):  # noqa: ANN001
        ok = username == self.user and password == self.password
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):  # noqa: ANN001
        return "password"

    def check_channel_request(self, kind, chanid):  # noqa: ANN001
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_pty_request(self, *a):  # noqa: ANN002
        return True

    def check_channel_shell_request(self, channel):  # noqa: ANN001
        self.event.set()
        return True

    def check_channel_window_change_request(self, *a):  # noqa: ANN002
        return True


def _shell(chan: paramiko.Channel) -> None:
    chan.send(b"\r\nFake device banner\r\n" + PROMPT)
    line = b""
    history: list[bytes] = []
    hidx = 0
    try:
        while True:
            data = chan.recv(1024)
            if not data:
                return
            i = 0
            while i < len(data):
                b = data[i : i + 1]
                if data[i : i + 3] == b"\x1b[A":  # Up
                    if history:
                        hidx = max(0, hidx - 1) if hidx else len(history) - 1
                        chan.send(b"\x08" * len(line) + b" " * len(line) + b"\x08" * len(line))
                        line = history[hidx]
                        chan.send(line)
                    i += 3
                    continue
                if b == b"\t":
                    if line.startswith(b"sh") and b" " not in line:
                        chan.send(b"ow "[len(line) - 2 :] if len(line) <= 4 else b"")
                        line = b"show "
                    i += 1
                    continue
                if b in (b"\r", b"\n"):
                    chan.send(b"\r\n")
                    cmd = line.strip()
                    line = b""
                    if cmd:
                        history.append(cmd)
                        hidx = len(history)
                    if cmd == b"quit":
                        chan.close()
                        return
                    if cmd == b"show clock":
                        chan.send(b"2026-10-12 12:00:00\r\nMonday\r\n")
                    elif cmd.startswith(b"bulk"):
                        mb = float(cmd.split()[1]) if len(cmd.split()) > 1 else 1.0
                        row = b"Ethernet1/0/%d                     unassigned           up         down     --\r\n"
                        sent = 0
                        n = 0
                        buf = []
                        while sent < mb * 1024 * 1024:
                            r = row % (n % 999)
                            buf.append(r)
                            sent += len(r)
                            n += 1
                            if len(buf) >= 400:
                                chan.sendall(b"".join(buf))
                                buf = []
                        if buf:
                            chan.sendall(b"".join(buf))
                    elif cmd:
                        chan.send(b"%% Unknown command\r\n")
                    chan.send(PROMPT)
                    i += 1
                    continue
                if b in (b"\x08", b"\x7f"):
                    if line:
                        line = line[:-1]
                        chan.send(b"\x08 \x08")
                    i += 1
                    continue
                line += b
                chan.send(b)
                i += 1
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            chan.close()
        except Exception:  # noqa: BLE001
            pass


def _serve(sock: socket.socket, user: str, password: str) -> None:
    t = paramiko.Transport(sock)
    t.add_server_key(HOST_KEY)
    srv = _Server(user, password)
    try:
        t.start_server(server=srv)
        chan = t.accept(30)
        if chan is None:
            return
        srv.event.wait(10)
        _shell(chan)
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            t.close()
        except Exception:  # noqa: BLE001
            pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=2222)
    ap.add_argument("--user", default="lab")
    ap.add_argument("--password", default="lab")
    a = ap.parse_args()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", a.port))
    s.listen(20)
    print(f"fake device on 127.0.0.1:{a.port} user={a.user}", flush=True)
    while True:
        c, _ = s.accept()
        threading.Thread(target=_serve, args=(c, a.user, a.password), daemon=True).start()


if __name__ == "__main__":
    main()
