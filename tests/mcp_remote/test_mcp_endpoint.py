"""ASGI tests for built-in /mcp Streamable HTTP endpoint.

Uses a single TestClient lifespan: MCP session_manager.run() cannot restart on the
same instance after the event loop is closed, and remount races are flaky across
large suites.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

from starlette.testclient import TestClient

from netx_api.auth_service import create_api_token, get_user_by_username
from netx_api.config import settings
from netx_api.db import SessionLocal
from netx_api.main import app

_HDR = {
    "accept": "application/json, text/event-stream",
    "content-type": "application/json",
    "mcp-protocol-version": "2024-11-05",
}


def _token(*, scopes: list[str] | None = None) -> str:
    db = SessionLocal()
    try:
        user = get_user_by_username(db, str(settings.bootstrap_admin_username or "admin"))
        assert user is not None
        _row, raw = create_api_token(
            db,
            user=user,
            name=f"mcp-asgi-{uuid.uuid4().hex[:12]}",
            scopes=scopes,
            enforce_limit=False,
        )
        db.commit()
        return raw
    finally:
        db.close()


def test_mcp_http_auth_list_groups_and_rbac():
    with (
        patch("netx_api.mcp_remote.mount.settings.auth_enabled", True),
        patch("netx_api.auth_middleware.settings.auth_enabled", True),
        TestClient(app) as client,
    ):
        denied = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"},
                },
            },
            headers=_HDR,
        )
        assert denied.status_code == 401

        raw = _token(scopes=None)
        headers = {**_HDR, "authorization": f"Bearer {raw}"}
        init = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"},
                },
            },
            headers=headers,
        )
        assert init.status_code == 200
        info = init.json()["result"]["serverInfo"]
        assert info["name"] == "netx"
        assert info["version"]

        listed = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers=headers,
        )
        assert listed.status_code == 200
        tools = listed.json()["result"]["tools"]
        assert len(tools) == 39
        names = {t["name"] for t in tools}
        assert "queryNmsAlarms" in names
        assert "getTopologyTree" in names
        assert "listBizMonitors" in names
        assert all("_meta" in t and t["_meta"]["netx"]["group"] for t in tools)

        ops = client.post(
            "/mcp?groups=ops",
            json={"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
            headers=headers,
        )
        assert ops.status_code == 200
        ops_tools = ops.json()["result"]["tools"]
        assert len(ops_tools) == 15
        assert all(t["_meta"]["netx"]["group"] == "ops" for t in ops_tools)

        raw_ro = _token(scopes=["ne:read", "alarms:read"])
        headers_ro = {**_HDR, "authorization": f"Bearer {raw_ro}"}
        listed_ro = client.post(
            "/mcp?groups=ops",
            json={"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {}},
            headers=headers_ro,
        )
        assert listed_ro.status_code == 200
        names_ro = {t["name"] for t in listed_ro.json()["result"]["tools"]}
        assert "listManagedNe" in names_ro
        assert "execManagedNe" not in names_ro
        assert "queryNmsAlarms" in names_ro
