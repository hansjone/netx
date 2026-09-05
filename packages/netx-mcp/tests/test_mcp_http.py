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
    assert len(names) == 14
    assert "queryNmsAlarms" in names
    assert "queryNmsAlarmsRaw" in names
    assert "execManagedNe" in names
    assert "listCliTargets" in names
    assert "findTopologyPaths" in names
    assert "queryUmeAlarms" not in names
    assert "queryTopologyEdges" not in names
    exec_tool = next(t for t in HTTP_MCP_TOOLS if t.get("name") == "execManagedNe")
    assert exec_tool["inputSchema"]["properties"]["commands"]["maxItems"] >= 5
    assert "nms_ne_id" in exec_tool["inputSchema"]["properties"]


def test_call_query_nms_alarms_forwards_http() -> None:
    with patch("netx_mcp.http_tools.http_json") as mock_http:
        mock_http.return_value = {"ok": True, "data": {"total": 0, "items": []}}
        out = call_http_tool("queryNmsAlarms", {"severity": "critical", "page": 1, "page_size": 10})
        mock_http.assert_called_once()
        assert mock_http.call_args[0][0] == "GET"
        assert mock_http.call_args[0][1] == "/v1/ume/alarms"
        params = mock_http.call_args[1]["params"]
        assert params["severity"] == "critical"
        text = out["content"][0]["text"]
        payload = json.loads(text)
        assert payload["ok"] is True


def test_call_aggregate_nms_alarms_forwards_top_ne() -> None:
    with patch("netx_mcp.http_tools.http_json") as mock_http:
        mock_http.return_value = {
            "ok": True,
            "data": {"total": 10, "by_severity": [], "by_ne": [], "by_ne_total": 3, "top_ne": 20},
        }
        out = call_http_tool("aggregateNmsAlarms", {"top_ne": 20, "severity": "critical"})
        mock_http.assert_called_once_with(
            "GET",
            "/v1/ume/alarms/aggregate",
            params={"top_ne": 20, "severity": "critical"},
        )
        payload = json.loads(out["content"][0]["text"])
        assert payload["ok"] is True
        assert payload["data"]["top_ne"] == 20


def test_call_aggregate_nms_alarms_group_by_routes_to_raw() -> None:
    with patch("netx_mcp.http_tools.http_json") as mock_http:
        mock_http.return_value = {"ok": True, "data": {"buckets": []}}
        out = call_http_tool(
            "aggregateNmsAlarms",
            {"group_by": "alarm_host_name", "severity": "critical", "limit": 20},
        )
        mock_http.assert_called_once()
        assert mock_http.call_args[0][0] == "GET"
        assert mock_http.call_args[0][1] == "/v1/ume/alarms/aggregate/raw"
        params = mock_http.call_args[1]["params"]
        assert params["group_by"] == "alarm_host_name"
        assert params["severity"] == "critical"
        assert params["limit"] == "20"
        payload = json.loads(out["content"][0]["text"])
        assert payload["ok"] is True


def test_aggregate_nms_alarms_schema_accepts_group_by() -> None:
    tool = next(t for t in HTTP_MCP_TOOLS if t.get("name") == "aggregateNmsAlarms")
    props = tool["inputSchema"]["properties"]
    assert "group_by" in props
    assert "group_by2" in props
    assert "alarm_host_name" in props["group_by"]["enum"]


def test_call_find_topology_paths_defaults_summary_detail() -> None:
    with patch("netx_mcp.http_tools.http_post_json") as mock_post:
        mock_post.return_value = {"ok": True, "data": {"path_count": 1, "detail": "summary", "paths": []}}
        out = call_http_tool(
            "findTopologyPaths",
            {"from_nms_ne_id": "a", "to_nms_ne_id": "b"},
        )
        mock_post.assert_called_once()
        body = mock_post.call_args[0][1]
        assert body["detail"] == "summary"
        assert body["from_ume_ne_id"] == "a"
        assert body["to_ume_ne_id"] == "b"
        payload = json.loads(out["content"][0]["text"])
        assert payload["ok"] is True


