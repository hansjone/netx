"""Topology fabric node/edge operations (narrow import surface for routers)."""

from __future__ import annotations

from .topology_service import (
    get_discover_job,
    get_fabric_neighborhood,
    get_fabric_summary,
    list_fabric_edges,
    list_fabric_nodes,
    merge_duplicate_fabric_nodes,
    refresh_fabric_stats,
    start_discover_job,
    upsert_fabric_edge,
)

__all__ = [
    "get_discover_job",
    "get_fabric_neighborhood",
    "get_fabric_summary",
    "list_fabric_edges",
    "list_fabric_nodes",
    "merge_duplicate_fabric_nodes",
    "refresh_fabric_stats",
    "start_discover_job",
    "upsert_fabric_edge",
]
