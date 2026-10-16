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
    assert len(names) == 15
    assert "queryNmsAlarms" in names
    assert "queryNmsAlarmsRaw" in names
    assert "execManagedNe" in names
    assert "getNeExecJob" in names
    assert "listCliTargets" in names
    assert "findTopologyPaths" in names
    assert "getBizMonitorContext" not in names
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
        assert params["limit"] == 20
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
    assert "ume_ne_ids" not in props
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

        # Notifications must not be answered; the next line belongs to ping.
        for note in ("notifications/initialized", "notifications/cancelled"):
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": note, "params": {}}) + "\n")
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"}) + "\n")
        proc.stdin.flush()
        pong = json.loads(proc.stdout.readline())
        assert pong == {"jsonrpc": "2.0", "id": 9, "result": {}}

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


def test_bad_integer_arg_is_tool_error_not_crash() -> None:
    with patch("netx_mcp.http_tools.http_json") as mock_http:
        out = call_http_tool("queryNmsAlarms", {"page": "two"})
        mock_http.assert_not_called()
    assert out["isError"] is True
    payload = json.loads(out["content"][0]["text"])
    assert payload["error"] == "invalid_argument"
    assert "page" in payload["detail"]


def test_query_nms_alarms_pages_past_two_and_normalizes_severity() -> None:
    with patch("netx_mcp.http_tools.http_json") as mock_http:
        mock_http.return_value = {
            "ok": True,
            "data": {"items": [{"ne_id": "u-1", "ne_name": "ME{u-1}", "host_name": "H1"}]},
        }
        out = call_http_tool("queryNmsAlarms", {"page": 5, "severity": "Critical", "is_cleared": "False"})
        params = mock_http.call_args[1]["params"]
    assert params["page"] == 5
    assert params["severity"] == "critical"
    assert params["is_cleared"] == "False"
    row = json.loads(out["content"][0]["text"])["data"]["items"][0]
    assert "ne_name" not in row
    assert row["host_name"] == "H1"


def test_nested_api_failure_is_marked_error() -> None:
    with patch("netx_mcp.http_tools.http_json") as mock_http:
        mock_http.return_value = {"ok": True, "data": {"ok": False, "error": "job_not_found"}}
        out = call_http_tool("getNeExecJob", {"job_id": "nope"})
    assert out.get("isError") is True


def test_raw_query_strips_echoed_field_lists() -> None:
    with patch("netx_mcp.http_tools.http_json") as mock_http:
        mock_http.return_value = {
            "ok": True,
            "data": {
                "items": [],
                "meta": {"available_fields": ["a"], "order_by_allowed": ["b"], "time_filter_field": "last_seen_at"},
            },
        }
        out = call_http_tool("queryNmsAlarmsRaw", {"field_preset": "brief"})
    meta = json.loads(out["content"][0]["text"])["data"]["meta"]
    assert meta == {"time_filter_field": "last_seen_at"}


def test_get_nms_ne_resolves_host_name() -> None:
    calls: list[str] = []

    def fake(method: str, path: str, params: dict | None = None) -> dict:
        calls.append(path)
        if path == "/v1/ume/inventory/ne":
            return {"ok": True, "data": {"items": [{"ne_id": "uuid-1", "host_name": "MDN-X-AN1"}]}}
        if path.endswith("/uuid-1"):
            return {"ok": True, "data": {"ne_id": "uuid-1"}}
        return {"ok": False, "error": "netx_http_404", "detail": "ume_ne_not_found"}

    with patch("netx_mcp.http_tools.http_json", side_effect=fake):
        out = call_http_tool("getNmsNe", {"ne_id": "mdn-x-an1"})
    assert json.loads(out["content"][0]["text"])["data"]["ne_id"] == "uuid-1"
    assert calls[-1] == "/v1/ume/inventory/ne/uuid-1"


def test_cli_targets_and_paths_use_nms_naming() -> None:
    with patch("netx_mcp.http_tools.http_json") as mock_http:
        mock_http.return_value = {"ok": True, "data": {"items": [{"source": "ume", "ume_ne_id": "u-1"}]}}
        out = call_http_tool("listCliTargets", {"source": "nms"})
    row = json.loads(out["content"][0]["text"])["data"]["items"][0]
    assert row == {"source": "nms", "nms_ne_id": "u-1"}

    with patch("netx_mcp.http_tools.http_post_json") as mock_post:
        mock_post.return_value = {"ok": True, "data": {"paths": [{"nodes": [{"name": "A", "ume_ne_id": "u-1"}]}]}}
        out = call_http_tool("findTopologyPaths", {"from_nms_ne_id": "u-1", "to_nms_ne_id": "u-2"})
    node = json.loads(out["content"][0]["text"])["data"]["paths"][0]["nodes"][0]
    assert node == {"name": "A", "nms_ne_id": "u-1"}


