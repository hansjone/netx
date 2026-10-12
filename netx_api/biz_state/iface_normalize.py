"""Interface name normalize + port-map resolve (exact + parent.subif).

Normalize rules (template-owned): rewrite type aliases like ``GE`` → ``gei``.
Port map (job-owned): rewrite before → after, with subinterface suffix inheritance.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence


def normalize_iface_rules(raw: Any) -> list[dict[str, str]]:
    """Accept list of {from,to} or ``from,to`` lines; drop empties; longest ``from`` first."""
    out: list[dict[str, str]] = []
    if not raw:
        return out
    if isinstance(raw, str):
        items: list[Any] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "," in line:
                a, b = line.split(",", 1)
                items.append({"from": a.strip(), "to": b.strip()})
            elif "\t" in line:
                a, b = line.split("\t", 1)
                items.append({"from": a.strip(), "to": b.strip()})
            else:
                parts = line.split()
                if len(parts) >= 2:
                    items.append({"from": parts[0], "to": parts[1]})
        raw = items
    if not isinstance(raw, (list, tuple)):
        return out
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        fr = str(item.get("from") or item.get("src") or "").strip()
        to = str(item.get("to") or item.get("dst") or "").strip()
        if fr and to:
            rule = {"from": fr, "to": to}
            side = str(item.get("side") or "").strip().lower()
            if side in ("before", "after"):
                rule["side"] = side
            vendor = str(item.get("vendor") or "").strip().lower()
            if vendor:
                rule["vendor"] = vendor
            out.append(rule)
    # Longest prefix first so XXVGE wins over XGE / GE.
    out.sort(key=lambda r: len(r["from"]), reverse=True)
    return out


def _prefix_ok(name: str, prefix: str) -> bool:
    """True when ``name`` starts with ``prefix`` (ci) and boundary is safe."""
    if not name or not prefix:
        return False
    n = name
    p = prefix
    if len(n) < len(p):
        return False
    if n[: len(p)].lower() != p.lower():
        return False
    if len(n) == len(p):
        return True
    nxt = n[len(p)]
    return nxt.isdigit() or nxt in "-/"


def normalize_iface_name(name: str, rules: Sequence[Mapping[str, str]] | None) -> str:
    """Apply first matching type-prefix rule; leave subif / rest intact."""
    text = str(name or "").strip()
    if not text or not rules:
        return text
    for rule in rules:
        fr = str(rule.get("from") or "").strip()
        to = str(rule.get("to") or "").strip()
        if not fr or not to:
            continue
        if _prefix_ok(text, fr):
            return to + text[len(fr) :]
    return text


def apply_iface_normalize(
    row: dict[str, Any],
    *,
    iface_fields: Sequence[str],
    rules: Sequence[Mapping[str, str]] | None,
) -> dict[str, Any]:
    if not rules or not iface_fields:
        return dict(row)
    out = dict(row)
    for f in iface_fields:
        key = str(f or "").strip()
        if not key:
            continue
        val = str(out.get(key) or "").strip()
        if val:
            out[key] = normalize_iface_name(val, rules)
    return out


def apply_iface_normalize_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    iface_fields: Sequence[str],
    rules: Sequence[Mapping[str, str]] | None,
) -> list[dict[str, Any]]:
    return [
        apply_iface_normalize(dict(r), iface_fields=iface_fields, rules=rules)
        for r in rows
    ]


def normalize_port_map(
    port_map: Mapping[str, str] | None,
    rules: Sequence[Mapping[str, str]] | None,
    value_rules: Sequence[Mapping[str, str]] | None = None,
    *,
    key_normalizer: Any = None,
    value_normalizer: Any = None,
) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Normalize port-map keys and values with the same rules as iface fields.

    ``value_rules`` (default: ``rules``) normalizes the after-side values, for
    per-side rules. ``key_normalizer`` / ``value_normalizer`` are callables that
    replace the rule-based normalization (used by the identity pipeline).

    Returns ``(normalized_map, conflicts)``. A conflict is two raw keys that
    normalize to the same key but point at different (normalized) targets; the
    first one wins in ``normalized_map``.
    """
    out: dict[str, str] = {}
    origin: dict[str, str] = {}
    conflicts: list[dict[str, str]] = []
    for raw_k, raw_v in (port_map or {}).items():
        k = key_normalizer(raw_k) if key_normalizer else normalize_iface_name(raw_k, rules)
        v = (
            value_normalizer(raw_v) if value_normalizer
            else normalize_iface_name(raw_v, rules if value_rules is None else value_rules)
        )
        if not k or not v:
            continue
        if k in out:
            if out[k] != v:
                conflicts.append({
                    "before_if": k, "kept_from": origin[k], "kept_to": out[k],
                    "dropped_from": str(raw_k), "dropped_to": v,
                })
            continue
        out[k] = v
        origin[k] = str(raw_k)
    return out, conflicts


