"""Tests for layoutTopologyView MCP wrapper."""

from __future__ import annotations

import json
from unittest.mock import patch

from netx_topology_mcp.http_tools import call_http_tool
from netx_topology_mcp.layout_tool import build_params, list_layout_catalog, resolve_recipe, run_layout_on_graph


def test_catalog_and_aliases() -> None:
    cat = list_layout_catalog()
    assert "rings" in cat["recipes"]
    assert "compact" in cat["recipes"]
    assert "corridor" in cat["recipes"]
    assert "unstick" in cat["recipes"]
    assert "compact_soft" not in cat["recipes"]
    assert "ortho_metro" not in cat["recipes"]
    assert "dual_mass" not in cat["recipes"]
    assert "balanced" in cat["presets"]
    assert "fix_overlaps" in cat["actions"]
    assert "orbit_sweep" in cat["actions"]
    assert "polish_crossings" in cat["actions"]
    assert "clear_edge_hits" in cat["actions"]
    assert "mass_merge" not in cat["actions"]
    assert "compose_orbit" not in cat["actions"]
    assert resolve_recipe("rings") == "agg_rings_v1"
    assert resolve_recipe("compact") == "smd_corridor_compact_v1"
    assert resolve_recipe("corridor") == "smd_corridor_v1"
    try:
        resolve_recipe("compact_soft")
        raise AssertionError("compact_soft should be unpublished")
    except ValueError as e:
        assert "unknown_recipe" in str(e)
    p = build_params(preset="dense")
    assert p.target_util >= 0.12


def test_run_layout_on_small_graph() -> None:
    nodes = [
        {"fabric_node_id": "a", "name": "A-EN-1", "x": 0, "y": 0},
        {"fabric_node_id": "b", "name": "B-AN-1", "x": 10, "y": 0},
        {"fabric_node_id": "c", "name": "C-EN-2", "x": 20, "y": 0},
        {"fabric_node_id": "d", "name": "D-EN-3", "x": 30, "y": 0},
    ]
    edges = [
        {"a_node_id": "a", "b_node_id": "b"},
        {"a_node_id": "b", "b_node_id": "c"},
        {"a_node_id": "c", "b_node_id": "d"},
    ]
    out = run_layout_on_graph(nodes, edges, recipe="corridor", preset="balanced")
    assert out["ok"] is True
    assert len(out["positions"]) == 4
    assert "verdict" in out
    assert out["overlap"]["status"] in {"ok", "warn", "fail"}


def test_layout_topology_view_catalog_tool() -> None:
    out = call_http_tool("layoutTopologyView", {"catalog": True})
    assert out.get("isError") is not True
    payload = json.loads(out["content"][0]["text"])
    assert payload["ok"] is True
    assert "recipes" in payload
    assert "rings" in payload["recipes"]
    assert "compact" in payload["recipes"]
    assert "actions" in payload
    assert "fix_overlaps" in payload["actions"]


def test_layout_topology_view_preview() -> None:
    graph = {
        "view": {"id": "v1", "name": "demo", "folder_id": "f1"},
        "nodes": [
            {"fabric_node_id": "a", "name": "A-EN-1", "x": 0, "y": 0},
            {"fabric_node_id": "b", "name": "B-AN-1", "x": 5, "y": 0},
            {"fabric_node_id": "c", "name": "C-EN-2", "x": 5, "y": 5},
        ],
        "edges": [
            {"a_node_id": "a", "b_node_id": "b"},
            {"a_node_id": "b", "b_node_id": "c"},
        ],
    }
    with patch("netx_topology_mcp.http_tools.http_json", return_value={"ok": True, "data": graph}):
        out = call_http_tool(
            "layoutTopologyView",
            {"view_id": "v1", "recipe": "corridor", "preset": "loose", "mode": "preview"},
        )
    assert out.get("isError") is not True
    payload = json.loads(out["content"][0]["text"])
    assert payload["ok"] is True
    assert payload["applied"] is False
    assert payload["mode"] == "preview"
    assert payload["verdict"]["total"] is not None


