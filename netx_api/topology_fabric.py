"""Fabric nodes/edges, stats, inventory matching, and merge (facade)."""
from __future__ import annotations

from .topology_fabric_links import (
    _apply_missing_and_purge,
    _mark_replaced_port_peers,
    merge_duplicate_fabric_nodes,
    upsert_fabric_edge,
)
from .topology_fabric_nodes import (
    _edge_out,
    _node_out,
    _nodes_by_ids,
    ensure_fabric_node_for_managed,
    ensure_fabric_node_for_ume,
    get_fabric_neighborhood,
    get_fabric_summary,
    list_fabric_edges,
    list_fabric_nodes,
    refresh_fabric_stats,
)
from .topology_fabric_peers import (
    _FabricPeerIndex,
    _fabric_match_score,
    _is_inventory_node,
    _match_hit_to_fabric_node,
    ensure_lldp_discovered_managed_ne,
)

__all__ = [
    "_FabricPeerIndex",
    "_apply_missing_and_purge",
    "_edge_out",
    "_fabric_match_score",
    "_is_inventory_node",
    "_mark_replaced_port_peers",
    "_match_hit_to_fabric_node",
    "_node_out",
    "_nodes_by_ids",
    "ensure_fabric_node_for_managed",
    "ensure_fabric_node_for_ume",
    "ensure_lldp_discovered_managed_ne",
    "get_fabric_neighborhood",
    "get_fabric_summary",
    "list_fabric_edges",
    "list_fabric_nodes",
    "merge_duplicate_fabric_nodes",
    "refresh_fabric_stats",
    "upsert_fabric_edge",
]
