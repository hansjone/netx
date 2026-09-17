"""Pure compare engine: same-table row diff with optional port mapping on before side."""

from __future__ import annotations

from typing import Any


def apply_port_map(
    row: dict[str, Any],
    *,
    iface_fields: list[str],
    port_map: dict[str, str],
) -> dict[str, Any]:
    out = dict(row)
    for f in iface_fields:
        val = str(out.get(f) or "").strip()
        if val and val in port_map:
            out[f] = port_map[val]
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
    """hit/miss/unused for port mapping validation."""
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

    mapped_before = set(port_map.keys())
    mapped_after = set(port_map.values())
    hit_before = sorted(mapped_before & before_ifaces)
    miss_before = sorted(mapped_before - before_ifaces)
    unused = sorted(mapped_before - before_ifaces)  # same as miss for before presence
    hit_after = sorted(mapped_after & after_ifaces)
    miss_after = sorted(mapped_after - after_ifaces)
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
    }


def compare_rows(
    *,
    before_rows: list[dict[str, Any]],
    after_rows: list[dict[str, Any]],
    key_fields: list[str],
    iface_fields: list[str],
    compare_fields: list[str],
    port_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return summary + diffs list.

    Diff kinds: added | removed | changed | unchanged
    """
    if not key_fields:
        raise ValueError("key_fields required")
    pmap = dict(port_map or {})

    before_mapped: list[dict[str, Any]] = [
        apply_port_map(r, iface_fields=iface_fields, port_map=pmap) for r in before_rows
    ]

    after_index: dict[tuple[str, ...], dict[str, Any]] = {}
    for r in after_rows:
        after_index[row_key(r, key_fields)] = r

    before_keys: set[tuple[str, ...]] = set()
    diffs: list[dict[str, Any]] = []
    added = removed = changed = unchanged = 0

    for orig, mapped in zip(before_rows, before_mapped):
        k = row_key(mapped, key_fields)
        before_keys.add(k)
        after = after_index.get(k)
        if after is None:
            removed += 1
            diffs.append(
                {
                    "kind": "removed",
                    "key": {f: mapped.get(f, "") for f in key_fields},
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
            # Compare using mapped before for iface fields already rewritten
            if str(bv) != str(av):
                field_changes[f] = {"before": bv, "after": av}
        if field_changes:
            changed += 1
            diffs.append(
                {
                    "kind": "changed",
                    "key": {f: mapped.get(f, "") for f in key_fields},
                    "before": orig,
                    "after": after,
                    "mapped_before": mapped,
                    "changes": field_changes,
                }
            )
        else:
            unchanged += 1

    for k, after in after_index.items():
        if k in before_keys:
            continue
        added += 1
        diffs.append(
            {
                "kind": "added",
                "key": {f: after.get(f, "") for f in key_fields},
                "before": None,
                "after": after,
                "mapped_before": None,
                "changes": {},
            }
        )

    stats = mapping_stats(
        before_rows=before_rows,
        after_rows=after_rows,
        iface_fields=iface_fields,
        port_map=pmap,
    )
    return {
        "summary": {
            "before_count": len(before_rows),
            "after_count": len(after_rows),
            "added": added,
            "removed": removed,
            "changed": changed,
            "unchanged": unchanged,
        },
        "diffs": diffs,
        "mapping_stats": stats,
    }
