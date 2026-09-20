"""Biz-state CLI parsers: vendor folders + one module per command/metric.

Layout::

    parsers/
      common/          # cross-vendor + pipeline.prefer_fsm
      zte/             # implemented status tables
      cisco/ huawei/ … # skeletons

Each vendor package exports ``PARSERS: dict[parser_id, normalize_fn]``.

Adding a status metric
----------------------
1. ``cli_templates/<vendor>/<stem>.textfsm`` + ``index`` (FSM first)
2. ``parsers/<vendor>/<metric>.py``: ``RULE_KEYS`` + ``normalize`` + ``prefer_fsm``
3. Register in vendor ``PARSERS``
4. ``profiles.py``: ``ParseProfile`` (match + ``FieldDef``)

Cross-command (multi aux)
-------------------------
1. Implement each aux as a normal status profile (steps 1–4)
2. On the primary profile::

       aux_commands=[AuxCommand(key="if_intf", profile_id="zte.config_interface")]
       enrich_joins=[EnrichJoin(from_aux="if_intf", on="interface", take=("vrf",))]

3. CollectSession caches identical concrete CLI in one batch; enrich runs after
   primary normalize (no join logic inside the parser).

Complex joins that cannot be expressed as equal-field copy still go in
``normalize`` using ``raws`` / ``aux_records`` / ``fsm_tables``.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Mapping, Sequence

from ...lldp_shared import resolve_vendor_key
from ...ntc_parse import apply_rules, resolve_cli_platform, rules_for_command
from .cisco import PARSERS as _CISCO_PARSERS
from .common.lldp_neighbors import normalize_lldp_neighbors
from .common.vrf_list import normalize_vrf_list
from .ericsson import PARSERS as _ERICSSON_PARSERS
from .h3c import PARSERS as _H3C_PARSERS
from .huawei import PARSERS as _HUAWEI_PARSERS
from .juniper import PARSERS as _JUNIPER_PARSERS
from .nokia import PARSERS as _NOKIA_PARSERS
from .zte import PARSERS as _ZTE_PARSERS

NormalizeFn = Callable[..., list[dict[str, Any]]]

_VENDOR_PARSERS: list[dict[str, NormalizeFn]] = [
    _CISCO_PARSERS,
    _HUAWEI_PARSERS,
    _H3C_PARSERS,
    _JUNIPER_PARSERS,
    _NOKIA_PARSERS,
    _ERICSSON_PARSERS,
    _ZTE_PARSERS,
]

_REGISTRY: dict[str, NormalizeFn] = {
    "lldp_neighbors": normalize_lldp_neighbors,
    "vrf_list": normalize_vrf_list,
}
for _pack in _VENDOR_PARSERS:
    _REGISTRY.update(_pack)


def get_parser(parser_id: str) -> NormalizeFn | None:
    return _REGISTRY.get(str(parser_id or "").strip())


def registered_parser_ids() -> list[str]:
    return sorted(_REGISTRY.keys())


def _rule_keys_for_fn(fn: NormalizeFn) -> tuple[tuple[str, ...], bool]:
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
    raws: Mapping[str, str] | None = None,
    command_rules: Mapping[str, Sequence[str]] | None = None,
    aux_records: Mapping[str, list[dict[str, Any]]] | None = None,
    fsm_tables_extra: Mapping[str, list[dict[str, Any]]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], list[str]]:
    """Apply TextFSM per-command then normalize.

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
    raw_map: dict[str, str] = {"primary": str(raw_text or "")}
    if raws:
        for k, v in raws.items():
            key = str(k or "").strip() or "primary"
            raw_map[key] = str(v or "")

    rules_map: dict[str, list[str]] = {}
    if command_rules:
        for k, seq in command_rules.items():
            rules_map[str(k)] = [str(x).strip() for x in (seq or ()) if str(x).strip()]
    if "primary" not in rules_map:
        rules_map["primary"] = resolve_rule_keys(
            parser_id=parser_id,
            platform=platform,
            command=command,
            textfsm_command=textfsm_command,
        )

    fsm_tables: dict[str, list[dict[str, Any]]] = {}
    used_keys: list[str] = []
    for key, rules in rules_map.items():
        if not rules:
            continue
        text = raw_map.get(key) or (raw_map.get("primary") if key == "primary" else "")
        if not str(text or "").strip():
            for rk in rules:
                fsm_tables.setdefault(rk, [])
                if rk not in used_keys:
                    used_keys.append(rk)
            continue
        wrap_cmd = cmd_hint if key == "primary" else key.replace("_", " ")
        part = apply_rules(
            platform=platform,
            text=text,
            rule_keys=rules,
            command=wrap_cmd,
        )
        fsm_tables.update(part)
        for rk in rules:
            if rk not in used_keys:
                used_keys.append(rk)

    if fsm_tables_extra:
        for k, rows in fsm_tables_extra.items():
            stem = str(k or "").strip()
            if not stem:
                continue
            fsm_tables[stem] = list(rows or [])
            if stem not in used_keys:
                used_keys.append(stem)

    call_kw: dict[str, Any] = {
        "raw_text": raw_map.get("primary") or "",
        "fsm_tables": fsm_tables,
        "raws": raw_map,
        "aux_records": dict(aux_records or {}),
        "vendor": vendor,
        "device_type": device_type,
        "command": cmd_hint or command,
        "params": params or {},
    }
    try:
        sig = inspect.signature(fn)
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
            filtered = call_kw
        else:
            filtered = {k: v for k, v in call_kw.items() if k in sig.parameters}
    except (TypeError, ValueError):
        filtered = {
            "raw_text": call_kw["raw_text"],
            "fsm_tables": fsm_tables,
            "vendor": vendor,
            "device_type": device_type,
            "command": cmd_hint or command,
            "params": params or {},
        }
    records = fn(**filtered)
    if not isinstance(records, list):
        records = []
    return records, fsm_tables, used_keys
