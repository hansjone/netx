"""Regex-based fabric role/region classification + slice map generation."""
from __future__ import annotations

from .topology_classify_apply import (
    apply_classify,
    apply_classify_empty_only,
    bulk_tag_fabric_nodes,
    list_unmatched,
    match_fabric_nodes,
    patch_fabric_node_tags,
    preview_classify,
)
from .topology_classify_rules import create_rule, delete_rule, list_rules, update_rule
from .topology_classify_slices import (
    generate_slices,
    preview_slices,
    search_fabric_nodes_with_views,
)

__all__ = [
    "apply_classify",
    "apply_classify_empty_only",
    "bulk_tag_fabric_nodes",
    "create_rule",
    "delete_rule",
    "generate_slices",
    "list_rules",
    "list_unmatched",
    "match_fabric_nodes",
    "patch_fabric_node_tags",
    "preview_classify",
    "preview_slices",
    "search_fabric_nodes_with_views",
    "update_rule",
]
