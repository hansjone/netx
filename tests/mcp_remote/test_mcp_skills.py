"""MCP skills: resources / getNetxSkill / prompts / instructions / filtering."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from starlette.testclient import TestClient

from netx_api.auth_service import create_api_token, get_user_by_username
from netx_api.config import settings
from netx_api.db import SessionLocal
from netx_api.main import app
from netx_api.mcp_remote.registry import tools_for_request
from netx_api.mcp_remote.skills import (
    build_instructions,
    call_get_netx_skill,
    list_visible_skill_files,
    scan_skill_files,
)

_HDR = {
    "accept": "application/json, text/event-stream",
    "content-type": "application/json",
    "mcp-protocol-version": "2024-11-05",
}

ROOT = Path(__file__).resolve().parents[2]
PY = Path(os.environ.get("NETX_TEST_PYTHON") or sys.executable)


def _token(*, scopes: list[str] | None = None) -> str:
    db = SessionLocal()
    try:
        user = get_user_by_username(db, str(settings.bootstrap_admin_username or "admin"))
        assert user is not None
        _row, raw = create_api_token(
            db,
            user=user,
            name=f"mcp-skills-{uuid.uuid4().hex[:12]}",
            scopes=scopes,
            enforce_limit=False,
        )
        db.commit()
        return raw
    finally:
        db.close()


def _mcp_post(client: TestClient, path: str, payload: dict, headers: dict) -> dict:
    r = client.post(path, json=payload, headers=headers)
    assert r.status_code == 200, r.text[:400]
    body = r.json()
    assert "error" not in body, body
    return body["result"]


def test_scan_excludes_biz_state():
    files = scan_skill_files()
    uris = [f.uri for f in files]
    assert any(u.endswith("/netx-ops/SKILL.md") for u in uris)
    assert any("/biz-monitor/" in u for u in uris)
    assert all("biz-state" not in u for u in uris)


def test_resources_list_and_read_via_http():
    with (
        patch("netx_api.mcp_remote.mount.settings.auth_enabled", True),
        patch("netx_api.auth_middleware.settings.auth_enabled", True),
        TestClient(app) as client,
    ):
        raw = _token(scopes=None)
        headers = {**_HDR, "authorization": f"Bearer {raw}"}
        _mcp_post(
            client,
            "/mcp",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "skills", "version": "0"},
                },
            },
            headers,
        )
        listed = _mcp_post(
            client,
            "/mcp",
            {"jsonrpc": "2.0", "id": 2, "method": "resources/list", "params": {}},
            headers,
        )
        resources = listed["resources"]
        uris = {r["uri"] for r in resources}
        assert "netx://skills/ops/netx-ops/SKILL.md" in uris
        assert "netx://skills/ops/netx-ops/reference.md" in uris
        assert "netx://skills/topology/netx-topology/SKILL.md" in uris
        assert "netx://skills/biz-monitor/netx-biz-monitor/SKILL.md" in uris
        assert all("biz-state" not in u for u in uris)
        entry = next(r for r in resources if r["uri"].endswith("/netx-ops/SKILL.md"))
        assert entry["name"] == "SKILL.md"
        assert entry["mimeType"] == "text/markdown"
        assert entry["description"]
        assert entry["_meta"]["netx"] == {
            "group": "ops",
            "skill": "netx-ops",
            "entry": True,
        }
        ref = next(r for r in resources if r["uri"].endswith("/reference.md"))
        assert "description" not in ref or not ref.get("description")
        assert ref["_meta"]["netx"]["entry"] is False

        read = _mcp_post(
            client,
            "/mcp",
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "resources/read",
                "params": {"uri": "netx://skills/ops/netx-ops/SKILL.md"},
            },
            headers,
        )
        text = read["contents"][0]["text"]
        assert text.startswith("---")
        assert "\r" not in text
        assert "netx-ops" in text


def test_instructions_contain_skill_catalog():
    with (
        patch("netx_api.mcp_remote.mount.settings.auth_enabled", True),
        patch("netx_api.auth_middleware.settings.auth_enabled", True),
        TestClient(app) as client,
    ):
        raw = _token(scopes=None)
        headers = {**_HDR, "authorization": f"Bearer {raw}"}
        init = _mcp_post(
            client,
            "/mcp",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "skills", "version": "0"},
                },
            },
            headers,
        )
        instr = init["instructions"]
        assert "netx-ops（ops）" in instr
        assert "getNetxSkill" in instr
        assert "netx://skills/" in instr
        assert len(instr) <= 1500
        caps = init["capabilities"]
        assert "resources" in caps
        assert caps["resources"].get("subscribe") in (None, False)
        assert "prompts" in caps


def test_scope_filters_biz_skill():
    files = list_visible_skill_files(frozenset({"ne:read", "alarms:read"}), frozenset())
    groups = {f.group for f in files}
    assert "ops" in groups
    assert "bizMonitor" not in groups
    assert all("biz-monitor" not in f.uri for f in files)


def test_groups_query_filters_skills():
    with (
        patch("netx_api.mcp_remote.mount.settings.auth_enabled", True),
        patch("netx_api.auth_middleware.settings.auth_enabled", True),
        TestClient(app) as client,
    ):
        raw = _token(scopes=None)
        headers = {**_HDR, "authorization": f"Bearer {raw}"}
        _mcp_post(
            client,
            "/mcp?groups=ops",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "skills", "version": "0"},
                },
            },
            headers,
        )
        listed = _mcp_post(
            client,
            "/mcp?groups=ops",
            {"jsonrpc": "2.0", "id": 2, "method": "resources/list", "params": {}},
            headers,
        )
        uris = {r["uri"] for r in listed["resources"]}
        assert uris
        assert all("/ops/" in u for u in uris)
        assert all("/topology/" not in u for u in uris)
        assert all("/biz-monitor/" not in u for u in uris)


def test_unauthorized_or_missing_uri_errors():
    with (
        patch("netx_api.mcp_remote.mount.settings.auth_enabled", True),
        patch("netx_api.auth_middleware.settings.auth_enabled", True),
        TestClient(app) as client,
    ):
        raw = _token(scopes=["ne:read", "alarms:read"])
        headers = {**_HDR, "authorization": f"Bearer {raw}"}
        _mcp_post(
            client,
            "/mcp",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "skills", "version": "0"},
                },
            },
            headers,
        )
        for uri in (
            "netx://skills/biz-monitor/netx-biz-monitor/SKILL.md",
            "netx://skills/ops/netx-ops/../../biz-state/netx-biz-state/SKILL.md",
            "netx://skills/nope/x/SKILL.md",
        ):
            r = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 9,
                    "method": "resources/read",
                    "params": {"uri": uri},
                },
                headers=headers,
            )
            assert r.status_code == 200
            err = r.json().get("error") or {}
            assert err, r.text[:300]
            msg = str(err.get("message") or "")
            assert "not found" in msg.lower() or "not authorized" in msg.lower()
            # Must not leak filesystem paths
            assert "D:\\" not in msg
            assert "/skills/" not in msg or "netx://skills/" in msg


def test_get_netx_skill_tool():
    out = call_get_netx_skill({"name": "netx-ops"}, None, frozenset())
    assert out.get("isError") is False
    text = out["content"][0]["text"]
    assert text.startswith("---")
    assert "其他文件" in text
    assert "reference.md" in text

    ref = call_get_netx_skill(
        {"name": "netx-ops", "file": "reference.md"}, None, frozenset()
    )
    assert ref.get("isError") is False
    assert "速查" in ref["content"][0]["text"]

    denied = call_get_netx_skill(
        {"name": "netx-biz-monitor"},
        frozenset({"ne:read"}),
        frozenset(),
    )
    assert denied.get("isError") is True
    assert "可用 skill" in denied["content"][0]["text"]
    assert "netx-ops" in denied["content"][0]["text"] or "（无）" in denied["content"][0]["text"]


def test_get_netx_skill_hidden_when_no_skills_visible():
    tools = tools_for_request(frozenset({"ne:read"}), frozenset({"bizMonitor"}))
    assert tools == []
    assert "getNetxSkill" not in {t["name"] for t in tools}


def test_prompts_list_and_get():
    with (
        patch("netx_api.mcp_remote.mount.settings.auth_enabled", True),
        patch("netx_api.auth_middleware.settings.auth_enabled", True),
        TestClient(app) as client,
    ):
        raw = _token(scopes=None)
        headers = {**_HDR, "authorization": f"Bearer {raw}"}
        _mcp_post(
            client,
            "/mcp",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "skills", "version": "0"},
                },
            },
            headers,
        )
        listed = _mcp_post(
            client,
            "/mcp",
            {"jsonrpc": "2.0", "id": 2, "method": "prompts/list", "params": {}},
            headers,
        )
        names = {p["name"] for p in listed["prompts"]}
        assert names == {"netx-ops", "netx-topology", "netx-biz-monitor"}
        got = _mcp_post(
            client,
            "/mcp",
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "prompts/get",
                "params": {"name": "netx-ops"},
            },
            headers,
        )
        msg = got["messages"][0]
        assert msg["role"] == "user"
        assert "其他文件用 getNetxSkill 读取" in msg["content"]["text"]


def test_build_instructions_dynamic():
    full = build_instructions(None, frozenset())
    assert "netx-ops（ops）" in full
    ops_only = build_instructions(None, frozenset({"ops"}))
    assert "netx-ops（ops）" in ops_only
    assert "netx-topology" not in ops_only
    assert len(full) <= 1500


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


@pytest.fixture(scope="module")
def live_skills_port():
    port = _free_port()
    tmp = Path(tempfile.mkdtemp(prefix="netx-mcp-skills-tcp-"))
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


def test_mcp_tcp_resources_list(live_skills_port: int):
    """Real TCP: Sangfor LSP rewrites /mcp; must verify resources/list on the wire."""
    init_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "skills-tcp", "version": "0"},
        },
    }
    r = httpx.post(
        f"http://127.0.0.1:{live_skills_port}/mcp",
        json=init_body,
        headers={
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2024-11-05",
        },
        timeout=30.0,
        trust_env=False,
    )
    assert r.status_code == 200, r.text[:300]
    assert "resources" in r.json()["result"]["capabilities"]
    assert "Skills:" in (r.json()["result"].get("instructions") or "")

    listed = httpx.post(
        f"http://127.0.0.1:{live_skills_port}/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "resources/list", "params": {}},
        headers={
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2024-11-05",
        },
        timeout=30.0,
        trust_env=False,
    )
    assert listed.status_code == 200, listed.text[:300]
    data = listed.json()
    assert "error" not in data, data
    uris = {x["uri"] for x in data["result"]["resources"]}
    assert "netx://skills/ops/netx-ops/SKILL.md" in uris
    assert all("biz-state" not in u for u in uris)
