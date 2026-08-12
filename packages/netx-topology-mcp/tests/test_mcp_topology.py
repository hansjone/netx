"""Tests for netx topology HTTP MCP server."""

from __future__ import annotations

import json
import subprocess
import sys
from unittest.mock import patch

from netx_topology_mcp.http_tools import (
    HTTP_MCP_TOOLS,
    call_http_tool,
    resolve_draw_target,
    tools_for_scopes,
)
from netx_topology_mcp.server import _fetch_scopes


def test_tool_list_has_draw_and_query_tools() -> None:
    names = {str(t.get("name") or "") for t in HTTP_MCP_TOOLS}
    assert len(names) == 14
    assert "sinkTopologyDualUnits" in names
    assert "copyTopologyViewNodes" in names
    assert "createTopologyFolder" in names
    assert "createTopologyView" not in names
    assert "listTopologyViews" not in names
    assert "queryTopologyFabricNodes" in names
    assert "listTopologyFabricNodes" not in names
    assert "searchTopologyFabricNodes" not in names
    assert "getTopologyFabricSummary" not in names
    assert "addTopologyViewNodes" in names
    assert "updateTopologyViewPositions" in names
    assert "queryTopologyEdges" in names
    assert "analyzeTopologyViewLayout" in names
    assert "layoutTopologyView" in names
    assert "getTopologyTree" in names
    assert "createTopologyManualEdge" not in names
    assert "populateTopologyView" not in names
    folder_tool = next(t for t in HTTP_MCP_TOOLS if t["name"] == "createTopologyFolder")
    assert "Does not create a canvas" not in str(folder_tool.get("description") or "")
    assert "view_id" in str(folder_tool.get("description") or "")


def test_create_folder_requires_name() -> None:
    out = call_http_tool("createTopologyFolder", {})
    assert out.get("isError") is True
    payload = json.loads(out["content"][0]["text"])
    assert payload["error"] == "name_required"


def test_create_folder_posts_region_and_resolves_view() -> None:
    tree = {
        "root": {
            "id": "sys-root",
            "kind": "root",
            "children": [
                {
                    "id": "f1",
                    "name": "华北",
                    "kind": "region",
                    "views": [],
                    "ne_count": 0,
                    "children": [
                        {
                            "id": "rm1",
                            "name": "根图",
                            "kind": "region",
                            "is_system": True,
                            "ne_count": 0,
                            "views": [{"id": "v-root-map", "name": "根图", "kind": "physical"}],
                            "children": [],
                        }
                    ],
                }
            ],
        }
    }

    def fake_http(method: str, path: str, body: dict | None = None, **_kwargs):
        if method == "POST" and path == "/v1/topology/folders":
            return {"ok": True, "data": {"id": "f1", "name": "华北", "kind": "region"}}
        if method == "GET" and path == "/v1/topology/tree":
            return {"ok": True, "data": tree}
        return {"ok": False, "error": f"unexpected {method} {path}"}

    with patch("netx_topology_mcp.http_tools.http_json", side_effect=fake_http) as mock_http:
        out = call_http_tool(
            "createTopologyFolder",
            {"name": "华北", "sort_order": 1, "locale": "zh"},
        )
        assert mock_http.call_count == 2
        post = mock_http.call_args_list[0]
        assert post[0][0] == "POST"
        assert post[0][1] == "/v1/topology/folders"
        body = post[1]["body"]
        assert body["name"] == "华北"
        assert body["kind"] == "region"
        assert body["sort_order"] == 1
        assert body["locale"] == "zh"
        payload = json.loads(out["content"][0]["text"])
        assert payload["ok"] is True
        assert payload["id"] == "f1"
        assert payload["view_id"] == "v-root-map"
        assert payload["canvas_folder_id"] == "rm1"
        assert "view_id" in str(payload.get("hint") or "")


