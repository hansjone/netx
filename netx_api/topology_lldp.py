"""LLDP/CDP neighbor command templates and output parsers (per vendor).

Multi-vendor fabrics default to LLDP. Resolve profile primarily from Netmiko
``device_type`` (managed NE / UME already store it), then fall back to vendor label.

Each vendor has:
  - a show/display command
  - a dedicated parse_* stub (fill with real lab echoes later)
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass


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


ParserFn = Callable[[str], list[NeighborHit]]

_IPV4_RE = re.compile(
    r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)(?![\d.])"
)

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


# ---------------------------------------------------------------------------
# Vendor registry — command templates (edit / refine with lab echoes)
# ---------------------------------------------------------------------------

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
        notes="Placeholder Comware command; confirm on lab.",
    ),
    "zte": VendorLldpProfile(
        key="zte",
        lldp_command="show lldp neighbor brief",
        notes="device_type zte_*; ZXROS brief table (Local Interface / Port ID / System Name).",
    ),
    "juniper": VendorLldpProfile(
        key="juniper",
        lldp_command="show lldp neighbors",
        notes="device_type juniper*; detail form TBD.",
    ),
    "nokia": VendorLldpProfile(
        key="nokia",
        lldp_command="show system lldp neighbor",
        notes="device_type nokia_* / alcatel_*; SRL may differ.",
    ),
    "ericsson": VendorLldpProfile(
        key="ericsson",
        lldp_command="show lldp neighbors",
        notes="device_type ericsson_*; confirm IPOS/SEOS on lab.",
    ),
    "generic": VendorLldpProfile(
        key="generic",
        lldp_command="show lldp neighbors",
        notes="Fallback when device_type/vendor unknown.",
    ),
}


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
    profile = get_vendor_profile(vendor, device_type)
    return profile.lldp_command, "lldp"


# ---------------------------------------------------------------------------
# Parsers — keep working ones; stubs return [] until lab echoes are added
# ---------------------------------------------------------------------------


def parse_cisco_lldp(text: str) -> list[NeighborHit]:
    """Cisco `show lldp neighbors detail` (preferred); brief table as fallback."""
    hits = _parse_cisco_lldp_detail(text)
    if hits:
        return hits
    return _parse_lldp_brief_table(text)


def parse_cisco_cdp(text: str) -> list[NeighborHit]:
    """Cisco `show cdp neighbors detail`."""
    return _parse_cdp_detail(text)


def parse_huawei_lldp(text: str) -> list[NeighborHit]:
    """Huawei `display lldp neighbor`."""
    hits = _parse_huawei_lldp_neighbor(text)
    if hits:
        return hits
    return _parse_lldp_brief_table(text)


def parse_h3c_lldp(text: str) -> list[NeighborHit]:
    """H3C Comware LLDP — placeholder until lab echo is captured."""
    # TODO: replace with Comware-specific parser using real `display lldp ...` output.
    _ = text
    return []


def parse_zte_lldp(text: str) -> list[NeighborHit]:
    """ZTE ZXROS `show lldp neighbor brief` table."""
    hits = _parse_zte_lldp_brief(text)
    if hits:
        return hits
    return _parse_lldp_brief_table(text)


def parse_juniper_lldp(text: str) -> list[NeighborHit]:
    """Juniper Junos LLDP — placeholder until lab echo is captured."""
    # TODO: parse `show lldp neighbors` / detail from Junos sample.
    _ = text
    return []


def parse_nokia_lldp(text: str) -> list[NeighborHit]:
    """Nokia SROS/SRL LLDP — placeholder until lab echo is captured."""
    # TODO: parse `show system lldp neighbor` (SROS) / SRL equivalent.
    _ = text
    return []


def parse_ericsson_lldp(text: str) -> list[NeighborHit]:
    """Ericsson IPOS/SEOS LLDP — placeholder until lab echo is captured."""
    # TODO: parse vendor show output from lab.
    _ = text
    return []


def parse_generic_lldp(text: str) -> list[NeighborHit]:
    """Best-effort fallback when vendor is unknown."""
    hits = _parse_cisco_lldp_detail(text)
    if hits:
        return hits
    hits = _parse_huawei_lldp_neighbor(text)
    if hits:
        return hits
    return _parse_lldp_brief_table(text)


_VENDOR_PARSERS: dict[str, ParserFn] = {
    "cisco": parse_cisco_lldp,
    "huawei": parse_huawei_lldp,
    "h3c": parse_h3c_lldp,
    "zte": parse_zte_lldp,
    "juniper": parse_juniper_lldp,
    "nokia": parse_nokia_lldp,
    "ericsson": parse_ericsson_lldp,
    "generic": parse_generic_lldp,
}

# Parsers that intentionally return [] until lab samples are added.
STUB_PARSER_KEYS = frozenset({"h3c", "juniper", "nokia", "ericsson"})


def parser_meta(*, vendor: str = "", device_type: str = "") -> tuple[str, bool]:
    """Return (parser_key, is_stub)."""
    key = resolve_vendor_key(vendor, device_type)
    return key, key in STUB_PARSER_KEYS


def parse_neighbor_output(
    text: str,
    *,
    protocol: str = "lldp",
    vendor: str = "",
    device_type: str = "",
) -> list[NeighborHit]:
    """Parse neighbor CLI output using the device_type/vendor-specific parser."""
    raw = str(text or "")
    if not raw.strip():
        return []
    _ = protocol  # CDP discovery removed; always parse as LLDP
    key = resolve_vendor_key(vendor, device_type)

    parser = _VENDOR_PARSERS.get(key) or parse_generic_lldp
    hits = parser(raw)
    if hits:
        return hits

    # Soft fallbacks so an early/wrong tag still yields something useful.
    if key != "cisco":
        hits = parse_cisco_lldp(raw)
        if hits:
            return hits
    if key != "huawei":
        hits = parse_huawei_lldp(raw)
        if hits:
            return hits
    return []


# ---------------------------------------------------------------------------
# Shared low-level helpers
# ---------------------------------------------------------------------------


def _parse_cisco_lldp_detail(text: str) -> list[NeighborHit]:
    """Cisco IOS `show lldp neighbors detail` blocks starting at Local Intf."""
    raw = str(text or "")
    if not re.search(r"(?i)Local\s+Intf\s*:", raw):
        return []
    chunks = re.split(r"(?i)(?=Local\s+Intf\s*:)", raw)
    hits: list[NeighborHit] = []
    for chunk in chunks:
        if not re.search(r"(?i)Local\s+Intf\s*:", chunk):
            continue
        local_port = _kv(chunk, r"Local\s+Intf\s*:\s*(.+)")
        remote_port = _kv(chunk, r"Port\s+id\s*:\s*(.+)")
        sys_name = _kv(chunk, r"System\s+Name\s*:\s*(.+)")
        # Prefer IPv4 under Management Addresses; skip OID / MAC "Other:" lines.
        ip = ""
        m = re.search(
            r"(?is)Management\s+Addresses?\s*:(.*?)(?:\n\s*\n|Auto Negotiation|Total entries|$)",
            chunk,
        )
        if m:
            mgmt_lines = []
            for ln in (m.group(1) or "").splitlines():
                low = ln.lower()
                if "oid" in low or re.search(r"(?i)^\s*other\s*:", ln):
                    continue
                mgmt_lines.append(ln)
            ip_m = _IPV4_RE.search("\n".join(mgmt_lines))
            if ip_m:
                ip = ip_m.group(0)
        if not sys_name and not remote_port and not local_port:
            continue
        # Skip empty / not-advertised system names
        name = (sys_name or "").strip()
        if name.lower() in {"", "-", "not advertised"}:
            name = ""
        hits.append(
            NeighborHit(
                remote_name=name,
                remote_ip=ip,
                local_port=(local_port or "").strip(),
                remote_port=(remote_port or "").strip(),
                protocol="lldp",
            )
        )
    return hits


def _parse_zte_lldp_brief(text: str) -> list[NeighborHit]:
    """ZTE ZXROS `show lldp neighbor brief`.

    Columns: Local Interface | Scope | Chassis ID | Port ID | Holdtime | System Name
    Example:
      cgei-1/1/0/34  NB  744a.a42d.8970  cgei-1/1/0/36  91  KND-VKAU-EN1-Z20HS
    """
    raw = str(text or "")
    if not re.search(r"(?i)Local\s+Interface", raw) or not re.search(r"(?i)System\s+Name", raw):
        return []

    # Scope codes seen on ZXROS: NB / NC / NTPMR (and possibly others).
    row_re = re.compile(
        r"^(?P<local>\S+)\s+"
        r"(?P<scope>[A-Za-z]{2,8})\s+"
        r"(?P<chassis>\S+)\s+"
        r"(?P<port>\S+)\s+"
        r"(?P<hold>\d+)\s+"
        r"(?P<name>\S.*?)\s*$"
    )
    hits: list[NeighborHit] = []
    for ln in raw.splitlines():
        s = ln.strip()
        if not s or set(s) <= {"-", "="}:
            continue
        low = s.lower()
        if "local interface" in low or low.startswith(("total", "scope", "capability", "---")):
            continue
        if s.endswith("#") or "show lldp" in low:
            continue
        m = row_re.match(s)
        if not m:
            continue
        scope = m.group("scope").upper()
        # Reject rows that clearly aren't neighbor entries (e.g. mis-split header leftovers).
        if scope in {"INTERFACE", "CHASSIS", "PORT", "HOLDTIME", "SYSTEM"}:
            continue
        name = (m.group("name") or "").strip()
        local_port = (m.group("local") or "").strip()
        remote_port = (m.group("port") or "").strip()
        if not name and not remote_port and not local_port:
            continue
        hits.append(
            NeighborHit(
                remote_name=name,
                local_port=local_port,
                remote_port=remote_port,
                protocol="lldp",
            )
        )
    return hits


def _parse_lldp_brief_table(text: str) -> list[NeighborHit]:
    """Cisco/ZTE-style brief table: Device ID / Local Intf / ... / Port ID."""
    lines = [ln.rstrip() for ln in str(text or "").splitlines()]
    start = -1
    for i, ln in enumerate(lines):
        low = ln.lower()
        if "device id" in low and ("local" in low or "intf" in low or "port" in low):
            start = i + 1
            break
        if "system name" in low and "local" in low:
            start = i + 1
            break
    if start < 0:
        return []
    hits: list[NeighborHit] = []
    for ln in lines[start:]:
        s = ln.strip()
        if not s or set(s) <= {"-", "="}:
            continue
        if s.lower().startswith(("total", "capability", "---")):
            continue
        parts = s.split()
        if len(parts) < 2:
            continue
        remote = parts[0]
        local_port = parts[1] if len(parts) >= 2 else ""
        remote_port = parts[-1] if len(parts) >= 4 else ""
        if remote.lower() in {"device", "system", "chassis"}:
            continue
        hits.append(
            NeighborHit(
                remote_name=remote,
                local_port=local_port,
                remote_port=remote_port,
                protocol="lldp",
            )
        )
    return hits


def _parse_huawei_lldp_neighbor(text: str) -> list[NeighborHit]:
    """Huawei VRP `display lldp neighbor` — per-interface sections."""
    raw = str(text or "")
    # Split on "<ifname> has N neighbor(s):"
    header_re = re.compile(
        r"(?im)^(\S+)\s+has\s+(\d+)\s+neighbor\(s\)\s*:\s*$"
    )
    hits: list[NeighborHit] = []
    matches = list(header_re.finditer(raw))
    if not matches:
        # Older compact sample with Local Interface: field
        return _parse_huawei_lldp_blocks_legacy(raw)

    for i, m in enumerate(matches):
        local_if = m.group(1).strip()
        count = int(m.group(2))
        if count <= 0:
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        section = raw[start:end]
        # One section may contain multiple neighbors; split on Neighbor index
        sub_chunks = re.split(r"(?im)(?=^Neighbor\s+index\s*:)", section)
        for chunk in sub_chunks:
            if not re.search(r"(?i)Neighbor\s+index\s*:", chunk):
                # Sometimes fields appear without explicit index; still try once.
                if not re.search(r"(?i)System\s+name\s*:", chunk):
                    continue
            sys_name = _kv(chunk, r"System\s+name\s*:\s*(.+)")
            port_id = _kv(chunk, r"Port\s+ID\s*:\s*(.+)")
            mgmt = _kv(chunk, r"Management\s+address\s*:\s*(.+)")
            ip = ""
            if mgmt:
                ip_m = _IPV4_RE.search(mgmt)
                if ip_m:
                    ip = ip_m.group(0)
            name = (sys_name or "").strip()
            # Hostname may be FQDN — keep as-is; matcher strips domain.
            if not name and not port_id and not ip:
                continue
            hits.append(
                NeighborHit(
                    remote_name=name,
                    remote_ip=ip,
                    local_port=local_if,
                    remote_port=(port_id or "").strip(),
                    protocol="lldp",
                )
            )
    return hits


def _parse_huawei_lldp_blocks_legacy(text: str) -> list[NeighborHit]:
    """Older/compact Huawei block with Local Interface field."""
    hits: list[NeighborHit] = []
    blocks = re.split(r"\n\s*\n", str(text or ""))
    for block in blocks:
        if not block.strip():
            continue
        sys_name = _kv(block, r"System\s+name\s*[:=]\s*(.+)")
        local_if = _kv(block, r"Local\s+(?:Interface|Port)\s*[:=]\s*(.+)")
        port_id = _kv(block, r"Port\s+ID\s*[:=]\s*(.+)")
        mgmt = _kv(block, r"Management\s+address\s*[:=]\s*(.+)")
        if not sys_name and not port_id:
            continue
        ip = ""
        if mgmt:
            m = _IPV4_RE.search(mgmt)
            if m:
                ip = m.group(0)
        hits.append(
            NeighborHit(
                remote_name=(sys_name or "").strip(),
                remote_ip=ip,
                local_port=(local_if or "").strip(),
                remote_port=(port_id or "").strip(),
                protocol="lldp",
            )
        )
    return hits


def _parse_cdp_detail(text: str) -> list[NeighborHit]:
    """Cisco `show cdp neighbors detail`."""
    hits: list[NeighborHit] = []
    chunks = re.split(r"(?i)\n(?=Device ID\s*:)", str(text or ""))
    for chunk in chunks:
        if not re.search(r"(?i)Device\s+ID\s*:", chunk):
            continue
        device_id = _kv(chunk, r"Device\s+ID\s*:\s*(.+)")
        ip = ""
        ip_line = _kv(chunk, r"IP(?:v4)?\s+address\s*:\s*(.+)")
        if ip_line:
            m = _IPV4_RE.search(ip_line)
            if m:
                ip = m.group(0)
        local_port = _kv(chunk, r"Interface\s*:\s*([^,\n]+)")
        remote_port = _kv(chunk, r"Port ID\s*(?:\(outgoing port\))?\s*:\s*(.+)")
        if not device_id and not ip:
            continue
        hits.append(
            NeighborHit(
                remote_name=(device_id or "").strip(),
                remote_ip=ip,
                local_port=(local_port or "").strip().rstrip(","),
                remote_port=(remote_port or "").strip(),
                protocol="cdp",
            )
        )
    return hits


def _kv(text: str, pattern: str) -> str:
    m = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    if not m:
        return ""
    return str(m.group(1) or "").strip()


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
    # Already-short forms: gi0/0, te1/0/1, xge0/0/1, 10ge1/0/1
    return s
