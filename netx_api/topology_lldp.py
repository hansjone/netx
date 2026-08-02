"""LLDP neighbor command profiles and TextFSM parsers (per vendor).

Multi-vendor fabrics default to LLDP. Resolve profile primarily from Netmiko
``device_type`` (managed NE / UME already store it), then fall back to vendor label.

Parsing is TextFSM-only via ``ntc_parse`` (custom ``cli_templates/`` then community).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .ntc_parse import parse_cli, resolve_cli_platform, row_get


@dataclass
class NeighborHit:
    remote_name: str = ""
    remote_ip: str = ""
    local_port: str = ""
    remote_port: str = ""
    protocol: str = "lldp"  # lldp | cdp


@dataclass(frozen=True)
class VendorLldpProfile:
    """One vendor's LLDP (and optional CDP) discovery profile."""

    key: str
    lldp_command: str
    cdp_command: str = ""
    notes: str = ""


# device_type (Netmiko) -> profile key. Prefer inventory device_type over fuzzy text.
# Keep aligned with netx_api.device_types.SUPPORTED_DEVICE_TYPES families.
_DEVICE_TYPE_PREFIX_RULES: tuple[tuple[str, str], ...] = (
    ("cisco_", "cisco"),
    ("huawei", "huawei"),  # huawei, huawei_vrp, huawei_olt, ...
    ("zte_", "zte"),
    ("juniper", "juniper"),  # juniper, juniper_junos, ...
    ("nokia_", "nokia"),
    ("alcatel_sros", "nokia"),
    ("alcatel_", "nokia"),
    ("ericsson_", "ericsson"),
    ("hp_comware", "h3c"),
    ("h3c_", "h3c"),
)

_VENDOR_LABEL_TO_KEY: dict[str, str] = {
    "cisco": "cisco",
    "huawei": "huawei",
    "h3c": "h3c",
    "zte": "zte",
    "juniper": "juniper",
    "nokia": "nokia",
    "ericsson": "ericsson",
    "alcatel": "nokia",
    "alcatel-lucent": "nokia",
}


VENDOR_LLDP_PROFILES: dict[str, VendorLldpProfile] = {
    "cisco": VendorLldpProfile(
        key="cisco",
        lldp_command="show lldp neighbors detail",
        cdp_command="show cdp neighbors detail",
        notes="device_type cisco_*; prefer detail for System Name / Port id / mgmt IP.",
    ),
    "huawei": VendorLldpProfile(
        key="huawei",
        lldp_command="display lldp neighbor",
        notes="device_type huawei*; per-interface neighbor blocks.",
    ),
    "h3c": VendorLldpProfile(
        key="h3c",
        lldp_command="display lldp neighbor-information list",
        notes="Community hp_comware TextFSM (list; verbose also available).",
    ),
    "zte": VendorLldpProfile(
        key="zte",
        lldp_command="show lldp neighbor brief",
        notes="device_type zte_*; NetX custom TextFSM.",
    ),
    "juniper": VendorLldpProfile(
        key="juniper",
        lldp_command="show lldp neighbors",
        notes="Community juniper_junos TextFSM.",
    ),
    "nokia": VendorLldpProfile(
        key="nokia",
        lldp_command="show system lldp neighbor",
        notes="SROS: no community LLDP template yet. alcatel_aos uses show lldp remote-system.",
    ),
    "ericsson": VendorLldpProfile(
        key="ericsson",
        lldp_command="show lldp neighbors",
        notes="No community ericsson_ipos LLDP TextFSM yet.",
    ),
    "generic": VendorLldpProfile(
        key="generic",
        lldp_command="show lldp neighbors",
        notes="Fallback when device_type/vendor unknown.",
    ),
}

# Vendors without a working TextFSM path yet (custom or community).
STUB_PARSER_KEYS = frozenset({"nokia", "ericsson", "generic"})


