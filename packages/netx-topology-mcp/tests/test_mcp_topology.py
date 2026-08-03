"""Tests for netx topology HTTP MCP server."""

from __future__ import annotations

import json
import subprocess
import sys
from unittest.mock import patch

from netx_topology_mcp.http_tools import HTTP_MCP_TOOLS, call_http_tool, tools_for_scopes
from netx_topology_mcp.server import _fetch_scopes


def test_tool_list_has_draw_and_query_tools() -> None:
    names = {str(t.get("name") or "") for t in HTTP_MCP_TOOLS}
    assert len(names) == 13
    assert "createTopologyView" in names
    assert "addTopologyViewNodes" in names
    assert "updateTopologyViewPositions" in names
    assert "queryTopologyEdges" in names
    assert "getTopologyTree" in names
    assert "createTopologyManualEdge" not in names
    assert "populateTopologyView" not in names


def test_add_nodes_rejects_managed_ume_ids() -> None:
    out = call_http_tool(
        "addTopologyViewNodes",
        {"view_id": "v1", "managed_ne_ids": ["m1"], "fabric_node_ids": ["f1"]},
    )
    assert out.get("isError") is True
    payload = json.loads(out["content"][0]["text"])
    assert payload["error"] == "fabric_nodes_only"


def test_add_nodes_posts_fabric_ids_only() -> None:
    with patch("netx_topology_mcp.http_tools.http_json") as mock_http:
        mock_http.return_value = {"ok": True, "data": {"ok": True, "added": 2, "return_graph": False}}
        out = call_http_tool(
            "addTopologyViewNodes",
            {"view_id": "v1", "fabric_node_ids": ["f1", "f2"], "layout": "grid"},
        )
        body = mock_http.call_args[1]["body"]
        assert body["fabric_node_ids"] == ["f1", "f2"]
        assert body["managed_ne_ids"] == []
        assert body["ume_ne_ids"] == []
        assert body["return_graph"] is False
        payload = json.loads(out["content"][0]["text"])
        assert payload["ok"] is True


def test_add_nodes_filter_posts_without_ids() -> None:
    with patch("netx_topology_mcp.http_tools.http_json") as mock_http:
        mock_http.return_value = {
            "ok": True,
            "data": {"ok": True, "added": 10, "matched": 40, "next_offset": 10, "truncated": True},
        }
        out = call_http_tool(
            "addTopologyViewNodes",
            {"view_id": "v1", "keyword": "BJ-", "limit": 10, "offset": 0},
        )
        body = mock_http.call_args[1]["body"]
        assert body["keyword"] == "BJ-"
        assert body["fabric_node_ids"] == []
        assert body["return_graph"] is False
        payload = json.loads(out["content"][0]["text"])
        assert payload["added"] == 10
        assert payload["next_offset"] == 10


def test_update_positions_layout_filter() -> None:
    with patch("netx_topology_mcp.http_tools.http_json") as mock_http:
        mock_http.return_value = {"ok": True, "data": {"ok": True, "updated": 5}}
        out = call_http_tool(
            "updateTopologyViewPositions",
            {"view_id": "v1", "layout": "grid", "keyword": "core", "origin_x": 0, "origin_y": 0},
        )
        body = mock_http.call_args[1]["body"]
        assert body["layout"] == "grid"
        assert body["keyword"] == "core"
        assert body["return_graph"] is False
        payload = json.loads(out["content"][0]["text"])
        assert payload["updated"] == 5


def test_create_view_requires_folder() -> None:
    out = call_http_tool("createTopologyView", {"name": "map1"})
    assert out.get("isError") is True
    payload = json.loads(out["content"][0]["text"])
    assert payload["error"] == "folder_id_required"


def test_create_view_posts_body() -> None:
    with patch("netx_topology_mcp.http_tools.http_json") as mock_http:
        mock_http.return_value = {"ok": True, "data": {"id": "v1", "name": "map1"}}
        out = call_http_tool(
            "createTopologyView",
            {"name": "map1", "folder_id": "f1", "kind": "custom"},
        )
        mock_http.assert_called_once()
        assert mock_http.call_args[0][0] == "POST"
        assert mock_http.call_args[0][1] == "/v1/topology/views"
        body = mock_http.call_args[1]["body"]
        assert body["name"] == "map1"
        assert body["folder_id"] == "f1"
        payload = json.loads(out["content"][0]["text"])
        assert payload["ok"] is True


def test_query_edges_enriches_peers() -> None:
    with patch("netx_topology_mcp.http_tools.http_json") as mock_http:
        mock_http.return_value = {
            "ok": True,
            "data": {
                "total": 1,
                "items": [
                    {
                        "a_node_id": "A",
                        "b_node_id": "B",
                        "a_name": "ne-a",
                        "b_name": "ne-b",
                        "a_ip": "1.1.1.1",
                        "b_ip": "2.2.2.2",
                    }
                ],
            },
        }
        out = call_http_tool("queryTopologyEdges", {"node_id": "A", "page_size": 100})
        payload = json.loads(out["content"][0]["text"])
        assert payload["ok"] is True
        assert payload["peer_count"] == 1
        assert payload["peers"][0]["node_id"] == "B"
        assert payload["peers_complete"] is True


def test_tools_for_scopes_filters_write() -> None:
    read_only = {str(t.get("name") or "") for t in tools_for_scopes(["ne:read"])}
    assert "queryTopologyEdges" in read_only
    assert "createTopologyView" not in read_only
    write = {str(t.get("name") or "") for t in tools_for_scopes(["ne:read", "ne:write"])}
    assert "createTopologyView" in write


def test_fetch_scopes_unwraps_envelope() -> None:
    with patch("netx_topology_mcp.server.http_json") as mock_http:
        mock_http.return_value = {"ok": True, "data": {"scopes": ["ne:read", "ne:write"]}}
        assert _fetch_scopes() == ["ne:read", "ne:write"]


def test_stdio_initialize_and_tools_list() -> None:
    import os

    # Force scopes fetch to fail so tools/list returns the full catalog.
    env = os.environ.copy()
    env["NETX_API_URL"] = "http://127.0.0.1:1"
    env.pop("NETX_API_TOKEN", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "netx_topology_mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    assert proc.stdin and proc.stdout
    try:
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n")
        proc.stdin.flush()
        init_resp = json.loads(proc.stdout.readline())
        assert init_resp["result"]["serverInfo"]["name"] == "netx-topology-mcp"

        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + "\n")
        proc.stdin.flush()
        list_resp = json.loads(proc.stdout.readline())
        tools = list_resp["result"]["tools"]
        assert len(tools) == 13
    finally:
        proc.terminate()
        proc.wait(timeout=5)
