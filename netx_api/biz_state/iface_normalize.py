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
            out.append({"from": fr, "to": to})
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


def resolve_mapped_iface(name: str, port_map: Mapping[str, str] | None) -> str:
    """Exact map hit, else parent + ``.suffix`` if parent mapped, else unchanged."""
    text = str(name or "").strip()
    if not text or not port_map:
        return text
    if text in port_map:
        return str(port_map[text])
    if "." not in text:
        return text
    parent, suffix = text.rsplit(".", 1)
    if not parent or not suffix:
        return text
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
