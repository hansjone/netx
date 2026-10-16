from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from netx_api.mcp_remote.asgi_client import api_token, http_json, redact
from netx_api.mcp_remote.bizmonitor.http_tools import HTTP_MCP_TOOLS, TOOL_REQUIRED_SCOPE, call_http_tool, tools_for_scopes
from netx_api.mcp_remote.registry import tools_for_request


def payload(result):
    return json.loads(result["content"][0]["text"])


def test_exact_eight_tools_read_only_and_canonical_scope():
    assert {t["name"] for t in HTTP_MCP_TOOLS} == {
        "listBizMonitors", "listBizMonitorBatches", "getBizMonitorContext", "getBizMonitorBoard",
        "listBizMonitorReds", "getBizMonitorDiffs", "getBizCollectBatch", "getBizCollectCommandRaw",
    }
    assert len(HTTP_MCP_TOOLS) == 8
    assert all(t["annotations"]["readOnlyHint"] for t in HTTP_MCP_TOOLS)
    assert set(TOOL_REQUIRED_SCOPE.values()) == {"biz-monitor:read"}
    assert tools_for_scopes(["ne:read", "ops:write"]) == []
    assert len(tools_for_scopes(["biz-monitor:read"])) == 8
    assert len(tools_for_scopes(None)) == 8


def test_catalog_filters_slims_projects_and_keeps_totals():
    def fake(method, path, *, params):
        assert method == "GET"
        if path == "/v1/biz-migration/projects":
            assert params == {}
            return {"ok": True, "data": {"items": [
                {"id": f"p{i}", "status": "active", "monitor_template": {"name": "Core"},
                 "old_task": {"ne_name": "R1", "ne_ip": "10.0.0.1", "commands": "large"}, "large_template": "drop"}
                for i in range(3)]}}
        assert path == "/v1/biz-state/tasks" and params == {"purpose": "portrait"}
        return {"ok": True, "data": {"items": [{"id": "t", "status": "paused", "ne_name": "R1"}]}}
    with patch("netx_api.mcp_remote.bizmonitor.http_tools.http_json", side_effect=fake):
        result = payload(call_http_tool("listBizMonitors", {"q": "r1", "status": "ACTIVE", "purpose": "portrait", "limit": 1}))
    data = result["data"]
    assert data["counts"] == {"projects": 3, "tasks": 0, "projects_returned": 1, "tasks_returned": 0}
    assert data["projects"][0]["old_ne"] == "R1 (10.0.0.1)"
    assert "old_task" not in data["projects"][0] and "large_template" not in data["projects"][0]
    assert "netx__" not in data["next"]


def test_batch_cutover_expand_runs_and_partial_error():
    def fake(method, path, *, params):
        if path.endswith("/batches"):
            return {"ok": True, "data": {"items": [{"id": "b1", "status": "done", "large": "drop"}, {"id": "b2", "status": "done"}]}}
        assert params == {"limit": 3}
        if "/b2/" in path:
            return {"ok": False, "error": "netx_http_404"}
        return {"ok": True, "data": {"items": [{"id": "r1", "summary": {"anomaly": 2, "progress": {"ok": 4, "total": 5}, "large": "drop"}}]}}
    with patch("netx_api.mcp_remote.bizmonitor.http_tools.http_json", side_effect=fake) as mock:
        out = call_http_tool("listBizMonitorBatches", {"project_id": "p/1", "include_runs": True, "runs_limit": 3})
    data = payload(out)["data"]
    assert "isError" not in out and mock.call_count == 3
    assert mock.call_args_list[0].args == ("GET", "/v1/biz-migration/projects/p%2F1/batches")
    assert data["cutover_batches"][1]["runs_error"] == "netx_http_404"
    assert data["cutover_batches"][0]["runs"] == [{"id": "r1", "anomaly": 2, "progress_ok": 4, "progress_total": 5}]
    assert data["counts"]["runs"] == 1 and "runs" not in data
    assert "large" not in data["cutover_batches"][0]


