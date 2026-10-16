"""Real-TCP regression: /mcp must not emit TE:chunked with an unframed body."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
PY = Path(os.environ.get("NETX_TEST_PYTHON") or sys.executable)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def _wait_health(port: int, timeout: float = 45.0) -> None:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/health"
    last = ""
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=1.0, trust_env=False)
            if r.status_code == 200:
                return
            last = f"status={r.status_code}"
        except Exception as exc:
            last = str(exc)
        time.sleep(0.2)
    raise TimeoutError(f"health not ready on {port}: {last}")


def _raw_post(port: int, path: str, body: bytes, headers: dict[str, str] | None = None) -> bytes:
    hdrs = {
        "Host": f"127.0.0.1:{port}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Connection": "close",
        "Content-Length": str(len(body)),
    }
    if headers:
        hdrs.update(headers)
    lines = [f"POST {path} HTTP/1.1"] + [f"{k}: {v}" for k, v in hdrs.items()]
    req = ("\r\n".join(lines) + "\r\n\r\n").encode() + body
    s = socket.create_connection(("127.0.0.1", port), 10)
    s.sendall(req)
    s.settimeout(10)
    data = b""
    try:
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    except TimeoutError:
        pass
    finally:
        s.close()
    return data


def _assert_framed(raw: bytes) -> None:
    head, _, rest = raw.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 "), head[:80]
    hl = head.lower()
    has_cl = b"content-length:" in hl
    has_te = b"transfer-encoding: chunked" in hl
    assert has_cl or has_te, f"missing framing headers: {head!r}"
    if has_te and not has_cl:
        first = rest.split(b"\r\n", 1)[0]
        try:
            int(first, 16)
        except ValueError as exc:
            raise AssertionError(
                f"TE:chunked but body is not chunk-framed (first line={first!r})"
            ) from exc
        assert b"\r\n0\r\n" in rest or rest.rstrip().endswith(b"0"), rest[-40:]


@pytest.fixture(scope="module")
def live_mcp_port():
    port = _free_port()
    tmp = Path(tempfile.mkdtemp(prefix="netx-mcp-tcp-"))
    env = os.environ.copy()
    env.update(
        {
            "NETX_DATABASE_URL": f"sqlite:///{(tmp / 'netx.sqlite').as_posix()}",
            "NETX_AUTH_SECRET_FILE": str(tmp / "jwt_secret"),
            "NETX_AUTH_MCP_TOKEN_FILE": str(tmp / "mcp_token"),
            "NETX_CREDENTIAL_SECRET_FILE": str(tmp / "cred_secret"),
            "NETX_NE_COLLECTION_DATA_DIR": str(tmp / "ne_collections"),
            "NETX_BIZ_STATE_SPOOL_DIR": str(tmp / "biz_spool"),
            "NETX_NE_EXEC_JOB_DIR": str(tmp / "ne_exec_jobs"),
            "NETX_WEBCRT_DATA_DIR": str(tmp / "webcrt"),
            "NETX_SCHEDULER_HEARTBEAT_PATH": str(tmp / "scheduler_heartbeat.json"),
            "NETX_DOCS_ENABLED": "false",
            "NETX_AUTH_ENABLED": "false",
            "NETX_ALLOW_INSECURE_DEFAULTS": "true",
            "NETX_RUN_INLINE_SCHEDULERS": "false",
            "PYTHONUTF8": "1",
            # Avoid Windows system proxy (httpx trust_env) returning 502 on loopback.
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }
    )
    proc = subprocess.Popen(
        [
            str(PY),
            "-m",
            "uvicorn",
            "netx_api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--http",
            "h11",
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_health(port)
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def test_mcp_tcp_initialize_framing(live_mcp_port: int):
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "tcp-framing", "version": "0"},
            },
        }
    ).encode()
    raw = _raw_post(live_mcp_port, "/mcp", body)
    _assert_framed(raw)
    assert b'"serverInfo"' in raw or b"serverInfo" in raw or b"server_info" in raw


def test_mcp_tcp_httpx_initialize(live_mcp_port: int):
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "httpx-framing", "version": "0"},
        },
    }
    r = httpx.post(
        f"http://127.0.0.1:{live_mcp_port}/mcp",
        json=body,
        headers={
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2024-11-05",
        },
        timeout=30.0,
        trust_env=False,
    )
    assert r.status_code == 200, (r.status_code, r.text[:300])
    data = r.json()
    assert data["result"]["serverInfo"]["name"] == "netx"
