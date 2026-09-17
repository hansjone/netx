"""ParseProfile registry: command template + parser + schema (code as source of truth).

Display fields may be overridden at runtime via biz_state_command_override (hot edit).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FieldDef:
    name: str
    dtype: str = "str"  # str|int|float|bool
    nullable: bool = True
    indexed: bool = False
    is_key: bool = False
    is_interface: bool = False
    role: str = "identity"  # identity|state|counter|meta
    display_name: str = ""
    from_command_param: bool = False
    length: int = 256


@dataclass(frozen=True)
class PlaceholderDef:
    name: str
    schema_field: str
    required: bool = True
    bind_mode: str = "manual_text"  # discover_select | manual_text
    discover_profile_id: str = ""
    discover_value_field: str = ""
    discover_label_field: str = ""


@dataclass
class ParseProfile:
    profile_id: str
    vendor_key: str  # zte|huawei|cisco|... or "*" for all
    metric_id: str
    parser_id: str
    title: str
    command_template: str
    match: str  # regex with optional named groups
    textfsm_command: str = ""
    description: str = ""
    sample_output: str = ""
    placeholders: list[PlaceholderDef] = field(default_factory=list)
    fields: list[FieldDef] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    sort_order: int = 100
    enabled: bool = True
    kind: str = "collect"  # collect | discover


_LLDP_FIELDS: list[FieldDef] = [
    FieldDef("local_if", length=128, indexed=True, is_key=True, is_interface=True, display_name="本端接口"),
    FieldDef("remote_sys", length=256, indexed=True, is_key=True, display_name="对端系统名"),
    FieldDef("remote_if", length=128, is_key=True, display_name="对端接口"),
    FieldDef("remote_ip", length=128, role="meta", display_name="对端管理IP"),
    FieldDef("protocol", length=32, role="meta", display_name="协议"),
]


def _lldp_profiles() -> list[ParseProfile]:
    """One logical LLDP collect profile per vendor_key (commands differ)."""
    from ..lldp_shared import VENDOR_LLDP_PROFILES, STUB_PARSER_KEYS

    out: list[ParseProfile] = []
    order = 10
    for key, vp in VENDOR_LLDP_PROFILES.items():
        if key in STUB_PARSER_KEYS and key != "nokia":
            # Still register so UI can show; parse may return empty until templates exist.
            pass
        cmd = vp.lldp_command
        # Escape for regex: match exact command ignoring extra whitespace flexibility
        escaped = r"\s+".join(
            __import__("re").escape(p) for p in cmd.split() if p
        )
        out.append(
            ParseProfile(
                profile_id=f"{key}.lldp_neighbors",
                vendor_key=key,
                metric_id="lldp_neighbor",
                parser_id="lldp_neighbors",
                title="LLDP Neighbors",
                command_template=cmd,
                match=rf"(?i)^\s*{escaped}\s*$",
                textfsm_command=cmd,
                description=vp.notes or "LLDP neighbor table snapshot for cutover compare.",
                sample_output="",
                placeholders=[],
                fields=list(_LLDP_FIELDS),
                tags=["lldp", "l2"],
                sort_order=order,
                enabled=key not in ("ericsson", "generic"),
                kind="collect",
            )
        )
        order += 10
    # AOS uses a different command than generic nokia profile
    out.append(
        ParseProfile(
            profile_id="nokia_aos.lldp_neighbors",
            vendor_key="nokia",
            metric_id="lldp_neighbor",
            parser_id="lldp_neighbors",
            title="LLDP Neighbors (AOS)",
            command_template="show lldp remote-system",
            match=r"(?i)^\s*show\s+lldp\s+remote-system\s*$",
            textfsm_command="show lldp remote-system",
            description="Alcatel AOS LLDP remote-system.",
            fields=list(_LLDP_FIELDS),
            tags=["lldp", "l2", "aos"],
            sort_order=95,
            enabled=True,
            kind="collect",
        )
    )
    return out


_PROFILES: list[ParseProfile] | None = None


def all_profiles() -> list[ParseProfile]:
    global _PROFILES
    if _PROFILES is None:
        _PROFILES = _lldp_profiles()
    return list(_PROFILES)


def reload_profiles() -> None:
    global _PROFILES
    _PROFILES = None


def profiles_for_vendor(vendor_key: str) -> list[ParseProfile]:
    key = str(vendor_key or "").strip().lower()
    return [
        p
        for p in all_profiles()
        if p.enabled and (p.vendor_key == key or p.vendor_key == "*")
    ]


def get_profile(profile_id: str) -> ParseProfile | None:
    pid = str(profile_id or "").strip()
    for p in all_profiles():
        if p.profile_id == pid:
            return p
    return None


def metric_field_map() -> dict[str, list[FieldDef]]:
    """Merge fields by metric_id (first-seen wins on name conflict)."""
    out: dict[str, list[FieldDef]] = {}
    seen: dict[str, set[str]] = {}
    for p in all_profiles():
        mid = p.metric_id
        bucket = out.setdefault(mid, [])
        names = seen.setdefault(mid, set())
        for f in p.fields:
            if f.name in names:
                continue
            names.add(f.name)
            bucket.append(f)
    return out


def profile_to_public_dict(p: ParseProfile, *, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    ov = overrides or {}
    return {
        "profile_id": p.profile_id,
        "vendor_key": p.vendor_key,
        "metric_id": p.metric_id,
        "parser_id": p.parser_id,
        "title": str(ov.get("title") or p.title),
        "command_template": str(ov.get("command_template") or p.command_template),
        "description": str(ov.get("description") if ov.get("description") is not None else p.description),
        "sample_output": str(ov.get("sample_output") if ov.get("sample_output") is not None else p.sample_output),
        "placeholders": [
            {
                "name": ph.name,
                "schema_field": ph.schema_field,
                "required": ph.required,
                "bind_mode": ph.bind_mode,
                "discover_profile_id": ph.discover_profile_id,
                "discover_value_field": ph.discover_value_field,
                "discover_label_field": ph.discover_label_field,
            }
            for ph in p.placeholders
        ],
        "fields": [
            {
                "name": f.name,
                "dtype": f.dtype,
                "is_key": f.is_key,
                "is_interface": f.is_interface,
                "role": f.role,
                "display_name": f.display_name or f.name,
                "from_command_param": f.from_command_param,
            }
            for f in p.fields
        ],
        "tags": list(p.tags),
        "sort_order": p.sort_order,
        "enabled": bool(ov.get("enabled")) if "enabled" in ov else p.enabled,
        "kind": p.kind,
        "match": p.match,
        "textfsm_command": p.textfsm_command or p.command_template,
    }