def test_managed_ne_list_drops_disabled_hop_noise() -> None:
    with patch("netx_mcp.http_tools.http_json") as mock_http:
        mock_http.return_value = {
            "ok": True,
            "data": {"items": [{"id": "m1", "hop_enabled": False, "hop_vrf": "", "connect_detail": "long"}]},
        }
        out = call_http_tool("listManagedNe", {"connect_status": "pass"})
    row = json.loads(out["content"][0]["text"])["data"]["items"][0]
    assert row == {"id": "m1", "hop_enabled": False}


@pytest.mark.parametrize(("name", "args"), [
    ("queryNmsAlarms", {"ne_name": "old"}),
    ("getManagedNe", {"managed_ne_id": "old"}),
    ("getManagedNe", {"id": "old"}),
    ("execManagedNe", {"ume_ne_id": "old", "commands": ["show version"]}),
    ("execManagedNe", {"ume_ne_ids": ["old"], "commands": ["show version"]}),
    ("execManagedNe", {"targets": [{"ume_ne_id": "old", "commands": ["show version"]}]}),
    ("findTopologyPaths", {"from_ume_ne_id": "old", "to_nms_ne_id": "b"}),
    ("findTopologyPaths", {"from_nms_ne_id": "a", "to_ume_ne_id": "old"}),
    ("listCliTargets", {"source": "ume"}),
])
def test_obsolete_aliases_are_rejected_before_http(name: str, args: dict) -> None:
    with patch("netx_mcp.http_tools.http_json") as get, patch("netx_mcp.http_tools.http_post_json") as post:
        out = call_http_tool(name, args)
    get.assert_not_called()
    post.assert_not_called()
    assert out["isError"] is True
    assert json.loads(out["content"][0]["text"])["error"] == "invalid_argument"


def test_all_fifteen_tools_match_backend_scope_policy() -> None:
    from netx_api.auth_scopes import required_scope_for_request
    from netx_mcp.http_tools import TOOL_REQUIRED_SCOPE

    routes = {
        "queryNmsAlarms": ("GET", "/v1/ume/alarms"),
        "aggregateNmsAlarms": ("GET", "/v1/ume/alarms/aggregate"),
        "runNmsDiagnostics": ("GET", "/v1/ume/diagnostics"),
        "queryNmsNeInventory": ("GET", "/v1/ume/inventory/ne"),
        "getNmsNe": ("GET", "/v1/ume/inventory/ne/id"),
        "queryNmsAlarmsRaw": ("GET", "/v1/ume/alarms/raw"),
        "aggregateNmsAlarmsRaw": ("GET", "/v1/ume/alarms/aggregate/raw"),
        "listNmsAlarmFields": ("GET", "/v1/ume/alarms/fields"),
        "sqlQueryNms": ("POST", "/v1/sql/ume_query"),
        "listManagedNe": ("GET", "/v1/managed-ne"),
        "getManagedNe": ("GET", "/v1/managed-ne/id"),
        "execManagedNe": ("POST", "/v1/managed-ne/exec"),
        "getNeExecJob": ("GET", "/v1/managed-ne/exec-jobs/id"),
        "listCliTargets": ("GET", "/v1/cli/targets"),
        "findTopologyPaths": ("POST", "/v1/topology/fabric/paths"),
    }
    assert set(routes) == {t["name"] for t in HTTP_MCP_TOOLS} == set(TOOL_REQUIRED_SCOPE)
    for name, (method, path) in routes.items():
        assert TOOL_REQUIRED_SCOPE[name] == required_scope_for_request(method, path)
        assert name in {t["name"] for t in tools_for_scopes([TOOL_REQUIRED_SCOPE[name]])}
    assert tools_for_scopes([]) == []


