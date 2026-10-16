"""HTTP contract and partial-failure regression tests; no backend required."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from netx_topology_mcp import http_tools as tools


def payload(name, args):
    result = tools.call_http_tool(name, args)
    return result, json.loads(result["content"][0]["text"])


@pytest.mark.parametrize("name,args", [
    ("getTopologyTree", {}),
    ("getTopologyView", {"view_id": "v"}),
    ("createTopologyFolder", {"name": "测试"}),
    ("addTopologyViewNodes", {"view_id": "v", "fabric_node_ids": ["n"]}),
    ("removeTopologyViewNodes", {"view_id": "v", "fabric_node_ids": ["n"]}),
    ("copyTopologyViewNodes", {"source_view_id": "s", "target_view_id": "t"}),
    ("updateTopologyViewPositions", {"view_id": "v", "layout": "grid"}),
    ("projectTopologyNeighbors", {"view_id": "v"}),
    ("queryTopologyFabricNodes", {"mode": "list"}),
    ("classifyTopologyFabricNodes", {"action": "apply_rules"}),
    ("queryTopologyNeighborhood", {"node_id": "n"}),
    ("queryTopologyEdges", {}),
    ("layoutTopologyView", {"view_id": "v"}),
    ("suggestSinkHubs", {"source_view_id": "s"}),
    ("analyzeTopologyViewLayout", {"view_id": "v"}),
    ("sinkTopologyDualUnits", {"source_view_id": "s", "sink_view_id": "t"}),
])
def test_business_failure_is_mcp_error(name, args):
    with patch.object(tools, "http_json", return_value={
        "ok": True, "data": {"ok": False, "error": "backend_rejected"},
    }):
        result, data = payload(name, args)
    assert result.get("isError") is True, (name, data)
    assert data["ok"] is False
    assert data["error"] == "backend_rejected"


@pytest.mark.parametrize("name,args,method", [
    ("addTopologyViewNodes", {"view_id": "v", "fabric_node_ids": [f"n{i}" for i in range(501)]}, "POST"),
    ("removeTopologyViewNodes", {"view_id": "v", "fabric_node_ids": [f"n{i}" for i in range(501)]}, "POST"),
    ("updateTopologyViewPositions", {"view_id": "v", "positions": [
        {"fabric_node_id": f"n{i}", "x": i, "y": 0} for i in range(501)
    ]}, "PATCH"),
])
def test_chunk_failure_stops_later_mutations(name, args, method):
    with patch.object(tools, "http_json", side_effect=[
        {"ok": True, "data": {"ok": True, "added": 200, "updated": 200, "removed": 200}},
        {"ok": True, "data": {"ok": False, "error": "write_failed"}},
    ]) as call:
        result, data = payload(name, args)
    assert result.get("isError") is True
    assert call.call_count == 2
    assert call.call_args.args[0] == method
    assert data["error"] == "write_failed"


def graph(node="n"):
    return {"ok": True, "nodes": [{"fabric_node_id": node, "x": 1, "y": 2}], "edges": []}


@pytest.mark.parametrize("name,args", [
    ("copyTopologyViewNodes", {"source_view_id": "s", "target_view_id": "t"}),
    ("layoutTopologyView", {"view_id": "t", "source_view_id": "s", "action": "move_nodes", "mode": "apply", "params": {"fabric_node_ids": ["n"]}}),
    ("sinkTopologyDualUnits", {"source_view_id": "s", "sink_view_id": "t", "layout_batch": False}),
])
def test_position_failure_preserves_source(name, args):
    def http(method, path, **kwargs):
        if method == "GET":
            return {"ok": True, "data": graph() if path.endswith("/s") else {"nodes": [], "edges": []}}
        raise AssertionError(f"Unexpected mutation {method} {path}")

    with patch.object(tools, "http_json", side_effect=http), patch.object(
        tools, "_ensure_view_max_nodes", return_value={"ok": True}
    ), patch.object(tools, "_add_topology_view_nodes", return_value={"ok": True, "added": 1}), patch.object(
        tools, "_patch_positions_chunked", return_value={"ok": False, "error": "patch_denied", "updated": 0}
    ), patch.object(tools, "_fabric_bridges_into", return_value=[]), patch.object(
        tools, "_remove_topology_view_nodes"
    ) as remove:
        result, data = payload(name, args)
    assert result.get("isError") is True, data
    assert data["error"] == "patch_denied"
    assert data["added"] == 1
    remove.assert_not_called()


def test_move_capacity_error_stays_error():
    with patch.object(tools, "http_json", return_value={"ok": True, "data": graph()}), patch.object(
        tools, "_ensure_view_max_nodes", return_value={"ok": False, "error": "cap_denied"}
    ), patch.object(tools, "_add_topology_view_nodes") as add:
        result, data = payload("layoutTopologyView", {
            "view_id": "t", "source_view_id": "s", "action": "move_nodes", "mode": "apply",
            "params": {"fabric_node_ids": ["n"]},
        })
    assert result.get("isError") is True
    assert data["error"] == "cap_denied"
    add.assert_not_called()


def test_invalid_add_does_not_change_capacity():
    with patch.object(tools, "http_json") as http:
        result, _ = payload("addTopologyViewNodes", {"view_id": "v", "max_nodes": 2000})
    assert result.get("isError") is True
    http.assert_not_called()


@pytest.mark.parametrize("name,args", [
    ("getTopologyView", {"view_id": "v", "sample": 0}),
    ("projectTopologyNeighbors", {"view_id": "v", "sample": 0, "dry_run": True}),
])
def test_zero_sample_and_projection_preview(name, args):
    with patch.object(tools, "http_json", return_value={"ok": True, "data": graph()}) as http:
        result, data = payload(name, args)
    assert result.get("isError") is not True
    assert data["sample_nodes"] == []
    if name == "projectTopologyNeighbors":
        assert http.call_args.kwargs["body"]["dry_run"] is True
        assert data["dry_run"] is True


def test_invalid_projection_detail_does_not_write():
    with patch.object(tools, "http_json") as http:
        result, data = payload("projectTopologyNeighbors", {"view_id": "v", "detail": "raw"})
    assert result.get("isError") is True
    assert data["error"] == "detail_invalid"
    http.assert_not_called()


def test_zero_updated_is_not_reported_as_successful_count():
    with patch.object(tools, "http_json", return_value={"ok": True, "data": {"ok": True, "updated": 0, "skipped_locked": 1}}):
        assert tools._patch_positions_chunked("v", [{"fabric_node_id": "n", "x": 1, "y": 2}])["updated"] == 0


def test_apply_rules_uses_query_flags():
    with patch.object(tools, "http_json", return_value={"ok": True, "data": {"updated": 1}}) as http:
        payload("classifyTopologyFabricNodes", {"action": "apply_rules", "skip_manual": False, "fill_empty_only": True})
    assert http.call_args.args == ("POST", "/v1/topology/classify/apply")
    assert http.call_args.kwargs["params"] == {"skip_manual": False, "fill_empty_only": True}


@pytest.mark.parametrize("action", ["resolve_overlaps", "sink_nodes", "align_to_reference", "compose_views", "compose_orbit"])
def test_removed_layout_actions_are_not_advertised_or_called(action):
    with patch.object(tools, "http_json") as http:
        result, data = payload("layoutTopologyView", {"view_id": "v", "action": action})
    assert result.get("isError") is True
    assert data["error"] == f"unknown_action:{action}"
    assert action not in data["actions"]
    schema = next(t for t in tools.HTTP_MCP_TOOLS if t["name"] == "layoutTopologyView")["inputSchema"]
    assert action not in schema["properties"]["action"]["enum"]
    http.assert_not_called()


@pytest.mark.parametrize("name,args", [
    ("queryTopologyFabricNodes", {"mode": "find"}),
    ("classifyTopologyFabricNodes", {"action": "apply"}),
    ("queryTopologyEdges", {"detail": "raw"}),
])
def test_removed_query_aliases_do_not_call_backend(name, args):
    with patch.object(tools, "http_json") as http:
        result, _ = payload(name, args)
    assert result.get("isError") is True
    http.assert_not_called()


def test_explicit_background_layout_starts_worker():
    canvas = graph()
    canvas["nodes"].append({"fabric_node_id": "n2", "x": 100, "y": 200})
    with patch.object(tools, "http_json", return_value={"ok": True, "data": canvas}), patch.object(
        tools, "start_job", return_value="test-job"
    ) as start, patch.object(tools, "job_public", return_value=None), patch.object(tools, "run_layout_on_graph") as compute:
        result, data = payload("layoutTopologyView", {"view_id": "v", "action": "layout", "mode": "apply", "params": {"background": True}})
    assert result.get("isError") is not True
    assert data["job_id"] == "test-job"
    assert data["applied"] is False
    assert start.call_args.kwargs["tool_args"]["params"]["_force_sync"] is True
    compute.assert_not_called()


def test_background_spawn_error_is_immediately_reported():
    from netx_topology_mcp import layout_jobs as jobs
    canvas = graph()
    canvas["nodes"].append({"fabric_node_id": "n2", "x": 100, "y": 200})
    with patch.object(tools, "http_json", return_value={"ok": True, "data": canvas}), patch.object(
        jobs.subprocess, "Popen", side_effect=OSError("worker unavailable")
    ):
        result, data = payload("layoutTopologyView", {"view_id": "v", "action": "layout", "mode": "apply", "params": {"background": True}})
    assert result.get("isError") is True
    assert data["status"] == "error"
    assert data["error"].startswith("worker_start_failed:")
    assert data["job_id"]


def test_background_apply_poll_and_persistent_view_readback():
    """Execute worker orchestration inline against an in-memory HTTP backend."""
    from types import SimpleNamespace
    from netx_topology_mcp import layout_jobs as jobs
    from netx_topology_mcp.layout_job_worker import main as worker_main

    canvas = {"nodes": [
        {"fabric_node_id": "n", "x": 0, "y": 0},
        {"fabric_node_id": "n2", "x": 100, "y": 200},
    ], "edges": []}
    positions = [
        {"fabric_node_id": "n", "x": 20, "y": 30},
        {"fabric_node_id": "n2", "x": 220, "y": 330},
    ]

    def http(method, path, **kwargs):
        if method == "GET":
            return {"ok": True, "data": canvas}
        assert method == "PATCH" and path == "/v1/topology/views/v/positions"
        canvas["nodes"] = kwargs["body"]["positions"]
        return {"ok": True, "data": {"ok": True, "updated": 2}}

    def spawn(argv, **kwargs):
        assert worker_main(["--job-id", argv[-1]]) == 0
        return SimpleNamespace(pid=123)

    with patch.object(tools, "http_json", side_effect=http), patch.object(
        jobs.subprocess, "Popen", side_effect=spawn
    ), patch.object(tools, "run_layout_on_graph", return_value={"ok": True, "positions": positions, "overlap": {}}):
        _, started = payload("layoutTopologyView", {
            "view_id": "v", "action": "layout", "mode": "apply", "params": {"background": True},
        })
        jobs._JOBS.clear()
        _, polled = payload("layoutTopologyView", {"action": "job_status", "params": {"job_id": started["job_id"]}})
        _, view = payload("getTopologyView", {"view_id": "v", "detail": "full"})
    assert polled["status"] == "done"
    assert polled["result"]["applied"] is True
    assert polled["result"]["updated"] == 2
    assert view["nodes"] == positions


def test_hub_schema_matches_default_exclusion_behavior():
    tool = next(t for t in tools.HTTP_MCP_TOOLS if t["name"] == "suggestSinkHubs")
    assert tool["inputSchema"]["properties"]["use_source_dual_portals"]["default"] is False


@pytest.mark.parametrize("name,args", [
    ("copyTopologyViewNodes", {"source_view_id": "s", "target_view_id": "t"}),
    ("layoutTopologyView", {"view_id": "t", "source_view_id": "s", "action": "move_nodes", "mode": "apply", "params": {"fabric_node_ids": ["n"]}}),
    ("sinkTopologyDualUnits", {"source_view_id": "s", "sink_view_id": "t", "layout_batch": False}),
])
def test_failed_final_readback_reports_completed_writes(name, args):
    with patch.object(tools, "http_json", side_effect=[
        {"ok": True, "data": graph()}, {"ok": True, "data": {"nodes": [], "edges": []}},
        {"ok": False, "error": "read_timeout"}, {"ok": False, "error": "read_timeout"},
    ]), patch.object(tools, "_ensure_view_max_nodes", return_value={"ok": True}), patch.object(
        tools, "_add_topology_view_nodes", return_value={"ok": True, "added": 1}
    ), patch.object(tools, "_patch_positions_chunked", return_value={"ok": True, "updated": 1}), patch.object(
        tools, "_remove_topology_view_nodes", return_value={"ok": True, "removed": 1}
    ), patch.object(tools, "_fabric_bridges_into", return_value=[]):
        result, data = payload(name, args)
    assert result.get("isError") is True
    assert data["error"] == "view_readback_failed"
    assert data["write_completed"] is True
