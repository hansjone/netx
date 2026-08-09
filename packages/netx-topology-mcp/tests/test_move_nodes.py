"""layoutTopologyView action=move_nodes registration + validation."""

from __future__ import annotations

from netx_topology_mcp.http_tools import HTTP_MCP_TOOLS, _move_topology_view_nodes
from netx_topology_mcp.layout_tool import list_layout_catalog


def test_move_nodes_in_layout_schema_and_catalog() -> None:
    tool = next(t for t in HTTP_MCP_TOOLS if t["name"] == "layoutTopologyView")
    enum = tool["inputSchema"]["properties"]["action"]["enum"]
    assert "move_nodes" in enum
    assert "sink_nodes" in enum
    cat = list_layout_catalog()
    assert "move_nodes" in cat["actions"]
    assert "sink_nodes" in cat["actions"]


def test_move_nodes_requires_ids_and_views() -> None:
    assert _move_topology_view_nodes({}).get("error") == "view_id_and_source_view_id_required"
    assert (
        _move_topology_view_nodes(
            {
                "view_id": "a",
                "source_view_id": "a",
                "params": {"fabric_node_ids": ["x"]},
            }
        ).get("error")
        == "source_and_dest_must_differ"
    )
    assert (
        _move_topology_view_nodes(
            {"view_id": "a", "source_view_id": "b", "params": {}}
        ).get("error")
        == "fabric_node_ids_required"
    )
