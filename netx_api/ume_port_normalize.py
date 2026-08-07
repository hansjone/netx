"""Normalize UME TP DN / userLabel into CLI-like ifnames for Fabric merge.

Authority order for each link end:
1. EQ+PTP → numeric ``shelf/slot/…/port`` (same as LLDP ``x/x/x/x``)
2. userLabel media token whose numeric tail matches that suffix (``xxvgei-…``)
3. ``[slot]_ETH:N`` / ``NGE:N`` forms that match the suffix
4. bare TP suffix (still LLDP-compatible via ``port_keys_compatible``)

Never assign A/Z from label token order alone — labels often embed the
*neighbor* port first and swap the two ends.
"""

from __future__ import annotations

import re

from .topology_lldp import normalize_ifname

# xxvgei-1/1/0/32, xgei-0/0/1/4, gei-1/1/0/1, … (may follow `_` in userLabel)
_IFNAME_TOKEN_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])((?:xxvgei|xgei|cgei|xlgei|lgei|gei|gi|eth)"
    r"(?:-[\d./]+|[\d./]+))"
)
# `_ETH:28` — `\b` fails after `_` (word char); use lookbehind instead.
_ETH_COLON_RE = re.compile(r"(?i)(?<![A-Za-z0-9])ETH:(\d+)\b")
# `25GE:14` / `10GE:32` style local port markers in some regions' labels.
_RATE_GE_RE = re.compile(r"(?i)(?<![A-Za-z0-9])(?:\d+)?GE:(\d+)\b")
# `[1/1/0]` or `[0-1-1]`
_BRACKET_SLOT_RE = re.compile(r"\[(\d+[/\-]\d+[/\-]\d+)\]")
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
        # Drop truncated junk like ``cgei-0`` / ``xxvgei-0`` from cut labels.
        if numeric_port_tail(key).count("/") < 2:
            continue
        seen.add(key)
        out.append(key)
    return out


def numeric_port_tail(ifname: str) -> str:
    """Digits path after media prefix: ``xxvgei-1/1/0/14`` → ``1/1/0/14``."""
    s = normalize_ifname(ifname)
    if not s:
        return ""
    for i, ch in enumerate(s):
        if ch.isdigit():
            return s[i:]
    return ""


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


def _norm_bracket_slot(raw: str) -> str:
    return str(raw or "").strip().replace("-", "/")


def iter_eth_style_suffixes(label: str) -> list[str]:
    """``[1/1/0]_ETH:28`` / ``[0-1-1]-25GE:14`` → ``1/1/0/28`` / ``0/1/1/14``."""
    s = str(label or "")
    slots = [
        (m.start(), _norm_bracket_slot(m.group(1)))
        for m in _BRACKET_SLOT_RE.finditer(s)
    ]
    ports: list[tuple[int, str]] = []
    for m in _ETH_COLON_RE.finditer(s):
        ports.append((m.start(), m.group(1)))
    for m in _RATE_GE_RE.finditer(s):
        ports.append((m.start(), m.group(1)))
    ports.sort(key=lambda x: x[0])
    if not slots or not ports:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for (_spos, slot), (_ppos, port) in zip(slots, ports):
        if slot.count("/") != 2:
            continue
        key = normalize_ifname(f"{slot}/{port}")
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def port_suffix_from_eth_label(label: str, *, end_index: int = 0) -> str:
    """From ``…[1/1/0]_ETH:28_…`` build ``1/1/0/28`` (nth ETH occurrence)."""
    all_suf = iter_eth_style_suffixes(label)
    if not all_suf:
        return ""
    i = max(0, min(int(end_index), len(all_suf) - 1))
    return all_suf[i]


def find_ifname_matching_suffix(suffix: str, user_label: str) -> str:
    """Return a label CLI ifname whose numeric tail equals ``suffix``."""
    want = normalize_ifname(suffix)
    if not want:
        return ""
    for tok in extract_ifnames_from_user_label(user_label):
        tail = numeric_port_tail(tok)
        if tail == want or normalize_ifname(tok) == want:
            return tok
    return ""


def resolve_ume_ifname(
    *,
    tp_ref: str,
    user_label: str = "",
    end_index: int = 0,
) -> str:
    """Best-effort CLI ifname for one end of a UME link.

    Preference: EQ+PTP suffix → matching label media token → ETH/GE match → bare suffix.
    Without TP: return empty (caller should show ``userLabel``, not invent A/Z ports).
    """
    _ = end_index  # kept for call-site compatibility; unused without TP
    suffix = port_suffix_from_tp_ref(tp_ref)
    if not suffix:
        return ""
    hit = find_ifname_matching_suffix(suffix, user_label)
    if hit:
        return hit[:128]
    want = normalize_ifname(suffix)
    for eth in iter_eth_style_suffixes(user_label):
        if normalize_ifname(eth) == want:
            return want[:128]
    return want[:128]


def resolve_link_ifnames(
    *,
    a_end_tp_ref: str,
    z_end_tp_ref: str,
    user_label: str = "",
) -> tuple[str, str]:
    """Resolve A/Z independently — TP pins the port; label only supplies media prefix.

    If neither end has EQ+PTP, returns empty ports (display ``userLabel`` instead).
    """
    return (
        resolve_ume_ifname(tp_ref=a_end_tp_ref, user_label=user_label, end_index=0),
        resolve_ume_ifname(tp_ref=z_end_tp_ref, user_label=user_label, end_index=1),
    )


def is_label_placeholder_port(ifname: str) -> bool:
    """Synthetic fabric port for UME links that only have userLabel (no TP)."""
    return normalize_ifname(ifname).startswith("label:")


def label_placeholder_ports(link_id: str) -> tuple[str, str]:
    lid = str(link_id or "").strip() or "unknown"
    return (f"label:{lid}:a"[:128], f"label:{lid}:z"[:128])


def port_keys_compatible(a: str, b: str) -> bool:
    """True if two ports are the same after normalize, or share the same numeric path.

    Uses exact equality of ``numeric_port_tail`` (e.g. ``xxvgei-1/1/0/32`` ↔ ``1/1/0/32``).
    Does **not** use naive ``endswith`` (avoids ``11/1/0/1`` matching ``1/1/0/1``).
    """
    na = normalize_ifname(a)
    nb = normalize_ifname(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if is_label_placeholder_port(na) or is_label_placeholder_port(nb):
        return False
    ta = numeric_port_tail(na)
    tb = numeric_port_tail(nb)
    # Require a real shelf/…/port path (at least one slash).
    return bool(ta) and ta == tb and "/" in ta


_MEDIA_PREFIX_RE = re.compile(
    r"(?i)^(xxvgei|xgei|cgei|xlgei|lgei|gei|gi|eth)-"
)


def has_media_prefix(ifname: str) -> bool:
    return bool(_MEDIA_PREFIX_RE.match(normalize_ifname(ifname)))


def prefer_richer_ifname(current: str, candidate: str) -> str:
    """When ports are compatible, prefer the media-prefixed (LLDP-style) name.

    Bare ``1/1/0/32`` + ``xxvgei-1/1/0/32`` → ``xxvgei-1/1/0/32``.
    Incompatible candidates are ignored (keep ``current``).
    """
    cur = normalize_ifname(current)
    can = normalize_ifname(candidate)
    if not can:
        return cur
    if not cur:
        return can
    if not port_keys_compatible(cur, can):
        return cur
    if has_media_prefix(can) and not has_media_prefix(cur):
        return can
    return cur
