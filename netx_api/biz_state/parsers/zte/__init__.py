"""ZTE ZXROS status parsers — one module per show command.

LLDP uses ``common.lldp_neighbors`` (``show lldp neighbor brief``).
Status tables below are ZTE-specific implementations.
"""

from __future__ import annotations

from typing import Any, Callable

from .arp import is_valid_arp_age, normalize_arp
from .bgp_peer import normalize_bgp_peer
from .bgp_route import normalize_bgp_route
from .config_bgp_peer import normalize_config_bgp_peer
from .config_interface import normalize_config_interface
from .config_isis import normalize_config_isis
from .config_l2vpn_pw import normalize_config_l2vpn_pw
from .config_ospf import normalize_config_ospf
from .config_static_route import normalize_config_static_route
from .config_vrf import normalize_config_vrf
from .evpn_mac import normalize_evpn_mac
from .if_intf import normalize_if_intf, parse_if_intf_vrf_map
from .interface_brief import normalize_interface_brief
from .interface_detail import normalize_interface_detail
from .ip_route import normalize_ip_route
from .ipv6_route import normalize_ipv6_route
from .isis_adjacency import normalize_isis_adjacency
from .l2vpn_mac import normalize_l2vpn_mac
from .l2vpn_pw import normalize_l2vpn_pw
from .nd6_cache import normalize_nd6_cache
from .optical_brief import normalize_optical_brief
from .ospf_neighbor import normalize_ospf_neighbor
from .vrrp import normalize_vrrp

NormalizeFn = Callable[..., list[dict[str, Any]]]

PARSERS: dict[str, NormalizeFn] = {
    "isis_adjacency": normalize_isis_adjacency,
    "interface_brief": normalize_interface_brief,
    "interface_detail": normalize_interface_detail,
    "arp": normalize_arp,
    "if_intf": normalize_if_intf,
    "nd6_cache": normalize_nd6_cache,
    "bgp_peer": normalize_bgp_peer,
    "ospf_neighbor": normalize_ospf_neighbor,
    "vrrp": normalize_vrrp,
    "optical_brief": normalize_optical_brief,
    "bgp_route": normalize_bgp_route,
    "ip_route": normalize_ip_route,
    "ipv6_route": normalize_ipv6_route,
    "l2vpn_pw": normalize_l2vpn_pw,
    "l2vpn_mac": normalize_l2vpn_mac,
    "evpn_mac": normalize_evpn_mac,
    "config_vrf": normalize_config_vrf,
    "config_interface": normalize_config_interface,
    "config_bgp_peer": normalize_config_bgp_peer,
    "config_l2vpn_pw": normalize_config_l2vpn_pw,
    "config_static_route": normalize_config_static_route,
    "config_ospf": normalize_config_ospf,
    "config_isis": normalize_config_isis,
}

__all__ = [
    "PARSERS",
    "is_valid_arp_age",
    "normalize_arp",
    "normalize_bgp_peer",
    "normalize_bgp_route",
    "normalize_config_bgp_peer",
    "normalize_config_interface",
    "normalize_config_isis",
    "normalize_config_l2vpn_pw",
    "normalize_config_ospf",
    "normalize_config_static_route",
    "normalize_config_vrf",
    "normalize_evpn_mac",
    "normalize_if_intf",
    "normalize_interface_brief",
    "normalize_interface_detail",
    "normalize_ip_route",
    "normalize_ipv6_route",
    "normalize_isis_adjacency",
    "normalize_l2vpn_mac",
    "normalize_l2vpn_pw",
    "normalize_nd6_cache",
    "normalize_optical_brief",
    "normalize_ospf_neighbor",
    "normalize_vrrp",
    "parse_if_intf_vrf_map",
]
