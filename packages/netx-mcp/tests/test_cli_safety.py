"""无设备/网络副作用地验证 MCP 执行边界与终态失败语义。"""
import json
from unittest.mock import patch

import pytest

from netx_mcp.http_tools import call_http_tool


@pytest.mark.parametrize("command", [
    "reboot", "configure terminal", "show version; reboot", "show version && reboot",
    "show version | reboot", "show version | redirect flash:config", "show $(reboot)",
    "show `reboot`", "display version\nreboot", "show version\u2028reboot",
    "show version > config", "show version\x00", "/system reboot",
])
@pytest.mark.parametrize("batch", [False, True])
def test_unsafe_cli_never_reaches_http(command, batch):
    args = {"targets": [{"ne_id": "test-only", "commands": [command]}]} if batch else {
        "ne_id": "test-only", "commands": [command], "async": True}
    with patch("netx_mcp.http_tools.http_post_json") as post:
        result = call_http_tool("execManagedNe", args)
    assert result.get("isError") is True
    post.assert_not_called()


@pytest.mark.parametrize("command", ["show version", "display version", "show bgp summary | one-line"])
def test_readonly_cli_still_executes(command):
    with patch("netx_mcp.http_tools.http_post_json", return_value={"ok": True, "data": {"ok": True}}) as post:
        result = call_http_tool("execManagedNe", {"ne_id": "test-only", "commands": [command]})
    assert not result.get("isError")
    assert post.call_args.args[0] == "/v1/managed-ne/exec"
    assert post.call_args.args[1]["commands"] == [command]


def test_async_ack_explains_polling_without_forcing_turn_end():
    with patch("netx_mcp.http_tools.http_post_json", return_value={"ok": True, "data": {
        "ok": True, "job_id": "test-job", "status": "running", "hint": "end the turn"}}):
        result = call_http_tool("execManagedNe", {"ne_id": "test-only", "commands": ["show version"], "async": True})
    data = json.loads(result["content"][0]["text"])
    assert data["poll_after_ms"] == 5000 and data["job_id"] == "test-job"
    assert "end the turn" not in data["hint"]


def test_failed_exec_job_is_tool_error_and_preserves_evidence():
    terminal = {"ok": True, "terminal": True, "status": "failed", "result": {
        "ok": False, "detail": "SSH connection failed", "commands": ["display version"]}}
    with patch("netx_mcp.http_tools.http_json", return_value={"ok": True, "data": terminal}):
        result = call_http_tool("getNeExecJob", {"job_id": "test-job"})
    assert result.get("isError") is True
    data = json.loads(result["content"][0]["text"])
    assert data["data"]["result"]["detail"] == "SSH connection failed"


@pytest.mark.parametrize("failed", [1, 2])
@pytest.mark.parametrize("async_job", [False, True])
def test_batch_target_failures_are_tool_errors_even_when_backend_ok(failed, async_job):
    result = {"ok": True, "summary": {"total": 2, "ok": 2-failed, "failed": failed},
              "results": [{"ok": failed == 1, "output": "version"}, {"ok": False, "detail": "SSH banner timeout"}]}
    terminal = {"ok": True, "terminal": True, "status": "succeeded", "result": result}
    if async_job:
        with patch("netx_mcp.http_tools.http_json", return_value={"ok": True, "data": terminal}):
            out = call_http_tool("getNeExecJob", {"job_id": "test-job"})
        assert json.loads(out["content"][0]["text"])["data"] == terminal
    else:
        with patch("netx_mcp.http_tools.http_post_json", return_value={"ok": True, "data": result}):
            out = call_http_tool("execManagedNe", {"ne_ids": ["m1", "m2"], "commands": ["show version"], "async": False})
        assert json.loads(out["content"][0]["text"])["data"] == result
    assert out["isError"] is True


@pytest.mark.parametrize("target_ok", [False, True])
def test_batch_checks_individual_results_without_summary(target_ok):
    terminal = {"ok": True, "terminal": True, "status": "succeeded", "result": {
        "ok": True, "results": [{"ok": target_ok, "output": "version"}]}}
    with patch("netx_mcp.http_tools.http_json", return_value={"ok": True, "data": terminal}):
        out = call_http_tool("getNeExecJob", {"job_id": "test-job"})
    assert bool(out.get("isError")) is not target_ok
