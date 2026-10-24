"""无设备/网络副作用地验证 MCP exec 转发与终态失败语义。

命令允许/拒绝由 REST ne_exec_guard（设备 exec_policy）负责；MCP 不再前置 show/display 硬门。
"""
import json
from unittest.mock import patch

import pytest

from netx_api.mcp_remote.ops.http_tools import call_http_tool


@pytest.mark.parametrize(
    "command",
    [
        "reboot",
        "configure terminal",
        "show version; reboot",
        "ip -br addr",
        "/interface print",
        "/system reboot",
    ],
)
@pytest.mark.parametrize("batch", [False, True])
def test_mcp_forwards_commands_to_http_for_policy_enforcement(command, batch):
    """MCP must not reject by prefix; backend policy decides."""
    args = (
        {"targets": [{"ne_id": "test-only", "commands": [command]}], "async": False}
        if batch
        else {"ne_id": "test-only", "commands": [command], "async": False}
    )
    with patch(
        "netx_api.mcp_remote.ops.http_tools.http_post_json",
        return_value={"ok": True, "data": {"ok": True}},
    ) as post:
        result = call_http_tool("execManagedNe", args)
    assert not result.get("isError")
    post.assert_called_once()
    body = post.call_args.args[1]
    if batch:
        assert body["targets"][0]["commands"] == [command]
    else:
        assert body["commands"] == [command]


@pytest.mark.parametrize("command", ["show version", "display version", "show bgp summary | one-line"])
def test_readonly_cli_still_executes(command):
    with patch("netx_api.mcp_remote.ops.http_tools.http_post_json", return_value={"ok": True, "data": {"ok": True}}) as post:
        result = call_http_tool("execManagedNe", {"ne_id": "test-only", "commands": [command]})
    assert not result.get("isError")
    assert post.call_args.args[0] == "/v1/managed-ne/exec"
    assert post.call_args.args[1]["commands"] == [command]


def test_async_ack_explains_polling_without_forcing_turn_end():
    with patch("netx_api.mcp_remote.ops.http_tools.http_post_json", return_value={"ok": True, "data": {
        "ok": True, "job_id": "test-job", "status": "running", "hint": "end the turn"}}):
        result = call_http_tool("execManagedNe", {"ne_id": "test-only", "commands": ["show version"], "async": True})
    data = json.loads(result["content"][0]["text"])
    assert data["poll_after_ms"] == 5000 and data["job_id"] == "test-job"
    assert "end the turn" not in data["hint"]


def test_failed_exec_job_is_tool_error_and_preserves_evidence():
    terminal = {"ok": True, "terminal": True, "status": "failed", "result": {
        "ok": False, "detail": "SSH connection failed", "commands": ["display version"]}}
    with patch("netx_api.mcp_remote.ops.http_tools.http_json", return_value={"ok": True, "data": terminal}):
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
        with patch("netx_api.mcp_remote.ops.http_tools.http_json", return_value={"ok": True, "data": terminal}):
            out = call_http_tool("getNeExecJob", {"job_id": "test-job"})
        assert json.loads(out["content"][0]["text"])["data"] == terminal
    else:
        with patch("netx_api.mcp_remote.ops.http_tools.http_post_json", return_value={"ok": True, "data": result}):
            out = call_http_tool("execManagedNe", {"ne_ids": ["m1", "m2"], "commands": ["show version"], "async": False})
        assert json.loads(out["content"][0]["text"])["data"] == result
    assert out["isError"] is True


@pytest.mark.parametrize("target_ok", [False, True])
def test_batch_checks_individual_results_without_summary(target_ok):
    terminal = {"ok": True, "terminal": True, "status": "succeeded", "result": {
        "ok": True, "results": [{"ok": target_ok, "output": "version"}]}}
    with patch("netx_api.mcp_remote.ops.http_tools.http_json", return_value={"ok": True, "data": terminal}):
        out = call_http_tool("getNeExecJob", {"job_id": "test-job"})
    assert bool(out.get("isError")) is not target_ok