def resolve_vendor_key(vendor: str = "", device_type: str = "") -> str:
    """Map inventory device_type (preferred) or vendor label -> profile key."""
    dtype = str(device_type or "").strip().lower()
    if dtype:
        for prefix, key in _DEVICE_TYPE_PREFIX_RULES:
            if dtype == prefix.rstrip("_") or dtype.startswith(prefix):
                return key
        if dtype in VENDOR_LLDP_PROFILES and dtype != "generic":
            return dtype

    label = str(vendor or "").strip().lower()
    if label:
        if label in _VENDOR_LABEL_TO_KEY:
            return _VENDOR_LABEL_TO_KEY[label]
        for token, key in _VENDOR_LABEL_TO_KEY.items():
            if label == token or label.startswith(f"{token} ") or label.startswith(f"{token}-"):
                return key
    return "generic"


def get_vendor_profile(vendor: str = "", device_type: str = "") -> VendorLldpProfile:
    key = resolve_vendor_key(vendor, device_type)
    return VENDOR_LLDP_PROFILES.get(key) or VENDOR_LLDP_PROFILES["generic"]


def lldp_command_for_vendor(vendor: str = "", device_type: str = "") -> str:
    dtype = str(device_type or "").strip().lower()
    # AOS has a community template; SROS profile command differs.
    if dtype.startswith("alcatel_aos"):
        return "show lldp remote-system"
    return get_vendor_profile(vendor, device_type).lldp_command


def cdp_command_for_vendor(vendor: str = "", device_type: str = "") -> str:
    """Deprecated: CDP is not used for fabric discovery (LLDP only)."""
    return get_vendor_profile(vendor, device_type).cdp_command


def pick_neighbor_command(
    *,
    protocol: str = "lldp",
    vendor: str = "",
    device_type: str = "",
) -> tuple[str, str]:
    """Return (lldp_command, \"lldp\"). Physical discovery is LLDP-only (CDP ignored)."""
    _ = protocol  # accepted for call-site compat; always LLDP
    return lldp_command_for_vendor(vendor, device_type), "lldp"


def parser_meta(*, vendor: str = "", device_type: str = "") -> tuple[str, bool]:
    """Return (parser_key, is_stub)."""
    key = resolve_vendor_key(vendor, device_type)
    dtype = str(device_type or "").strip().lower()
    if dtype.startswith("alcatel_aos"):
        return key, False
    return key, key in STUB_PARSER_KEYS


def _map_lldp_rows(rows: list[dict[str, Any]]) -> list[NeighborHit]:
    hits: list[NeighborHit] = []
    for row in rows:
        local_port = row_get(row, "local_interface", "local_intf", "local_port")
        remote_name = row_get(row, "neighbor_name", "system_name", "neighbor", "device_id")
        remote_port = row_get(
            row,
            "neighbor_interface",
            "neighbor_port_id",
            "port_id",
            "neighbor_port",
            "remote_port",
        )
        remote_ip = row_get(
            row, "mgmt_address", "management_address", "management_ip", "neighbor_ip"
        )
        name = remote_name.strip()
        if name.lower() in {"", "-", "not advertised"}:
            name = ""
        # Require a remote identity; skip filldown-only leftovers.
        if not name and not remote_port and not remote_ip:
            continue
        hits.append(
            NeighborHit(
                remote_name=name,
                remote_ip=remote_ip,
                local_port=local_port,
                remote_port=remote_port,
                protocol="lldp",
            )
        )
    return hits


def _parse_lldp_via_ntc(
    text: str,
    *,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
) -> list[NeighborHit]:
    plat = resolve_cli_platform(vendor=vendor, device_type=device_type)
    cmd = (command or "").strip() or lldp_command_for_vendor(vendor, device_type)
    if not plat or not cmd:
        return []
    rows = parse_cli(platform=plat, command=cmd, text=text)
    return _map_lldp_rows(rows)


def parse_neighbor_output(
    text: str,
    *,
    protocol: str = "lldp",
    vendor: str = "",
    device_type: str = "",
    command: str = "",
) -> list[NeighborHit]:
    """Parse neighbor CLI via TextFSM only (custom then community)."""
    raw = str(text or "")
    if not raw.strip():
        return []
    _ = protocol  # CDP discovery removed; always parse as LLDP
    return _parse_lldp_via_ntc(
        raw,
        vendor=vendor,
        device_type=device_type,
        command=command or lldp_command_for_vendor(vendor, device_type),
    )


