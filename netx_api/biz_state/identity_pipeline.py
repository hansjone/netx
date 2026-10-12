"""One identity pipeline for every compare path.

A row's *match key* must come out identical whether it is computed while a run
executes, while its results are searched / hydrated / exported, while a mapping
is validated, or while a cutover monitor evaluates the same sheet. All of those
paths therefore build a :class:`Pipeline` and call the same methods instead of
re-implementing "normalize → map → key".

Stages for one row on one side (``before`` or ``after``)::

    raw row ──normalize──▶ iface aliases (+vendor canonical) ▶ key normalize ▶ value_map
            ──filter────▶ ``row_filters`` (v2: on the normalized row; legacy: on the raw row)
            ──map───────▶ port map (before side only)
            ──key───────▶ ``key_tuple`` over the match key fields

``legacy=True`` reproduces the pre-pipeline behaviour (snapshot v1 runs): the
template alias rules are the only normalization, keys are never normalized,
there is no value map and the filters see the raw row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .compare_rules import (
    normalize_value,
    row_matches_filter,
    scalar_text,
)
from .iface_normalize import (
    normalize_iface_name,
    normalize_iface_rules,
    normalize_port_map,
    resolve_mapped_iface,
    rules_for_side,
    vendor_canonical_rules,
)

SIDES = ("before", "after")
DIRECTIONAL_MODES = {"not_worse", "numeric_min", "numeric_max"}
UNMAPPED_POLICIES = ("by_name", "ignore_port")
DUPLICATE_PAIRING = ("sorted", "ordered")


def filter_fields(filt: Mapping[str, Any] | None) -> set[str]:
    """Every field name a (possibly nested) row filter reads."""
    out: set[str] = set()
    if not isinstance(filt, Mapping):
        return out
    for group in ("any", "all"):
        if group in filt:
            for kid in filt.get(group) or []:
                out |= filter_fields(kid)
            return out
    name = str(filt.get("field") or "").strip()
    if name:
        out.add(name)
    return out


def _vendor_key(vendor: str = "", device_type: str = "") -> str:
    from ..topology_lldp import resolve_vendor_key

    key = resolve_vendor_key(vendor, device_type)
    return "" if key == "generic" else key


@dataclass(eq=False)
class SideSpec:
    side: str
    vendor: str = ""
    canonical: list[dict[str, str]] = field(default_factory=list)
    alias_rules: list[dict[str, str]] = field(default_factory=list)
    # field -> {lowercased source value: canonical value}
    value_maps: dict[str, dict[str, str]] = field(default_factory=dict)
    # key field -> normalize mode (only fields whose rule asks for one)
    key_norms: dict[str, str] = field(default_factory=dict)
    strip_inner_space: bool = True

    def iface(self, name: Any) -> str:
        text = str(name or "").strip()
        if not text:
            return ""
        if self.strip_inner_space:
            text = "".join(text.split())
        if self.canonical:
            text = normalize_iface_name(text, self.canonical)
        if self.alias_rules:
            text = normalize_iface_name(text, self.alias_rules)
        return text

    @property
    def has_iface_rules(self) -> bool:
        return bool(self.canonical or self.alias_rules)

    def describe(self) -> dict[str, Any]:
        """JSON-able record of the rules in force (stored in the run snapshot)."""
        return {
            "vendor": self.vendor,
            "canonical": list(self.canonical),
            "alias_rules": list(self.alias_rules),
            "value_maps": {k: dict(v) for k, v in self.value_maps.items()},
            "key_norms": dict(self.key_norms),
        }


@dataclass(eq=False)
class Pipeline:
    key_fields: list[str]
    iface_fields: list[str]
    compare_fields: list[str]
    field_rules: list[dict[str, Any]]
    row_filters: list[dict[str, Any]]
    port_map: dict[str, str]
    before: SideSpec
    after: SideSpec
    legacy: bool = False
    ignore_port_changes: bool | None = None
    unmapped_port_policy: str = "by_name"
    duplicate_pairing: str = "ordered"
    _iface_set: set[str] = field(default_factory=set, repr=False)
    _transformed: set[str] = field(default_factory=set, repr=False)
    _targets: set[str] = field(default_factory=set, repr=False)

    def __post_init__(self) -> None:
        self._iface_set = set(self.iface_fields)
        self._transformed = set(self._iface_set)
        for spec in (self.before, self.after):
            self._transformed |= set(spec.value_maps) | set(spec.key_norms)
        self._targets = set(self.port_map.values())

    # -- side helpers ----------------------------------------------------
    def spec(self, side: str) -> SideSpec:
        return self.before if side == "before" else self.after

    # -- stage 1: normalize ----------------------------------------------
    def normalize(self, row: Mapping[str, Any], side: str) -> dict[str, Any]:
        out = dict(row)
        spec = self.spec(side)
        for f in self.iface_fields:
            val = scalar_text(out.get(f)).strip()
            if val:
                out[f] = spec.iface(val)
        if self.legacy:
            return out
        for f, mode in spec.key_norms.items():
            if f in out:
                out[f] = normalize_value(out[f], mode)
        for f, vmap in spec.value_maps.items():
            if f not in out:
                continue
            raw = scalar_text(out[f]).strip()
            hit = vmap.get(raw.lower())
            if hit is not None:
                out[f] = hit
        return out

    def normalize_name(self, name: Any, side: str) -> str:
        """Interface-name normalization only (port-map keys / values, selectors)."""
        return self.spec(side).iface(name)

    # -- stage 2: filter -------------------------------------------------
    def filters_for(self, side: str) -> list[dict[str, Any]]:
        out = []
        for f in self.row_filters:
            fs = str(f.get("side") or "").strip().lower()
            if not fs or fs == "both" or fs == side:
                out.append(f)
        return out

    def keep(self, row: Mapping[str, Any], side: str) -> bool:
        """Evaluate row filters. v2 expects the *normalized* row, legacy the raw one."""
        return all(row_matches_filter(row, f) for f in self.filters_for(side))

    def accepts(self, raw: Mapping[str, Any], norm: Mapping[str, Any], side: str) -> bool:
        """Row-filter verdict: legacy reads the raw row, v2 the normalized one."""
        return self.keep(raw if self.legacy else norm, side)

    def pushdown_filters(self, side: str) -> list[dict[str, Any]]:
        """Filters that read only untransformed fields, so they may run in SQL on raw rows."""
        if self.legacy:
            return list(self.row_filters)
        return [
            f for f in self.row_filters
            if not str(f.get("side") or "").strip()
            and not (filter_fields(f) & self._transformed)
        ]

    def prepare(self, rows: Sequence[Mapping[str, Any]], side: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """``[(original row, normalized row)]`` after filtering, in input order."""
        out: list[tuple[dict[str, Any], dict[str, Any]]] = []
        has_filters = bool(self.filters_for(side))
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            norm = self.normalize(raw, side)
            if has_filters and not self.accepts(raw, norm, side):
                continue
            out.append((raw, norm))
        return out

    # -- stage 3: port map -----------------------------------------------
    def apply_port_map(self, norm_row: Mapping[str, Any]) -> dict[str, Any]:
        out = dict(norm_row)
        if not self.port_map:
            return out
        for f in self.iface_fields:
            val = scalar_text(out.get(f)).strip()
            if val:
                out[f] = resolve_mapped_iface(val, self.port_map)
        return out

    def is_mapped(self, norm_row: Mapping[str, Any], side: str) -> bool:
        """True when every interface value of the row is covered by the port map."""
        if not self.iface_fields:
            return True
        values = [scalar_text(norm_row.get(f)).strip() for f in self.iface_fields]
        values = [v for v in values if v]
        if not values:
            return False
        for v in values:
            if side == "before":
                if resolve_mapped_iface(v, self.port_map) == v and v not in self.port_map:
                    return False
            else:
                if not self._after_value_is_target(v):
                    return False
        return True

    def _after_value_is_target(self, value: str) -> bool:
        if value in self._targets:
            return True
        parts = value.split(".")
        return any(".".join(parts[:i]) in self._targets for i in range(len(parts) - 1, 0, -1))

    def transform(self, row: Mapping[str, Any], side: str) -> dict[str, Any]:
        norm = self.normalize(row, side)
        return self.apply_port_map(norm) if side == "before" else norm

    # -- stage 4: key ----------------------------------------------------
    def effective_match_keys(self, *, unique_without_iface: bool | None = None) -> list[str]:
        """Match-key fields once the port policy is resolved.

        With a port map the full key is used (the map aligns the names). Without
        one, ``ignore_port_changes`` may drop the interface fields; ``None`` means
        "auto", resolved by the caller via ``unique_without_iface``.
        """
        keys = list(self.key_fields)
        if self.port_map or not self._iface_set:
            return keys
        candidate = [k for k in keys if k not in self._iface_set]
        if not candidate:
            return keys
        policy = self.ignore_port_changes
        if policy is True:
            return candidate
        if policy is False:
            return keys
        return candidate if unique_without_iface else keys

    def ignores_ports_by_policy(self) -> bool:
        return bool(self.port_map) and self.unmapped_port_policy == "ignore_port" and bool(self._iface_set)

    def key_of(
        self, norm_row: Mapping[str, Any], side: str, match_keys: Sequence[str],
    ) -> tuple[dict[str, Any], tuple[str, ...]]:
        """``(row after port map, match key)`` for a *normalized* row."""
        row = self.apply_port_map(norm_row) if side == "before" else dict(norm_row)
        if self.ignores_ports_by_policy():
            if not self.is_mapped(norm_row, side):
                reduced = [k for k in match_keys if k not in self._iface_set] or list(match_keys)
                return row, ("U",) + tuple(scalar_text(row.get(k)).strip() for k in reduced)
            return row, ("M",) + tuple(scalar_text(row.get(k)).strip() for k in match_keys)
        return row, tuple(scalar_text(row.get(k)).strip() for k in match_keys)

    def match_key(
        self, row: Mapping[str, Any], side: str, match_keys: Sequence[str] | None = None,
    ) -> tuple[str, ...]:
        """Match key of a *raw* row (normalize → port map → key)."""
        keys = list(match_keys) if match_keys is not None else self.effective_match_keys()
        return self.key_of(self.normalize(row, side), side, keys)[1]

    # -- duplicate pairing -----------------------------------------------
    def pair_sort_key(self, norm_row: Mapping[str, Any]) -> tuple[str, ...]:
        rules = {str(r.get("field")): r for r in self.field_rules if isinstance(r, dict)}
        out = []
        for f in self.compare_fields:
            mode = str((rules.get(f) or {}).get("normalize") or "strip")
            out.append(normalize_value(norm_row.get(f), mode))
        return tuple(out)

    # -- SQL eligibility -------------------------------------------------
    def sql_blockers(self, used_fields: set[str]) -> str:
        """Reason the SQL engine must not run this sheet ('' when it may)."""
        if self.legacy:
            return ""
        for spec in (self.before, self.after):
            if set(spec.value_maps) & used_fields:
                return "value_map"
            if set(spec.key_norms) & set(self.key_fields):
                return "key_normalize"
        if used_fields & self._iface_set and (self.before.has_iface_rules or self.after.has_iface_rules):
            return "iface_normalize"
        if any(str(f.get("side") or "").strip() for f in self.row_filters):
            return "row_filters_side"
        if any(filter_fields(f) & self._transformed for f in self.row_filters):
            return "row_filters_post_normalize"
        if any(
            str(r.get("compare") or "").strip().lower() in DIRECTIONAL_MODES
            for r in self.field_rules if isinstance(r, dict)
        ):
            return "directional_compare"
        return ""

    def describe(self) -> dict[str, Any]:
        return {
            "legacy": self.legacy,
            "before": self.before.describe(),
            "after": self.after.describe(),
            "unmapped_port_policy": self.unmapped_port_policy,
            "duplicate_pairing": self.duplicate_pairing,
        }


def _field_rule_specs(
    field_rules: Sequence[Mapping[str, Any]], key_fields: Sequence[str], side: str,
) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    value_maps: dict[str, dict[str, str]] = {}
    key_norms: dict[str, str] = {}
    key_set = set(key_fields)
    for rule in field_rules or []:
        if not isinstance(rule, Mapping):
            continue
        name = str(rule.get("field") or "").strip()
        if not name:
            continue
        rs = str(rule.get("side") or "").strip().lower()
        if rs and rs != "both" and rs != side:
            continue
        apply_to_key = rule.get("apply_to_key")
        apply_to_key = (name in key_set) if apply_to_key is None else bool(apply_to_key)
        is_key = name in key_set
        if is_key and not apply_to_key:
            continue
        mode = str(rule.get("normalize") or "").strip().lower()
        vmap = rule.get("value_map")
        if is_key and mode and mode not in ("none", "strip"):
            key_norms[name] = mode
        if isinstance(vmap, Mapping) and vmap:
            pre = mode if (mode and mode not in ("none", "strip")) else "strip"
            built: dict[str, str] = {}
            for src, dst in vmap.items():
                built[normalize_value(src, pre).lower()] = scalar_text(dst).strip()
            value_maps[name] = built
    return value_maps, key_norms


def build_pipeline(
    sheet: Mapping[str, Any],
    *,
    iface_rules: Sequence[Mapping[str, Any]] | None,
    port_map: Mapping[str, str] | None,
    before_vendor: str = "",
    after_vendor: str = "",
    legacy: bool = False,
    normalize_map: bool = True,
) -> Pipeline:
    """Build the pipeline for one sheet.

    ``port_map`` is the map as the user wrote it. Unless ``normalize_map`` is
    false (legacy runs, whose maps were written post-normalization) its keys are
    normalized with the *before* side rules and its values with the *after* side
    rules.
    """
    key_fields = [str(k).strip() for k in (sheet.get("key_fields") or []) if str(k).strip()]
    iface_fields = [str(k).strip() for k in (sheet.get("iface_fields") or []) if str(k).strip()]
    field_rules = [dict(r) for r in (sheet.get("field_rules") or []) if isinstance(r, Mapping)]
    from .compare_rules import effective_compare_fields

    compare_fields = effective_compare_fields(list(sheet.get("compare_fields") or []), field_rules)
    canonical_on = sheet.get("builtin_vendor_canonical", True) is not False and not legacy
    all_rules = normalize_iface_rules(list(iface_rules or []))

    specs: dict[str, SideSpec] = {}
    for side, vendor_raw in (("before", before_vendor), ("after", after_vendor)):
        vendor = _vendor_key(vendor_raw) if vendor_raw else ""
        if legacy:
            alias = normalize_iface_rules([{"from": r["from"], "to": r["to"]} for r in all_rules])
            value_maps, key_norms = {}, {}
        else:
            alias = rules_for_side(all_rules, side, vendor)
            value_maps, key_norms = _field_rule_specs(field_rules, key_fields, side)
        specs[side] = SideSpec(
            side=side, vendor=vendor,
            canonical=vendor_canonical_rules(vendor) if canonical_on else [],
            alias_rules=alias, value_maps=value_maps, key_norms=key_norms,
            strip_inner_space=not legacy,
        )

    pmap = dict(port_map or {})
    if pmap and normalize_map and not legacy:
        pmap, _ = normalize_port_map(
            pmap, None,
            key_normalizer=specs["before"].iface, value_normalizer=specs["after"].iface,
        )
    ignore_ports = sheet.get("ignore_port_changes")
    policy = str(sheet.get("unmapped_port_policy") or "by_name").strip().lower()
    pairing = str(sheet.get("duplicate_pairing") or ("ordered" if legacy else "sorted")).strip().lower()
    return Pipeline(
        key_fields=key_fields,
        iface_fields=iface_fields,
        compare_fields=compare_fields,
        field_rules=field_rules,
        row_filters=[dict(f) for f in (sheet.get("row_filters") or []) if isinstance(f, Mapping)],
        port_map=pmap,
        before=specs["before"],
        after=specs["after"],
        legacy=legacy,
        ignore_port_changes=None if ignore_ports is None else bool(ignore_ports),
        unmapped_port_policy=policy if policy in UNMAPPED_POLICIES else "by_name",
        duplicate_pairing="ordered" if legacy else (pairing if pairing in DUPLICATE_PAIRING else "sorted"),
    )


def legacy_pipeline(
    *,
    key_fields: Sequence[str],
    iface_fields: Sequence[str],
    compare_fields: Sequence[str],
    field_rules: Sequence[Mapping[str, Any]] | None = None,
    port_map: Mapping[str, str] | None = None,
    iface_normalize_rules: Sequence[Mapping[str, Any]] | None = None,
    ignore_port_changes: bool | None = None,
    row_filters: Sequence[Mapping[str, Any]] | None = None,
    duplicate_pairing: str = "ordered",
) -> Pipeline:
    """Pipeline with the pre-v2 semantics, for snapshot-v1 runs and bare callers."""
    alias = normalize_iface_rules(
        [{"from": r.get("from"), "to": r.get("to")} for r in (iface_normalize_rules or [])]
    )
    return Pipeline(
        key_fields=[str(k) for k in key_fields],
        iface_fields=[str(f) for f in iface_fields if str(f).strip()],
        compare_fields=[str(f) for f in compare_fields],
        field_rules=[dict(r) for r in (field_rules or []) if isinstance(r, Mapping)],
        row_filters=[dict(f) for f in (row_filters or []) if isinstance(f, Mapping)],
        port_map=dict(port_map or {}),
        before=SideSpec(side="before", alias_rules=list(alias), strip_inner_space=False),
        after=SideSpec(side="after", alias_rules=list(alias), strip_inner_space=False),
        legacy=True,
        ignore_port_changes=ignore_port_changes,
        duplicate_pairing=duplicate_pairing if duplicate_pairing in DUPLICATE_PAIRING else "ordered",
    )
