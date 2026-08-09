"""Composable topology layout atoms + multipass recipes.

Usage:
  state = build_state_from_nodes_edges(nodes, edges)
  state, trace, score = run_recipe(state, "smd_corridor_v1", LayoutParams(...))
  positions = positions_for_api(state)

Scoped / block ops:
  select_scope(mode=component|bbox|layer|ids|all)
  list_blocks(mode=component|hub_territory|leiden|soft|…)
  foreach_blocks in recipes runs sub-passes per block (default=CC).
  Soft partition: layout_ops.partition (hub BFS + optional igraph).
"""

from netx_topology_mcp.layout_ops.graph_util import build_state_from_nodes_edges
from netx_topology_mcp.layout_ops.partition import (
    igraph_available,
    pack_block_centers,
    pack_soft_blocks,
    partition_report,
    partition_soft_blocks,
    resolve_block_mode,
)
from netx_topology_mcp.layout_ops.recipe import (
    RECIPES,
    REGISTRY,
    agg_rings_v1_passes,
    positions_for_api,
    run_recipe,
    smd_corridor_compact_v1_passes,
    smd_corridor_unstick_v1_passes,
    smd_corridor_v1_passes,
)
from netx_topology_mcp.layout_ops.rings import build_ring_skeleton
from netx_topology_mcp.layout_ops.hotspots import fix_overlaps_local, relax_hotspots
from netx_topology_mcp.layout_ops.scope import list_blocks, select_scope
from netx_topology_mcp.layout_ops.score import score_state
from netx_topology_mcp.layout_ops.compose_views import (
    compose_into_state,
    strip_pack_blocks,
)
from netx_topology_mcp.layout_ops.dual_units import (
    find_dual_portal_units,
    layout_dual_unit,
)
from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState, OpResult

__all__ = [
    "LayoutParams",
    "LayoutState",
    "OpResult",
    "REGISTRY",
    "RECIPES",
    "agg_rings_v1_passes",
    "build_ring_skeleton",
    "build_state_from_nodes_edges",
    "compose_into_state",
    "find_dual_portal_units",
    "fix_overlaps_local",
    "igraph_available",
    "layout_dual_unit",
    "list_blocks",
    "pack_block_centers",
    "pack_soft_blocks",
    "partition_report",
    "partition_soft_blocks",
    "positions_for_api",
    "relax_hotspots",
    "resolve_block_mode",
    "run_recipe",
    "score_state",
    "select_scope",
    "strip_pack_blocks",
    "smd_corridor_v1_passes",
    "smd_corridor_compact_v1_passes",
    "smd_corridor_unstick_v1_passes",
]