def test_local_polish_refuses_crossing_regression() -> None:
    """untangle apply must not silently worsen crossings."""
    nodes = [
        {"fabric_node_id": "h", "name": "H-AN-1", "x": 0, "y": 0},
        {"fabric_node_id": "a", "name": "A-EN-1", "x": -200, "y": -40},
        {"fabric_node_id": "b", "name": "B-EN-2", "x": 200, "y": -40},
        {"fabric_node_id": "c", "name": "C-EN-3", "x": -200, "y": 200},
        {"fabric_node_id": "d", "name": "D-EN-4", "x": 200, "y": 200},
        {"fabric_node_id": "e", "name": "E-EN-5", "x": 0, "y": 100},
    ]
    edges = [
        {"a_node_id": "h", "b_node_id": "a"},
        {"a_node_id": "h", "b_node_id": "b"},
        {"a_node_id": "h", "b_node_id": "c"},
        {"a_node_id": "h", "b_node_id": "d"},
        {"a_node_id": "a", "b_node_id": "d"},
        {"a_node_id": "b", "b_node_id": "c"},
        {"a_node_id": "e", "b_node_id": "a"},
        {"a_node_id": "e", "b_node_id": "b"},
    ]
    graph = {"view": {"id": "v1", "name": "demo", "folder_id": "f1"}, "nodes": nodes, "edges": edges}
    bad = run_layout_on_graph(nodes, edges, action="untangle")
    worse = dict(bad)
    worse["crossing"] = {**(bad.get("crossing") or {}), "edge_crossings": 9999}
    worse["positions"] = bad["positions"]

    def fake_http(method, path, body=None, **_kw):
        if method == "GET":
            return {"ok": True, "data": graph}
        raise AssertionError("PATCH must not run on crossing regression")

    with patch("netx_topology_mcp.http_tools.http_json", side_effect=fake_http):
        with patch(
            "netx_topology_mcp.http_tools.run_layout_on_graph",
            return_value=worse,
        ):
            out = call_http_tool(
                "layoutTopologyView",
                {"view_id": "v1", "action": "untangle", "mode": "apply"},
            )
    payload = json.loads(out["content"][0]["text"])
    assert payload.get("ok") is False
    assert payload.get("error") == "crossing_regression"
    assert payload.get("applied") is False


def test_unpublished_action_rejected() -> None:
    out = call_http_tool(
        "layoutTopologyView",
        {"view_id": "v1", "action": "mass_merge", "mode": "preview"},
    )
    payload = json.loads(out["content"][0]["text"])
    assert payload.get("ok") is False
    assert "unknown_action" in str(payload.get("error") or "")
    assert "mass_merge" not in (payload.get("actions") or {})


def test_layout_topology_view_apply_patches() -> None:
    graph = {
        "view": {"id": "v1", "name": "demo", "folder_id": "f1"},
        "nodes": [
            {"fabric_node_id": "a", "name": "A-EN-1", "x": 0, "y": 0},
            {"fabric_node_id": "b", "name": "B-AN-1", "x": 5, "y": 0},
            {"fabric_node_id": "c", "name": "C-EN-2", "x": 5, "y": 5},
        ],
        "edges": [
            {"a_node_id": "a", "b_node_id": "b"},
            {"a_node_id": "b", "b_node_id": "c"},
        ],
    }
    calls: list[tuple] = []

    def fake_http(method, path, body=None, **_kw):
        calls.append((method, path, body))
        if method == "GET":
            return {"ok": True, "data": graph}
        return {"ok": True, "data": {"updated": len((body or {}).get("positions") or [])}}

    with patch("netx_topology_mcp.http_tools.http_json", side_effect=fake_http):
        out = call_http_tool(
            "layoutTopologyView",
            {"view_id": "v1", "recipe": "compact", "preset": "balanced", "mode": "apply"},
        )
    assert out.get("isError") is not True
    payload = json.loads(out["content"][0]["text"])
    assert payload["ok"] is True
    assert payload["applied"] is True
    assert payload["updated"] >= 3
    assert any(m == "PATCH" for m, _p, _b in calls)