def test_call_find_topology_paths_accepts_legacy_ume_params() -> None:
    with patch("netx_mcp.http_tools.http_post_json") as mock_post:
        mock_post.return_value = {"ok": True, "data": {"path_count": 0, "paths": []}}
        call_http_tool(
            "findTopologyPaths",
            {"from_ume_ne_id": "a", "to_ume_ne_id": "b"},
        )
        body = mock_post.call_args[0][1]
        assert body["from_ume_ne_id"] == "a"
        assert body["to_ume_ne_id"] == "b"


def test_call_exec_managed_ne_defaults_read_timeout() -> None:
    with patch("netx_mcp.http_tools.http_post_json") as mock_post:
        mock_post.return_value = {"ok": True, "data": {"ok": True, "output": "hi"}}
        out = call_http_tool(
            "execManagedNe",
            {"nms_ne_id": "u1", "commands": ["show version"]},
        )
        mock_post.assert_called_once()
        body = mock_post.call_args[0][1]
        assert body["read_timeout_sec"] == 60
        assert body["ume_ne_id"] == "u1"
        payload = json.loads(out["content"][0]["text"])
        assert payload["ok"] is True


def test_get_managed_ne_requires_id_with_hint() -> None:
    out = call_http_tool("getManagedNe", {})
    assert out.get("isError") is True
    payload = json.loads(out["content"][0]["text"])
    assert payload["error"] == "ne_id_required"
    assert "hint" in payload


def test_get_managed_ne_accepts_managed_ne_id_alias() -> None:
    with patch("netx_mcp.http_tools.http_json") as mock_http:
        mock_http.return_value = {"ok": True, "data": {"ne_id": "m1"}}
        out = call_http_tool("getManagedNe", {"managed_ne_id": "m1"})
        mock_http.assert_called_once()
        assert mock_http.call_args[0][1].endswith("/m1")
        payload = json.loads(out["content"][0]["text"])
        assert payload["ok"] is True


def test_call_get_nms_ne_requires_id() -> None:
    out = call_http_tool("getNmsNe", {})
    assert out.get("isError") is True
    payload = json.loads(out["content"][0]["text"])
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


def test_call_exec_managed_ne_batch_ne_ids() -> None:
    with patch("netx_mcp.http_tools.http_post_json") as mock_post:
        mock_post.return_value = {
            "ok": True,
            "data": {
                "ok": True,
                "summary": {"total": 2, "ok": 2, "failed": 0},
                "results": [{"ok": True}, {"ok": True}],
            },
        }
        out = call_http_tool(
            "execManagedNe",
            {"ne_ids": ["a", "b"], "commands": ["show version"], "concurrency": 2},
        )
        mock_post.assert_called_once()
        assert mock_post.call_args[0][0] == "/v1/managed-ne/exec-batch"
        body = mock_post.call_args[0][1]
        assert body["ne_ids"] == ["a", "b"]
        assert body["commands"] == ["show version"]
        assert body["concurrency"] == 2
        payload = json.loads(out["content"][0]["text"])
        assert payload["ok"] is True
        assert payload["data"]["summary"]["total"] == 2


def test_exec_managed_ne_schema_documents_batch() -> None:
    tool = next(t for t in HTTP_MCP_TOOLS if t.get("name") == "execManagedNe")
    props = tool["inputSchema"]["properties"]
    assert "ne_ids" in props
    assert "nms_ne_ids" in props
    assert "ume_ne_ids" in props
    assert "targets" in props
    assert "concurrency" in props
    assert "Many NEs" in tool["description"] or "ne_ids" in tool["description"]


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
    assert "queryNmsAlarms" not in names
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
    assert proc.stdin is not None and proc.stdout is not None
    try:
        init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        }
        proc.stdin.write(json.dumps(init) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        assert line
        msg = json.loads(line)
        assert msg.get("id") == 1
        assert "result" in msg

        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n")
        proc.stdin.flush()
        line2 = proc.stdout.readline()
        assert line2
        listed = json.loads(line2)
        tools = listed["result"]["tools"]
        names = {t["name"] for t in tools}
        assert "queryNmsAlarms" in names
        assert "execManagedNe" in names
    finally:
        proc.kill()
        proc.wait(timeout=5)