def test_batch_collect_and_runs_modes():
    with patch("netx_api.mcp_remote.bizmonitor.http_tools.http_json") as mock:
        mock.return_value = {"ok": True, "data": {"items": [{"id": "c1", "status": "done", "raw_text": "drop"}]}}
        out = payload(call_http_tool("listBizMonitorBatches", {"task_id": "t", "limit": 20}))
        mock.assert_called_once_with("GET", "/v1/biz-state/tasks/t/batches", params={"limit": 20})
        assert out["data"]["collect_batches"] == [{"id": "c1", "status": "done", "task_id": "t"}]
        mock.reset_mock()
        mock.return_value = {"ok": True, "data": {"items": [{"id": "r1", "summary": {"progress_ok": 2}}]}}
        out = payload(call_http_tool("listBizMonitorBatches", {"batch_id": "b"}))
        mock.assert_called_once_with("GET", "/v1/biz-migration/batches/b/runs", params={"limit": 10})
        assert out["data"]["runs"] == [{"id": "r1", "progress_ok": 2}]


def test_batch_all_caps_run_expansion_at_five():
    def fake(method, path, *, params):
        if path == "/v1/biz-migration/projects/p/batches":
            return {"ok": True, "data": {"items": [{"id": f"b{i}"} for i in range(8)]}}
        if path == "/v1/biz-state/tasks/t/batches":
            return {"ok": True, "data": {"items": [{"id": "c"}]}}
        assert path.endswith("/runs")
        return {"ok": True, "data": {"items": [{"id": "r"}]}}
    with patch("netx_api.mcp_remote.bizmonitor.http_tools.http_json", side_effect=fake) as mock:
        data = payload(call_http_tool("listBizMonitorBatches", {
            "project_id": "p", "task_id": "t", "include_runs": True,
        }))["data"]
    assert mock.call_count == 7  # two catalogs plus five run lookups
    assert data["kind"] == "all"
    assert data["counts"] == {"cutover_batches": 8, "collect_batches": 1, "runs": 5}
    assert sum("runs" in row for row in data["cutover_batches"]) == 5


@pytest.mark.parametrize("name,args,path,params", [
    ("getBizMonitorContext", {"project_id": " p/1 ", "task_id": "t"}, "/v1/biz-migration/monitor-context", {"project_id": "p/1", "task_id": "t"}),
    ("getBizMonitorBoard", {"batch_id": "b/1", "run_id": "r"}, "/v1/biz-migration/batches/b%2F1/board", {"run_id": "r"}),
    ("listBizMonitorReds", {"project_id": "p", "status": "open", "limit": 700}, "/v1/biz-migration/projects/p/red-tickets", {"status": "open", "limit": 500}),
    ("getBizMonitorDiffs", {"run_id": "r/1", "metric_id": "m", "sheet_id": "s", "verdict": "changed", "color": "red", "q": "xe-1", "offset": -2, "limit": 10}, "/v1/biz-migration/runs/r%2F1/diffs", {"metric_id": "m", "sheet_id": "s", "verdict": "changed", "color": "red", "kw": "xe-1", "offset": 0, "limit": 10}),
    ("getBizCollectBatch", {"batch_id": "c/1"}, "/v1/biz-state/batches/c%2F1", {}),
    ("getBizCollectCommandRaw", {"batch_id": "c", "command_id": "cmd/1"}, "/v1/biz-state/batches/c/commands/cmd%2F1", {}),
])
def test_each_read_tool_forwards_exact_http(name, args, path, params):
    evidence = {"old_key": "xe-1", "new_key": "ge-2", "evidence": {"raw_command": "show interface"}}
    with patch("netx_api.mcp_remote.bizmonitor.http_tools.http_json", return_value={"ok": True, "data": evidence}) as mock:
        result = call_http_tool(name, args)
    mock.assert_called_once_with("GET", path, params=params)
    assert payload(result)["data"] == evidence


def test_diff_default_limit_not_forwarded_and_only_kw_used():
    with patch("netx_api.mcp_remote.bizmonitor.http_tools.http_json", return_value={"ok": True, "data": {}}) as mock:
        call_http_tool("getBizMonitorDiffs", {"run_id": "r", "q": " A B "})
    assert mock.call_args.kwargs["params"] == {"kw": "A B"}