def resolve_mapped_iface(name: str, port_map: Mapping[str, str] | None) -> str:
    """Exact map hit, else longest mapped parent + remaining suffix (QinQ-safe).

    ``gei-0/1/0/1.100.200`` tries exact, then ``gei-0/1/0/1.100``, then
    ``gei-0/1/0/1`` — not only the last ``.`` segment.
    """
    text = str(name or "").strip()
    if not text or not port_map:
        return text
    if text in port_map:
        return str(port_map[text])
    if "." not in text:
        return text
    parts = text.split(".")
    for i in range(len(parts) - 1, 0, -1):
        parent = ".".join(parts[:i])
        suffix = ".".join(parts[i:])
        if not parent or not suffix:
            continue
        if parent in port_map:
            return f"{port_map[parent]}.{suffix}"
    return text


def default_zte_iface_normalize_rules() -> list[dict[str, str]]:
    """Common ZTE brief aliases → canonical CLI names."""
    return normalize_iface_rules(
        [
            {"from": "XXVGE", "to": "xxvgei"},
            {"from": "XGE", "to": "xgei"},
            {"from": "CGE", "to": "cgei"},
            {"from": "GE", "to": "gei"},
            {"from": "SG", "to": "smartgroup"},
        ]
    )


# ---------------------------------------------------------------------------
# Built-in per-vendor canonical names (same-vendor synonyms only; cross-vendor
# numbering is the port map's job). Applied before template alias rules.
# ---------------------------------------------------------------------------

VENDOR_CANONICAL: dict[str, list[dict[str, str]]] = {
    "huawei": [
        {"from": "XGigabitEthernet", "to": "XGE"}, {"from": "GigabitEthernet", "to": "GE"},
        {"from": "100GE", "to": "100GE"}, {"from": "40GE", "to": "40GE"}, {"from": "25GE", "to": "25GE"},
        {"from": "10GE", "to": "10GE"}, {"from": "Eth-Trunk", "to": "Eth-Trunk"},
    ],
    "cisco": [
        {"from": "GigabitEthernet", "to": "Gi"}, {"from": "TenGigabitEthernet", "to": "Te"},
        {"from": "TenGigE", "to": "Te"}, {"from": "HundredGigE", "to": "Hu"}, {"from": "FortyGigE", "to": "Fo"},
        {"from": "TwentyFiveGigE", "to": "Twe"}, {"from": "FastEthernet", "to": "Fa"},
        {"from": "Bundle-Ether", "to": "BE"}, {"from": "Loopback", "to": "Lo"},
    ],
    "h3c": [
        {"from": "Ten-GigabitEthernet", "to": "XGE"}, {"from": "GigabitEthernet", "to": "GE"},
        {"from": "FortyGigE", "to": "FGE"}, {"from": "HundredGigE", "to": "HGE"},
        {"from": "Bridge-Aggregation", "to": "BAGG"},
    ],
    "zte": [
        {"from": "xxvgei", "to": "xxvgei"}, {"from": "xgei", "to": "xgei"}, {"from": "cgei", "to": "cgei"},
        {"from": "gei", "to": "gei"}, {"from": "smartgroup", "to": "smartgroup"},
    ],
}


def vendor_canonical_rules(vendor: str) -> list[dict[str, str]]:
    return normalize_iface_rules(VENDOR_CANONICAL.get(str(vendor or "").strip().lower(), []))


def rules_for_side(
    rules: Sequence[Mapping[str, str]] | None, side: str, vendor: str = "",
) -> list[dict[str, str]]:
    """Template alias rules that apply to ``side`` (``before``/``after``) and ``vendor``."""
    vendor_n = str(vendor or "").strip().lower()
    out: list[dict[str, str]] = []
    for r in rules or []:
        rs = str(r.get("side") or "").strip().lower()
        if rs and rs != side:
            continue
        rv = str(r.get("vendor") or "").strip().lower()
        if rv and rv != vendor_n:
            continue
        out.append({"from": str(r.get("from") or ""), "to": str(r.get("to") or "")})
    return normalize_iface_rules(out)
