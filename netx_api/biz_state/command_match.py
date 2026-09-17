"""Match concrete CLI commands to ParseProfiles (longest / most specific wins)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .profiles import ParseProfile, all_profiles, get_profile


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
        p for p in all_profiles() if p.enabled and (p.vendor_key == key or p.vendor_key == "*")
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


def expand_from_bindings(
    *,
    profile: ParseProfile,
    bindings: list[dict[str, str]] | None = None,
    command_override: str = "",
) -> list[tuple[str, dict[str, str]]]:
    """Return list of (concrete_command, params). Reject leftover placeholders."""
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

    binds = list(bindings or [])
    if not binds:
        raise ValueError(f"profile {profile.profile_id} requires parameter bindings")

    out: list[tuple[str, dict[str, str]]] = []
    for b in binds:
        params = {str(k): str(v).strip() for k, v in dict(b or {}).items() if str(v).strip()}
        rendered = tmpl
        for ph in profile.placeholders:
            val = params.get(ph.name) or params.get(ph.schema_field) or ""
            if ph.required and not val:
                raise ValueError(f"missing placeholder {ph.name} for {profile.profile_id}")
            rendered = rendered.replace(f"<{ph.name}>", val)
        concrete = normalize_command(rendered)
        if re.search(r"<[^>]+>", concrete):
            raise ValueError(f"unresolved placeholders in: {concrete}")
        out.append((concrete, params))
    return out


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
