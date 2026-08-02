"""Topology folder tree + leaf view operations.

Re-exports view/tree APIs from ``topology_service`` so routers can depend on a
narrower module boundary while the monolith file is gradually split.
"""

from __future__ import annotations

from .topology_service import (
    add_nodes_to_view,
    bootstrap_topology_tree,
    create_folder,
    create_view,
    delete_folder,
    delete_view,
    get_topology_tree,
    get_view_graph,
    list_views,
    patch_view_edge_style,
    patch_view_positions,
    populate_view,
    project_fabric_neighbors_to_view,
    remove_view_nodes,
    update_folder,
    update_view,
)

__all__ = [
    "add_nodes_to_view",
    "bootstrap_topology_tree",
    "create_folder",
    "create_view",
    "delete_folder",
    "delete_view",
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
