"""Pre-flight check of a port mapping against the two batches it will be applied to.

The same function backs the compare "validate mapping" button and the cutover
project pre-flight, so both modes judge mapping coverage the same way. It answers
before anyone runs a comparison:

- which *before* interfaces are mapped (exact / by parent), unmapped but present on
  the *after* side by the same name, or **suspect** (not mapped, not on *after*)
- which map keys / targets match nothing, which targets receive several sources
- whether each sheet's two sides were actually collected (``data_state``)
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from sqlalchemy.orm import Session

from .compare_quality import assess_sheet, command_coverage, collect_quality, side_receipts
from .identity_pipeline import build_pipeline
from .iface_normalize import normalize_port_map, resolve_mapped_iface

LIST_CAP = 200
# Million-row tables are never loaded just to look at interface names.
HEAVY_METRICS = frozenset({"bgp_route", "ip_route", "ipv6_route", "l2vpn_mac", "evpn_mac", "interface_detail"})

RowsLoader = Callable[[str, Mapping[str, Any]], list[dict[str, Any]]]


def _parents(name: str) -> list[str]:
    parts = name.split(".")
    return [".".join(parts[:i]) for i in range(len(parts) - 1, 0, -1)]


def _classify(name: str, pmap: Mapping[str, str], after: set[str]) -> str:
    if name in pmap:
        return "mapped_exact"
    if any(p in pmap for p in _parents(name)):
        return "mapped_by_parent"
    if name in after:
        return "identity_same_name"
    return "suspect_unmapped"


def _present(name: str, names: set[str]) -> bool:
    """``name`` exists, or is the parent of an existing sub-interface."""
    if name in names:
        return True
    prefix = name + "."
    return any(n.startswith(prefix) for n in names)


def _top_level(names: Sequence[str]) -> list[str]:
    """Drop sub-interfaces whose parent is already in the list (noise)."""
    pool = set(names)
    return sorted(n for n in pool if not any(p in pool for p in _parents(n)))


def sheet_mapping_coverage(
    pipe: Any,
    before_pairs: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]],
    after_pairs: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]],
) -> dict[str, Any]:
    """Classify every *before* interface of a sheet against the map and the *after* side.

    ``before_pairs`` / ``after_pairs`` are ``pipeline.prepare`` outputs. The result
    carries two private sets (``_after_ifaces``, ``_used_keys``) for aggregation.
    """
    b_if = {str(n.get(f) or "").strip() for _, n in before_pairs for f in pipe.iface_fields}
    a_if = {str(n.get(f) or "").strip() for _, n in after_pairs for f in pipe.iface_fields}
    b_if.discard("")
    a_if.discard("")
    buckets: dict[str, list[str]] = {
        "mapped_exact": [], "mapped_by_parent": [], "identity_same_name": [], "suspect_unmapped": [],
    }
    for name in sorted(b_if):
        buckets[_classify(name, pipe.port_map, a_if)].append(name)
    used = {k for name in b_if for k in pipe.port_map if name == k or name.startswith(k + ".")}
    missing_targets = sorted({
        resolve_mapped_iface(n, pipe.port_map) for n in buckets["mapped_exact"] + buckets["mapped_by_parent"]
        if not _present(resolve_mapped_iface(n, pipe.port_map), a_if)
    })
    suspects = _top_level(buckets["suspect_unmapped"])
    return {
        "counts": {k: len(v) for k, v in buckets.items()},
        "mapped_exact": buckets["mapped_exact"][:LIST_CAP],
        "mapped_by_parent": buckets["mapped_by_parent"][:LIST_CAP],
        "identity_same_name": buckets["identity_same_name"][:LIST_CAP],
        "suspect_unmapped": suspects[:LIST_CAP],
        "suspect_unmapped_total": len(suspects),
        "mapped_target_missing_on_after": missing_targets[:LIST_CAP],
        "_after_ifaces": a_if,
        "_used_keys": used,
    }


def preflight(
    db: Session,
    *,
    sheets: Sequence[Mapping[str, Any]],
    iface_rules: Sequence[Mapping[str, Any]] | None,
    raw_port_map: Mapping[str, str] | None,
    before_batch_id: str,
    after_batch_id: str,
    before_vendor: str = "",
    after_vendor: str = "",
    load_rows: RowsLoader,
) -> dict[str, Any]:
    """Build the pre-flight report. ``load_rows(side, sheet)`` returns raw rows."""
    raw_map = dict(raw_port_map or {})
    out_sheets: list[dict[str, Any]] = []
    used_keys: set[str] = set()
    after_all: set[str] = set()
    totals = {"mapped": 0, "suspect": 0, "same_name": 0}
    risk_reasons: list[str] = []
    full_pmap: dict[str, str] = {}
    conflicts: list[dict[str, str]] = []

    for sheet in sheets:
        sid = str(sheet.get("sheet_id") or sheet.get("metric_id") or "")
        mid = str(sheet.get("metric_id") or "")
        title = str(sheet.get("title") or sid)
        pipe = build_pipeline(
            sheet, iface_rules=iface_rules, port_map=raw_map,
            before_vendor=before_vendor, after_vendor=after_vendor,
        )
        if not full_pmap and pipe.port_map:
            full_pmap = dict(pipe.port_map)
        heavy = mid in HEAVY_METRICS
        card: dict[str, Any] = {"sheet_id": sid, "metric_id": mid, "title": title}
        if heavy:
            b_n = collect_quality(side_receipts(db, before_batch_id, mid))["row_count"]
            a_n = collect_quality(side_receipts(db, after_batch_id, mid))["row_count"]
            card.update(skipped="heavy_table", before_count=b_n, after_count=a_n)
        else:
            b_pairs = pipe.prepare(load_rows("before", sheet), "before")
            a_pairs = pipe.prepare(load_rows("after", sheet), "after")
            card.update(before_count=len(b_pairs), after_count=len(a_pairs))
            if pipe.iface_fields:
                cov = sheet_mapping_coverage(pipe, b_pairs, a_pairs)
                after_all |= cov.pop("_after_ifaces")
                used_keys |= cov.pop("_used_keys")
                card.update(cov)
                totals["mapped"] += cov["counts"]["mapped_exact"] + cov["counts"]["mapped_by_parent"]
                totals["same_name"] += cov["counts"]["identity_same_name"]
                totals["suspect"] += cov["suspect_unmapped_total"]
        card.update(assess_sheet(
            db, sheet, before_batch_id=before_batch_id, after_batch_id=after_batch_id,
            before_rows=int(card.get("before_count") or 0), after_rows=int(card.get("after_count") or 0),
        ))
        if card["data_state"] in ("not_collected", "suspect_parse"):
            risk_reasons.append(f"{sid}:{card['data_state']}")
        out_sheets.append(card)

    if raw_map and not full_pmap:
        full_pmap, conflicts = normalize_port_map(raw_map, iface_rules)
    elif raw_map:
        _, conflicts = normalize_port_map(raw_map, iface_rules)
    sources: dict[str, list[str]] = {}
    for k, v in full_pmap.items():
        sources.setdefault(v, []).append(k)
    n_to_1 = [{"target": t, "sources": sorted(src)} for t, src in sorted(sources.items()) if len(src) > 1]
    unused = sorted(k for k in full_pmap if k not in used_keys)
    targets_missing = sorted(v for v in set(full_pmap.values()) if after_all and not _present(v, after_all))

    judged = totals["mapped"] + totals["suspect"]
    coverage = {
        "mapped": totals["mapped"], "suspect_unmapped": totals["suspect"],
        "identity_same_name": totals["same_name"],
        "rate": round(totals["mapped"] / judged * 100, 1) if judged else None,
    }
    mapping = {
        "pairs": len(full_pmap), "conflicts": conflicts[:LIST_CAP],
        "unused_map_keys": unused[:LIST_CAP], "unused_map_keys_total": len(unused),
        "map_targets_missing_on_after": targets_missing[:LIST_CAP],
        "map_targets_missing_on_after_total": len(targets_missing),
        "n_to_1_targets": n_to_1[:LIST_CAP],
    }
    if totals["suspect"]:
        risk_reasons.append("suspect_unmapped")
    if unused:
        risk_reasons.append("unused_map_keys")
    if targets_missing:
        risk_reasons.append("map_targets_missing_on_after")
    if conflicts:
        risk_reasons.append("map_conflicts")
    return {
        "verdict": "risk" if risk_reasons else "ready",
        "risk_reasons": sorted(set(risk_reasons)),
        "coverage": coverage,
        "mapping": mapping,
        "sheets": out_sheets,
        "command_coverage": command_coverage(db, before_batch_id=before_batch_id, after_batch_id=after_batch_id),
    }
