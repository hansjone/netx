"""Pure compare engine: same-table row diff with optional port mapping on before side."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .compare_rules import field_rule_map, values_equal, explain_diff
from .iface_normalize import (
    apply_iface_normalize_rows,
    normalize_iface_rules,
    resolve_mapped_iface,
)


def apply_port_map(
    row: dict[str, Any],
    *,
    iface_fields: list[str],
    port_map: dict[str, str],
) -> dict[str, Any]:
    """Rewrite iface columns on a row (exact map or parent.subif inheritance)."""
    out = dict(row)
    for f in iface_fields:
        val = str(out.get(f) or "").strip()
        if val:
            out[f] = resolve_mapped_iface(val, port_map)
    return out


def row_key(row: dict[str, Any], key_fields: list[str]) -> tuple[str, ...]:
    return tuple(str(row.get(k) or "").strip() for k in key_fields)


def mapping_stats(
    *,
    before_rows: list[dict[str, Any]],
    after_rows: list[dict[str, Any]],
    iface_fields: list[str],
    port_map: dict[str, str],
) -> dict[str, Any]:
    """hit/miss/unused for port mapping validation.

    ``hit_before``: map key appears as a normalized before iface, or as the
    parent of a before subinterface (so main-port-only maps still validate).

    Map keys are matched against **normalized** iface values (same pipeline as
    compare). Enter mapping keys in normalized form.
    """
    before_ifaces: set[str] = set()
    after_ifaces: set[str] = set()
    for f in iface_fields:
        for r in before_rows:
            v = str(r.get(f) or "").strip()
            if v:
                before_ifaces.add(v)
        for r in after_rows:
            v = str(r.get(f) or "").strip()
            if v:
                after_ifaces.add(v)

    before_bases: set[str] = set()
    for v in before_ifaces:
        before_bases.add(v)
        # Multi-level parents: a.b.c → a.b, a
        parts = v.split(".")
        for i in range(len(parts) - 1, 0, -1):
            before_bases.add(".".join(parts[:i]))

    after_bases: set[str] = set()
    for v in after_ifaces:
        after_bases.add(v)
        parts = v.split(".")
        for i in range(len(parts) - 1, 0, -1):
            after_bases.add(".".join(parts[:i]))

    mapped_before = set(port_map.keys())
    mapped_after = set(port_map.values())
    hit_before = sorted(mapped_before & before_bases)
    miss_before = sorted(mapped_before - before_bases)
    unused = list(miss_before)
    hit_after = sorted(mapped_after & after_bases)
    miss_after = sorted(mapped_after - after_bases)
    return {
        "before_iface_count": len(before_ifaces),
        "after_iface_count": len(after_ifaces),
        "map_pairs": len(port_map),
        "hit_before": hit_before,
        "miss_before": miss_before,
        "hit_after": hit_after,
        "miss_after": miss_after,
        "unused_before_keys": unused,
        "ok": not miss_before and not miss_after,
        "hint": "map keys must match normalized iface names (post iface_normalize)",
    }


def compare_rows(
    *,
    before_rows: list[dict[str, Any]],
    after_rows: list[dict[str, Any]],
    key_fields: list[str],
    iface_fields: list[str],
    compare_fields: list[str],
    port_map: dict[str, str] | None = None,
    field_rules: Sequence[Mapping[str, Any]] | None = None,
    iface_normalize_rules: Sequence[Mapping[str, str]] | None = None,
    ignore_port_changes: bool | None = None,
) -> dict[str, Any]:
    """Return summary + diffs list.

    Diff kinds: added | removed | changed | unchanged | duplicate

    Pipeline: iface normalize (both sides) → port map (before) → match.

    ``ignore_port_changes``:
      - ``None`` (default): auto — drop iface from match key only when remaining
        keys stay unique on both sides (legacy LLDP-style heuristic).
      - ``True``: force drop iface from match key when a non-empty candidate exists.
      - ``False``: never drop iface from match key.

    Duplicate match keys are not silently discarded: extras become ``duplicate``
    diffs and ``summary.duplicate_key_list`` lists the colliding keys.

    ``field_rules`` drives normalize / numeric tolerance / per-field compare mode
    (template-driven; no metric-specific branches here).
    """
    if not key_fields:
        raise ValueError("key_fields required")
    pmap = dict(port_map or {})
    rules = field_rule_map(field_rules)
    norm_rules = normalize_iface_rules(iface_normalize_rules)
    iface_list = [str(f) for f in (iface_fields or []) if str(f).strip()]
    iface_set = set(iface_list)

    before_norm = apply_iface_normalize_rows(
        before_rows, iface_fields=iface_list, rules=norm_rules
    )
    after_norm = apply_iface_normalize_rows(
        after_rows, iface_fields=iface_list, rules=norm_rules
    )

    ignore_ports = False
    # No map → optionally ignore port renames by dropping iface from match key.
    if not pmap and iface_set:
        candidate = [k for k in key_fields if k not in iface_set]
        if not candidate:
            match_keys = list(key_fields)
        elif ignore_port_changes is True:
            match_keys = candidate
            ignore_ports = True
        elif ignore_port_changes is False:
            match_keys = list(key_fields)
        else:
            # Auto heuristic (legacy default)
            before_c = [row_key(r, candidate) for r in before_norm]
            after_c = [row_key(r, candidate) for r in after_norm]
            if len(before_c) == len(set(before_c)) and len(after_c) == len(set(after_c)):
                match_keys = candidate
                ignore_ports = True
            else:
                match_keys = list(key_fields)
    else:
        match_keys = list(key_fields)

    before_mapped: list[dict[str, Any]] = [
        apply_port_map(r, iface_fields=iface_list, port_map=pmap) for r in before_norm
    ]

    after_index: dict[tuple[str, ...], dict[str, Any]] = {}
    after_dup = 0
    after_dup_keys: list[tuple[str, ...]] = []
    after_dup_rows: list[tuple[tuple[str, ...], dict[str, Any]]] = []
    for r in after_norm:
        k = row_key(r, match_keys)
        if k in after_index:
            after_dup += 1
            after_dup_keys.append(k)
            after_dup_rows.append((k, r))
            continue  # first wins — do not overwrite
        after_index[k] = r

    before_keys: set[tuple[str, ...]] = set()
    before_dup = 0
    before_dup_keys: list[tuple[str, ...]] = []
    diffs: list[dict[str, Any]] = []
    added = removed = changed = unchanged = duplicate = 0

    def _key_obj(row: dict[str, Any]) -> dict[str, Any]:
        return {f: row.get(f, "") for f in key_fields}

    for orig, mapped in zip(before_rows, before_mapped):
        k = row_key(mapped, match_keys)
        if k in before_keys:
            before_dup += 1
            before_dup_keys.append(k)
            duplicate += 1
            diffs.append(
                {
                    "kind": "duplicate",
                    "side": "before",
                    "key": _key_obj(mapped),
                    "before": orig,
                    "after": after_index.get(k),
                    "mapped_before": mapped,
                    "changes": {},
                }
            )
            continue  # only first before row participates in match
        before_keys.add(k)
        after = after_index.get(k)
        if after is None:
            removed += 1
            diffs.append(
                {
                    "kind": "removed",
                    "key": _key_obj(mapped),
                    "before": orig,
                    "after": None,
                    "mapped_before": mapped,
                    "changes": {},
                }
            )
            continue
        # Empty compare_fields = presence-only: keyed rows that exist on both
        # sides are unchanged (no value checks).
        field_changes: dict[str, dict[str, Any]] = {}
        for f in compare_fields:
            bv = mapped.get(f, "")
            av = after.get(f, "")
            rule = rules.get(f)
            if not values_equal(bv, av, rule=rule):
                entry: dict[str, Any] = {"before": bv, "after": av}
                reason = explain_diff(bv, av, rule=rule)
                if reason:
                    entry["reason"] = reason
                field_changes[f] = entry
        if field_changes:
            changed += 1
            diffs.append(
                {
                    "kind": "changed",
                    "key": _key_obj(mapped),
                    "before": orig,
                    "after": after,
                    "mapped_before": mapped,
                    "changes": field_changes,
                }
            )
        else:
            unchanged += 1
            diffs.append(
                {
                    "kind": "unchanged",
                    "key": _key_obj(mapped),
                    "before": orig,
                    "after": after,
                    "mapped_before": mapped,
                    "changes": {},
                }
            )

    for k, after in after_index.items():
        if k in before_keys:
            continue
        added += 1
        diffs.append(
            {
                "kind": "added",
                "key": _key_obj(after),
                "before": None,
                "after": after,
                "mapped_before": None,
                "changes": {},
            }
        )

    for k, after in after_dup_rows:
        duplicate += 1
        diffs.append(
            {
                "kind": "duplicate",
                "side": "after",
                "key": _key_obj(after),
                "before": None,
                "after": after,
                "mapped_before": None,
                "changes": {},
            }
        )

    def _fmt_keys(keys: list[tuple[str, ...]]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for k in keys:
            s = "|".join(k)
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out

    stats = mapping_stats(
        before_rows=before_norm,
        after_rows=after_norm,
        iface_fields=iface_list,
        port_map=pmap,
    )
    if not pmap:
        stats = {**stats, "ok": True, "ignore_port_changes": ignore_ports}
    return {
        "summary": {
            "before_count": len(before_rows),
            "after_count": len(after_rows),
            "added": added,
            "removed": removed,
            "changed": changed,
            "unchanged": unchanged,
            "duplicate": duplicate,
            "match_key_fields": match_keys,
            "duplicate_keys_before": before_dup,
            "duplicate_keys_after": after_dup,
            "duplicate_key_list": _fmt_keys(before_dup_keys + after_dup_keys),
        },
        "diffs": diffs,
        "mapping_stats": stats,
    }