def test_raw_not_truncated_and_credentials_removed(monkeypatch):
    monkeypatch.setenv("NETX_API_TOKEN", "nxt_do_not_leak")
    raw = "show interfaces\n" + "interface xe-1 up\n" * 10000
    with patch("netx_api.mcp_remote.bizmonitor.http_tools.http_json", return_value={"ok": True, "data": {
        "raw_text": raw, "device": {"password": "hidden", "ne_name": "R1"},
        "api_token": "hidden", "note": "Bearer abcd password=secret nxt_do_not_leak",
    }}):
        result = call_http_tool("getBizCollectCommandRaw", {"batch_id": "b", "command_id": "c"})
    data = payload(result)["data"]
    assert data["raw_text"] == raw and data["device"] == {"ne_name": "R1"}
    assert "api_token" not in data
    assert "abcd" not in data["note"] and "secret" not in data["note"] and "nxt_do_not_leak" not in data["note"]


@pytest.mark.parametrize("name,args,error", [
    ("getBizMonitorContext", {}, "project_id_or_task_id_required"),
    ("getBizMonitorBoard", {}, "batch_id_required"),
    ("listBizMonitorReds", {}, "project_id_required"),
    ("getBizMonitorDiffs", {}, "run_id_required"),
    ("getBizCollectBatch", {}, "batch_id_required"),
    ("getBizCollectCommandRaw", {"batch_id": "b"}, "batch_id_and_command_id_required"),
    ("listBizMonitorBatches", {}, "project_id_or_task_id_or_batch_id_required"),
    ("listBizMonitorBatches", {"kind": "all", "project_id": "p"}, "task_id_required"),
    ("listBizMonitors", {"kind": "project"}, "kind_invalid"),
    ("listBizMonitors", {"limit": "two"}, "invalid_argument"),
    ("getBizMonitorDiffs", {"run_id": "r", "offset": True}, "invalid_argument"),
    ("getBizMonitorDiffs", {"run_id": "r", "kw": "x"}, "invalid_argument"),
])
def test_bad_arguments_never_make_http_call(name, args, error):
    with patch("netx_api.mcp_remote.bizmonitor.http_tools.http_json") as mock:
        result = call_http_tool(name, args)
    mock.assert_not_called()
    assert result["isError"] is True and payload(result)["error"] == error


def test_argument_error_names_field_without_echoing_value():
    result = payload(call_http_tool("listBizMonitors", {"limit": "password=hidden"}))
    assert "limit" in result["detail"]
    assert "hidden" not in json.dumps(result)


@pytest.mark.parametrize("failure", [
    {"ok": False, "error": "netx_http_404"},
    {"ok": True, "data": {"ok": False, "error": "not_found"}},
])
def test_upstream_failures_are_tool_errors(failure):
    with patch("netx_api.mcp_remote.bizmonitor.http_tools.http_json", return_value=failure):
        result = call_http_tool("getBizCollectBatch", {"batch_id": "b"})
    assert result["isError"] is True


def test_scope_and_group_filter_for_bizmonitor():
    assert tools_for_request(frozenset({"ne:read"}), frozenset({"bizMonitor"})) == []
    tools = tools_for_request(frozenset({"biz-monitor:read"}), frozenset({"bizMonitor"}))
    assert len(tools) == 8
    assert all(t["_group"] == "bizMonitor" for t in tools)
    assert all(t["annotations"]["readOnlyHint"] for t in tools)


def test_http_auth_lang_redaction_and_error_sanitization(monkeypatch):
    monkeypatch.setenv("NETX_API_TOKEN", "nxt_secret")
    monkeypatch.setenv("NETX_API_URL", "http://example.test/netx")
    monkeypatch.setenv("NETX_LANG", "en")
    with patch("netx_api.mcp_remote.asgi_client.get_app", return_value=None):
        with patch("netx_api.mcp_remote.asgi_client.httpx.Client") as client:
            response = httpx.Response(200, json={"id": "b", "password": "secret", "nested": {"api_key": "bad"}})
            client.return_value.__enter__.return_value.request.return_value = response
            result = http_json("GET", "/v1/biz-state/batches/b", params={"limit": 2})
            assert client.called
            kwargs = client.return_value.__enter__.return_value.request.call_args.kwargs
            assert kwargs["headers"]["authorization"] == "Bearer nxt_secret"
            assert kwargs["params"] == {"lang": "en", "limit": 2}
            assert result["data"] == {"id": "b", "nested": {}}
            client.return_value.__enter__.return_value.request.return_value = httpx.Response(
                500, text="password=leak nxt_secret"
            )
            result = http_json("GET", "/v1/test")
            assert result["error"] == "netx_http_500"
            # detail may contain upstream text; redact strips token strings in nested payloads
            client.return_value.__enter__.return_value.request.side_effect = httpx.ConnectError("Bearer leak")
            failed = http_json("GET", "/v1/test")
            assert failed["ok"] is False


