"""Per-batch CLI session helpers: aux resolution, command cache, parse bundle."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .command_match import normalize_command
from .enrich import EnrichJoin, apply_enrich_joins
from .parsers import get_parser, get_parser_meta, run_parser
from .profiles import AuxCommand, ParseProfile, get_profile

SendFn = Callable[..., str]


@dataclass
class CachedCommand:
    raw: str = ""
    fsm_tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    records: list[dict[str, Any]] = field(default_factory=list)
    ok: bool = True
    error: str = ""
    cmd_row_id: str = ""


@dataclass(frozen=True)
class ResolvedAux:
    key: str
    profile_id: str
    command: str
    textfsm_command: str
    parser_id: str
    rule_keys: tuple[str, ...]
    profile: ParseProfile


def resolve_aux_command(aux: AuxCommand) -> ResolvedAux:
    """Resolve aux from ``profile_id`` (single source of truth)."""
    key = str(aux.key or "").strip()
    pid = str(aux.profile_id or "").strip()
    if not key or not pid:
        raise ValueError("AuxCommand requires key and profile_id")
    prof = get_profile(pid)
    if not prof:
        raise ValueError(f"aux profile not found: {pid}")
    cmd = normalize_command(prof.command_template)
    if not cmd:
        raise ValueError(f"aux profile {pid} has empty command_template")
    textfsm = str(prof.textfsm_command or cmd).strip()
    parser_id = str(prof.parser_id or "").strip()
    meta = get_parser_meta(parser_id) if parser_id else None
    rule_keys = tuple((meta or {}).get("rule_keys") or ())
    return ResolvedAux(
        key=key,
        profile_id=pid,
        command=cmd,
        textfsm_command=textfsm,
        parser_id=parser_id,
        rule_keys=rule_keys,
        profile=prof,
    )


@dataclass
class ParseBundle:
    """Inputs for primary ``run_parser`` after primary + aux collection."""

    raws: dict[str, str] = field(default_factory=dict)
    command_rules: dict[str, list[str]] = field(default_factory=dict)
    aux_records: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    fsm_extra: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


class CollectSession:
    """SSH session-scoped command cache + aux fetch/parse."""

    def __init__(
        self,
        conn: Any,
        *,
        vendor: str = "",
        device_type: str = "",
        vendor_key: str = "",
        read_timeout: int = 120,
        send_fn: SendFn | None = None,
    ) -> None:
        self.conn = conn
        self.vendor = vendor
        self.device_type = device_type
        self.vendor_key = vendor_key
        self.read_timeout = int(read_timeout or 120)
        self._send = send_fn
        self.cache: dict[str, CachedCommand] = {}

    def _send_show(self, command: str) -> str:
        if self._send is None:
            from ..ne_netmiko import send_show_command

            return str(send_show_command(self.conn, command, read_timeout=self.read_timeout) or "")
        return str(self._send(self.conn, command, read_timeout=self.read_timeout) or "")

    def remember(
        self,
        command: str,
        *,
        raw: str = "",
        fsm_tables: dict[str, list[dict[str, Any]]] | None = None,
        records: list[dict[str, Any]] | None = None,
        ok: bool = True,
        error: str = "",
        cmd_row_id: str = "",
    ) -> CachedCommand:
        ck = normalize_command(command)
        entry = CachedCommand(
            raw=str(raw or ""),
            fsm_tables=dict(fsm_tables or {}),
            records=list(records or []),
            ok=bool(ok),
            error=str(error or ""),
            cmd_row_id=str(cmd_row_id or ""),
        )
        self.cache[ck] = entry
        return entry

    def get_cached(self, command: str) -> CachedCommand | None:
        ck = normalize_command(command)
        hit = self.cache.get(ck)
        if hit and hit.ok:
            return hit
        return None

    def fetch_and_parse(
        self,
        command: str,
        *,
        parser_id: str = "",
        textfsm_command: str = "",
        params: dict[str, str] | None = None,
        cmd_row_id: str = "",
    ) -> tuple[CachedCommand, bool]:
        """Return ``(entry, cache_hit)``. On miss: CLI + optional parser."""
        cached = self.get_cached(command)
        if cached is not None:
            return cached, True
        try:
            raw = self._send_show(command)
        except Exception as exc:
            entry = self.remember(
                command,
                raw="",
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                cmd_row_id=cmd_row_id,
            )
            return entry, False
        records: list[dict[str, Any]] = []
        fsm_tables: dict[str, list[dict[str, Any]]] = {}
        if parser_id and get_parser(parser_id):
            try:
                records, fsm_tables, _keys = run_parser(
                    parser_id,
                    raw_text=raw,
                    vendor=self.vendor,
                    device_type=self.device_type,
                    command=textfsm_command or command,
                    textfsm_command=textfsm_command or "",
                    params=params or {},
                )
            except Exception as exc:
                entry = self.remember(
                    command,
                    raw=raw,
                    ok=False,
                    error=f"parse: {type(exc).__name__}: {exc}",
                    cmd_row_id=cmd_row_id,
                )
                return entry, False
        entry = self.remember(
            command,
            raw=raw,
            fsm_tables=fsm_tables,
            records=records,
            ok=True,
            cmd_row_id=cmd_row_id,
        )
        return entry, False


def primary_rule_keys(parser_id: str) -> list[str]:
    meta = get_parser_meta(parser_id) or {}
    return list(meta.get("rule_keys") or ())


def build_parse_bundle(
    *,
    primary_raw: str,
    primary_parser_id: str,
    aux_results: dict[str, CachedCommand],
    resolved_aux: list[ResolvedAux],
) -> ParseBundle:
    bundle = ParseBundle(
        raws={"primary": str(primary_raw or "")},
        command_rules={"primary": primary_rule_keys(primary_parser_id)},
    )
    for ra in resolved_aux:
        entry = aux_results.get(ra.key) or CachedCommand(ok=False)
        bundle.raws[ra.key] = entry.raw
        bundle.command_rules[ra.key] = list(ra.rule_keys)
        if entry.records:
            bundle.aux_records[ra.key] = list(entry.records)
        bundle.fsm_extra.update(entry.fsm_tables or {})
    return bundle


def run_primary_with_bundle(
    parser_id: str,
    *,
    bundle: ParseBundle,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    textfsm_command: str = "",
    params: dict[str, str] | None = None,
    enrich_joins: list[EnrichJoin] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], list[str]]:
    records, fsm_tables, keys = run_parser(
        parser_id,
        raw_text=bundle.raws.get("primary") or "",
        vendor=vendor,
        device_type=device_type,
        command=command,
        textfsm_command=textfsm_command,
        params=params,
        raws=bundle.raws,
        command_rules=bundle.command_rules,
        aux_records=bundle.aux_records,
        fsm_tables_extra=bundle.fsm_extra,
    )
    if enrich_joins:
        apply_enrich_joins(records, bundle.aux_records, enrich_joins)
    return records, fsm_tables, keys
