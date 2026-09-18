"""Template-driven compare rules: row filters + per-field compare/normalize.

All metric-specific compare behavior belongs in the sheet template
(``row_filters`` / ``field_rules``), not in hardcoded service branches.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence


_AGE_TIMER_RE = re.compile(r"^\d{1,2}:\d{2}:\d{2}$")


def _as_list(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return list(raw)
    return [raw]


def _field_val(row: Mapping[str, Any], field: str) -> str:
    return str((row or {}).get(field) or "").strip()


def eval_leaf_filter(row: Mapping[str, Any], filt: Mapping[str, Any]) -> bool:
    """Evaluate one leaf predicate. Unknown ops → True (do not drop)."""
    field = str(filt.get("field") or "").strip()
    op = str(filt.get("op") or "eq").strip().lower()
    if not field and op not in ("any", "all"):
        return True
    raw = _field_val(row, field)
    expect = filt.get("value")

    if op in ("eq", "=="):
        return raw.lower() == str(expect or "").strip().lower()
    if op in ("ne", "!="):
        return raw.lower() != str(expect or "").strip().lower()
    if op == "in":
        opts = {str(x).strip().lower() for x in _as_list(expect) if str(x).strip()}
        return raw.lower() in opts
    if op in ("not_in", "nin"):
        opts = {str(x).strip().lower() for x in _as_list(expect) if str(x).strip()}
        return raw.lower() not in opts
    if op == "contains":
        needle = str(expect or "").strip().lower()
        return bool(needle) and needle in raw.lower()
    if op == "empty":
        return not raw
    if op in ("not_empty", "nonempty"):
        return bool(raw)
    if op == "regex":
        pat = str(expect or "")
        if not pat:
            return True
        try:
            return bool(re.search(pat, raw, re.I))
        except re.error:
            return True
    if op == "age_timer":
        # HH:MM:SS dynamic ARP age
        return bool(_AGE_TIMER_RE.match(raw))
    if op == "ci_eq":
        return raw.lower() == str(expect or "").strip().lower()
    return True


def row_matches_filter(row: Mapping[str, Any], filt: Mapping[str, Any] | None) -> bool:
    if not filt or not isinstance(filt, dict):
        return True
    if "any" in filt:
        kids = filt.get("any") or []
        if not isinstance(kids, list) or not kids:
            return True
        return any(row_matches_filter(row, k) for k in kids if isinstance(k, dict))
    if "all" in filt:
        kids = filt.get("all") or []
        if not isinstance(kids, list) or not kids:
            return True
        return all(row_matches_filter(row, k) for k in kids if isinstance(k, dict))
    return eval_leaf_filter(row, filt)


def apply_row_filters(
    rows: Sequence[Mapping[str, Any]],
    filters: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Keep rows matching all top-level filters (AND). Nested any/all supported."""
    fl = [f for f in (filters or []) if isinstance(f, dict)]
    out: list[dict[str, Any]] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        if all(row_matches_filter(r, f) for f in fl):
            out.append(dict(r))
    return out


def normalize_value(value: Any, how: str) -> str:
    text = str(value if value is not None else "").strip()
    mode = str(how or "").strip().lower()
    if not mode or mode in ("none", "strip"):
        return text
    if mode == "lower":
        return text.lower()
    if mode == "upper":
        return text.upper()
    if mode == "mac":
        # 0011.2233.4455 / 00-11-22-33-44-55 / 00:11:… → lowercase hex only
        hex_only = re.sub(r"[^0-9a-fA-F]", "", text).lower()
        return hex_only
    if mode == "empty_as_blank":
        if text.lower() in ("n/a", "na", "-", "--", "none", "null"):
            return ""
        return text
    return text


def field_rule_map(rules: Sequence[Mapping[str, Any]] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for raw in rules or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("field") or "").strip()
        if not name:
            continue
        out[name] = dict(raw)
    return out


def effective_compare_fields(
    compare_fields: Sequence[str],
    field_rules: Sequence[Mapping[str, Any]] | None,
) -> list[str]:
    """Drop fields marked compare=ignore (or legacy ignore:true)."""
    rules = field_rule_map(field_rules)
    out: list[str] = []
    for f in compare_fields or []:
        name = str(f).strip()
        if not name:
            continue
        rule = rules.get(name) or {}
        mode = str(rule.get("compare") or "").strip().lower()
        if mode in ("ignore", "skip", "off"):
            continue
        if rule.get("ignore") is True:
            continue
        out.append(name)
    return out


