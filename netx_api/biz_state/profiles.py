"""ParseProfile registry: command template + parser + schema (code as source of truth).

Display fields may be overridden at runtime via biz_state_command_override (hot edit).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .enrich import EnrichJoin


@dataclass(frozen=True)
class FieldDef:
    name: str
    dtype: str = "str"  # str|int|float|bool
    nullable: bool = True
    indexed: bool = False
    is_key: bool = False
    is_interface: bool = False
    role: str = "identity"  # identity|state|counter|meta
    display_name: str = ""
    from_command_param: bool = False
    length: int = 256


@dataclass(frozen=True)
class PlaceholderDef:
    name: str
    schema_field: str
    required: bool = True
    bind_mode: str = "manual_text"  # discover_select | manual_text
    discover_profile_id: str = ""
    discover_value_field: str = ""
    discover_label_field: str = ""
    # When required=False and no bindings: collect may expand all discover values.
    discover_filter_field: str = ""
    discover_filter_contains: str = ""
    # Skip rows where this field is empty (e.g. CE peers require non-empty vrf).
    discover_require_nonempty: str = ""


@dataclass(frozen=True)
class AuxCommand:
    """Secondary collect: only key + profile_id (rest from that profile)."""

    key: str
    profile_id: str


@dataclass
class ParseProfile:
    profile_id: str
    vendor_key: str  # zte|huawei|cisco|... or "*" for all
    metric_id: str
    parser_id: str
    title: str
    command_template: str
    match: str  # regex with optional named groups
    textfsm_command: str = ""
    description: str = ""
    sample_output: str = ""
    placeholders: list[PlaceholderDef] = field(default_factory=list)
    fields: list[FieldDef] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    sort_order: int = 100
    enabled: bool = True
    kind: str = "collect"  # collect | discover
    aux_commands: list[AuxCommand] = field(default_factory=list)
    enrich_joins: list[EnrichJoin] = field(default_factory=list)
    # light: default shared SSH lane; heavy: dedicated long-timeout connection
    collect_lane: str = "light"


_LLDP_FIELDS: list[FieldDef] = [
    FieldDef("local_if", length=128, indexed=True, is_key=True, is_interface=True, display_name="本端接口"),
    FieldDef("remote_sys", length=256, indexed=True, is_key=True, display_name="对端系统名"),
    FieldDef("remote_if", length=128, is_key=True, display_name="对端接口"),
    FieldDef("remote_ip", length=128, role="meta", display_name="对端管理IP"),
    FieldDef("protocol", length=32, role="meta", display_name="协议"),
]


def _lldp_profiles() -> list[ParseProfile]:
    """One logical LLDP collect profile per vendor_key (commands differ)."""
    from ..lldp_shared import VENDOR_LLDP_PROFILES, STUB_PARSER_KEYS

    out: list[ParseProfile] = []
    order = 10
    for key, vp in VENDOR_LLDP_PROFILES.items():
        if key in STUB_PARSER_KEYS and key != "nokia":
            # Still register so UI can show; parse may return empty until templates exist.
            pass
        cmd = vp.lldp_command
        # Escape for regex; ZTE (and others) may append "| one-line" on the template.
        base_cmd = re.sub(r"\s*\|\s*one-line\s*$", "", cmd, flags=re.I).strip() or cmd
        escaped = r"\s+".join(re.escape(p) for p in base_cmd.split() if p)
        out.append(
            ParseProfile(
                profile_id=f"{key}.lldp_neighbors",
                vendor_key=key,
                metric_id="lldp_neighbor",
                parser_id="lldp_neighbors",
                title="LLDP Neighbors",
                command_template=cmd,
                match=rf"(?i)^\s*{escaped}(?:\s*\|\s*one-line)?\s*$",
                textfsm_command=base_cmd,
                description=vp.notes or "LLDP neighbor table snapshot for cutover compare.",
                sample_output="",
                placeholders=[],
                fields=list(_LLDP_FIELDS),
                tags=["lldp", "l2"],
                sort_order=order,
                enabled=key not in ("ericsson", "generic"),
                kind="collect",
            )
        )
        order += 10
    # AOS uses a different command than generic nokia profile
    out.append(
        ParseProfile(
            profile_id="nokia_aos.lldp_neighbors",
            vendor_key="nokia",
            metric_id="lldp_neighbor",
            parser_id="lldp_neighbors",
            title="LLDP Neighbors (AOS)",
            command_template="show lldp remote-system",
            match=r"(?i)^\s*show\s+lldp\s+remote-system\s*$",
            textfsm_command="show lldp remote-system",
            description="Alcatel AOS LLDP remote-system.",
            fields=list(_LLDP_FIELDS),
            tags=["lldp", "l2", "aos"],
            sort_order=95,
            enabled=True,
            kind="collect",
        )
    )
    return out


def _vrf_profiles() -> list[ParseProfile]:
    """Discover VRF list for parameterized collect bindings (e.g. BGP VRF)."""
    discover_cmds: dict[str, tuple[str, str, str]] = {
        # vendor_key: (command, match, textfsm_command)
        "cisco": ("show vrf", r"(?i)^\s*show\s+vrf\s*$", "show vrf"),
        "huawei": (
            "display ip vpn-instance",
            r"(?i)^\s*display\s+ip\s+vpn-instance\s*$",
            "display ip vpn-instance",
        ),
        "h3c": (
            "display ip vpn-instance",
            r"(?i)^\s*display\s+ip\s+vpn-instance\s*$",
            "display ip vpn-instance",
        ),
        "zte": (
            "show ip vrf | one-line",
            r"(?i)^\s*show\s+ip\s+vrf(?:\s*\|\s*one-line)?\s*$",
            "show ip vrf",
        ),
    }
    out: list[ParseProfile] = []
    order = 200
    for key, (cmd, match, fsm_cmd) in discover_cmds.items():
        out.append(
            ParseProfile(
                profile_id=f"{key}.vrf_list",
                vendor_key=key,
                metric_id="vrf_list",
                parser_id="vrf_list",
                title="VRF / VPN-Instance List",
                command_template=cmd,
                match=match,
                textfsm_command=fsm_cmd,
                description="Discover VRF names for parameterized collect bindings.",
                placeholders=[],
                fields=[
                    FieldDef("vrf_name", length=128, indexed=True, is_key=True, display_name="VRF"),
                    FieldDef("rd", length=64, role="meta", display_name="RD"),
                    FieldDef("protocols", length=64, role="meta", display_name="协议"),
                    FieldDef("vrf_id", length=32, role="meta", display_name="VRF ID"),
                ],
                tags=["vrf", "discover"],
                sort_order=order,
                enabled=True,
                kind="discover",
            )
        )
        order += 5
    return out


# --- ZTE ZXROS status tables (cutover monitoring + compare) ---

_ISIS_FIELDS: list[FieldDef] = [
    FieldDef("process_id", length=32, indexed=True, is_key=True, display_name="Process ID"),
    FieldDef("interface", length=128, indexed=True, is_key=True, is_interface=True, display_name="接口"),
    FieldDef("system_id", length=128, indexed=True, is_key=True, display_name="System ID"),
    FieldDef("state", length=32, role="state", display_name="状态"),
    FieldDef("lev", length=16, role="state", display_name="Level"),
    FieldDef("holds", length=32, role="meta", display_name="Holds"),
    FieldDef("snpa", length=64, role="meta", display_name="SNPA"),
    FieldDef("pri", length=16, role="meta", display_name="Pri"),
    FieldDef("mt", length=16, role="meta", display_name="MT"),
    FieldDef("nsf", length=32, role="meta", display_name="NSF"),
    FieldDef("af", length=64, role="state", display_name="AF"),
]

_IFACE_BRIEF_FIELDS: list[FieldDef] = [
    FieldDef("interface", length=128, indexed=True, is_key=True, is_interface=True, display_name="接口"),
    FieldDef("attribute", length=64, role="meta", display_name="属性"),
    FieldDef("mode", length=64, role="meta", display_name="模式"),
    FieldDef("bw", length=32, role="meta", display_name="带宽"),
    FieldDef("admin", length=16, role="state", display_name="Admin"),
    FieldDef("phy", length=16, role="state", display_name="Phy"),
    FieldDef("prot", length=16, role="state", display_name="Prot"),
    FieldDef("description", length=256, role="meta", display_name="描述"),
]

_IFACE_DETAIL_FIELDS: list[FieldDef] = [
    FieldDef("interface", length=128, indexed=True, is_key=True, is_interface=True, display_name="接口"),
    FieldDef("port_status", length=32, role="state", display_name="Port Status"),
    # Rates churn between collects — display only, never default-compare.
    FieldDef("input_bps", length=32, role="counter", display_name="Input bps"),
    FieldDef("output_bps", length=32, role="counter", display_name="Output bps"),
    FieldDef("in_util", length=16, role="counter", display_name="In Util%"),
    FieldDef("out_util", length=16, role="counter", display_name="Out Util%"),
    FieldDef("bw", length=64, role="meta", display_name="BW"),
    FieldDef("ip_mtu", length=16, role="meta", display_name="IP MTU"),
    FieldDef("mtu", length=16, role="meta", display_name="MTU"),
    FieldDef("mpls_mtu", length=16, role="meta", display_name="MPLS MTU"),
    FieldDef("ipv6_mtu", length=16, role="meta", display_name="IPv6 MTU"),
    FieldDef("ifindex", length=32, role="meta", display_name="ifindex"),
    FieldDef("description", length=256, role="meta", display_name="描述"),
    FieldDef("port_media", length=32, role="meta", display_name="Media"),
    FieldDef("negotiation", length=32, role="meta", display_name="Negotiation"),
    FieldDef("rate_period", length=16, role="meta", display_name="Rate period"),
]

_ARP_FIELDS: list[FieldDef] = [
    FieldDef("ip", length=64, indexed=True, is_key=True, display_name="IP"),
    FieldDef("interface", length=128, indexed=True, is_key=True, is_interface=True, display_name="接口"),
    FieldDef("mac", length=64, role="state", display_name="MAC"),
    FieldDef("age", length=32, role="meta", display_name="Age"),
    FieldDef("entry_type", length=16, role="meta", display_name="类型"),
    FieldDef("vrf", length=128, role="meta", display_name="VRF"),
    FieldDef("exter_vlan", length=32, role="meta", display_name="Exter VLAN"),
    FieldDef("inter_vlan", length=32, role="meta", display_name="Inter VLAN"),
    FieldDef("sub_interface", length=128, role="meta", display_name="Sub-IF"),
]

_IF_INTF_FIELDS: list[FieldDef] = [
    FieldDef("interface", length=128, indexed=True, is_key=True, is_interface=True, display_name="接口"),
    FieldDef("vrf", length=128, indexed=True, is_key=True, display_name="VRF"),
]

_ND6_FIELDS: list[FieldDef] = [
    FieldDef("address", length=128, indexed=True, is_key=True, display_name="IPv6"),
    FieldDef("interface", length=128, indexed=True, is_key=True, is_interface=True, display_name="接口"),
    FieldDef("link_address", length=64, role="state", display_name="Link-Address"),
    FieldDef("status", length=32, role="state", display_name="Status"),
    FieldDef("type", length=32, role="meta", display_name="Type"),
    FieldDef("age", length=64, role="meta", display_name="Age"),
]

_BGP_PEER_FIELDS: list[FieldDef] = [
    FieldDef("afi", length=32, indexed=True, is_key=True, display_name="AFI", from_command_param=True),
    FieldDef("vrf", length=128, indexed=True, is_key=True, display_name="VRF", from_command_param=True),
    FieldDef("neighbor", length=128, indexed=True, is_key=True, display_name="Neighbor"),
    FieldDef("as_num", length=16, role="state", display_name="AS"),
    FieldDef("state", length=64, role="state", display_name="State"),
    # Prefix count churns between collects — display only for cutover compare.
    FieldDef("pfx_rcd", length=32, role="counter", display_name="PfxRcd"),
    FieldDef("ver", length=8, role="meta", display_name="Ver"),
    FieldDef("msg_rcvd", length=32, role="meta", display_name="MsgRcvd"),
    FieldDef("msg_send", length=32, role="meta", display_name="MsgSend"),
    FieldDef("up_down", length=32, role="meta", display_name="Up/Down"),
    # From config_vrf enrich (VRF summary aux).
    FieldDef("rd", length=64, role="meta", display_name="RD"),
    FieldDef("address_families", length=64, role="meta", display_name="AF"),
]

# BGP VRF summary still binds VRF from config VRF intent.
_VRF_PLACEHOLDER_IPV4 = PlaceholderDef(
    name="vrf",
    schema_field="vrf",
    required=True,
    bind_mode="discover_select",
    discover_profile_id="zte.config_vrf",
    discover_value_field="vrf_name",
    discover_label_field="vrf_name",
    discover_filter_field="address_families",
    discover_filter_contains="ipv4",
)

_VRF_PLACEHOLDER_IPV6 = PlaceholderDef(
    name="vrf",
    schema_field="vrf",
    required=True,
    bind_mode="discover_select",
    discover_profile_id="zte.config_vrf",
    discover_value_field="vrf_name",
    discover_label_field="vrf_name",
    discover_filter_field="address_families",
    discover_filter_contains="ipv6",
)

# BGP neighbor in/out: discover peers from Config BGP Peer Intent, filtered by AF.
_BGP_NEIGHBOR_VPNV4 = PlaceholderDef(
    name="neighbor",
    schema_field="neighbor",
    required=True,
    bind_mode="discover_select",
    discover_profile_id="zte.config_bgp_peer",
    discover_value_field="neighbor",
    discover_label_field="neighbor",
    discover_filter_field="afi",
    discover_filter_contains="vpnv4",
)

_BGP_NEIGHBOR_VPNV6 = PlaceholderDef(
    name="neighbor",
    schema_field="neighbor",
    required=True,
    bind_mode="discover_select",
    discover_profile_id="zte.config_bgp_peer",
    discover_value_field="neighbor",
    discover_label_field="neighbor",
    discover_filter_field="afi",
    discover_filter_contains="vpnv6",
)

# Per-VRF CE peers live under address-family ipv4/ipv6 vrf <name>.
_BGP_VRF_PEER_IPV4 = PlaceholderDef(
    name="vrf",
    schema_field="vrf",
    required=True,
    bind_mode="discover_select",
    discover_profile_id="zte.config_bgp_peer",
    discover_value_field="vrf",
    discover_label_field="vrf",
    discover_filter_field="afi",
    discover_filter_contains="ipv4",
    discover_require_nonempty="neighbor",
)

_BGP_NEIGHBOR_IPV4_VRF = PlaceholderDef(
    name="neighbor",
    schema_field="neighbor",
    required=True,
    bind_mode="discover_select",
    discover_profile_id="zte.config_bgp_peer",
    discover_value_field="neighbor",
    discover_label_field="neighbor",
    discover_filter_field="afi",
    discover_filter_contains="ipv4",
    discover_require_nonempty="vrf",
)

_BGP_VRF_PEER_IPV6 = PlaceholderDef(
    name="vrf",
    schema_field="vrf",
    required=True,
    bind_mode="discover_select",
    discover_profile_id="zte.config_bgp_peer",
    discover_value_field="vrf",
    discover_label_field="vrf",
    discover_filter_field="afi",
    discover_filter_contains="ipv6",
    discover_require_nonempty="neighbor",
)

_BGP_NEIGHBOR_IPV6_VRF = PlaceholderDef(
    name="neighbor",
    schema_field="neighbor",
    required=True,
    bind_mode="discover_select",
    discover_profile_id="zte.config_bgp_peer",
    discover_value_field="neighbor",
    discover_label_field="neighbor",
    discover_filter_field="afi",
    discover_filter_contains="ipv6",
    discover_require_nonempty="vrf",
)

_OSPF_FIELDS: list[FieldDef] = [
    FieldDef("process_id", length=32, indexed=True, is_key=True, display_name="Process ID"),
    FieldDef("neighbor_id", length=64, indexed=True, is_key=True, display_name="Neighbor ID"),
    FieldDef("interface", length=128, indexed=True, is_key=True, is_interface=True, display_name="接口"),
    FieldDef("state", length=64, role="state", display_name="State"),
    FieldDef("address", length=64, role="state", display_name="Address"),
    FieldDef("dead_time", length=32, role="meta", display_name="DeadTime"),
    FieldDef("pri", length=16, role="meta", display_name="Pri"),
]

_VRRP_FIELDS: list[FieldDef] = [
    FieldDef("af", length=16, indexed=True, is_key=True, display_name="AF", from_command_param=True),
    FieldDef("interface", length=128, indexed=True, is_key=True, is_interface=True, display_name="接口"),
    FieldDef("vr_id", length=32, indexed=True, is_key=True, display_name="VR ID"),
    FieldDef("state", length=32, role="state", display_name="State"),
    FieldDef("priority", length=16, role="state", display_name="Priority"),
    FieldDef("master_addr", length=128, role="state", display_name="Master"),
    FieldDef("vrouter_addr", length=128, role="state", display_name="VRouter"),
    FieldDef("time", length=16, role="meta", display_name="Time"),
    FieldDef("flags", length=16, role="meta", display_name="Flags"),
]

_OPTICAL_FIELDS: list[FieldDef] = [
    FieldDef("interface", length=128, indexed=True, is_key=True, is_interface=True, display_name="接口"),
    FieldDef("optic_type", length=64, role="meta", display_name="Type"),
    FieldDef("wavelength", length=32, role="meta", display_name="Wavelength"),
    # Optical power jitters — display only; compare status for cutover.
    FieldDef("rx_power", length=128, role="counter", display_name="RxPower"),
    FieldDef("rx_threshold", length=128, role="meta", display_name="Rx Threshold"),
    FieldDef("tx_power", length=128, role="counter", display_name="TxPower"),
    FieldDef("tx_threshold", length=128, role="meta", display_name="Tx Threshold"),
    FieldDef("status", length=32, role="state", display_name="Status"),
]

_BGP_ROUTE_FIELDS: list[FieldDef] = [
    FieldDef("afi", length=32, indexed=True, is_key=True, display_name="AFI", from_command_param=True),
    FieldDef("vrf", length=128, indexed=True, is_key=True, display_name="VRF", from_command_param=True),
    FieldDef("neighbor", length=128, indexed=True, is_key=True, display_name="Neighbor", from_command_param=True),
    FieldDef("direction", length=8, indexed=True, is_key=True, display_name="Dir", from_command_param=True),
    FieldDef("network", length=128, indexed=True, is_key=True, display_name="Network"),
    FieldDef("next_hop", length=128, role="state", display_name="NextHop"),
    FieldDef("metric", length=32, role="meta", display_name="Metric"),
    FieldDef("loc_prf", length=32, role="meta", display_name="LocPrf"),
    FieldDef("tag", length=32, role="meta", display_name="Tag"),
    FieldDef("path", length=256, role="state", display_name="Path"),
    FieldDef("status_codes", length=16, role="meta", display_name="Codes"),
    FieldDef("as_num", length=16, role="state", display_name="AS"),
    FieldDef("state", length=64, role="state", display_name="PeerState"),
    FieldDef("pfx_rcd", length=32, role="meta", display_name="PfxRcd"),
]

_IP_ROUTE_FIELDS: list[FieldDef] = [
    FieldDef("vrf", length=128, indexed=True, is_key=True, display_name="VRF", from_command_param=True),
    FieldDef("dest", length=64, indexed=True, is_key=True, display_name="Dest"),
    FieldDef("gateway", length=64, indexed=True, is_key=True, display_name="Gateway"),
    FieldDef("interface", length=128, is_key=True, is_interface=True, display_name="接口"),
    FieldDef("owner", length=64, role="state", display_name="Owner"),
    FieldDef("pri", length=16, role="meta", display_name="Pri"),
    FieldDef("metric", length=32, role="meta", display_name="Metric"),
    FieldDef("flags", length=16, role="meta", display_name="Flags"),
    FieldDef("rd", length=64, role="meta", display_name="RD"),
    FieldDef("address_families", length=64, role="meta", display_name="AF"),
]

_IPV6_ROUTE_FIELDS: list[FieldDef] = [
    FieldDef("vrf", length=128, indexed=True, is_key=True, display_name="VRF", from_command_param=True),
    FieldDef("dest", length=128, indexed=True, is_key=True, display_name="Dest"),
    FieldDef("gateway", length=128, indexed=True, is_key=True, display_name="Gateway"),
    FieldDef("interface", length=128, is_key=True, is_interface=True, display_name="接口"),
    FieldDef("owner", length=64, role="state", display_name="Owner"),
    FieldDef("pri", length=16, role="meta", display_name="Pri"),
    FieldDef("metric", length=32, role="meta", display_name="Metric"),
    FieldDef("flags", length=16, role="meta", display_name="Flags"),
    FieldDef("rd", length=64, role="meta", display_name="RD"),
    FieldDef("address_families", length=64, role="meta", display_name="AF"),
]

_L2VPN_PW_FIELDS: list[FieldDef] = [
    FieldDef("pw_name", length=128, indexed=True, is_key=True, display_name="PW"),
    FieldDef("peer", length=64, indexed=True, is_key=True, display_name="Peer"),
    FieldDef("state", length=32, role="state", display_name="State"),
    FieldDef("fec", length=32, role="meta", display_name="FEC"),
    FieldDef("pw_type", length=64, role="meta", display_name="PWType"),
    FieldDef("local_label", length=32, role="meta", display_name="Llabel"),
    FieldDef("remote_label", length=32, role="meta", display_name="Rlabel"),
    FieldDef("vpn_owner", length=256, role="meta", display_name="VPNOwner"),
]

_L2VPN_PW_DETAIL_FIELDS: list[FieldDef] = [
    FieldDef("pw_name", length=128, indexed=True, is_key=True, display_name="PW"),
    FieldDef("peer", length=64, indexed=True, is_key=True, display_name="Peer"),
    FieldDef("vcid", length=64, indexed=True, is_key=True, display_name="VCID"),
    FieldDef("vc_status", length=32, role="state", display_name="VC Status"),
    FieldDef("remote_status", length=32, role="state", display_name="Remote"),
    FieldDef("activation_status", length=32, role="state", display_name="Activation"),
    FieldDef("service_instance_type", length=32, role="meta", display_name="Service Type"),
    FieldDef("service_instance", length=256, role="meta", display_name="Service"),
    FieldDef("conn_mode", length=32, role="meta", display_name="Mode"),
    FieldDef("signaling", length=32, role="meta", display_name="Signaling"),
    FieldDef("vc_type", length=32, role="meta", display_name="VC Type"),
    FieldDef("control_word", length=32, role="meta", display_name="CW"),
    FieldDef("local_label", length=32, role="meta", display_name="Local Label"),
    FieldDef("remote_label", length=32, role="meta", display_name="Remote Label"),
    FieldDef("tunnel_dest", length=64, role="meta", display_name="Tunnel Dest"),
    FieldDef("related_if", length=128, is_interface=True, role="meta", display_name="Related IF"),
    FieldDef("frr_type", length=32, role="meta", display_name="FRR"),
    FieldDef("vccv_cc", length=64, role="meta", display_name="VCCV CC"),
    FieldDef("vccv_cv", length=64, role="meta", display_name="VCCV CV"),
    FieldDef("bandwidth", length=64, role="meta", display_name="Bandwidth"),
    FieldDef("create_time", length=64, role="meta", display_name="Create"),
    FieldDef("last_change", length=64, role="meta", display_name="Last Change"),
]

_L2VPN_MAC_FIELDS: list[FieldDef] = [
    FieldDef("mac", length=64, indexed=True, is_key=True, display_name="MAC"),
    FieldDef("vpn", length=128, indexed=True, is_key=True, display_name="VPN"),
    FieldDef("vlan", length=32, role="meta", display_name="VLAN"),
    FieldDef("pw", length=128, role="state", display_name="PW"),
    FieldDef("neighbor", length=128, role="state", display_name="Neighbor"),
    FieldDef("ac_port", length=128, role="state", display_name="AC Port"),
    FieldDef("exter_vlan", length=32, role="state", display_name="Exter VLAN"),
    FieldDef("vpn_sid", length=128, role="state", display_name="VPN SID"),
    FieldDef("neighbor_sid", length=128, role="state", display_name="Neighbor SID"),
    FieldDef("outgoing", length=256, role="meta", display_name="Outgoing"),
    FieldDef("attribute", length=64, role="meta", display_name="Attribute"),
]

_EVPN_MAC_FIELDS: list[FieldDef] = [
    FieldDef("network", length=256, indexed=True, is_key=True, display_name="Network"),
    FieldDef("next_hop", length=128, role="state", display_name="NextHop"),
    FieldDef("path", length=256, role="state", display_name="Path"),
    FieldDef("metric", length=32, role="meta", display_name="Metric"),
    FieldDef("loc_prf", length=32, role="meta", display_name="LocPrf"),
    FieldDef("rt_prf", length=32, role="meta", display_name="RtPrf"),
    FieldDef("status_codes", length=16, role="meta", display_name="Codes"),
]

_CONFIG_VRF_FIELDS: list[FieldDef] = [
    FieldDef("vrf_name", length=128, indexed=True, is_key=True, display_name="VRF"),
    FieldDef("rd", length=64, role="state", display_name="RD"),
    FieldDef("address_families", length=64, role="state", display_name="AF"),
    FieldDef("rt_export", length=512, role="state", display_name="RT Export"),
    FieldDef("rt_import", length=512, role="state", display_name="RT Import"),
    FieldDef("description", length=256, role="meta", display_name="描述"),
]

_CONFIG_IFACE_FIELDS: list[FieldDef] = [
    FieldDef("interface", length=128, indexed=True, is_key=True, is_interface=True, display_name="接口"),
    FieldDef("vrf", length=128, indexed=True, is_key=True, display_name="VRF"),
    FieldDef("admin", length=16, role="state", display_name="Admin"),
    FieldDef("ip_address", length=256, role="state", display_name="IPv4"),
    FieldDef("secondary_tag", length=64, role="state", display_name="IPv4 M/S"),
    FieldDef("ipv6_address", length=256, role="state", display_name="IPv6"),
    FieldDef("ipv6_secondary_tag", length=64, role="state", display_name="IPv6 M/S"),
    FieldDef("mtu", length=16, role="meta", display_name="MTU"),
    FieldDef("description", length=256, role="meta", display_name="描述"),
]

_CONFIG_BGP_PEER_FIELDS: list[FieldDef] = [
    FieldDef("afi", length=32, indexed=True, is_key=True, display_name="AFI"),
    FieldDef("vrf", length=128, indexed=True, is_key=True, display_name="VRF"),
    FieldDef("neighbor", length=128, indexed=True, is_key=True, display_name="Neighbor"),
    FieldDef("peer_group", length=64, indexed=True, is_key=True, display_name="Group"),
    FieldDef("remote_as", length=16, role="state", display_name="Remote AS"),
    FieldDef("activate", length=16, role="state", display_name="Activate"),
    FieldDef("update_source", length=64, role="meta", display_name="Update-Source"),
    FieldDef("route_map_in", length=128, role="meta", display_name="RM In"),
    FieldDef("route_map_out", length=128, role="meta", display_name="RM Out"),
]

_CONFIG_L2VPN_PW_FIELDS: list[FieldDef] = [
    FieldDef("vpn_type", length=16, indexed=True, is_key=True, display_name="Type"),
    FieldDef("vpn_name", length=128, indexed=True, is_key=True, display_name="VPN"),
    FieldDef("pw_name", length=128, indexed=True, is_key=True, display_name="PW"),
    FieldDef("peer", length=64, role="state", display_name="Peer"),
    FieldDef("vcid", length=64, role="state", display_name="VCID"),
    FieldDef("encapsulation", length=32, role="meta", display_name="Encap"),
    FieldDef("access_point", length=128, role="meta", display_name="Access"),
    FieldDef("description", length=256, role="meta", display_name="描述"),
]

_CONFIG_STATIC_ROUTE_FIELDS: list[FieldDef] = [
    FieldDef("af", length=8, indexed=True, is_key=True, display_name="AF"),
    FieldDef("vrf", length=128, indexed=True, is_key=True, display_name="VRF"),
    FieldDef("prefix", length=128, indexed=True, is_key=True, display_name="Prefix"),
    FieldDef("mask", length=64, indexed=True, is_key=True, display_name="Mask"),
    FieldDef("next_hop", length=64, indexed=True, is_key=True, display_name="Next-Hop"),
    FieldDef("interface", length=128, indexed=True, is_key=True, is_interface=True, display_name="接口"),
    FieldDef("nexthop_vrf", length=128, role="state", display_name="Nexthop-VRF"),
    FieldDef("metric", length=16, role="state", display_name="Metric"),
    FieldDef("bfd", length=16, role="state", display_name="BFD"),
    FieldDef("track", length=64, role="meta", display_name="Track"),
    FieldDef("route_name", length=128, role="meta", display_name="Name"),
    FieldDef("tag", length=32, role="meta", display_name="Tag"),
    FieldDef("distance", length=16, role="meta", display_name="Distance"),
]

_CONFIG_OSPF_FIELDS: list[FieldDef] = [
    FieldDef("af", length=8, indexed=True, is_key=True, display_name="AF"),
    FieldDef("process_id", length=32, indexed=True, is_key=True, display_name="Process"),
    FieldDef("vrf", length=128, indexed=True, is_key=True, display_name="VRF"),
    FieldDef("area", length=64, indexed=True, is_key=True, display_name="Area"),
    FieldDef("interface", length=128, indexed=True, is_key=True, is_interface=True, display_name="接口"),
    FieldDef("area_type", length=32, role="state", display_name="Area Type"),
    FieldDef("network_type", length=32, role="state", display_name="Network"),
    FieldDef("cost", length=16, role="state", display_name="Cost"),
    FieldDef("hello_interval", length=16, role="state", display_name="Hello"),
    FieldDef("dead_interval", length=16, role="state", display_name="Dead"),
    FieldDef("bfd", length=16, role="state", display_name="BFD"),
    FieldDef("router_id", length=64, role="meta", display_name="Router-ID"),
    FieldDef("redistribute", length=256, role="meta", display_name="Redistribute"),
]

_CONFIG_ISIS_FIELDS: list[FieldDef] = [
    FieldDef("process_id", length=32, indexed=True, is_key=True, display_name="Process"),
    FieldDef("vrf", length=128, indexed=True, is_key=True, display_name="VRF"),
    FieldDef("interface", length=128, indexed=True, is_key=True, is_interface=True, display_name="接口"),
    FieldDef("circuit_type", length=32, role="state", display_name="Circuit"),
    FieldDef("network_type", length=32, role="state", display_name="Network"),
    FieldDef("ip_enable", length=8, role="state", display_name="IPv4"),
    FieldDef("ipv6_enable", length=8, role="state", display_name="IPv6"),
    FieldDef("metric", length=16, role="state", display_name="Metric"),
    FieldDef("ipv6_metric", length=16, role="state", display_name="IPv6 Metric"),
    FieldDef("passive", length=8, role="state", display_name="Passive"),
    FieldDef("bfd", length=16, role="state", display_name="BFD"),
    FieldDef("ipv6_bfd", length=16, role="state", display_name="IPv6 BFD"),
    FieldDef("area", length=64, role="meta", display_name="Area"),
    FieldDef("system_id", length=64, role="meta", display_name="System-ID"),
    FieldDef("router_id", length=64, role="meta", display_name="Router-ID"),
    FieldDef("is_type", length=32, role="meta", display_name="IS-Type"),
]


def _zte_status_profiles() -> list[ParseProfile]:
    """ZTE ZXROS status snapshots from lab show commands."""
    return [
        ParseProfile(
            profile_id="zte.isis_adjacency",
            vendor_key="zte",
            metric_id="isis_adjacency",
            parser_id="isis_adjacency",
            title="ISIS Adjacency",
            command_template="show isis adjacency | one-line",
            match=r"(?i)^\s*show\s+isis\s+adjacency(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show isis adjacency",
            description="ISIS adjacency table (Process ID blocks).",
            fields=list(_ISIS_FIELDS),
            tags=["isis", "l3", "status"],
            sort_order=300,
            enabled=True,
            kind="collect",
        ),
        ParseProfile(
            profile_id="zte.interface_brief",
            vendor_key="zte",
            metric_id="interface_brief",
            parser_id="interface_brief",
            title="Interface Brief",
            command_template="show interface brief | one-line",
            match=r"(?i)^\s*show\s+interface\s+brief(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show interface brief",
            description="Interface admin/phy/prot status brief.",
            fields=list(_IFACE_BRIEF_FIELDS),
            tags=["interface", "l2", "status"],
            sort_order=310,
            enabled=True,
            kind="collect",
        ),
        ParseProfile(
            profile_id="zte.interface_detail",
            vendor_key="zte",
            metric_id="interface_detail",
            parser_id="interface_detail",
            title="Interface Detail (filtered)",
            command_template=(
                "show interface | include ifindex|BW|The port is|MTU|Negotiation|"
                "Description|Current|Rate|Peak|Input|Output|utilization"
            ),
            match=(
                r"(?i)^\s*show\s+interface\s*\|\s*include\s+"
                r".*\bifindex\b.*$"
            ),
            textfsm_command="show interface",
            description="Filtered show interface: BW/rate/util/description.",
            fields=list(_IFACE_DETAIL_FIELDS),
            tags=["interface", "rate", "status"],
            sort_order=315,
            enabled=True,
            kind="collect",
        ),
        ParseProfile(
            profile_id="zte.arp",
            vendor_key="zte",
            metric_id="arp",
            parser_id="arp",
            title="ARP Table",
            command_template="show arp | one-line",
            match=r"(?i)^\s*show\s+arp(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show arp",
            description="ARP entries (IP/MAC/interface); VRF from config_interface aux.",
            fields=list(_ARP_FIELDS),
            tags=["arp", "l3", "status"],
            sort_order=320,
            enabled=True,
            kind="collect",
            aux_commands=[
                AuxCommand(key="if_intf", profile_id="zte.config_interface"),
            ],
            enrich_joins=[
                EnrichJoin(from_aux="if_intf", on="interface", take=("vrf",)),
            ],
        ),
        ParseProfile(
            profile_id="zte.if_intf",
            vendor_key="zte",
            metric_id="if_intf",
            parser_id="if_intf",
            title="IF VRF (if-intf)",
            command_template="show running-config if-intf | one-line",
            match=r"(?i)^\s*show\s+running-config\s+if-intf\s*$",
            textfsm_command="show running-config if-intf",
            description=(
                "Compat/aux-only interface→VRF map (disabled in catalog). "
                "Use Config Interface Intent (zte.config_interface) instead."
            ),
            fields=list(_IF_INTF_FIELDS),
            tags=["interface", "vrf", "config", "status", "aux"],
            sort_order=325,
            enabled=False,
            kind="collect",
        ),
        ParseProfile(
            profile_id="zte.nd6_cache",
            vendor_key="zte",
            metric_id="nd6_cache",
            parser_id="nd6_cache",
            title="ND6 Cache",
            command_template="show nd6 cache | one-line",
            match=r"(?i)^\s*show\s+nd6\s+cache(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show nd6 cache",
            description="IPv6 neighbor discovery cache.",
            fields=list(_ND6_FIELDS),
            tags=["nd6", "ipv6", "status"],
            sort_order=330,
            enabled=True,
            kind="collect",
        ),
        ParseProfile(
            profile_id="zte.bgp_vpnv4_summary",
            vendor_key="zte",
            metric_id="bgp_peer",
            parser_id="bgp_peer",
            title="BGP VPNv4 Summary",
            command_template="show bgp vpnv4 unicast summary | one-line",
            match=r"(?i)^\s*show\s+bgp\s+vpnv4\s+unicast\s+summary(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show bgp vpnv4 unicast summary",
            description="BGP VPNv4 peer summary (afi=vpnv4).",
            fields=list(_BGP_PEER_FIELDS),
            tags=["bgp", "vpnv4", "status"],
            sort_order=340,
            enabled=True,
            kind="collect",
        ),
        ParseProfile(
            profile_id="zte.bgp_ipv4_summary",
            vendor_key="zte",
            metric_id="bgp_peer",
            parser_id="bgp_peer",
            title="BGP IPv4 Summary",
            command_template="show bgp ipv4 unicast summary | one-line",
            match=r"(?i)^\s*show\s+bgp\s+ipv4\s+unicast\s+summary(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show bgp ipv4 unicast summary",
            description="BGP IPv4 unicast peer summary (afi=ipv4).",
            fields=list(_BGP_PEER_FIELDS),
            tags=["bgp", "ipv4", "status"],
            sort_order=350,
            enabled=True,
            kind="collect",
        ),
        ParseProfile(
            profile_id="zte.bgp_vpnv6_summary",
            vendor_key="zte",
            metric_id="bgp_peer",
            parser_id="bgp_peer",
            title="BGP VPNv6 Summary",
            command_template="show bgp vpnv6 unicast summary | one-line",
            match=r"(?i)^\s*show\s+bgp\s+vpnv6\s+unicast\s+summary(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show bgp vpnv6 unicast summary",
            description="BGP VPNv6 peer summary (afi=vpnv6).",
            fields=list(_BGP_PEER_FIELDS),
            tags=["bgp", "vpnv6", "status"],
            sort_order=360,
            enabled=True,
            kind="collect",
        ),
        ParseProfile(
            profile_id="zte.bgp_ipv6_summary",
            vendor_key="zte",
            metric_id="bgp_peer",
            parser_id="bgp_peer",
            title="BGP IPv6 Summary",
            command_template="show bgp ipv6 unicast summary | one-line",
            match=r"(?i)^\s*show\s+bgp\s+ipv6\s+unicast\s+summary(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show bgp ipv6 unicast summary",
            description="BGP IPv6 unicast peer summary (afi=ipv6).",
            fields=list(_BGP_PEER_FIELDS),
            tags=["bgp", "ipv6", "status"],
            sort_order=365,
            enabled=True,
            kind="collect",
        ),
        ParseProfile(
            profile_id="zte.bgp_vpnv4_vrf_summary",
            vendor_key="zte",
            metric_id="bgp_peer",
            parser_id="bgp_peer",
            title="BGP VPNv4 VRF Summary",
            command_template="show bgp vpnv4 unicast vrf <vrf> summary | one-line",
            match=r"(?i)^\s*show\s+bgp\s+vpnv4\s+unicast\s+vrf\s+(?P<vrf>\S+)\s+summary(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show bgp vpnv4 unicast summary",
            description=(
                "Per-VRF BGP VPNv4 peer summary. Bind one or more VRFs; "
                "aux: config_vrf (RD/AF enrich). IPv4 FIB is a separate monitor item."
            ),
            placeholders=[_VRF_PLACEHOLDER_IPV4],
            fields=list(_BGP_PEER_FIELDS),
            tags=["bgp", "vpnv4", "vrf", "status"],
            sort_order=370,
            enabled=True,
            kind="collect",
            aux_commands=[
                AuxCommand(key="config_vrf", profile_id="zte.config_vrf"),
            ],
            enrich_joins=[
                EnrichJoin(
                    from_aux="config_vrf",
                    left_on="vrf",
                    right_on="vrf_name",
                    take=("rd", "address_families"),
                ),
            ],
        ),
        ParseProfile(
            profile_id="zte.bgp_vpnv6_vrf_summary",
            vendor_key="zte",
            metric_id="bgp_peer",
            parser_id="bgp_peer",
            title="BGP VPNv6 VRF Summary",
            command_template="show bgp vpnv6 unicast vrf <vrf> summary | one-line",
            match=r"(?i)^\s*show\s+bgp\s+vpnv6\s+unicast\s+vrf\s+(?P<vrf>\S+)\s+summary(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show bgp vpnv6 unicast summary",
            description=(
                "Per-VRF BGP VPNv6 peer summary. Bind one or more VRFs; "
                "aux: config_vrf (RD/AF enrich). IPv6 FIB is a separate monitor item."
            ),
            placeholders=[_VRF_PLACEHOLDER_IPV6],
            fields=list(_BGP_PEER_FIELDS),
            tags=["bgp", "vpnv6", "vrf", "status"],
            sort_order=375,
            enabled=True,
            kind="collect",
            aux_commands=[
                AuxCommand(key="config_vrf", profile_id="zte.config_vrf"),
            ],
            enrich_joins=[
                EnrichJoin(
                    from_aux="config_vrf",
                    left_on="vrf",
                    right_on="vrf_name",
                    take=("rd", "address_families"),
                ),
            ],
        ),
        ParseProfile(
            profile_id="zte.bgp_evpn_summary",
            vendor_key="zte",
            metric_id="bgp_peer",
            parser_id="bgp_peer",
            title="BGP L2VPN EVPN Summary",
            command_template="show bgp l2vpn evpn summary | one-line",
            match=r"(?i)^\s*show\s+bgp\s+l2vpn\s+evpn\s+summary(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show bgp l2vpn evpn summary",
            description="BGP L2VPN EVPN peer summary (afi=evpn).",
            fields=list(_BGP_PEER_FIELDS),
            tags=["bgp", "evpn", "status"],
            sort_order=380,
            enabled=True,
            kind="collect",
        ),
        ParseProfile(
            profile_id="zte.bgp_vpls_summary",
            vendor_key="zte",
            metric_id="bgp_peer",
            parser_id="bgp_peer",
            title="BGP L2VPN VPLS Summary",
            command_template="show bgp l2vpn vpls summary | one-line",
            match=r"(?i)^\s*show\s+bgp\s+l2vpn\s+vpls\s+summary(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show bgp l2vpn vpls summary",
            description="BGP L2VPN VPLS peer summary (afi=vpls).",
            fields=list(_BGP_PEER_FIELDS),
            tags=["bgp", "vpls", "status"],
            sort_order=385,
            enabled=True,
            kind="collect",
        ),
        # --- Phase 1 status ---
        ParseProfile(
            profile_id="zte.ospf_neighbor",
            vendor_key="zte",
            metric_id="ospf_neighbor",
            parser_id="ospf_neighbor",
            title="OSPF Neighbor",
            command_template="show ip ospf neighbor | one-line",
            match=r"(?i)^\s*show\s+ip\s+ospf\s+neighbor(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show ip ospf neighbor",
            description="OSPF neighbor table (multi Process ID).",
            fields=list(_OSPF_FIELDS),
            tags=["ospf", "l3", "status"],
            sort_order=390,
            enabled=True,
            kind="collect",
        ),
        ParseProfile(
            profile_id="zte.vrrp_ipv4",
            vendor_key="zte",
            metric_id="vrrp",
            parser_id="vrrp",
            title="VRRP IPv4 Brief",
            command_template="show vrrp ipv4 brief | one-line",
            match=r"(?i)^\s*show\s+vrrp\s+ipv4\s+brief(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show vrrp ipv4 brief",
            description="VRRP IPv4 brief (af=ipv4).",
            fields=list(_VRRP_FIELDS),
            tags=["vrrp", "l3", "status"],
            sort_order=400,
            enabled=True,
            kind="collect",
        ),
        ParseProfile(
            profile_id="zte.vrrp_ipv6",
            vendor_key="zte",
            metric_id="vrrp",
            parser_id="vrrp",
            title="VRRP IPv6 Brief",
            command_template="show vrrp ipv6 brief | one-line",
            match=r"(?i)^\s*show\s+vrrp\s+ipv6\s+brief(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show vrrp ipv6 brief",
            description="VRRP IPv6 brief (af=ipv6).",
            fields=list(_VRRP_FIELDS),
            tags=["vrrp", "ipv6", "status"],
            sort_order=405,
            enabled=True,
            kind="collect",
        ),
        ParseProfile(
            profile_id="zte.optical_brief",
            vendor_key="zte",
            metric_id="optical_brief",
            parser_id="optical_brief",
            title="Optical Info Brief",
            command_template="show opticalinfo brief | one-line",
            match=r"(?i)^\s*show\s+opticalinfo\s+brief(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show opticalinfo brief",
            description="Transceiver Rx/Tx power and status.",
            fields=list(_OPTICAL_FIELDS),
            tags=["optical", "interface", "status"],
            sort_order=410,
            enabled=True,
            kind="collect",
        ),
        # --- Phase 3 BGP neighbor routes ---
        ParseProfile(
            profile_id="zte.bgp_vpnv4_neighbor_in",
            vendor_key="zte",
            metric_id="bgp_route",
            parser_id="bgp_route",
            title="BGP VPNv4 Neighbor In",
            command_template="show bgp vpnv4 unicast neighbor in <neighbor> | one-line",
            match=r"(?i)^\s*show\s+bgp\s+vpnv4\s+unicast\s+neighbor\s+(?P<direction>in)\s+(?P<neighbor>\S+)(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show bgp vpnv4 unicast neighbor in",
            description="Routes learned from VPNv4 neighbor; summary aux for peer state.",
            placeholders=[_BGP_NEIGHBOR_VPNV4],
            fields=list(_BGP_ROUTE_FIELDS),
            tags=["bgp", "vpnv4", "route"],
            sort_order=420,
            enabled=True,
            kind="collect",
            aux_commands=[AuxCommand(key="bgp_summary", profile_id="zte.bgp_vpnv4_summary")],
            enrich_joins=[
                EnrichJoin(
                    from_aux="bgp_summary",
                    on="neighbor",
                    take=("as_num", "state", "pfx_rcd"),
                ),
            ],
        ),
        ParseProfile(
            profile_id="zte.bgp_vpnv4_neighbor_out",
            vendor_key="zte",
            metric_id="bgp_route",
            parser_id="bgp_route",
            title="BGP VPNv4 Neighbor Out",
            command_template="show bgp vpnv4 unicast neighbor out <neighbor> | one-line",
            match=r"(?i)^\s*show\s+bgp\s+vpnv4\s+unicast\s+neighbor\s+(?P<direction>out)\s+(?P<neighbor>\S+)(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show bgp vpnv4 unicast neighbor out",
            description="Routes advertised to VPNv4 neighbor (large; bind neighbor).",
            placeholders=[_BGP_NEIGHBOR_VPNV4],
            fields=list(_BGP_ROUTE_FIELDS),
            tags=["bgp", "vpnv4", "route"],
            sort_order=425,
            enabled=True,
            kind="collect",
            collect_lane="heavy",
            aux_commands=[AuxCommand(key="bgp_summary", profile_id="zte.bgp_vpnv4_summary")],
            enrich_joins=[
                EnrichJoin(
                    from_aux="bgp_summary",
                    on="neighbor",
                    take=("as_num", "state", "pfx_rcd"),
                ),
            ],
        ),
        ParseProfile(
            profile_id="zte.bgp_vpnv6_neighbor_in",
            vendor_key="zte",
            metric_id="bgp_route",
            parser_id="bgp_route",
            title="BGP VPNv6 Neighbor In",
            command_template="show bgp vpnv6 unicast neighbor in <neighbor> | one-line",
            match=r"(?i)^\s*show\s+bgp\s+vpnv6\s+unicast\s+neighbor\s+(?P<direction>in)\s+(?P<neighbor>\S+)(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show bgp vpnv6 unicast neighbor in",
            description="Routes learned from VPNv6 neighbor; summary aux for peer state.",
            placeholders=[_BGP_NEIGHBOR_VPNV6],
            fields=list(_BGP_ROUTE_FIELDS),
            tags=["bgp", "vpnv6", "route"],
            sort_order=426,
            enabled=True,
            kind="collect",
            aux_commands=[AuxCommand(key="bgp_summary", profile_id="zte.bgp_vpnv6_summary")],
            enrich_joins=[
                EnrichJoin(
                    from_aux="bgp_summary",
                    on="neighbor",
                    take=("as_num", "state", "pfx_rcd"),
                ),
            ],
        ),
        ParseProfile(
            profile_id="zte.bgp_vpnv6_neighbor_out",
            vendor_key="zte",
            metric_id="bgp_route",
            parser_id="bgp_route",
            title="BGP VPNv6 Neighbor Out",
            command_template="show bgp vpnv6 unicast neighbor out <neighbor> | one-line",
            match=r"(?i)^\s*show\s+bgp\s+vpnv6\s+unicast\s+neighbor\s+(?P<direction>out)\s+(?P<neighbor>\S+)(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show bgp vpnv6 unicast neighbor out",
            description="Routes advertised to VPNv6 neighbor (large; bind neighbor).",
            placeholders=[_BGP_NEIGHBOR_VPNV6],
            fields=list(_BGP_ROUTE_FIELDS),
            tags=["bgp", "vpnv6", "route"],
            sort_order=427,
            enabled=True,
            kind="collect",
            collect_lane="heavy",
            aux_commands=[AuxCommand(key="bgp_summary", profile_id="zte.bgp_vpnv6_summary")],
            enrich_joins=[
                EnrichJoin(
                    from_aux="bgp_summary",
                    on="neighbor",
                    take=("as_num", "state", "pfx_rcd"),
                ),
            ],
        ),
        ParseProfile(
            profile_id="zte.bgp_vpnv4_vrf_neighbor_in",
            vendor_key="zte",
            metric_id="bgp_route",
            parser_id="bgp_route",
            title="BGP VPNv4 VRF Neighbor In",
            command_template="show bgp vpnv4 unicast vrf <vrf> neighbor in <neighbor> | one-line",
            match=(
                r"(?i)^\s*show\s+bgp\s+vpnv4\s+unicast\s+vrf\s+(?P<vrf>\S+)\s+"
                r"neighbor\s+(?P<direction>in)\s+(?P<neighbor>\S+)(?:\s*\|\s*one-line)?\s*$"
            ),
            textfsm_command="show bgp vpnv4 unicast neighbor in",
            description="Per-VRF CE peer routes; discover (vrf,neighbor) from BGP peer intent.",
            placeholders=[_BGP_VRF_PEER_IPV4, _BGP_NEIGHBOR_IPV4_VRF],
            fields=list(_BGP_ROUTE_FIELDS),
            tags=["bgp", "vpnv4", "vrf", "route"],
            sort_order=430,
            enabled=True,
            kind="collect",
            aux_commands=[AuxCommand(key="bgp_summary", profile_id="zte.bgp_vpnv4_vrf_summary")],
            enrich_joins=[
                EnrichJoin(
                    from_aux="bgp_summary",
                    on="neighbor",
                    take=("as_num", "state", "pfx_rcd"),
                ),
            ],
        ),
        ParseProfile(
            profile_id="zte.bgp_vpnv4_vrf_neighbor_out",
            vendor_key="zte",
            metric_id="bgp_route",
            parser_id="bgp_route",
            title="BGP VPNv4 VRF Neighbor Out",
            command_template="show bgp vpnv4 unicast vrf <vrf> neighbor out <neighbor> | one-line",
            match=(
                r"(?i)^\s*show\s+bgp\s+vpnv4\s+unicast\s+vrf\s+(?P<vrf>\S+)\s+"
                r"neighbor\s+(?P<direction>out)\s+(?P<neighbor>\S+)(?:\s*\|\s*one-line)?\s*$"
            ),
            textfsm_command="show bgp vpnv4 unicast neighbor out",
            description="Per-VRF CE peer advertised routes; discover pairs from BGP peer intent.",
            placeholders=[_BGP_VRF_PEER_IPV4, _BGP_NEIGHBOR_IPV4_VRF],
            fields=list(_BGP_ROUTE_FIELDS),
            tags=["bgp", "vpnv4", "vrf", "route"],
            sort_order=435,
            enabled=True,
            kind="collect",
            collect_lane="heavy",
            aux_commands=[AuxCommand(key="bgp_summary", profile_id="zte.bgp_vpnv4_vrf_summary")],
            enrich_joins=[
                EnrichJoin(
                    from_aux="bgp_summary",
                    on="neighbor",
                    take=("as_num", "state", "pfx_rcd"),
                ),
            ],
        ),
        ParseProfile(
            profile_id="zte.bgp_vpnv6_vrf_neighbor_in",
            vendor_key="zte",
            metric_id="bgp_route",
            parser_id="bgp_route",
            title="BGP VPNv6 VRF Neighbor In",
            command_template="show bgp vpnv6 unicast vrf <vrf> neighbor in <neighbor> | one-line",
            match=(
                r"(?i)^\s*show\s+bgp\s+vpnv6\s+unicast\s+vrf\s+(?P<vrf>\S+)\s+"
                r"neighbor\s+(?P<direction>in)\s+(?P<neighbor>\S+)(?:\s*\|\s*one-line)?\s*$"
            ),
            textfsm_command="show bgp vpnv6 unicast neighbor in",
            description="Per-VRF IPv6 CE peer routes; discover pairs from BGP peer intent.",
            placeholders=[_BGP_VRF_PEER_IPV6, _BGP_NEIGHBOR_IPV6_VRF],
            fields=list(_BGP_ROUTE_FIELDS),
            tags=["bgp", "vpnv6", "vrf", "route"],
            sort_order=440,
            enabled=True,
            kind="collect",
            aux_commands=[AuxCommand(key="bgp_summary", profile_id="zte.bgp_vpnv6_vrf_summary")],
            enrich_joins=[
                EnrichJoin(
                    from_aux="bgp_summary",
                    on="neighbor",
                    take=("as_num", "state", "pfx_rcd"),
                ),
            ],
        ),
        ParseProfile(
            profile_id="zte.bgp_vpnv6_vrf_neighbor_out",
            vendor_key="zte",
            metric_id="bgp_route",
            parser_id="bgp_route",
            title="BGP VPNv6 VRF Neighbor Out",
            command_template="show bgp vpnv6 unicast vrf <vrf> neighbor out <neighbor> | one-line",
            match=(
                r"(?i)^\s*show\s+bgp\s+vpnv6\s+unicast\s+vrf\s+(?P<vrf>\S+)\s+"
                r"neighbor\s+(?P<direction>out)\s+(?P<neighbor>\S+)(?:\s*\|\s*one-line)?\s*$"
            ),
            textfsm_command="show bgp vpnv6 unicast neighbor out",
            description="Per-VRF IPv6 CE advertised routes; discover pairs from BGP peer intent.",
            placeholders=[_BGP_VRF_PEER_IPV6, _BGP_NEIGHBOR_IPV6_VRF],
            fields=list(_BGP_ROUTE_FIELDS),
            tags=["bgp", "vpnv6", "vrf", "route"],
            sort_order=445,
            enabled=True,
            kind="collect",
            collect_lane="heavy",
            aux_commands=[AuxCommand(key="bgp_summary", profile_id="zte.bgp_vpnv6_vrf_summary")],
            enrich_joins=[
                EnrichJoin(
                    from_aux="bgp_summary",
                    on="neighbor",
                    take=("as_num", "state", "pfx_rcd"),
                ),
            ],
        ),
        # --- Phase 4 forwarding / L2 ---
        ParseProfile(
            profile_id="zte.ip_route_vrf",
            vendor_key="zte",
            metric_id="ip_route",
            parser_id="ip_route",
            title="IPv4 Forwarding VRF",
            command_template="show ip forwarding route vrf <vrf> | one-line",
            match=r"(?i)^\s*show\s+ip\s+forwarding\s+route\s+vrf\s+(?P<vrf>\S+)(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show ip forwarding route",
            description="IPv4 FIB per VRF. Bind one or more VRFs (config VRF intent aux).",
            placeholders=[_VRF_PLACEHOLDER_IPV4],
            fields=list(_IP_ROUTE_FIELDS),
            tags=["route", "ipv4", "vrf"],
            sort_order=450,
            enabled=True,
            kind="collect",
            aux_commands=[AuxCommand(key="config_vrf", profile_id="zte.config_vrf")],
            enrich_joins=[
                EnrichJoin(
                    from_aux="config_vrf",
                    left_on="vrf",
                    right_on="vrf_name",
                    take=("rd",),
                ),
            ],
        ),
        ParseProfile(
            profile_id="zte.ip_route",
            vendor_key="zte",
            metric_id="ip_route",
            parser_id="ip_route",
            title="IPv4 Forwarding Global",
            command_template="show ip forwarding route | one-line",
            match=r"(?i)^\s*show\s+ip\s+forwarding\s+route(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show ip forwarding route",
            description="Global IPv4 FIB (large; disabled by default).",
            fields=list(_IP_ROUTE_FIELDS),
            tags=["route", "ipv4"],
            sort_order=455,
            enabled=False,
            kind="collect",
        ),
        ParseProfile(
            profile_id="zte.ipv6_route_vrf",
            vendor_key="zte",
            metric_id="ipv6_route",
            parser_id="ipv6_route",
            title="IPv6 Forwarding VRF",
            command_template="show ipv6 forwarding route vrf <vrf> | one-line",
            match=r"(?i)^\s*show\s+ipv6\s+forwarding\s+route\s+vrf\s+(?P<vrf>\S+)(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show ipv6 forwarding route",
            description="IPv6 FIB per VRF. Bind one or more VRFs (config VRF intent aux).",
            placeholders=[_VRF_PLACEHOLDER_IPV6],
            fields=list(_IPV6_ROUTE_FIELDS),
            tags=["route", "ipv6", "vrf"],
            sort_order=460,
            enabled=True,
            kind="collect",
            aux_commands=[AuxCommand(key="config_vrf", profile_id="zte.config_vrf")],
            enrich_joins=[
                EnrichJoin(
                    from_aux="config_vrf",
                    left_on="vrf",
                    right_on="vrf_name",
                    take=("rd",),
                ),
            ],
        ),
        ParseProfile(
            profile_id="zte.ipv6_route",
            vendor_key="zte",
            metric_id="ipv6_route",
            parser_id="ipv6_route",
            title="IPv6 Forwarding Global",
            command_template="show ipv6 forwarding route | one-line",
            match=r"(?i)^\s*show\s+ipv6\s+forwarding\s+route(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show ipv6 forwarding route",
            description="Global IPv6 FIB (large; disabled by default).",
            fields=list(_IPV6_ROUTE_FIELDS),
            tags=["route", "ipv6"],
            sort_order=465,
            enabled=False,
            kind="collect",
        ),
        ParseProfile(
            profile_id="zte.l2vpn_pw",
            vendor_key="zte",
            metric_id="l2vpn_pw",
            parser_id="l2vpn_pw",
            title="L2VPN PW State",
            command_template="show l2vpn forwardinfo | one-line",
            match=r"(?i)^\s*show\s+l2vpn\s+forwardinfo(?!\s+detail)(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show l2vpn forwardinfo",
            description="L2VPN pseudowire forwardinfo state (brief table).",
            fields=list(_L2VPN_PW_FIELDS),
            tags=["l2vpn", "pw", "status"],
            sort_order=470,
            enabled=True,
            kind="collect",
        ),
        ParseProfile(
            profile_id="zte.l2vpn_pw_detail",
            vendor_key="zte",
            metric_id="l2vpn_pw_detail",
            parser_id="l2vpn_pw_detail",
            title="L2VPN PW Detail",
            command_template="show l2vpn forwardinfo detail",
            match=r"(?i)^\s*show\s+l2vpn\s+forwardinfo\s+detail(?:\s*\|.*)?\s*$",
            textfsm_command="show l2vpn forwardinfo detail",
            description="L2VPN PW forwardinfo detail (VPLS/VPWS block: VC/remote/activation).",
            fields=list(_L2VPN_PW_DETAIL_FIELDS),
            tags=["l2vpn", "pw", "status", "detail"],
            sort_order=475,
            enabled=True,
            kind="collect",
        ),
        ParseProfile(
            profile_id="zte.l2vpn_mac",
            vendor_key="zte",
            metric_id="l2vpn_mac",
            parser_id="l2vpn_mac",
            title="L2VPN MAC Table",
            command_template="show mac l2vpn | one-line",
            match=r"(?i)^\s*show\s+mac\s+l2vpn(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show mac l2vpn",
            description="L2VPN MAC table (large; heavy collect lane).",
            fields=list(_L2VPN_MAC_FIELDS),
            tags=["l2vpn", "mac"],
            sort_order=480,
            enabled=True,
            kind="collect",
        ),
        ParseProfile(
            profile_id="zte.evpn_mac",
            vendor_key="zte",
            metric_id="evpn_mac",
            parser_id="evpn_mac",
            title="BGP EVPN MAC",
            command_template="show bgp evpn mac | one-line",
            match=r"(?i)^\s*show\s+bgp\s+evpn\s+mac(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show bgp evpn mac",
            description="BGP EVPN MAC NLRI (large; disabled by default).",
            fields=list(_EVPN_MAC_FIELDS),
            tags=["bgp", "evpn", "mac"],
            sort_order=490,
            enabled=False,
            kind="collect",
        ),
        # --- Config intent (P0) ---
        ParseProfile(
            profile_id="zte.config_vrf",
            vendor_key="zte",
            metric_id="config_vrf",
            parser_id="config_vrf",
            title="Config VRF Intent",
            command_template="show running-config vrf | one-line",
            match=r"(?i)^\s*show\s+running-config\s+vrf(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show running-config vrf",
            description="VRF RD/RT/AF intent from running-config vrf (no secrets).",
            fields=list(_CONFIG_VRF_FIELDS),
            tags=["config", "vrf", "intent"],
            sort_order=500,
            enabled=True,
            kind="collect",
        ),
        ParseProfile(
            profile_id="zte.config_interface",
            vendor_key="zte",
            metric_id="config_interface",
            parser_id="config_interface",
            title="Config Interface Intent",
            command_template="show running-config if-intf | one-line",
            match=r"(?i)^\s*show\s+running-config\s+if-intf(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show running-config if-intf",
            description=(
                "Interface VRF/IP/admin intent; also ARP VRF enrich source "
                "(replaces standalone IF VRF / if_intf check)."
            ),
            fields=list(_CONFIG_IFACE_FIELDS),
            tags=["config", "interface", "intent"],
            sort_order=510,
            enabled=True,
            kind="collect",
        ),
        ParseProfile(
            profile_id="zte.config_bgp_peer",
            vendor_key="zte",
            metric_id="config_bgp_peer",
            parser_id="config_bgp_peer",
            title="Config BGP Peer Intent",
            command_template="show running-config bgp | one-line",
            match=r"(?i)^\s*show\s+running-config\s+bgp(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show running-config bgp",
            description="BGP neighbor AF activate / remote-as intent (passwords skipped).",
            fields=list(_CONFIG_BGP_PEER_FIELDS),
            tags=["config", "bgp", "intent"],
            sort_order=520,
            enabled=True,
            kind="collect",
        ),
        ParseProfile(
            profile_id="zte.config_l2vpn_pw",
            vendor_key="zte",
            metric_id="config_l2vpn_pw",
            parser_id="config_l2vpn_pw",
            title="Config L2VPN PW Intent",
            command_template="show running-config l2vpn | one-line",
            match=r"(?i)^\s*show\s+running-config\s+l2vpn(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show running-config l2vpn",
            description="VPWS/VPLS pseudo-wire peer/vcid intent.",
            fields=list(_CONFIG_L2VPN_PW_FIELDS),
            tags=["config", "l2vpn", "intent"],
            sort_order=530,
            enabled=True,
            kind="collect",
        ),
        ParseProfile(
            profile_id="zte.config_static_route",
            vendor_key="zte",
            metric_id="config_static_route",
            parser_id="config_static_route",
            title="Config Static Route (IPv4)",
            command_template="show running-config static | one-line",
            match=r"(?i)^\s*show\s+running-config\s+static(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show running-config static",
            description="IPv4 static route intent from running-config static.",
            fields=list(_CONFIG_STATIC_ROUTE_FIELDS),
            tags=["config", "static", "route", "intent"],
            sort_order=540,
            enabled=True,
            kind="collect",
        ),
        ParseProfile(
            profile_id="zte.config_static_route_v6",
            vendor_key="zte",
            metric_id="config_static_route",
            parser_id="config_static_route",
            title="Config Static Route (IPv6)",
            command_template="show running-config ipv6-static-route | one-line",
            match=r"(?i)^\s*show\s+running-config\s+ipv6-static-route(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show running-config ipv6-static-route",
            description="IPv6 static route intent from running-config ipv6-static-route.",
            fields=list(_CONFIG_STATIC_ROUTE_FIELDS),
            tags=["config", "static", "route", "ipv6", "intent"],
            sort_order=541,
            enabled=True,
            kind="collect",
        ),
        ParseProfile(
            profile_id="zte.config_ospf",
            vendor_key="zte",
            metric_id="config_ospf",
            parser_id="config_ospf",
            title="Config OSPF Intent (v2)",
            command_template="show running-config ospfv2 | one-line",
            match=r"(?i)^\s*show\s+running-config\s+ospfv2(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show running-config ospfv2",
            description="OSPFv2 process/area/interface intent (auth secrets skipped).",
            fields=list(_CONFIG_OSPF_FIELDS),
            tags=["config", "ospf", "intent"],
            sort_order=550,
            enabled=True,
            kind="collect",
        ),
        ParseProfile(
            profile_id="zte.config_ospf_v3",
            vendor_key="zte",
            metric_id="config_ospf",
            parser_id="config_ospf",
            title="Config OSPF Intent (v3)",
            command_template="show running-config ospfv3 | one-line",
            match=r"(?i)^\s*show\s+running-config\s+ospfv3(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show running-config ospfv3",
            description="OSPFv3 process/area/interface intent.",
            fields=list(_CONFIG_OSPF_FIELDS),
            tags=["config", "ospf", "ipv6", "intent"],
            sort_order=551,
            enabled=True,
            kind="collect",
        ),
        ParseProfile(
            profile_id="zte.config_isis",
            vendor_key="zte",
            metric_id="config_isis",
            parser_id="config_isis",
            title="Config ISIS Intent",
            command_template="show running-config isis | one-line",
            match=r"(?i)^\s*show\s+running-config\s+isis(?:\s*\|\s*one-line)?\s*$",
            textfsm_command="show running-config isis",
            description="IS-IS process/interface intent (auth secrets skipped).",
            fields=list(_CONFIG_ISIS_FIELDS),
            tags=["config", "isis", "intent"],
            sort_order=560,
            enabled=True,
            kind="collect",
        ),
    ]


_PROFILES: list[ParseProfile] | None = None

# Huge CLI dumps: dedicated heavy lane (longer read_timeout / own SSH).
_HEAVY_LANE_METRICS = frozenset(
    {
        "interface_detail",
        "ip_route",
        "ipv6_route",
        "bgp_route",
        "l2vpn_mac",
        "evpn_mac",
    }
)


def _apply_collect_lanes(profiles: list[ParseProfile]) -> list[ParseProfile]:
    for p in profiles:
        if str(p.metric_id or "").strip() in _HEAVY_LANE_METRICS:
            p.collect_lane = "heavy"
    return profiles


def all_profiles() -> list[ParseProfile]:
    global _PROFILES
    if _PROFILES is None:
        _PROFILES = _apply_collect_lanes(
            _lldp_profiles() + _vrf_profiles() + _zte_status_profiles()
        )
    return list(_PROFILES)


def reload_profiles() -> None:
    global _PROFILES
    _PROFILES = None


def profiles_for_vendor(vendor_key: str, *, kind: str | None = None) -> list[ParseProfile]:
    key = str(vendor_key or "").strip().lower()
    out = [
        p
        for p in all_profiles()
        if p.enabled and (p.vendor_key == key or p.vendor_key == "*")
    ]
    if kind:
        out = [p for p in out if p.kind == kind]
    return out


def get_profile(profile_id: str) -> ParseProfile | None:
    pid = str(profile_id or "").strip()
    for p in all_profiles():
        if p.profile_id == pid:
            return p
    return None


def metric_field_map() -> dict[str, list[FieldDef]]:
    """Merge fields by metric_id (first-seen wins on name conflict)."""
    out: dict[str, list[FieldDef]] = {}
    seen: dict[str, set[str]] = {}
    for p in all_profiles():
        mid = p.metric_id
        bucket = out.setdefault(mid, [])
        names = seen.setdefault(mid, set())
        for f in p.fields:
            if f.name in names:
                continue
            names.add(f.name)
            bucket.append(f)
    return out


def profile_to_public_dict(p: ParseProfile, *, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    ov = overrides or {}
    aux_out: list[dict[str, Any]] = []
    for a in p.aux_commands or []:
        ap = get_profile(a.profile_id)
        aux_out.append(
            {
                "key": a.key,
                "profile_id": a.profile_id,
                "title": (ap.title if ap else "") or a.profile_id,
                "command_template": (ap.command_template if ap else "") or "",
            }
        )
    return {
        "profile_id": p.profile_id,
        "vendor_key": p.vendor_key,
        "metric_id": p.metric_id,
        "parser_id": p.parser_id,
        "title": str(ov.get("title") or p.title),
        "command_template": str(ov.get("command_template") or p.command_template),
        "description": str(ov.get("description") if ov.get("description") is not None else p.description),
        "sample_output": str(ov.get("sample_output") if ov.get("sample_output") is not None else p.sample_output),
        "placeholders": [
            {
                "name": ph.name,
                "schema_field": ph.schema_field,
                "required": ph.required,
                "bind_mode": ph.bind_mode,
                "discover_profile_id": ph.discover_profile_id,
                "discover_value_field": ph.discover_value_field,
                "discover_label_field": ph.discover_label_field,
                "discover_filter_field": ph.discover_filter_field,
                "discover_filter_contains": ph.discover_filter_contains,
                "discover_require_nonempty": ph.discover_require_nonempty,
            }
            for ph in p.placeholders
        ],
        "fields": [
            {
                "name": f.name,
                "dtype": f.dtype,
                "is_key": f.is_key,
                "is_interface": f.is_interface,
                "role": f.role,
                "display_name": f.display_name or f.name,
                "from_command_param": f.from_command_param,
            }
            for f in p.fields
        ],
        "tags": list(p.tags),
        "sort_order": p.sort_order,
        "enabled": bool(ov.get("enabled")) if "enabled" in ov else p.enabled,
        "collect_lane": str(p.collect_lane or "light"),
        "kind": p.kind,
        "match": p.match,
        "textfsm_command": p.textfsm_command or p.command_template,
        "aux_commands": aux_out,
        "enrich_joins": [
            {
                "from_aux": j.from_aux,
                "on": j.on,
                "left_on": j.left_on,
                "right_on": j.right_on,
                "take": list(j.take),
                "fill_missing": j.fill_missing,
            }
            for j in (p.enrich_joins or [])
        ],
    }
