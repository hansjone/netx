"""Pure compare engine: same-table row diff with optional port mapping on before side."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Mapping, Sequence

from .compare_rules import field_rule_map, values_equal, explain_diff
from .iface_normalize import (
    apply_iface_normalize_rows,
    normalize_iface_rules,
    resolve_mapped_iface,
)

# Prefer these fields when stratifying success samples (BGP multipath / multi-cmd).
_STRATUM_FIELDS = ("neighbor", "direction", "afi", "vrf")


def stratum_key(row: Mapping[str, Any] | None) -> str:
    """Bucket key for stratified unchanged sampling."""
    if not isinstance(row, Mapping):
        return "_"
    parts: list[str] = []
    for f in _STRATUM_FIELDS:
        v = str(row.get(f) or "").strip()
        if v:
            parts.append(f"{f}={v}")
    return "|".join(parts) if parts else "_"


def stratify_take(items: list[Any], limit: int, *, key_fn) -> list[Any]:
    """Round-robin across strata so one neighbor/direction cannot consume the whole sample."""
    lim = max(0, int(limit))
    if lim <= 0 or not items:
        return []
    if len(items) <= lim:
        return list(items)
    buckets: dict[str, deque[Any]] = defaultdict(deque)
    order: list[str] = []
    for it in items:
        sk = str(key_fn(it) or "_")
        if sk not in buckets:
            order.append(sk)
        buckets[sk].append(it)
    out: list[Any] = []
    while len(out) < lim and buckets:
        drained: list[str] = []
        for sk in order:
            q = buckets.get(sk)
            if not q:
                drained.append(sk)
                continue
            out.append(q.popleft())
            if len(out) >= lim:
                break
        for sk in drained:
            buckets.pop(sk, None)
            if sk in order:
                order = [x for x in order if x != sk]
    return out


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
    include_unchanged: bool = False,
    unchanged_limit: int | None = None,
    compact_unchanged: bool = False,
) -> dict[str, Any]:
    """Return summary + diffs list.

    Diff kinds: added | removed | changed | unchanged

    Pipeline: iface normalize (both sides) → port map (before) → ordered
    same-key pairing (load / list order within each match key).

    Same match key with N before and M after rows: zip by index
    ``0..min(N,M)-1`` for field compare; extras become ``removed`` (before)
    or ``added`` (after). Example: 5 vs 2 → 2 compared + 3 removed.

    ``include_unchanged``: when False, matching rows still increment
    ``summary.unchanged`` but are omitted from ``diffs``.

    ``unchanged_limit``: max unchanged diffs to emit (None = no cap). Use with
    large sheets so the UI can browse a sample without writing millions of rows.

    ``compact_unchanged``: emit key only (empty before/after) to cut storage.

    ``ignore_port_changes``:
      - ``None`` (default): auto — drop iface from match key only when remaining
        keys stay unique on both sides (legacy LLDP-style heuristic).
      - ``True``: force drop iface from match key when a non-empty candidate exists.
      - ``False``: never drop iface from match key.

    ``summary.duplicate`` is always 0. ``duplicate_keys_*`` count match keys
    that appear more than once on a side (diagnostic only).

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

    before_groups: dict[tuple[str, ...], list[tuple[dict[str, Any], dict[str, Any]]]] = (
        defaultdict(list)
    )
    after_groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for orig, mapped in zip(before_rows, before_mapped):
        before_groups[row_key(mapped, match_keys)].append((orig, mapped))
    for r in after_norm:
        after_groups[row_key(r, match_keys)].append(r)

    diffs: list[dict[str, Any]] = []
    added = removed = changed = unchanged = 0
    unchanged_listed = 0
    limit_n = None if unchanged_limit is None else max(0, int(unchanged_limit))
    multi_before_keys: list[tuple[str, ...]] = []
    multi_after_keys: list[tuple[str, ...]] = []
    unchanged_candidates: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []

    def _key_obj(row: dict[str, Any]) -> dict[str, Any]:
        return {f: row.get(f, "") for f in key_fields}

    def _row_id(row: dict[str, Any] | None) -> str:
        if not isinstance(row, dict):
            return ""
        netx = row.get("_netx")
        if isinstance(netx, dict):
            return str(netx.get("row_id") or "")
        return ""

    def _append_unchanged_diff(
        orig: dict[str, Any], mapped: dict[str, Any], after_row: dict[str, Any]
    ) -> None:
        nonlocal unchanged_listed
        unchanged_listed += 1
        before_rid = _row_id(orig)
        after_rid = _row_id(after_row)
        if compact_unchanged:
            diffs.append(
                {
                    "kind": "unchanged",
                    "key": _key_obj(mapped),
                    "before": {},
                    "after": {},
                    "mapped_before": {},
                    "changes": {},
                    "compact": True,
                    "before_row_id": before_rid,
                    "after_row_id": after_rid,
                }
            )
        else:
            diffs.append(
                {
                    "kind": "unchanged",
                    "key": _key_obj(mapped),
                    "before": orig,
                    "after": after_row,
                    "mapped_before": mapped,
                    "changes": {},
                    "before_row_id": before_rid,
                    "after_row_id": after_rid,
                }
            )

    # Stable key order: before encounter order, then after-only keys
    seen_keys: set[tuple[str, ...]] = set()
    ordered_keys: list[tuple[str, ...]] = []
    for orig, mapped in zip(before_rows, before_mapped):
        k = row_key(mapped, match_keys)
        if k not in seen_keys:
            seen_keys.add(k)
            ordered_keys.append(k)
    for r in after_norm:
        k = row_key(r, match_keys)
        if k not in seen_keys:
            seen_keys.add(k)
            ordered_keys.append(k)
    for k in ordered_keys:
        b_list = before_groups.get(k) or []
        a_list = after_groups.get(k) or []
        if len(b_list) > 1:
            multi_before_keys.append(k)
        if len(a_list) > 1:
            multi_after_keys.append(k)
        n = max(len(b_list), len(a_list))
        for i in range(n):
            if i >= len(b_list):
                after = a_list[i]
                added += 1
                diffs.append(
                    {
                        "kind": "added",
                        "key": _key_obj(after),
                        "before": None,
                        "after": after,
                        "mapped_before": None,
                        "changes": {},
                        "before_row_id": "",
                        "after_row_id": _row_id(after),
                    }
                )
                continue
            if i >= len(a_list):
                orig, mapped = b_list[i]
                removed += 1
                diffs.append(
                    {
                        "kind": "removed",
                        "key": _key_obj(mapped),
                        "before": orig,
                        "after": None,
                        "mapped_before": mapped,
                        "changes": {},
                        "before_row_id": _row_id(orig),
                        "after_row_id": "",
                    }
                )
                continue
            orig, mapped = b_list[i]
            after = a_list[i]
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
                        "before_row_id": _row_id(orig),
                        "after_row_id": _row_id(after),
                    }
                )
            else:
                unchanged += 1
                if include_unchanged:
                    unchanged_candidates.append((orig, mapped, after))

    if include_unchanged and unchanged_candidates:
        if limit_n is None:
            picked = unchanged_candidates
        else:
            picked = stratify_take(
                unchanged_candidates,
                limit_n,
                key_fn=lambda t: stratum_key(t[1]),
            )
        for orig, mapped, after_row in picked:
            _append_unchanged_diff(orig, mapped, after_row)

    def _fmt_keys(keys: list[tuple[str, ...]]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for k in keys:
            s = "|".join(k)
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out[:64]

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
            "duplicate": 0,
            "match_key_fields": match_keys,
            "duplicate_keys_before": len(multi_before_keys),
            "duplicate_keys_after": len(multi_after_keys),
            "duplicate_key_list": _fmt_keys(multi_before_keys + multi_after_keys),
            "unchanged_listed": unchanged_listed,
            "unchanged_truncated": bool(
                include_unchanged and limit_n is not None and unchanged > unchanged_listed
            ),
            "unchanged_compact": bool(compact_unchanged and unchanged_listed > 0),
            "unchanged_sample_mode": (
                "stratified"
                if include_unchanged and limit_n is not None and unchanged > unchanged_listed
                else ("full" if include_unchanged else "none")
            ),
        },
        "diffs": diffs,
        "mapping_stats": stats,
    }
