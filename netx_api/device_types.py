"""Netmiko device_type values supported for managed network elements."""

from __future__ import annotations

SUPPORTED_DEVICE_TYPES: tuple[str, ...] = (
    "zte_zxros",
    "alcatel_aos",
    "alcatel_sros",
    "cisco_asa",
    "cisco_ftd",
    "cisco_ios",
    "cisco_nxos",
    "cisco_s200",
    "cisco_s300",
    "cisco_tp",
    "cisco_viptela",
    "cisco_wlc",
    "cisco_xe",
    "cisco_xr",
    "ericsson_ipos",
    "ericsson_mltn63",
    "ericsson_mltn66",
    "huawei",
    "huawei_smartax",
    "huawei_olt",
    "huawei_vrp",
    "huawei_vrpv8",
    "juniper",
    "juniper_junos",
    "juniper_screenos",
    "mikrotik_routeros",
    "mikrotik_switchos",
    "nokia_sros",
    "nokia_srl",
    "ruijie_os",
)

SUPPORTED_VENDORS: tuple[str, ...] = (
    "ZTE",
    "Huawei",
    "Cisco",
    "H3C",
    "Juniper",
    "Nokia",
    "Other",
)

# WebCRT "New Session": inventory types + raw interactive hosts (generic/linux).
WEBCRT_DEVICE_TYPES: tuple[str, ...] = SUPPORTED_DEVICE_TYPES + ("linux", "generic")

# ManagedNE.source value for sessions created via WebCRT Quick Connect.
WEBCRT_NE_SOURCE = "webcrt"

# ManagedNE.source for LLDP-discovered peers not yet in inventory (SSH shell, empty creds).
LLDP_DISCOVERED_NE_SOURCE = "lldp"
