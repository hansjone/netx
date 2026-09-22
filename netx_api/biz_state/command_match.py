"""Match concrete CLI commands to ParseProfiles (longest / most specific wins)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .profiles import ParseProfile, PlaceholderDef, all_profiles, get_profile

# Sentinel concrete command: collect expands all discover values at runtime.
EXPAND_ALL_COMMAND = "__expand_all__"


@dataclass(frozen=True)
class MatchResult:
    profile: ParseProfile
    params: dict[str, str]
    matched_length: int


def normalize_command(command: str) -> str:
    text = str(command or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def match_command(
    *,
    vendor_key: str,
    command: str,
    profiles: list[ParseProfile] | None = None,
) -> MatchResult | None:
    cmd = normalize_command(command)
    if not cmd:
        return None
    key = str(vendor_key or "").strip().lower()
    cands = profiles if profiles is not None else [
        p
        for p in all_profiles()
        if p.enabled
        and p.kind == "collect"
        and (p.vendor_key == key or p.vendor_key == "*")
    ]
    best: MatchResult | None = None
    for p in cands:
        try:
            m = re.match(p.match, cmd)
        except re.error:
            continue
        if not m:
            continue
        params = {k: str(v or "").strip() for k, v in (m.groupdict() or {}).items()}
        scored = MatchResult(profile=p, params=params, matched_length=len(p.match))
        if best is None or scored.matched_length > best.matched_length:
            best = scored
    return best


def normalize_binding_dicts(
    bindings: list[dict[str, str]] | None,
    *,
    placeholders: list | None = None,
) -> list[dict[str, str]]:
    """Accept ``{vrf: X}`` or ``{placeholder, value}`` rows; expand multi-value placeholders.

    Multi-placeholder rows stored as interleaved ``{placeholder,value}`` pairs are
    zipped in order (e.g. vrf/neighbor/vrf/neighbor → combined dicts). Already
    combined dicts (multiple keys) are kept as-is.
    """
    raw = list(bindings or [])
    converted: list[dict[str, str]] = []
    for b in raw:
        d = dict(b or {})
        if "placeholder" in d or ("name" in d and "value" in d):
            ph = str(d.get("placeholder") or d.get("name") or "").strip()
            val = str(d.get("value") or "").strip()
            if ph and val:
                converted.append({ph: val})
            continue
        converted.append({str(k): str(v).strip() for k, v in d.items() if str(v).strip()})

    phs = list(placeholders or [])
    if len(phs) == 1:
        name = str(getattr(phs[0], "name", "") or "")
        schema = str(getattr(phs[0], "schema_field", "") or name)
        out: list[dict[str, str]] = []
        for c in converted:
            val = c.get(name) or c.get(schema) or ""
            if val:
                out.append({name: val})
        return out

    if len(phs) >= 2:
        ph_names = [str(getattr(ph, "name", "") or "").strip() for ph in phs]
        ph_names = [n for n in ph_names if n]
        combined: list[dict[str, str]] = []
        singles: list[dict[str, str]] = []
        for c in converted:
            if len(c) > 1:
                combined.append(dict(c))
            else:
                singles.append(c)
        if combined and not singles:
            return combined
        if singles and ph_names:
            from collections import defaultdict

            by_ph: dict[str, list[str]] = defaultdict(list)
            for c in singles:
                k, v = next(iter(c.items()))
                by_ph[k].append(v)
            lists = [by_ph.get(n, []) for n in ph_names]
            n0 = len(lists[0]) if lists else 0
            if n0 and all(len(L) == n0 for L in lists):
                zipped = [dict(zip(ph_names, vals)) for vals in zip(*lists)]
                return combined + zipped if combined else zipped
        if combined:
            return combined
    return converted


def _discover_placeholders(profile: ParseProfile) -> list[PlaceholderDef]:
    return [
        ph
        for ph in (profile.placeholders or [])
        if ph.bind_mode == "discover_select" and str(ph.discover_profile_id or "").strip()
    ]


def _optional_discover_placeholders(profile: ParseProfile) -> list[PlaceholderDef]:
    return [ph for ph in _discover_placeholders(profile) if not ph.required]


def _record_passes_discover_filter(rec: dict[str, Any], ph: PlaceholderDef) -> bool:
    filt_field = str(ph.discover_filter_field or "").strip()
    filt_contains = str(ph.discover_filter_contains or "").strip().lower()
    if filt_field and filt_contains:
        hay = str(rec.get(filt_field) or "").strip().lower()
        # ``afi`` must be exact (``ipv4`` must not match ``vpnv4``).
        # CSV fields like ``address_families`` still use substring/token contains.
        if filt_field == "afi":
            if hay != filt_contains:
                return False
        elif filt_contains not in hay:
            return False
    require = str(ph.discover_require_nonempty or "").strip()
    if require and not str(rec.get(require) or "").strip():
        return False
    return True


def filter_discover_records(
    records: list[dict[str, Any]] | None,
    ph: PlaceholderDef,
) -> list[str]:
    """Apply placeholder discover filter; return unique values for ``ph``."""
    value_field = str(ph.discover_value_field or ph.name or "").strip() or "vrf_name"
    out: list[str] = []
    seen: set[str] = set()
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        if not _record_passes_discover_filter(rec, ph):
            continue
        val = str(rec.get(value_field) or "").strip()
        if not val or val in seen:
            continue
        seen.add(val)
        out.append(val)
    return out


def shared_discover_placeholders(profile: ParseProfile) -> list[PlaceholderDef]:
    """Placeholders that share one discover_profile_id (pair/tuple bind)."""
    discover_phs = _discover_placeholders(profile)
    if len(discover_phs) < 2:
        return []
    disc_ids = {str(ph.discover_profile_id or "").strip() for ph in discover_phs}
    if len(disc_ids) != 1 or not next(iter(disc_ids)):
        return []
    return discover_phs


def expand_from_bindings(
    *,
    profile: ParseProfile,
    bindings: list[dict[str, str]] | None = None,
    command_override: str = "",
) -> list[tuple[str, dict[str, str]]]:
    """Return list of (concrete_command, params). Reject leftover placeholders.

    When all placeholders are optional discover_select and bindings are empty,
    returns a single ``(EXPAND_ALL_COMMAND, {})`` sentinel for collect-time expansion.
    """
    override = normalize_command(command_override)
    if override:
        if "<" in override and ">" in override:
            raise ValueError(f"command still has placeholders: {override}")
        return [(override, {})]

    tmpl = profile.command_template
    if not profile.placeholders:
        concrete = normalize_command(tmpl)
        if "<" in concrete and ">" in concrete:
            raise ValueError(f"template has placeholders but profile defines none: {concrete}")
        return [(concrete, {})]

    binds = normalize_binding_dicts(bindings, placeholders=profile.placeholders)
    if not binds:
        optional = _optional_discover_placeholders(profile)
        if optional and len(optional) == len(profile.placeholders):
            return [(EXPAND_ALL_COMMAND, {"__expand_all__": "1"})]
        raise ValueError(f"profile {profile.profile_id} requires parameter bindings")

    out: list[tuple[str, dict[str, str]]] = []
    for params in binds:
        rendered = tmpl
        for ph in profile.placeholders:
            val = params.get(ph.name) or params.get(ph.schema_field) or ""
            if ph.required and not val:
                raise ValueError(f"missing placeholder {ph.name} for {profile.profile_id}")
            rendered = rendered.replace(f"<{ph.name}>", val)
        concrete = normalize_command(rendered)
        if re.search(r"<[^>]+>", concrete):
            raise ValueError(f"unresolved placeholders in: {concrete}")
        out.append((concrete, dict(params)))
    return out


def expand_bindings_from_discover_records(
    *,
    profile: ParseProfile,
    records: list[dict[str, Any]] | None,
) -> list[tuple[str, dict[str, str]]]:
    """Build concrete commands from discover/parser records (e.g. config_vrf).

    Supports a single discover placeholder, or multiple placeholders that share
    the same discover_profile_id (zipped row-wise from each record).
    """
    discover_phs = _discover_placeholders(profile)
    if not discover_phs:
        raise ValueError(f"profile {profile.profile_id} has no discover placeholders")

    shared = shared_discover_placeholders(profile)
    if shared and len(shared) == len(profile.placeholders):
        ph0 = shared[0]
        bindings: list[dict[str, str]] = []
        seen: set[tuple[str, ...]] = set()
        for rec in records or []:
            if not isinstance(rec, dict):
                continue
            if not _record_passes_discover_filter(rec, ph0):
                continue
            params: dict[str, str] = {}
            ok = True
            for ph in shared:
                vf = str(ph.discover_value_field or ph.name or "").strip()
                val = str(rec.get(vf) or "").strip()
                if not val:
                    ok = False
                    break
                params[ph.name] = val
            if not ok:
                continue
            key = tuple(params.get(ph.name, "") for ph in shared)
            if key in seen:
                continue
            seen.add(key)
            bindings.append(params)
        if not bindings:
            raise ValueError(
                f"no discover values for {profile.profile_id} ({ph0.discover_profile_id})"
            )
        return expand_from_bindings(profile=profile, bindings=bindings)

    if len(discover_phs) != 1 or len(profile.placeholders) != 1:
        raise ValueError(
            f"expand-from-discover only supports a single placeholder "
            f"or shared multi-placeholder: {profile.profile_id}"
        )
    ph = discover_phs[0]
    values = filter_discover_records(records, ph)
    if not values:
        raise ValueError(f"no discover values for {profile.profile_id} ({ph.discover_profile_id})")
    bindings = [{ph.name: v} for v in values]
    return expand_from_bindings(profile=profile, bindings=bindings)


def preview_task_item(
    *,
    vendor_key: str,
    profile_id: str = "",
    command: str = "",
    bindings: list[dict[str, str]] | None = None,
    kind: str = "catalog",
) -> dict[str, Any]:
    """Dry-run expand + match for UI preview."""
    if kind == "custom_raw":
        cmd = normalize_command(command)
        return {
            "ok": bool(cmd),
            "kind": "custom_raw",
            "commands": [cmd] if cmd else [],
            "parse": "skipped_custom",
            "message": "custom row: collect only, no parse",
        }

    profile = get_profile(profile_id) if profile_id else None
    if profile is None and command:
        hit = match_command(vendor_key=vendor_key, command=command)
        if hit:
            profile = hit.profile
    if profile is None:
        return {
            "ok": False,
            "kind": kind,
            "commands": [],
            "parse": "unmatched",
            "message": "no profile matched",
        }

    try:
        pairs = expand_from_bindings(
            profile=profile,
            bindings=bindings,
            command_override=command if command and command != profile.command_template else "",
        )
    except ValueError as exc:
        return {
            "ok": False,
            "kind": kind,
            "profile_id": profile.profile_id,
            "commands": [],
            "parse": "invalid",
            "message": str(exc),
        }

    if pairs and pairs[0][0] == EXPAND_ALL_COMMAND:
        return {
            "ok": True,
            "kind": kind,
            "profile_id": profile.profile_id,
            "commands": [],
            "parse": "expand_all",
            "message": "no bindings: collect will expand all discover VRFs",
        }

    previews = []
    for concrete, params in pairs:
        hit = match_command(vendor_key=vendor_key, command=concrete)
        previews.append(
            {
                "command": concrete,
                "params": params,
                "matched_profile_id": hit.profile.profile_id if hit else "",
                "metric_id": hit.profile.metric_id if hit else "",
                "parser_id": hit.profile.parser_id if hit else "",
                "match_params": hit.params if hit else {},
            }
        )
    return {
        "ok": all(bool(x["matched_profile_id"]) for x in previews),
        "kind": kind,
        "profile_id": profile.profile_id,
        "commands": previews,
        "parse": "ok" if previews and all(x["matched_profile_id"] for x in previews) else "partial",
        "message": "",
    }
