"""Biz-state CLI parsers: vendor folders + one module per command/metric.

Layout::

    parsers/
      common/          # cross-vendor (lldp, vrf_list, vrf_route_summary, pipeline)
      zte/             # implemented status tables
      cisco/           # skeleton — add command modules here
      huawei/
      h3c/
      juniper/
      nokia/
      ericsson/

Each vendor package exports ``PARSERS: dict[parser_id, normalize_fn]``.

Adding a status metric
----------------------
1. (Recommended) ``cli_templates/<vendor>/<stem>.textfsm`` + ``index`` line
2. ``parsers/<vendor>/<metric>.py``: ``RULE_KEYS = ("<stem>",)`` +
   ``normalize(..., fsm_tables=...)`` (use ``common.pipeline.prefer_fsm``)
3. Register in the vendor ``PARSERS`` dict
4. ``profiles.py``: add ``ParseProfile`` (command match + ``FieldDef`` schema)

Collect runs TextFSM rules first → ``fsm_tables``, then calls ``normalize`` with
both ``raw_text`` and ``fsm_tables``. The parser decides: return mapped FSM rows,
post-process them, or ignore FSM and hand-parse ``raw_text``.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Sequence

from ...lldp_shared import resolve_vendor_key
from ...ntc_parse import apply_rules, resolve_cli_platform, rules_for_command
from .cisco import PARSERS as _CISCO_PARSERS
from .common.lldp_neighbors import normalize_lldp_neighbors
from .common.vrf_list import normalize_vrf_list
from .common.vrf_route_summary import normalize_vrf_route_summary
from .ericsson import PARSERS as _ERICSSON_PARSERS
from .h3c import PARSERS as _H3C_PARSERS
from .huawei import PARSERS as _HUAWEI_PARSERS
from .juniper import PARSERS as _JUNIPER_PARSERS
from .nokia import PARSERS as _NOKIA_PARSERS
from .zte import PARSERS as _ZTE_PARSERS

NormalizeFn = Callable[..., list[dict[str, Any]]]

# Later vendor packages may override earlier ones for the same parser_id.
# Prefer moving shared logic to common/ when two vendors implement the same metric.
_VENDOR_PARSERS: list[dict[str, NormalizeFn]] = [
    _CISCO_PARSERS,
    _HUAWEI_PARSERS,
    _H3C_PARSERS,
    _JUNIPER_PARSERS,
    _NOKIA_PARSERS,
    _ERICSSON_PARSERS,
    _ZTE_PARSERS,  # last so current ZTE status parsers win on overlaps
]

_REGISTRY: dict[str, NormalizeFn] = {
    "lldp_neighbors": normalize_lldp_neighbors,
    "vrf_list": normalize_vrf_list,
    "vrf_route_summary": normalize_vrf_route_summary,
}
for _pack in _VENDOR_PARSERS:
    _REGISTRY.update(_pack)


def get_parser(parser_id: str) -> NormalizeFn | None:
    return _REGISTRY.get(str(parser_id or "").strip())


def registered_parser_ids() -> list[str]:
    return sorted(_REGISTRY.keys())


def _rule_keys_for_fn(fn: NormalizeFn) -> tuple[tuple[str, ...], bool]:
    """Return ``(keys, declared)``. ``declared`` is True when RULE_KEYS is set on fn/module."""
    keys_raw = None
    declared = False
    if hasattr(fn, "RULE_KEYS"):
        keys_raw = getattr(fn, "RULE_KEYS")
        declared = True
    else:
        mod = inspect.getmodule(fn)
        if mod is not None and hasattr(mod, "RULE_KEYS"):
            keys_raw = getattr(mod, "RULE_KEYS")
            declared = True
    if not keys_raw:
        return (), declared
    out: list[str] = []
    for k in keys_raw:
        s = str(k or "").strip()
        if s.lower().endswith(".textfsm"):
            s = s[: -len(".textfsm")]
        if s and s not in out:
            out.append(s)
    return tuple(out), declared


def get_parser_meta(parser_id: str) -> dict[str, Any] | None:
    """Return ``{fn, rule_keys, rule_keys_declared}`` for a registered parser, or None."""
    fn = get_parser(parser_id)
    if not fn:
        return None
    keys, declared = _rule_keys_for_fn(fn)
    return {"fn": fn, "rule_keys": keys, "rule_keys_declared": declared}


def resolve_rule_keys(
    *,
    parser_id: str,
    platform: str = "",
    command: str = "",
    textfsm_command: str = "",
) -> list[str]:
    """RULE_KEYS from parser when declared; else index stems for textfsm_command/command."""
    meta = get_parser_meta(parser_id)
    if not meta:
        return []
    if meta.get("rule_keys_declared"):
        return list(meta["rule_keys"])
    plat = str(platform or "").strip()
    cmd = str(textfsm_command or command or "").strip()
    if plat and cmd:
        return rules_for_command(plat, cmd)
    return []


def run_parser(
    parser_id: str,
    *,
    raw_text: str,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
    textfsm_command: str = "",
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], list[str]]:
    """Apply TextFSM rules then normalize.

    Returns ``(records, fsm_tables, rule_keys_used)``.
    """
    meta = get_parser_meta(parser_id)
    if not meta:
        raise KeyError(f"unknown parser {parser_id}")
    fn: NormalizeFn = meta["fn"]
    platform = resolve_cli_platform(
        vendor=vendor,
        device_type=device_type,
        vendor_key=resolve_vendor_key(vendor, device_type),
    )
    cmd_hint = str(textfsm_command or command or "").strip()
    rule_keys = resolve_rule_keys(
        parser_id=parser_id,
        platform=platform,
        command=command,
        textfsm_command=textfsm_command,
    )
    fsm_tables: dict[str, list[dict[str, Any]]] = {}
    if rule_keys and str(raw_text or "").strip():
        fsm_tables = apply_rules(
            platform=platform,
            text=raw_text,
            rule_keys=rule_keys,
            command=cmd_hint,
        )
    records = fn(
        raw_text=raw_text,
        fsm_tables=fsm_tables,
        vendor=vendor,
        device_type=device_type,
        command=cmd_hint or command,
        params=params or {},
    )
    if not isinstance(records, list):
        records = []
    return records, fsm_tables, rule_keys
