"""Normalize UME TP DN / userLabel into CLI-like ifnames for Fabric merge."""

from __future__ import annotations

import re

from .topology_lldp import normalize_ifname

# xxvgei-1/1/0/32, xgei-0/0/1/4, gei-1/1/0/1, … (may follow `_` in userLabel)
_IFNAME_TOKEN_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])((?:xxvgei|xgei|cgei|xlgei|lgei|gei|gi|eth)"
    r"(?:-[\d./]+|[\d./]+))"
)
_ETH_COLON_RE = re.compile(r"(?i)\bETH:(\d+)\b")
_BRACKET_SLOT_RE = re.compile(r"\[(\d+/\d+/\d+)\]")
_EQ_SH_RE = re.compile(r"(?i)/sh=(\d+)")
_EQ_SL_RE = re.compile(r"(?i)/sl=(\d+)")
_EQ_SSL_RE = re.compile(r"(?i)/ssl=(\d+)")
_PTP_PORT_RE = re.compile(
    r"(?i)PTP=\{/p=\d+_(\d+)\}|/p=\d+_(\d+)\b"
)


def extract_ifnames_from_user_label(label: str) -> list[str]:
    """Ordered unique CLI ifnames embedded in UME link userLabel."""
    s = str(label or "")
    out: list[str] = []
    seen: set[str] = set()
    for m in _IFNAME_TOKEN_RE.finditer(s):
        raw = m.group(1).strip().rstrip(".,);:")
        # Skip bare ETH: handled separately; require a digit somewhere.
        if not any(ch.isdigit() for ch in raw):
            continue
        key = normalize_ifname(raw)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def port_suffix_from_tp_ref(tp_ref: str) -> str:
    """Build shelf/slot/…/port suffix from EQ+PTP, e.g. ``1/1/0/32`` or ``0/0/1/4``."""
    s = str(tp_ref or "")
    if not s:
        return ""
    sh_m = _EQ_SH_RE.search(s)
    sl_m = _EQ_SL_RE.search(s)
    ssl_m = _EQ_SSL_RE.search(s)
    p_m = _PTP_PORT_RE.search(s)
    if not (sh_m and sl_m and p_m):
        return ""
    sh = sh_m.group(1)
    sl = sl_m.group(1)
    port = p_m.group(1) or p_m.group(2)
    if ssl_m:
        return f"{sh}/{sl}/{ssl_m.group(1)}/{port}"
    return f"{sh}/{sl}/0/{port}"


def port_suffix_from_eth_label(label: str, *, end_index: int = 0) -> str:
    """From ``…[1/1/0]_ETH:28_…`` build ``1/1/0/28`` (nth ETH occurrence)."""
    s = str(label or "")
    slots = _BRACKET_SLOT_RE.findall(s)
    eths = _ETH_COLON_RE.findall(s)
    if not slots or not eths:
        return ""
    i = max(0, min(int(end_index), len(slots) - 1, len(eths) - 1))
    return f"{slots[i]}/{eths[i]}"


def resolve_ume_ifname(
    *,
    tp_ref: str,
    user_label: str = "",
    end_index: int = 0,
) -> str:
    """Best-effort CLI ifname for one end of a UME link.

    Preference: userLabel ifname token → ETH:+bracket → EQ+PTP suffix.
    """
    tokens = extract_ifnames_from_user_label(user_label)
    if tokens:
        idx = max(0, min(int(end_index), len(tokens) - 1))
        return tokens[idx][:128]

    eth = port_suffix_from_eth_label(user_label, end_index=end_index)
    if eth:
        return normalize_ifname(eth)[:128]

    suffix = port_suffix_from_tp_ref(tp_ref)
    if suffix:
        return normalize_ifname(suffix)[:128]
    return ""


def resolve_link_ifnames(
    *,
    a_end_tp_ref: str,
    z_end_tp_ref: str,
    user_label: str = "",
) -> tuple[str, str]:
    tokens = extract_ifnames_from_user_label(user_label)
    if len(tokens) >= 2:
        return tokens[0][:128], tokens[1][:128]
    if len(tokens) == 1:
        a = tokens[0]
        z = resolve_ume_ifname(tp_ref=z_end_tp_ref, user_label=user_label, end_index=1)
        return a[:128], z[:128]
    return (
        resolve_ume_ifname(tp_ref=a_end_tp_ref, user_label=user_label, end_index=0),
        resolve_ume_ifname(tp_ref=z_end_tp_ref, user_label=user_label, end_index=1),
    )


def port_keys_compatible(a: str, b: str) -> bool:
    """True if two ports are the same after normalize, or share a numeric suffix."""
    na = normalize_ifname(a)
    nb = normalize_ifname(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # xxvgei-1/1/0/32 vs 1/1/0/32
    if na.endswith(nb) or nb.endswith(na):
        return True
    # strip alpha prefix before first digit
    def _num_tail(s: str) -> str:
        for i, ch in enumerate(s):
            if ch.isdigit():
                return s[i:]
        return s

    return _num_tail(na) == _num_tail(nb) and bool(_num_tail(na))
