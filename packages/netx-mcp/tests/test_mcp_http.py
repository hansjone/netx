"""Tests for netx HTTP MCP server."""

from __future__ import annotations

import json
import subprocess
import sys
from unittest.mock import patch

import pytest

from netx_mcp.http_tools import HTTP_MCP_TOOLS, call_http_tool, tools_for_scopes
from netx_mcp.server import _fetch_scopes


def test_http_mcp_tool_list_has_expected_tools() -> None:
    names = [str(t.get("name") or "") for t in HTTP_MCP_TOOLS]
    assert len(names) == 13
    assert "queryUmeAlarms" in names
    assert "queryUmeAlarmsRaw" in names
    assert "execManagedNe" in names
    assert "listCliTargets" in names
    assert "queryTopologyEdges" not in names
    exec_tool = next(t for t in HTTP_MCP_TOOLS if t.get("name") == "execManagedNe")
    assert exec_tool["inputSchema"]["properties"]["commands"]["maxItems"] >= 5


def test_call_query_ume_alarms_forwards_http() -> None:
    with patch("netx_mcp.http_tools.http_json") as mock_http:
        mock_http.return_value = {"ok": True, "data": {"total": 0, "items": []}}
        out = call_http_tool("queryUmeAlarms", {"severity": "critical", "page": 1, "page_size": 10})
        mock_http.assert_called_once()
        assert mock_http.call_args[0][0] == "GET"
        assert mock_http.call_args[0][1] == "/v1/ume/alarms"
        params = mock_http.call_args[1]["params"]
        assert params["severity"] == "critical"
        text = out["content"][0]["text"]
        payload = json.loads(text)
        assert payload["ok"] is True


def test_call_get_ume_ne_requires_id() -> None:
    out = call_http_tool("getUmeNe", {})
    assert out.get("isError") is True
    text = out["content"][0]["text"]
    payload = json.loads(text)
    assert payload["error"] == "ne_id_required"


def test_call_exec_managed_ne_posts_body() -> None:
    with patch("netx_mcp.http_tools.http_post_json") as mock_post:
        mock_post.return_value = {"ok": True, "data": {"ok": True, "output": "ok"}}
        out = call_http_tool(
            "execManagedNe",
            {"ne_id": "abc", "commands": ["show version"]},
        )
        mock_post.assert_called_once()
        assert mock_post.call_args[0][0] == "/v1/managed-ne/exec"
        body = mock_post.call_args[0][1]
        assert body["ne_id"] == "abc"
        assert body["commands"] == ["show version"]
        text = out["content"][0]["text"]
        payload = json.loads(text)
        assert payload["ok"] is True


def test_call_exec_managed_ne_respects_max_commands_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETX_NE_EXEC_MAX_COMMANDS", "10")
    cmds = [f"show version {i}" for i in range(6)]
    with patch("netx_mcp.http_tools.http_post_json") as mock_post:
        mock_post.return_value = {"ok": True, "data": {"ok": True, "output": "ok"}}
        out = call_http_tool("execManagedNe", {"ne_id": "abc", "commands": cmds})
        mock_post.assert_called_once()
        text = out["content"][0]["text"]
        payload = json.loads(text)
        assert payload["ok"] is True

    monkeypatch.setenv("NETX_NE_EXEC_MAX_COMMANDS", "5")
    out = call_http_tool("execManagedNe", {"ne_id": "abc", "commands": cmds})
    text = out["content"][0]["text"]
    payload = json.loads(text)
    assert payload.get("ok") is False
    assert payload.get("error_code") == "too_many_commands"


def test_fetch_scopes_unwraps_http_json_envelope() -> None:
    with patch("netx_mcp.server.http_json") as mock_http:
        mock_http.return_value = {
            "ok": True,
            "data": {"scopes": ["ne:read", "alarms:read"], "user": {"username": "mcp"}},
        }
        assert _fetch_scopes() == ["ne:read", "alarms:read"]


def test_fetch_scopes_returns_none_on_http_failure() -> None:
    with patch("netx_mcp.server.http_json") as mock_http:
        mock_http.return_value = {"ok": False, "error": "netx_http_401"}
        assert _fetch_scopes() is None


def test_tools_for_scopes_filters_by_granted() -> None:
    names = {str(t.get("name") or "") for t in tools_for_scopes(["ne:read"])}
    assert "listManagedNe" in names
    assert "queryUmeAlarms" not in names
    assert tools_for_scopes(None) == list(HTTP_MCP_TOOLS)


def test_stdio_initialize_and_tools_list() -> None:
    import os

    env = os.environ.copy()
    env["NETX_API_URL"] = "http://127.0.0.1:1"
    env.pop("NETX_API_TOKEN", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "netx_mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    assert proc.stdin and proc.stdout
    init_req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
    proc.stdin.write(init_req)
    proc.stdin.flush()
    init_line = proc.stdout.readline()
    init_resp = json.loads(init_line)
    assert init_resp["result"]["serverInfo"]["mode"] == "http"

    list_req = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + "\n"
    proc.stdin.write(list_req)
    proc.stdin.flush()
    list_line = proc.stdout.readline()
    list_resp = json.loads(list_line)
    assert "error" not in list_resp, list_resp
    tools = list_resp["result"]["tools"]
    assert len(tools) == 13

    proc.terminate()
    proc.wait(timeout=5)