def effective_display_fields(
    *,
    key_fields: Sequence[str],
    compare_fields: Sequence[str],
    display_fields: Sequence[str] | None = None,
) -> list[str]:
    """Result-table columns: explicit display, else key+compare (legacy).

    Keys always lead; remaining display/compare fields follow in given order
    without duplicates.
    """
    keys = [str(x).strip() for x in (key_fields or []) if str(x).strip()]
    key_set = set(keys)
    compare = [str(x).strip() for x in (compare_fields or []) if str(x).strip()]
    raw_disp = display_fields
    if raw_disp is None:
        # Legacy templates: show key + compare only
        extra = [f for f in compare if f not in key_set]
        return keys + extra
    disp = [str(x).strip() for x in raw_disp if str(x).strip()]
    # Force keys first (always visible)
    out: list[str] = list(keys)
    seen = set(keys)
    for f in disp:
        if f in seen:
            continue
        out.append(f)
        seen.add(f)
    # Ensure compare fields appear even if UI forgot to tick display
    for f in compare:
        if f in seen or f in key_set:
            continue
        out.append(f)
        seen.add(f)
    return out


def _parse_float(text: str) -> float | None:
    try:
        return float(text) if text else 0.0
    except ValueError:
        return None


def explain_diff(
    before: Any,
    after: Any,
    *,
    rule: Mapping[str, Any] | None = None,
) -> str:
    """Human-readable reason when values_equal is False (for UI / export)."""
    rule = rule or {}
    norm = str(rule.get("normalize") or "strip").strip().lower() or "strip"
    bv = normalize_value(before, norm)
    av = normalize_value(after, norm)
    mode = str(rule.get("compare") or "eq").strip().lower() or "eq"
    if mode in ("percent", "pct", "rel"):
        bn = _parse_float(bv)
        an = _parse_float(av)
        if bn is None or an is None:
            return "neq"
        if bn == 0.0:
            return "pct_base_zero"
        try:
            t = float(rule.get("tolerance") or 0)
        except (TypeError, ValueError):
            t = 0.0
        pct = abs(an - bn) / abs(bn) * 100.0
        return f"pct {pct:.1f}% > {t:g}%"
    if mode in ("numeric", "number", "int", "float"):
        bn = _parse_float(bv)
        an = _parse_float(av)
        if bn is None or an is None:
            return "neq"
        try:
            t = float(rule.get("tolerance") or 0)
        except (TypeError, ValueError):
            t = 0.0
        delta = abs(an - bn)
        return f"abs Δ{delta:g} > {t:g}"
    return "neq"


def values_equal(
    before: Any,
    after: Any,
    *,
    rule: Mapping[str, Any] | None = None,
) -> bool:
    rule = rule or {}
    norm = str(rule.get("normalize") or "strip").strip().lower() or "strip"
    bv = normalize_value(before, norm)
    av = normalize_value(after, norm)
    mode = str(rule.get("compare") or "eq").strip().lower() or "eq"
    if mode in ("ignore", "skip", "off"):
        return True
    if mode in ("numeric", "number", "int", "float", "percent", "pct", "rel"):
        bn = _parse_float(bv)
        an = _parse_float(av)
        if bn is None or an is None:
            return bv == av
        tol = rule.get("tolerance", 0)
        try:
            t = float(tol or 0)
        except (TypeError, ValueError):
            t = 0.0
        if mode in ("percent", "pct", "rel"):
            # Relative % vs before: |a-b|/max(|b|,eps)*100 <= tol
            # before==0: both zero → ok; else fail (undefined relative base)
            if bn == 0.0:
                return an == 0.0
            pct = abs(an - bn) / abs(bn) * 100.0
            return pct <= t
        return abs(bn - an) <= t
    return bv == av


def arp_dynamic_row_filters() -> list[dict[str, Any]]:
    """Canonical ARP compare filter (replaces hardcoded service filter)."""
    return [
        {
            "any": [
                {"field": "entry_type", "op": "eq", "value": "dynamic"},
                {
                    "all": [
                        {"field": "entry_type", "op": "empty"},
                        {"field": "age", "op": "age_timer"},
                    ]
                },
            ]
        }
    ]


# Presets for seeding defaults (not a UI "apply preset" button)
ROW_FILTER_PRESETS: dict[str, list[dict[str, Any]]] = {
    "arp_dynamic": arp_dynamic_row_filters(),
    "bgp_established": [{"field": "state", "op": "eq", "value": "Established"}],
}
