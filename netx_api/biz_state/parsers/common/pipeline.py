"""Shared FSM-vs-hand helpers for biz_state parsers."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

MapRowsFn = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
HandFn = Callable[..., list[dict[str, Any]]]


def prefer_fsm(
    fsm_tables: Mapping[str, list[dict[str, Any]]] | None,
    keys: Sequence[str],
    map_rows: MapRowsFn,
    hand_fn: HandFn,
    *,
    raw_text: str,
    **kw: Any,
) -> list[dict[str, Any]]:
    """Use the first non-empty FSM table (mapped); otherwise hand-parse raw_text."""
    tables = fsm_tables or {}
    for key in keys or ():
        k = str(key or "").strip()
        if not k:
            continue
        rows = tables.get(k) or []
        if rows:
            mapped = map_rows(rows)
            if mapped:
                return mapped
    return hand_fn(raw_text=raw_text, **kw)


def prefer_hand(
    fsm_tables: Mapping[str, list[dict[str, Any]]] | None,
    keys: Sequence[str],
    map_rows: MapRowsFn,
    hand_fn: HandFn,
    *,
    raw_text: str,
    **kw: Any,
) -> list[dict[str, Any]]:
    """Hand-first dual path: callback when non-empty; else first mapped FSM table.

    Use when one TextFSM cannot cover divergent CLI layouts (e.g. BGP neighbor
    in vs out, status vs plain, heavy wraps). Empty ``keys`` / ``RULE_KEYS=()``
    makes this hand-only.
    """
    hand = hand_fn(raw_text=raw_text, **kw)
    if hand:
        return hand
    tables = fsm_tables or {}
    for key in keys or ():
        k = str(key or "").strip()
        if not k:
            continue
        rows = tables.get(k) or []
        if rows:
            mapped = map_rows(rows)
            if mapped:
                return mapped
    return hand
