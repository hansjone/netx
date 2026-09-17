"""ParseProfile registry: command template + parser + schema (code as source of truth).

Display fields may be overridden at runtime via biz_state_command_override (hot edit).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
        # Escape for regex: match exact command ignoring extra whitespace flexibility
        escaped = r"\s+".join(
            __import__("re").escape(p) for p in cmd.split() if p
        )
        out.append(
            ParseProfile(
                profile_id=f"{key}.lldp_neighbors",
                vendor_key=key,
                metric_id="lldp_neighbor",
                parser_id="lldp_neighbors",
                title="LLDP Neighbors",
                command_template=cmd,
                match=rf"(?i)^\s*{escaped}\s*$",
                textfsm_command=cmd,
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


_VRF_ROUTE_FIELDS: list[FieldDef] = [
    FieldDef("vrf", length=128, indexed=True, is_key=True, display_name="VRF", from_command_param=True),
    FieldDef("source", length=64, indexed=True, is_key=True, display_name="路由来源"),
    FieldDef("networks", dtype="int", role="state", display_name="路由条数"),
]


def _vrf_profiles() -> list[ParseProfile]:
    """Discover VRF list + parameterized route-summary collect (Phase3)."""
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
        "zte": ("show ip vrf", r"(?i)^\s*show\s+ip\s+vrf\s*$", "show ip vrf"),
    }
    collect_cmds: dict[str, tuple[str, str]] = {
        "cisco": (
            "show ip route vrf <vrf> summary",
            r"(?i)^\s*show\s+ip\s+route\s+vrf\s+(?P<vrf>\S+)\s+summary\s*$",
        ),
        "huawei": (
            "display ip routing-table vpn-instance <vrf> statistics",
            r"(?i)^\s*display\s+ip\s+routing-table\s+vpn-instance\s+(?P<vrf>\S+)\s+statistics\s*$",
        ),
        "h3c": (
            "display ip routing-table vpn-instance <vrf> statistics",
            r"(?i)^\s*display\s+ip\s+routing-table\s+vpn-instance\s+(?P<vrf>\S+)\s+statistics\s*$",
        ),
        "zte": (
            "show ip route vrf <vrf> summary",
            r"(?i)^\s*show\s+ip\s+route\s+vrf\s+(?P<vrf>\S+)\s+summary\s*$",
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
                ],
                tags=["vrf", "discover"],
                sort_order=order,
                enabled=True,
                kind="discover",
            )
        )
        order += 5

    order = 220
    for key, (tmpl, match) in collect_cmds.items():
        disc_id = f"{key}.vrf_list"
        out.append(
            ParseProfile(
                profile_id=f"{key}.route_vrf_summary",
                vendor_key=key,
                metric_id="vrf_route_summary",
                parser_id="vrf_route_summary",
                title="VRF Route Summary",
                command_template=tmpl,
                match=match,
                textfsm_command="",
                description="Per-VRF route source counts (discover VRF → select bindings → collect).",
                placeholders=[
                    PlaceholderDef(
                        name="vrf",
                        schema_field="vrf",
                        required=True,
                        bind_mode="discover_select",
                        discover_profile_id=disc_id,
                        discover_value_field="vrf_name",
                        discover_label_field="vrf_name",
                    )
                ],
                fields=list(_VRF_ROUTE_FIELDS),
                tags=["vrf", "route", "l3"],
                sort_order=order,
                enabled=True,
                kind="collect",
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

_ARP_FIELDS: list[FieldDef] = [
    FieldDef("ip", length=64, indexed=True, is_key=True, display_name="IP"),
    FieldDef("interface", length=128, indexed=True, is_key=True, is_interface=True, display_name="接口"),
    FieldDef("mac", length=64, role="state", display_name="MAC"),
    FieldDef("age", length=32, role="meta", display_name="Age"),
    FieldDef("exter_vlan", length=32, role="meta", display_name="Exter VLAN"),
    FieldDef("inter_vlan", length=32, role="meta", display_name="Inter VLAN"),
    FieldDef("sub_interface", length=128, role="meta", display_name="Sub-IF"),
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
    FieldDef("neighbor", length=64, indexed=True, is_key=True, display_name="Neighbor"),
    FieldDef("as_num", length=16, role="state", display_name="AS"),
    FieldDef("state", length=64, role="state", display_name="State"),
    FieldDef("pfx_rcd", length=32, role="state", display_name="PfxRcd"),
    FieldDef("state_or_pfx", length=64, role="meta", display_name="State/PfxRcd"),
    FieldDef("ver", length=8, role="meta", display_name="Ver"),
    FieldDef("msg_rcvd", length=32, role="meta", display_name="MsgRcvd"),
    FieldDef("msg_send", length=32, role="meta", display_name="MsgSend"),
    FieldDef("up_down", length=32, role="meta", display_name="Up/Down"),
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
            command_template="show interface brief",
            match=r"(?i)^\s*show\s+interface\s+brief\s*$",
            textfsm_command="show interface brief",
            description="Interface admin/phy/prot status brief.",
            fields=list(_IFACE_BRIEF_FIELDS),
            tags=["interface", "l2", "status"],
            sort_order=310,
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
            description="ARP entries (IP/MAC/interface).",
            fields=list(_ARP_FIELDS),
            tags=["arp", "l3", "status"],
            sort_order=320,
            enabled=True,
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
            command_template="show bgp vpnv4 unicast summary",
            match=r"(?i)^\s*show\s+bgp\s+vpnv4\s+unicast\s+summary\s*$",
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
            command_template="show bgp ipv4 unicast summary",
            match=r"(?i)^\s*show\s+bgp\s+ipv4\s+unicast\s+summary\s*$",
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
            command_template="show bgp vpnv6 unicast summary",
            match=r"(?i)^\s*show\s+bgp\s+vpnv6\s+unicast\s+summary\s*$",
            textfsm_command="show bgp vpnv6 unicast summary",
            description="BGP VPNv6 peer summary (afi=vpnv6).",
            fields=list(_BGP_PEER_FIELDS),
            tags=["bgp", "vpnv6", "status"],
            sort_order=360,
            enabled=True,
            kind="collect",
        ),
    ]


_PROFILES: list[ParseProfile] | None = None


def all_profiles() -> list[ParseProfile]:
    global _PROFILES
    if _PROFILES is None:
        _PROFILES = _lldp_profiles() + _vrf_profiles() + _zte_status_profiles()
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
        "kind": p.kind,
        "match": p.match,
        "textfsm_command": p.textfsm_command or p.command_template,
    }