@pytest.mark.parametrize(("name", "args", "path", "expected"), [
    ("aggregateNmsAlarms", {"top_ne": 3000}, "/v1/ume/alarms/aggregate", {"top_ne": 3000}),
    ("runNmsDiagnostics", {}, "/v1/ume/diagnostics", None),
    ("queryNmsNeInventory", {"keyword": "edge", "page": 9, "page_size": 900}, "/v1/ume/inventory/ne", {"keyword": "edge", "page": 9, "page_size": 500}),
    ("listNmsAlarmFields", {}, "/v1/ume/alarms/fields", None),
    ("getNeExecJob", {"job_id": "job /?"}, "/v1/managed-ne/exec-jobs/job%20%2F%3F", None),
    ("listCliTargets", {"source": "nms", "keyword": "edge"}, "/v1/cli/targets", {"source": "ume", "keyword": "edge", "page": 1, "page_size": 50}),
])
def test_ops_read_routes_and_backend_limits(name: str, args: dict, path: str, expected: dict | None) -> None:
    with patch("netx_mcp.http_tools.http_json", return_value={"ok": True, "data": {"items": []}}) as get:
        out = call_http_tool(name, args)
    get.assert_called_once_with("GET", path, params=expected)
    assert not out.get("isError")


def test_raw_aggregation_filters_and_compaction() -> None:
    with patch("netx_mcp.http_tools.http_json", return_value={"ok": True, "data": {"buckets": [{"key": "edge", "count": 4}], "meta": {"group_by_allowed": ["large"], "time_filter_field": "last_seen_at"}}}) as get:
        out = call_http_tool("aggregateNmsAlarmsRaw", {"group_by": "alarm_host_name", "severity": "CRITICAL", "exclude_missing_host": False, "limit": 9999})
    assert get.call_args.kwargs["params"] == {"group_by": "alarm_host_name", "severity": "critical", "exclude_missing_host": False, "limit": 2000}
    data = json.loads(out["content"][0]["text"])["data"]
    assert data["buckets"][0]["count"] == 4
    assert data["meta"] == {"time_filter_field": "last_seen_at"}


def test_sql_limits_are_forwarded_and_denial_is_tool_error() -> None:
    with patch("netx_mcp.http_tools.http_post_json", return_value={"ok": False, "error": "netx_http_403", "detail": "insufficient_scope:sql:query"}) as post:
        out = call_http_tool("sqlQueryNms", {"sql": " SELECT ne_id FROM ume_inventory_ne ", "limit": 9999, "statement_timeout_ms": 99999})
    post.assert_called_once_with("/v1/sql/ume_query", {"sql": "SELECT ne_id FROM ume_inventory_ne", "limit": 2000, "statement_timeout_ms": 30000}, timeout=60.0)
    assert out["isError"] is True
    assert "insufficient_scope" in out["content"][0]["text"]


@pytest.mark.parametrize("name,args", [
    ("listManagedNe", {"keyword": "edge"}),
    ("getManagedNe", {"ne_id": "m1"}),
    ("listCliTargets", {"source": "managed"}),
    ("getNmsNe", {"ne_id": "u1"}),
])
def test_structured_credentials_are_removed_but_capabilities_remain(name: str, args: dict) -> None:
    row = {"id": "m1", "password": "secret", "has_password": True, "capability": {"recommended_mode": "show_only", "token": "secret"}, "hop_password_enc": "secret"}
    data = {"items": [row]} if name.startswith("list") else row
    with patch("netx_mcp.http_tools.http_json", return_value={"ok": True, "data": data}):
        out = call_http_tool(name, args)
    text = out["content"][0]["text"]
    assert "secret" not in text
    assert "show_only" in text
    assert "has_password" in text