def test_missing_token_returns_empty_api_token(monkeypatch, tmp_path):
    monkeypatch.delenv("NETX_API_TOKEN", raising=False)
    monkeypatch.delenv("NETX_MCP_TOKEN_FILE", raising=False)
    monkeypatch.chdir(tmp_path)
    with (
        patch("netx_api.mcp_remote.asgi_client.get_app", return_value=None),
        patch("netx_api.mcp_remote.asgi_client.get_request_context", return_value=None),
    ):
        assert api_token() == ""


def test_bizmonitor_group_has_eight_tools():
    tools = tools_for_request(None, frozenset({"bizMonitor"}))
    assert len(tools) == 8
    assert {t["name"] for t in tools} == {t["name"] for t in HTTP_MCP_TOOLS}


def test_token_priority_env_then_explicit_file_then_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    default = tmp_path / "data" / "auth" / "mcp_token"
    default.parent.mkdir(parents=True)
    default.write_text("  default-token\n", encoding="utf-8")
    explicit = tmp_path / "custom-token"
    explicit.write_text("  explicit-token\n", encoding="utf-8")
    monkeypatch.setenv("NETX_API_TOKEN", "  env-token  ")
    monkeypatch.setenv("NETX_MCP_TOKEN_FILE", str(explicit))
    assert api_token() == "env-token"
    monkeypatch.setenv("NETX_API_TOKEN", "  ")
    assert api_token() == "explicit-token"
    explicit.write_text(" \n", encoding="utf-8")
    assert api_token() == "default-token"
    monkeypatch.delenv("NETX_MCP_TOKEN_FILE")
    assert api_token() == "default-token"


@pytest.mark.parametrize("file_error", ["missing", "directory", "invalid_utf8"])
def test_token_file_errors_fall_back_without_exposing_contents(monkeypatch, tmp_path, capsys, file_error):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NETX_API_TOKEN", raising=False)
    default = tmp_path / "data" / "auth" / "mcp_token"
    default.parent.mkdir(parents=True)
    default.write_text("fallback-token", encoding="utf-8")
    explicit = tmp_path / "sensitive-file"
    if file_error == "directory":
        explicit.mkdir()
    elif file_error == "invalid_utf8":
        explicit.write_bytes(b"secret\xff")
    monkeypatch.setenv("NETX_MCP_TOKEN_FILE", str(explicit))
    with patch("netx_api.mcp_remote.asgi_client.get_request_context", return_value=None):
        assert api_token() == "fallback-token"
        default.unlink()
        assert api_token() == ""
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


@pytest.mark.parametrize("use_explicit", [True, False])
def test_file_token_auth_and_redaction_survives_rotation(monkeypatch, tmp_path, use_explicit):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NETX_API_TOKEN", raising=False)
    monkeypatch.delenv("NETX_MCP_TOKEN_FILE", raising=False)
    monkeypatch.setenv("NETX_API_URL", "http://example.test")
    token_file = tmp_path / "custom-token" if use_explicit else tmp_path / "data" / "auth" / "mcp_token"
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text("nxt_file_secret", encoding="utf-8")
    if use_explicit:
        monkeypatch.setenv("NETX_MCP_TOKEN_FILE", str(token_file))
    assert redact({"nested": ["raw nxt_file_secret"]}) == {"nested": ["raw [REDACTED]"]}

    def response(*args, **kwargs):
        assert kwargs["headers"]["authorization"] == "Bearer nxt_file_secret"
        token_file.write_text("nxt_rotated", encoding="utf-8")
        return httpx.Response(200, json={"raw_text": "prefix nxt_file_secret suffix"})

    with (
        patch("netx_api.mcp_remote.asgi_client.get_app", return_value=None),
        patch("netx_api.mcp_remote.asgi_client.get_request_context", return_value=None),
        patch("netx_api.mcp_remote.asgi_client.httpx.Client") as client,
    ):
        client.return_value.__enter__.return_value.request.side_effect = response
        result = http_json("GET", "/v1/biz-state/batches/b/commands/c")
    assert result["data"]["raw_text"] == "prefix [REDACTED] suffix"
    assert "nxt_file_secret" not in json.dumps(result)
