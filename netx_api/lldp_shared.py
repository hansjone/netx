"""Shared LLDP parse API (template + TextFSM + normalize).

Topology discovery and biz_state monitoring both call this module.
Downstream flows diverge: Fabric edges vs biz_state batches.
"""

from __future__ import annotations

from .topology_lldp import (
    STUB_PARSER_KEYS,
    VENDOR_LLDP_PROFILES,
    NeighborHit,
    VendorLldpProfile,
    can_discover_lldp,
    get_vendor_profile,
    lldp_command_for_vendor,
    parse_neighbor_output,
    parser_meta,
    pick_neighbor_command,
    resolve_vendor_key,
)

__all__ = [
    "NeighborHit",
    "VendorLldpProfile",
    "VENDOR_LLDP_PROFILES",
    "STUB_PARSER_KEYS",
    "resolve_vendor_key",
    "get_vendor_profile",
    "lldp_command_for_vendor",
    "pick_neighbor_command",
    "can_discover_lldp",
    "parser_meta",
    "parse_neighbor_output",
]
