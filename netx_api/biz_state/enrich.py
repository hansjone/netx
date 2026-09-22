"""Declarative cross-command enrich (equal join) for biz-state metrics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class EnrichJoin:
    """Copy fields from an aux metric table onto primary rows by equal key.

    Example (ARP ← if_intf)::

        EnrichJoin(from_aux="if_intf", on="interface", take=("vrf",))

    Composite keys (VRF CE peer ← config_bgp_peer)::

        EnrichJoin(
            from_aux="config_bgp_peer",
            left_on="vrf,neighbor",
            right_on="vrf,neighbor",
            take=("remote_as", "activate"),
        )
    """

    from_aux: str
    take: tuple[str, ...]
    on: str = ""
    left_on: str = ""
    right_on: str = ""
    fill_missing: bool = True  # on miss, set take fields to ""


def _field_list(spec: str) -> list[str]:
    return [p.strip() for p in str(spec or "").split(",") if p.strip()]


def _sides(join: EnrichJoin) -> tuple[list[str], list[str]]:
    left = str(join.left_on or join.on or "").strip()
    right = str(join.right_on or join.on or "").strip()
    lf = _field_list(left)
    rf = _field_list(right)
    if not lf or not rf:
        raise ValueError(f"EnrichJoin {join.from_aux!r} needs on= or left_on/right_on")
    if len(lf) != len(rf):
        raise ValueError(
            f"EnrichJoin {join.from_aux!r} left/right field count mismatch: {lf} vs {rf}"
        )
    return lf, rf


def _row_key(row: Mapping[str, Any], fields: list[str]) -> str:
    parts = [str(row.get(f) or "").strip() for f in fields]
    if not any(parts):
        return ""
    return "\0".join(parts)


def apply_enrich_joins(
    records: list[dict[str, Any]],
    aux_records: Mapping[str, list[dict[str, Any]]] | None,
    joins: Sequence[EnrichJoin] | None,
) -> list[dict[str, Any]]:
    """In-place enrich of ``records``; returns the same list."""
    if not records or not joins:
        return records
    aux_map = aux_records or {}
    for join in joins:
        left_fields, right_fields = _sides(join)
        take = [str(t).strip() for t in (join.take or ()) if str(t).strip()]
        if not take:
            continue
        index: dict[str, dict[str, Any]] = {}
        for row in aux_map.get(str(join.from_aux or "").strip()) or []:
            if not isinstance(row, dict):
                continue
            key = _row_key(row, right_fields)
            if key and key not in index:
                index[key] = row
        for rec in records:
            if not isinstance(rec, dict):
                continue
            hit = index.get(_row_key(rec, left_fields))
            for field in take:
                if hit is not None:
                    rec[field] = str(hit.get(field) or "").strip()
                elif join.fill_missing:
                    rec[field] = ""
    return records
