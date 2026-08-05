"""Topology folder tree and leaf view operations (facade)."""
from __future__ import annotations

from .topology_views_graph import (
    _place_fabric_ids_on_view,
    add_nodes_to_view,
    create_topology_placeholder_on_view,
    get_view_graph,
    patch_view_edge_style,
    patch_view_positions,
    populate_view,
    project_fabric_neighbors_to_view,
    remove_view_nodes,
)
from .topology_views_tree import (
    bootstrap_topology_tree,
    create_folder,
    create_view,
    delete_folder,
    delete_view,
    ensure_region_physical_view,
    get_topology_tree,
    list_views,
    update_folder,
    update_view,
)

__all__ = [
    "_place_fabric_ids_on_view",
    "add_nodes_to_view",
    "bootstrap_topology_tree",
    "create_folder",
    "create_topology_placeholder_on_view",
    "create_view",
    "delete_folder",
    "delete_view",
    "ensure_region_physical_view",
    "get_topology_tree",
    "get_view_graph",
    "list_views",
    "patch_view_edge_style",
    "patch_view_positions",
    "populate_view",
    "project_fabric_neighbors_to_view",
    "remove_view_nodes",
    "update_folder",
    "update_view",
]