def test_resolve_draw_target_nested_region() -> None:
    tree = {
        "root": {
            "id": "sys",
            "children": [
                {
                    "id": "nav",
                    "views": [],
                    "children": [
                        {
                            "id": "rm",
                            "name": "Root map",
                            "is_system": True,
                            "views": [{"id": "v0", "kind": "physical"}],
                            "children": [
                                {
                                    "id": "zone",
                                    "ne_count": 3,
                                    "views": [{"id": "vz", "kind": "physical"}],
                                    "children": [],
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    }
    tip = resolve_draw_target(tree, "zone")
    assert tip["view_id"] == "vz"
    assert tip["canvas_folder_id"] == "zone"
    assert tip["ne_count"] == 3


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


def test_create_view_tool_removed() -> None:
    try:
        call_http_tool("createTopologyView", {"name": "map1", "folder_id": "f1"})
        raise AssertionError("createTopologyView should be unregistered")
    except ValueError as e:
        assert "unknown tool" in str(e)


def test_query_edges_defaults_to_adjacency() -> None:
    with patch("netx_topology_mcp.http_tools.http_json") as mock_http:
        mock_http.return_value = {
            "ok": True,
            "data": {
                "total": 2,
                "page": 1,
                "page_size": 100,
                "items": [
                    {
                        "a_node_id": "A",
                        "b_node_id": "B",
                        "a_name": "ne-a",
                        "b_name": "ne-b",
                        "a_ip": "1.1.1.1",
                        "b_ip": "2.2.2.2",
                        "a_port": "p1",
                        "b_port": "p2",
                    },
                    {
                        "a_node_id": "B",
                        "b_node_id": "A",
                        "a_name": "ne-b",
                        "b_name": "ne-a",
                        "a_port": "p3",
                        "b_port": "p4",
                    },
                ],
            },
        }
        out = call_http_tool("queryTopologyEdges", {"node_id": "A", "page_size": 100})
        payload = json.loads(out["content"][0]["text"])
        assert payload["ok"] is True
        assert payload["detail"] == "adjacency"
        assert payload["link_count"] == 1
        assert payload["links"][0]["link_count"] == 2
        assert "items" not in payload
        assert payload["peer_count"] == 1
        assert payload["peers"][0]["node_id"] == "B"
        assert payload["peers"][0]["link_count"] == 2
        assert payload["peers_complete"] is True

        out_ports = call_http_tool(
            "queryTopologyEdges", {"node_id": "A", "detail": "ports", "page_size": 100}
        )
        ports = json.loads(out_ports["content"][0]["text"])
        assert ports["detail"] == "ports"
        assert len(ports["items"]) == 2


def test_tools_for_scopes_filters_write() -> None:
    read_only = {str(t.get("name") or "") for t in tools_for_scopes(["ne:read"])}
    assert "queryTopologyEdges" in read_only
    assert "queryTopologyFabricNodes" in read_only
    assert "createTopologyFolder" not in read_only
    assert "classifyTopologyFabricNodes" not in read_only
    write = {str(t.get("name") or "") for t in tools_for_scopes(["ne:read", "ne:write"])}
    assert "createTopologyView" not in write
    assert "createTopologyFolder" in write
    assert "classifyTopologyFabricNodes" in write


def test_classify_topology_fabric_nodes_match_tag() -> None:
    with patch("netx_topology_mcp.http_tools.http_json") as mock_http:
        mock_http.return_value = {
            "ok": True,
            "data": {
                "pattern": "CORE",
                "match_field": "name",
                "total_matched": 1,
                "samples": [{"id": "n1", "name": "CORE-1", "attrs": {"x": 1}}],
                "fabric_node_ids": ["n1"],
            },
        }
        out = call_http_tool(
            "classifyTopologyFabricNodes",
            {"action": "match", "pattern": "CORE", "match_field": "name"},
        )
        payload = json.loads(out["content"][0]["text"])
        assert payload.get("action") == "match"
        assert payload.get("fabric_node_ids") == ["n1"]
        assert mock_http.call_args[0][:2] == ("POST", "/v1/topology/fabric/nodes/match")

        mock_http.return_value = {
            "ok": True,
            "data": {"dry_run": True, "matched": 1, "updated": 0, "level": 1.0, "samples": []},
        }
        out = call_http_tool(
            "classifyTopologyFabricNodes",
            {
                "action": "tag",
                "fabric_node_ids": ["n1"],
                "level": 1.0,
                "dry_run": True,
            },
        )
        payload = json.loads(out["content"][0]["text"])
        assert payload.get("action") == "tag"
        assert payload.get("dry_run") is True
        assert mock_http.call_args[0][:2] == ("POST", "/v1/topology/fabric/nodes/tags/bulk")
        assert mock_http.call_args[1]["body"]["level"] == 1.0


def test_query_fabric_nodes_modes() -> None:
    with patch("netx_topology_mcp.http_tools.http_json") as mock_http:
        mock_http.return_value = {"ok": True, "data": {"node_count": 3, "edge_count": 2}}
        out = call_http_tool("queryTopologyFabricNodes", {"mode": "summary"})
        payload = json.loads(out["content"][0]["text"])
        assert payload.get("mode") == "summary"
        assert mock_http.call_args[0][1] == "/v1/topology/fabric/summary"

        mock_http.return_value = {
            "ok": True,
            "data": {"items": [{"id": "n1", "name": "A"}], "total": 1, "page": 1, "page_size": 50},
        }
        out = call_http_tool("queryTopologyFabricNodes", {"q": "A"})
        payload = json.loads(out["content"][0]["text"])
        assert payload.get("mode") == "search"
        assert "/search" in mock_http.call_args[0][1]

        out = call_http_tool(
            "queryTopologyFabricNodes",
            {"mode": "list", "keyword": "core", "role": "core"},
        )
        payload = json.loads(out["content"][0]["text"])
        assert payload.get("mode") == "list"
        assert mock_http.call_args[0][1] == "/v1/topology/fabric/nodes"
        assert mock_http.call_args[1]["params"]["keyword"] == "core"


def test_get_topology_tree_compacts_by_default() -> None:
    tree = {
        "root": {
            "id": "r",
            "name": "Network",
            "kind": "root",
            "ne_count": 2,
            "external_ref": "drop-me",
            "views": [],
            "children": [
                {
                    "id": "a",
                    "name": "RootA",
                    "kind": "region",
                    "ne_count": 2,
                    "views": [],
                    "children": [
                        {
                            "id": "rm",
                            "name": "根图",
                            "kind": "region",
                            "is_system": True,
                            "ne_count": 2,
                            "views": [{"id": "v1", "name": "根图", "kind": "physical", "node_count": 2}],
                            "children": [
                                {
                                    "id": "deep",
                                    "name": "Deep",
                                    "kind": "region",
                                    "ne_count": 0,
                                    "views": [],
                                    "children": [],
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    }
    with patch("netx_topology_mcp.http_tools.http_json") as mock_http:
        mock_http.return_value = {"ok": True, "data": tree}
        out = call_http_tool("getTopologyTree", {"max_depth": 1})
        payload = json.loads(out["content"][0]["text"])
        assert payload["compact"] is True
        assert "external_ref" not in payload["root"]
        child = payload["root"]["children"][0]
        assert child["name"] == "RootA"
        # depth 1: grandchildren collapsed
        assert child["children_truncated"] == 1
        assert child["children"] == []


def test_get_topology_view_defaults_to_summary() -> None:
    graph = {
        "view": {"id": "v1", "name": "根图", "folder_id": "f1", "kind": "physical", "node_count": 2},
        "nodes": [
            {"fabric_node_id": "n1", "name": "A", "ip": "1.1.1.1", "x": 1, "y": 2},
            {"fabric_node_id": "n2", "name": "B", "ip": "2.2.2.2", "x": 3, "y": 4},
        ],
        "edges": [{"id": "e1", "a_node_id": "n1", "b_node_id": "n2", "display_label": "huge" * 50}],
        "outside_peers": [{"fabric_node_id": "n3"}],
        "truncated": False,
    }
    with patch("netx_topology_mcp.http_tools.http_json") as mock_http:
        mock_http.return_value = {"ok": True, "data": graph}
        out = call_http_tool("getTopologyView", {"view_id": "v1"})
        payload = json.loads(out["content"][0]["text"])
        assert payload["detail"] == "summary"
        assert payload["node_count"] == 2
        assert payload["edge_count"] == 1
        assert payload["link_count"] == 1
        assert payload["links"][0]["a_node_id"] == "n1"
        assert payload["links"][0]["b_node_id"] == "n2"
        assert "edges" not in payload
        assert len(payload["sample_nodes"]) == 2

        out_full = call_http_tool("getTopologyView", {"view_id": "v1", "detail": "full"})
        full = json.loads(out_full["content"][0]["text"])
        assert full["detail"] == "full"
        assert len(full["edges"]) == 1


def test_project_neighbors_defaults_to_summary() -> None:
    graph = {
        "view": {"id": "v1", "name": "根图", "node_count": 3},
        "nodes": [{"fabric_node_id": "n1", "name": "A", "ip": "", "x": 0, "y": 0}],
        "edges": [],
        "outside_peers": [],
        "truncated": True,
        "truncate_reason": "membership_cap",
    }
    with patch("netx_topology_mcp.http_tools.http_json") as mock_http:
        mock_http.return_value = {"ok": True, "data": graph}
        out = call_http_tool("projectTopologyNeighbors", {"view_id": "v1"})
        payload = json.loads(out["content"][0]["text"])
        assert payload["detail"] == "summary"
        assert payload["projected"] is True
        assert payload["truncated"] is True
        assert "edges" not in payload


def test_project_neighbors_passes_region_folder_and_reports_skips() -> None:
    graph = {
        "view": {"id": "v1", "name": "根图", "node_count": 2},
        "nodes": [{"fabric_node_id": "n1", "name": "A", "ip": "", "x": 0, "y": 0}],
        "edges": [],
        "outside_peers": [],
        "out_of_region_skipped": 3,
        "out_of_region_sample": [
            {"fabric_node_id": "x1", "name": "X", "region_folder_id": "other"}
        ],
    }
    with patch("netx_topology_mcp.http_tools.http_json") as mock_http:
        mock_http.return_value = {"ok": True, "data": graph}
        out = call_http_tool(
            "projectTopologyNeighbors",
            {"view_id": "v1", "region_folder_id": "reg-A"},
        )
        payload = json.loads(out["content"][0]["text"])
        assert payload["projected"] is True
        assert payload["region_folder_id"] == "reg-A"
        assert payload["out_of_region_skipped"] == 3
        assert payload["out_of_region_sample"][0]["fabric_node_id"] == "x1"
        assert mock_http.call_args.kwargs["body"]["region_folder_id"] == "reg-A"


def test_neighborhood_returns_links_not_ports() -> None:
    with patch("netx_topology_mcp.http_tools.http_json") as mock_http:
        mock_http.return_value = {
            "ok": True,
            "data": {
                "center_node_id": "A",
                "depth": 1,
                "nodes": [
                    {
                        "id": "A",
                        "name": "ne-a",
                        "ip": "1.1.1.1",
                        "attrs": {"sources": ["ume"], "huge": "x" * 200},
                        "world_x": 1,
                        "world_y": 2,
                    }
                ],
                "edges": [
                    {
                        "id": "e1",
                        "a_node_id": "A",
                        "b_node_id": "B",
                        "source": "ume",
                        "status": "active",
                        "a_port": "p1",
                        "attrs": {"display_label": "LONG" * 40},
                    },
                    {
                        "id": "e2",
                        "a_node_id": "A",
                        "b_node_id": "B",
                        "source": "ume",
                        "status": "active",
                        "a_port": "p2",
                    },
                ],
            },
        }
        out = call_http_tool("queryTopologyNeighborhood", {"node_id": "A"})
        payload = json.loads(out["content"][0]["text"])
        assert payload["node_count"] == 1
        assert payload["edge_count"] == 2
        assert payload["link_count"] == 1
        assert payload["links"][0]["link_count"] == 2
        assert "edges" not in payload
        assert "attrs" not in payload["nodes"][0]
        assert "world_x" not in payload["nodes"][0]


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
        assert len(tools) == 14
        names = {t["name"] for t in tools}
        assert "createTopologyView" not in names
        assert "listTopologyViews" not in names
        assert "queryTopologyFabricNodes" in names
        assert "createTopologyFolder" in names
        assert "analyzeTopologyViewLayout" in names
        assert "layoutTopologyView" in names
        assert "sinkTopologyDualUnits" in names
        assert "copyTopologyViewNodes" in names
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_layout_metrics_crossing_and_spacing() -> None:
    from netx_topology_mcp.layout_metrics import analyze_positions, grade_layout, segments_properly_intersect

    assert segments_properly_intersect((0, 0), (2, 2), (0, 2), (2, 0))
    assert not segments_properly_intersect((0, 0), (1, 0), (1, 0), (2, 0))  # shared endpoint

    # X crossing
    nodes = [
        {"fabric_node_id": "a", "name": "A", "x": 0, "y": 0},
        {"fabric_node_id": "b", "name": "B", "x": 200, "y": 200},
        {"fabric_node_id": "c", "name": "C", "x": 0, "y": 200},
        {"fabric_node_id": "d", "name": "D", "x": 200, "y": 0},
    ]
    edges = [
        {"a_node_id": "a", "b_node_id": "b"},
        {"a_node_id": "c", "b_node_id": "d"},
    ]
    m = analyze_positions(nodes, edges)
    assert m["edge_crossings"] == 1
    assert m["link_count"] == 2
    g = grade_layout(m)
    assert g["overall"] in {"ok", "warn", "fail"}


def test_analyze_topology_view_layout_single() -> None:
    graph = {
        "view": {"id": "v1", "name": "demo", "folder_id": "f1"},
        "nodes": [
            {"fabric_node_id": "a", "name": "N-A", "x": 0, "y": 0},
            {"fabric_node_id": "b", "name": "N-B", "x": 200, "y": 0},
            {"fabric_node_id": "c", "name": "N-C", "x": 0, "y": 200},
            {"fabric_node_id": "d", "name": "N-D", "x": 200, "y": 200},
        ],
        "edges": [
            {"a_node_id": "a", "b_node_id": "b"},
            {"a_node_id": "b", "b_node_id": "d"},
            {"a_node_id": "d", "b_node_id": "c"},
            {"a_node_id": "c", "b_node_id": "a"},
        ],
    }

    with patch("netx_topology_mcp.http_tools.http_json", return_value={"ok": True, "data": graph}):
        out = call_http_tool("analyzeTopologyViewLayout", {"view_id": "v1"})
    assert out.get("isError") is not True
    payload = json.loads(out["content"][0]["text"])
    assert payload["ok"] is True
    assert payload["size"]["links"] == 4
    assert payload["crossing"]["edge_crossings"] == 0
    assert payload["overlap"]["status"] == "ok"
    assert "sparsity" in payload
    assert "verdict" in payload
    assert "total" in payload["verdict"]