def parse_cisco_lldp(text: str) -> list[NeighborHit]:
    """Cisco LLDP: try detail command mapping first, then brief table."""
    for cmd in ("show lldp neighbors detail", "show lldp neighbors"):
        hits = _parse_lldp_via_ntc(
            text, vendor="cisco", device_type="cisco_ios", command=cmd
        )
        if hits:
            return hits
    return []


def parse_cisco_cdp(text: str) -> list[NeighborHit]:
    """CDP discovery disabled; kept for API compat."""
    _ = text
    return []


def parse_huawei_lldp(text: str) -> list[NeighborHit]:
    return _parse_lldp_via_ntc(
        text, vendor="huawei", device_type="huawei", command="display lldp neighbor"
    )


def parse_h3c_lldp(text: str) -> list[NeighborHit]:
    for cmd in (
        "display lldp neighbor-information list",
        "display lldp neighbor-information verbose",
    ):
        hits = _parse_lldp_via_ntc(
            text, vendor="h3c", device_type="hp_comware", command=cmd
        )
        if hits:
            return hits
    return []


def parse_zte_lldp(text: str) -> list[NeighborHit]:
    return _parse_lldp_via_ntc(
        text, vendor="zte", device_type="zte_zxros", command="show lldp neighbor brief"
    )


def parse_juniper_lldp(text: str) -> list[NeighborHit]:
    return _parse_lldp_via_ntc(
        text,
        vendor="juniper",
        device_type="juniper_junos",
        command="show lldp neighbors",
    )


def parse_nokia_lldp(text: str, *, device_type: str = "") -> list[NeighborHit]:
    dtype = str(device_type or "").strip() or "nokia_sros"
    cmd = lldp_command_for_vendor(vendor="nokia", device_type=dtype)
    return _parse_lldp_via_ntc(text, vendor="nokia", device_type=dtype, command=cmd)


def parse_ericsson_lldp(text: str) -> list[NeighborHit]:
    _ = text
    return []


def parse_generic_lldp(text: str) -> list[NeighborHit]:
    """Unknown vendor: no heuristic regex; require an explicit platform template."""
    _ = text
    return []


# Long media names → short canonical form (case-insensitive prefix).
_IFNAME_PREFIXES: tuple[tuple[str, str], ...] = (
    ("tengigabitethernet", "te"),
    ("ten-gigabitethernet", "te"),
    ("gigabitethernet", "gi"),
    ("fastethernet", "fa"),
    ("ethernet", "eth"),
    ("xgigabitethernet", "xge"),
    ("100ge", "100ge"),
    ("40ge", "40ge"),
    ("25ge", "25ge"),
    ("10ge", "10ge"),
    ("ge-trunk", "ge-trunk"),
    ("eth-trunk", "eth-trunk"),
    ("port-channel", "po"),
    ("portchannel", "po"),
    ("loopback", "lo"),
    ("vlanif", "vlanif"),
    ("vlan", "vlan"),
    ("mgmteth", "mgmt"),
    ("management", "mgmt"),
    ("hundredgige", "hu"),
    ("fiftygige", "fi"),
    ("fortygige", "fo"),
    ("twentyfivegige", "twe"),
    ("twogigabitethernet", "tw"),
)


def normalize_ifname(name: str) -> str:
    """Canonicalize interface names so Gi0/0 and GigabitEthernet0/0 share a key."""
    raw = str(name or "").strip()
    if not raw:
        return ""
    s = re.sub(r"\s+", "", raw).lower()
    s = s.replace("_", "/")
    for long, short in _IFNAME_PREFIXES:
        if s.startswith(long):
            rest = s[len(long) :]
            if rest.startswith((":", "/", "-")) or rest == "" or rest[0].isdigit():
                if rest.startswith(":"):
                    rest = rest[1:]
                return f"{short}{rest}"
            break
    return s
