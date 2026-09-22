"""ZTE config intent: ``show running-config bgp`` / MIM ``!<bgp>``.

Emits one row per ``(local_as, afi, vrf, neighbor|peer_group)`` activation under
address-family. IP literals go in ``neighbor``; non-IP names (peer-groups)
go in ``peer_group``. Never captures password / secret lines.

Supports **multiple** ``router bgp <asn>`` instances: each peer is scoped to
its enclosing local AS (meta / peer-group expand do not cross AS boundaries).

For **global** (non-VRF) address-families, a peer-group ``activate`` is also
expanded into one row per global member ``neighbor <ip> peer-group <name>``
so discover/bind sees the real Neighbor IPs (direct activates ∪ group
members). VRF address-families do **not** expand from global membership.

Route-maps under an address-family are scoped to that ``(afi, vrf)``;
global (top-level) route-maps apply to all AF rows for that neighbor and
are overlaid by AF-specific maps when both exist.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from .config_common import extract_mim_section, is_secret_line

RULE_KEYS: tuple[str, ...] = ()

_ROUTER_BGP_RE = re.compile(r"^\s*router\s+bgp\s+(\S+)\s*$", re.I)
_NEI_REMOTE_RE = re.compile(r"^\s*neighbor\s+(\S+)\s+remote-as\s+(\S+)\s*$", re.I)
_NEI_UPD_RE = re.compile(r"^\s*neighbor\s+(\S+)\s+update-source\s+(\S+)\s*$", re.I)
_NEI_PG_RE = re.compile(r"^\s*neighbor\s+(\S+)\s+peer-group(?:\s+(\S+))?\s*$", re.I)
_NEI_RM_RE = re.compile(
    r"^\s*neighbor\s+(\S+)\s+route-map\s+(\S+)\s+(in|out)\s*$", re.I
)
_NEI_ACT_RE = re.compile(
    r"^\s*neighbor\s+(\S+)\s+activate(?:\s+(disable))?\s*$", re.I
)
_AF_V4_VRF_RE = re.compile(r"^\s*address-family\s+ipv4\s+vrf\s+(\S+)\s*$", re.I)
_AF_V6_VRF_RE = re.compile(r"^\s*address-family\s+ipv6\s+vrf\s+(\S+)\s*$", re.I)
_AF_L2VPN_RE = re.compile(r"^\s*address-family\s+l2vpn\s+(\S+)\s*$", re.I)
# Multi-word AFs first (e.g. ``ipv6 sr-policy``); then single token.
_AF_MULTI_RE = re.compile(
    r"^\s*address-family\s+(ipv6\s+sr-policy|ipv4\s+sr-policy)\s*$", re.I
)
_AF_SINGLE_RE = re.compile(r"^\s*address-family\s+(\S+)\s*$", re.I)
_IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")

_AfKey = tuple[str, str, str, str]  # (local_as, afi, vrf, token)
_MetaKey = tuple[str, str]  # (local_as, token)


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
    local_as: str,
    afi: str,
    vrf: str,
    meta: Mapping[_MetaKey, dict[str, str]],
    af_rm: Mapping[_AfKey, dict[str, str]],
) -> dict[str, str]:
    """Global neighbor meta + AF-scoped route-maps (AF wins on conflict)."""
    info = dict(meta.get((local_as, token)) or {})
    scoped = af_rm.get((local_as, afi, vrf, token)) or {}
    for key in ("route_map_in", "route_map_out"):
        if scoped.get(key):
            info[key] = scoped[key]
    return info


def _row(
    *,
    local_as: str,
    afi: str,
    vrf: str,
    token: str,
    act: str,
    info: Mapping[str, str],
) -> dict[str, Any]:
    neighbor, peer_group = _split_neighbor_cols(token, info)
    return {
        "local_as": str(local_as or "")[:16],
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


def _normalize_afi_token(raw: str) -> str:
    """Collapse multi-word AF names to a stable token (``ipv6 sr-policy`` → ``ipv6-sr-policy``)."""
    parts = [p for p in re.split(r"\s+", str(raw or "").strip().lower()) if p]
    return "-".join(parts)


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
    meta: dict[_MetaKey, dict[str, str]] = {}
    af_rm: dict[_AfKey, dict[str, str]] = {}
    local_as = ""
    afi = ""
    vrf = ""
    activations: list[tuple[str, str, str, str, str]] = []
    # (local_as, afi, vrf, token, activate)

    def _meta(token: str) -> dict[str, str]:
        key = (local_as, token)
        slot = meta.get(key)
        if slot is None:
            slot = {}
            meta[key] = slot
        return slot

    for raw in body.splitlines():
        line = raw.rstrip()
        if is_secret_line(line):
            continue

        m = _ROUTER_BGP_RE.match(line)
        if m:
            local_as = m.group(1).strip()
            afi, vrf = "", ""
            continue

        # End of ``router bgp`` block (column-0 ``$``).
        if re.match(r"^\$\s*$", line):
            afi, vrf = "", ""
            local_as = ""
            continue

        # End of address-family (exactly two-space indent ``$``).
        if re.match(r"^  \$\s*$", line) and afi:
            afi, vrf = "", ""
            continue

        if not local_as:
            continue

        m = _NEI_REMOTE_RE.match(line)
        if m:
            nei = m.group(1).strip()
            _meta(nei)["remote_as"] = m.group(2).strip()
            continue
        m = _NEI_UPD_RE.match(line)
        if m:
            nei = m.group(1).strip()
            _meta(nei)["update_source"] = m.group(2).strip()
            continue
        m = _NEI_PG_RE.match(line)
        if m:
            nei = m.group(1).strip()
            slot = _meta(nei)
            group = (m.group(2) or "").strip()
            if group:
                # neighbor <ip> peer-group <name>
                slot["peer_group"] = group
            else:
                # neighbor <name> peer-group  → peer-group definition
                slot["is_group"] = "1"
            continue
        m = _NEI_RM_RE.match(line)
        if m and not afi:
            nei = m.group(1).strip()
            direction = m.group(3).lower()
            key = "route_map_in" if direction == "in" else "route_map_out"
            _meta(nei)[key] = m.group(2).strip()
            continue
        m = _AF_V4_VRF_RE.match(line)
        if m:
            afi, vrf = "ipv4", m.group(1).strip()
            continue
        m = _AF_V6_VRF_RE.match(line)
        if m:
            afi, vrf = "ipv6", m.group(1).strip()
            continue
        m = _AF_L2VPN_RE.match(line)
        if m:
            afi, vrf = f"l2vpn-{m.group(1).lower()}", ""
            continue
        m = _AF_MULTI_RE.match(line)
        if m:
            afi, vrf = _normalize_afi_token(m.group(1)), ""
            continue
        m = _AF_SINGLE_RE.match(line)
        if m:
            afi, vrf = m.group(1).lower(), ""
            continue
        m = _NEI_ACT_RE.match(line)
        if m and afi:
            nei = m.group(1).strip()
            act = "disable" if m.group(2) else "enable"
            activations.append((local_as, afi, vrf, nei, act))
            continue
        m = _NEI_RM_RE.match(line)
        if m and afi:
            nei = m.group(1).strip()
            direction = m.group(3).lower()
            rk = "route_map_in" if direction == "in" else "route_map_out"
            slot = af_rm.setdefault((local_as, afi, vrf, nei), {})
            slot[rk] = m.group(2).strip()

    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()

    def _append(row: dict[str, Any]) -> None:
        key = (
            row["local_as"],
            row["afi"],
            row["vrf"],
            row["neighbor"],
            row["peer_group"],
        )
        if key in seen:
            return
        seen.add(key)
        out.append(row)

    for las, afi_s, vrf_s, token, act in activations:
        info = _merge_info(
            token, local_as=las, afi=afi_s, vrf=vrf_s, meta=meta, af_rm=af_rm
        )
        row = _row(
            local_as=las, afi=afi_s, vrf=vrf_s, token=token, act=act, info=info
        )
        _append(row)

        # Global AF: peer-group activate → expand to member Neighbor IPs.
        # VRF AF: do not expand from global peer-group membership.
        if vrf_s:
            continue
        pg = str(row.get("peer_group") or "").strip()
        if row.get("neighbor") or not pg:
            continue
        for (m_as, member), minfo in meta.items():
            if m_as != las:
                continue
            if not _is_ip_neighbor(member):
                continue
            if str(minfo.get("peer_group") or "").strip() != pg:
                continue
            m_info = _merge_info(
                member, local_as=las, afi=afi_s, vrf=vrf_s, meta=meta, af_rm=af_rm
            )
            # AF route-maps on the peer-group name apply to expanded members
            # unless the member already has an AF-specific map.
            pg_info = _merge_info(
                pg, local_as=las, afi=afi_s, vrf=vrf_s, meta=meta, af_rm=af_rm
            )
            for key in ("route_map_in", "route_map_out"):
                if pg_info.get(key) and not m_info.get(key):
                    m_info[key] = pg_info[key]
            m_row = _row(
                local_as=las,
                afi=afi_s,
                vrf=vrf_s,
                token=member,
                act=act,
                info=m_info,
            )
            m_row["peer_group"] = pg[:64]
            _append(m_row)

    # Peers with remote-as but no AF activate (scoped per local AS)
    activated_tokens = {(las, n) for las, _, _, n, _ in activations}
    for (las, token), info in meta.items():
        if not info.get("remote_as"):
            continue
        if (las, token) in activated_tokens:
            continue
        row = _row(
            local_as=las, afi="global", vrf="", token=token, act="", info=info
        )
        _append(row)
    return out


normalize_config_bgp_peer.RULE_KEYS = RULE_KEYS
