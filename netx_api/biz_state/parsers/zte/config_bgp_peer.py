"""ZTE config intent: ``show running-config bgp`` / MIM ``!<bgp>``.

Emits one row per ``(afi, vrf, neighbor|peer_group)`` activation under
address-family. IP literals go in ``neighbor``; non-IP names (peer-groups)
go in ``peer_group``. Never captures password / secret lines.

Route-maps under an address-family are scoped to that ``(afi, vrf)``;
global (top-level) route-maps apply to all AF rows for that neighbor and
are overlaid by AF-specific maps when both exist.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from .config_common import extract_mim_section, is_secret_line

RULE_KEYS: tuple[str, ...] = ()

_NEI_REMOTE_RE = re.compile(r"^\s*neighbor\s+(\S+)\s+remote-as\s+(\S+)\s*$", re.I)
_NEI_UPD_RE = re.compile(r"^\s*neighbor\s+(\S+)\s+update-source\s+(\S+)\s*$", re.I)
_NEI_PG_RE = re.compile(r"^\s*neighbor\s+(\S+)\s+peer-group(?:\s+(\S+))?\s*$", re.I)
_NEI_RM_RE = re.compile(
    r"^\s*neighbor\s+(\S+)\s+route-map\s+(\S+)\s+(in|out)\s*$", re.I
)
_NEI_ACT_RE = re.compile(
    r"^\s*neighbor\s+(\S+)\s+activate(?:\s+(disable))?\s*$", re.I
)
_IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")

_AfKey = tuple[str, str, str]  # (afi, vrf, token)


def _is_ip_neighbor(token: str) -> bool:
    """True for IPv4/IPv6 literals; false for peer-group names (even with ':')."""
    tok = str(token or "").strip()
    if not tok:
        return False
    if _IPV4_RE.match(tok):
        return True
    # IPv6 must look like an address, not ``asn:name`` peer-group labels.
    if ":" not in tok:
        return False
    try:
        import ipaddress

        ipaddress.ip_address(tok.split("%", 1)[0])
        return True
    except ValueError:
        return False


def _split_neighbor_cols(token: str, info: Mapping[str, str]) -> tuple[str, str]:
    """Return ``(neighbor, peer_group)`` columns for a neighbor token."""
    tok = str(token or "").strip()
    if _is_ip_neighbor(tok):
        return tok, str(info.get("peer_group") or "")
    # Non-IP token is a peer-group name
    return "", tok


def _merge_info(
    token: str,
    *,
    afi: str,
    vrf: str,
    meta: Mapping[str, dict[str, str]],
    af_rm: Mapping[_AfKey, dict[str, str]],
) -> dict[str, str]:
    """Global neighbor meta + AF-scoped route-maps (AF wins on conflict)."""
    info = dict(meta.get(token) or {})
    scoped = af_rm.get((afi, vrf, token)) or {}
    for key in ("route_map_in", "route_map_out"):
        if scoped.get(key):
            info[key] = scoped[key]
    return info


def _row(
    *,
    afi: str,
    vrf: str,
    token: str,
    act: str,
    info: Mapping[str, str],
) -> dict[str, Any]:
    neighbor, peer_group = _split_neighbor_cols(token, info)
    return {
        "afi": afi[:32],
        "vrf": vrf[:128],
        "neighbor": neighbor[:128],
        "peer_group": peer_group[:64],
        "remote_as": str(info.get("remote_as") or "")[:16],
        "activate": act[:16],
        "update_source": str(info.get("update_source") or "")[:64],
        "route_map_in": str(info.get("route_map_in") or "")[:128],
        "route_map_out": str(info.get("route_map_out") or "")[:128],
    }


def normalize_config_bgp_peer(
    *,
    raw_text: str,
    fsm_tables: Mapping[str, list[dict[str, Any]]] | None = None,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    _ = (fsm_tables, vendor, device_type, command, params)
    body = extract_mim_section(raw_text, "bgp")
    meta: dict[str, dict[str, str]] = {}
    af_rm: dict[_AfKey, dict[str, str]] = {}
    afi = ""
    vrf = ""
    activations: list[tuple[str, str, str, str]] = []
    # (afi, vrf, token, activate)

    for raw in body.splitlines():
        line = raw.rstrip()
        if is_secret_line(line):
            continue
        m = _NEI_REMOTE_RE.match(line)
        if m:
            nei = m.group(1).strip()
            meta.setdefault(nei, {})
            meta[nei]["remote_as"] = m.group(2).strip()
            continue
        m = _NEI_UPD_RE.match(line)
        if m:
            nei = m.group(1).strip()
            meta.setdefault(nei, {})
            meta[nei]["update_source"] = m.group(2).strip()
            continue
        m = _NEI_PG_RE.match(line)
        if m:
            nei = m.group(1).strip()
            meta.setdefault(nei, {})
            group = (m.group(2) or "").strip()
            if group:
                # neighbor <ip> peer-group <name>
                meta[nei]["peer_group"] = group
            else:
                # neighbor <name> peer-group  → peer-group definition
                meta[nei]["is_group"] = "1"
            continue
        m = _NEI_RM_RE.match(line)
        if m and not afi:
            nei = m.group(1).strip()
            meta.setdefault(nei, {})
            direction = m.group(3).lower()
            key = "route_map_in" if direction == "in" else "route_map_out"
            meta[nei][key] = m.group(2).strip()
            continue
        m = re.match(r"^\s*address-family\s+ipv4\s+vrf\s+(\S+)\s*$", line, re.I)
        if m:
            afi, vrf = "ipv4", m.group(1).strip()
            continue
        m = re.match(r"^\s*address-family\s+ipv6\s+vrf\s+(\S+)\s*$", line, re.I)
        if m:
            afi, vrf = "ipv6", m.group(1).strip()
            continue
        m = re.match(r"^\s*address-family\s+l2vpn\s+(\S+)\s*$", line, re.I)
        if m:
            afi, vrf = f"l2vpn-{m.group(1).lower()}", ""
            continue
        m = re.match(r"^\s*address-family\s+(\S+)\s*$", line, re.I)
        if m:
            afi, vrf = m.group(1).lower(), ""
            continue
        if re.match(r"^\s*\$\s*$", line) and afi:
            if line.startswith("  ") and not line.startswith("    "):
                afi, vrf = "", ""
            continue
        m = _NEI_ACT_RE.match(line)
        if m and afi:
            nei = m.group(1).strip()
            act = "disable" if m.group(2) else "enable"
            activations.append((afi, vrf, nei, act))
            continue
        m = _NEI_RM_RE.match(line)
        if m and afi:
            nei = m.group(1).strip()
            direction = m.group(3).lower()
            rk = "route_map_in" if direction == "in" else "route_map_out"
            slot = af_rm.setdefault((afi, vrf, nei), {})
            slot[rk] = m.group(2).strip()

    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for afi_s, vrf_s, token, act in activations:
        info = _merge_info(token, afi=afi_s, vrf=vrf_s, meta=meta, af_rm=af_rm)
        row = _row(afi=afi_s, vrf=vrf_s, token=token, act=act, info=info)
        key = (row["afi"], row["vrf"], row["neighbor"], row["peer_group"])
        if key in seen:
            continue
        seen.add(key)
        out.append(row)

    # Global peers with remote-as but no AF activate
    for token, info in meta.items():
        if not info.get("remote_as"):
            continue
        if any(n == token for _, _, n, _ in activations):
            continue
        row = _row(afi="global", vrf="", token=token, act="", info=info)
        key = (row["afi"], row["vrf"], row["neighbor"], row["peer_group"])
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


normalize_config_bgp_peer.RULE_KEYS = RULE_KEYS