@pytest.mark.parametrize("args", [
    {"targets": [{"ne_id": "m1", "nms_ne_id": "u1", "commands": ["show version"]}]},
    {"targets": [{"ne_id": "m1"}]},
    {"targets": [{"ne_id": "m1", "commands": ["show version"] * 6}]},
    {"ne_ids": ["m1"]},
    {"ne_ids": ["m1"] * 11, "nms_ne_ids": ["u1"] * 10, "commands": ["show version"]},
    {"ne_id": "m1", "ne_ids": ["m2"], "commands": ["show version"]},
    {"targets": ["m1"], "commands": ["show version"]},
])
def test_invalid_exec_batches_do_not_reach_http(args: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETX_NE_EXEC_MAX_COMMANDS", "5")
    with patch("netx_mcp.http_tools.http_post_json") as post:
        out = call_http_tool("execManagedNe", args)
    post.assert_not_called()
    assert out["isError"] is True


def test_async_auto_counts_all_target_forms_and_poll_preserves_terminal_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETX_NE_EXEC_ASYNC_MIN_NES", "4")
    with patch("netx_mcp.http_tools.http_post_json", return_value={"ok": True, "data": {"ok": True, "job_id": "j1", "terminal": False}}) as post:
        ack = call_http_tool("execManagedNe", {"ne_ids": ["m1", "m2"], "nms_ne_ids": ["u1", "u2"], "commands": ["show version"], "concurrency": 20})
    assert post.call_args.args[0] == "/v1/managed-ne/exec-jobs"
    assert post.call_args.args[1]["concurrency"] == 8
    assert json.loads(ack["content"][0]["text"])["job_id"] == "j1"
    terminal = {"ok": True, "terminal": True, "result": {"ok": True, "results": [{"ok": False, "error": "timeout"}]}}
    with patch("netx_mcp.http_tools.http_json", return_value={"ok": True, "data": terminal}):
        out = call_http_tool("getNeExecJob", {"job_id": "j1"})
    assert json.loads(out["content"][0]["text"])["data"] == terminal
    assert out["isError"] is True


def test_explicit_sync_overrides_auto_async(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETX_NE_EXEC_ASYNC_MIN_NES", "1")
    with patch("netx_mcp.http_tools.http_post_json", return_value={"ok": True, "data": {"ok": True}}) as post:
        call_http_tool("execManagedNe", {"nms_ne_ids": ["u1", "u2"], "commands": ["show version"], "async": False})
    assert post.call_args.args[0] == "/v1/managed-ne/exec-batch"


def test_stdio_scope_denial_never_invokes_handler() -> None:
    import io
    from netx_mcp.server import run_stdio_loop

    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "execManagedNe", "arguments": {"ne_id": "m1", "commands": ["show version"]}}}
    output = io.StringIO()
    with patch("netx_mcp.server.sys.stdin", io.StringIO(json.dumps(request) + "\n")), patch("netx_mcp.server.sys.stdout", output), patch("netx_mcp.server._fetch_scopes", return_value=["ne:read"]), patch("netx_mcp.server.call_http_tool") as handler:
        run_stdio_loop()
    handler.assert_not_called()
    response = json.loads(output.getvalue())
    assert response["error"]["code"] == -32001
    assert "ne:exec" in response["error"]["message"]


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_http_errors_preserve_status_and_bound_detail(method: str, status: int) -> None:
    import httpx
    from netx_mcp.http_client import http_json, http_post_json

    transport = httpx.MockTransport(lambda request: httpx.Response(status, text="denied" * 300))
    client = httpx.Client(transport=transport)
    with patch("netx_mcp.http_client.httpx.Client", return_value=client):
        result = http_json("GET", "/v1/ume/alarms") if method == "GET" else http_post_json("/v1/managed-ne/exec", {})
    assert result["ok"] is False
    assert result["error"] == f"netx_http_{status}"
    assert len(result["detail"]) == 800



def test_mixed_exec_targets_translate_nms_ids_and_preserve_per_target_commands() -> None:
    with patch("netx_mcp.http_tools.http_post_json", return_value={"ok": True, "data": {"ok": True, "items": [{"ok": True, "password": "secret", "output": "version"}]}}) as post:
        out = call_http_tool("execManagedNe", {"targets": [{"ne_id": "m1"}, {"nms_ne_id": "u1", "commands": ["display version"]}], "commands": ["show version"], "async": False})
    assert post.call_args.args[0] == "/v1/managed-ne/exec-batch"
    assert post.call_args.args[1]["targets"] == [{"ne_id": "m1"}, {"ume_ne_id": "u1", "commands": ["display version"]}]
    text = out["content"][0]["text"]
    assert "secret" not in text
    assert json.loads(text)["data"]["items"][0]["output"] == "version"


def test_async_job_queue_failure_is_not_a_successful_ack() -> None:
    with patch("netx_mcp.http_tools.http_post_json", return_value={"ok": True, "data": {"ok": False, "error": "ne_exec_job_busy"}}):
        out = call_http_tool("execManagedNe", {"ne_id": "m1", "commands": ["show version"], "async": True})
    assert out["isError"] is True
    assert json.loads(out["content"][0]["text"])["error"] == "ne_exec_job_busy"


@pytest.mark.parametrize("args", [{}, {"group_by": ""}])
def test_raw_aggregation_missing_dimension_is_actionable(args: dict) -> None:
    with patch("netx_mcp.http_tools.http_json") as get:
        out = call_http_tool("aggregateNmsAlarmsRaw", args)
    get.assert_not_called()
    assert out["isError"] is True
    assert "group_by" in out["content"][0]["text"]
