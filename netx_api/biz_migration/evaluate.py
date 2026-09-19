"""Cutover monitor: relative-to-baseline drift + dual-device migration verdict."""

from __future__ import annotations

from typing import Any

from ..biz_state.compare_engine import apply_port_map, compare_rows

PORT_METRIC_ID = "interface_brief"
PORT_STATUS_FIELDS = ("admin", "phy", "prot")


def _key_str(key: tuple[str, ...] | list[str]) -> str:
    return "|".join(str(x) for x in key)


def port_status_label(row: dict[str, Any] | None) -> str:
    """Compact admin/phy/prot for board (e.g. up/up/up)."""
    if not row:
        return "—"
    parts = [str(row.get(f) or "-").lower() for f in PORT_STATUS_FIELDS]
    if all(p == "-" for p in parts):
        return "—"
    return "/".join(parts)


def status_label(row: dict[str, Any] | None, sheet_override: dict[str, Any] | None = None) -> str:
    """Human status for board; uses sheet_override.status_fields when set."""
    ov = sheet_override or {}
    fields = [str(f) for f in (ov.get("status_fields") or []) if str(f).strip()]
    if not fields:
        return port_status_label(row)
    if not row:
        return "—"
    parts = [str(row.get(f) or "-").lower() for f in fields]
    if all(p == "-" for p in parts):
        return "—"
    return "/".join(parts)


def classify_status(row: dict[str, Any] | None, sheet_override: dict[str, Any] | None) -> str:
    """Classify current row as up|down|other|none from sheet_override status semantics."""
    ov = sheet_override or {}
    fields = [str(f) for f in (ov.get("status_fields") or []) if str(f).strip()]
    if not fields or not row:
        return "none"
    down_vals = {str(x).lower() for x in (ov.get("down_values") or ["down"])}
    up_vals = {str(x).lower() for x in (ov.get("up_values") or ["up"])}
    vals = [str(row.get(f) or "").strip().lower() for f in fields]
    vals = [v for v in vals if v]
    if not vals:
        return "none"
    if any(v in down_vals for v in vals):
        return "down"
    if vals and all(v in up_vals for v in vals):
        return "up"
    return "other"


def parse_expect_set(raw: dict[str, Any] | None) -> dict[str, set[str]]:
    """Return metric_id → set of key strings (old-side / before-map identity).

    Supports:
      {"ports": ["gei-0/1/0/1", ...]}  → interface_brief
      {"items": [{"metric_id": "...", "key": "a|b"}, {"metric_id":"...", "keys":["a","b"]}]}
    """
    out: dict[str, set[str]] = {}
    data = raw or {}
    ports = data.get("ports") or []
    if isinstance(ports, list):
        for p in ports:
            s = str(p or "").strip()
            if s:
                out.setdefault(PORT_METRIC_ID, set()).add(s)
                out.setdefault("_ports", set()).add(s)
    items = data.get("items") or []
    if isinstance(items, list):
        for it in items:
            if not isinstance(it, dict):
                continue
            mid = str(it.get("metric_id") or "").strip()
            sid = str(it.get("sheet_id") or "").strip() or mid
            if not sid:
                continue
            bucket = out.setdefault(sid, set())
            if it.get("key") is not None:
                k = it.get("key")
                if isinstance(k, (list, tuple)):
                    joined = "|".join(str(p).strip() for p in k if str(p).strip())
                    if joined:
                        bucket.add(joined)
                else:
                    s = str(k or "").strip()
                    if s:
                        bucket.add(s)
            keys = it.get("keys")
            if isinstance(keys, list):
                # Flat list of segments → one composite key; else each entry is a key
                # (string or nested list/tuple of segments).
                if keys and all(not isinstance(x, (list, tuple, dict)) for x in keys):
                    joined = "|".join(str(x).strip() for x in keys if str(x).strip())
                    if joined:
                        bucket.add(joined)
                else:
                    for x in keys:
                        if isinstance(x, (list, tuple)):
                            joined = "|".join(str(p).strip() for p in x if str(p).strip())
                            if joined:
                                bucket.add(joined)
                        else:
                            s = str(x or "").strip()
                            if s:
                                bucket.add(s)
    return {k: {x for x in v if x} for k, v in out.items() if v}


def side_verdict(
    *,
    kind: str,
    in_expect: bool,
    window_active: bool,
) -> tuple[str, str]:
    """Map compare kind → (verdict, color) for one device vs its baseline."""
    k = str(kind or "")
    if k == "unchanged":
        return "ok", "green"
    if k == "removed":
        if in_expect:
            return "expected_gone", "yellow"
        return "anomaly_gone", "red"
    if k == "added":
        if in_expect:
            return "expected_new", "green"
        return "unexpected_new", "yellow" if window_active else "yellow"
    if k == "changed":
        if in_expect:
            return "expected_change", "yellow"
        return "anomaly_change", "red"
    return "ok", "gray"


def field_token(field: str, value: str) -> str:
    """Canonical field token used in success patterns: field:<name>:<value>."""
    return f"field:{str(field).strip().lower()}:{str(value).strip().lower()}"


def _fields_for_tokens(
    sheet_override: dict[str, Any] | None,
    *pattern_lists: list[dict[str, Any]] | None,
) -> set[str]:
    """Fields whose current values should be emitted as field:* tokens."""
    ov = sheet_override or {}
    fields: set[str] = set()
    for f in ov.get("status_fields") or []:
        s = str(f or "").strip()
        if s:
            fields.add(s)
    for ft in ov.get("field_tokens") or []:
        if isinstance(ft, dict):
            s = str(ft.get("field") or "").strip()
            if s:
                fields.add(s)
    patterns: list[Any] = []
    for pl in pattern_lists:
        if pl:
            patterns.extend(pl)
    if not patterns:
        for key in ("success", "anomaly"):
            raw = ov.get(key)
            if isinstance(raw, list):
                patterns.extend(raw)
    for pat in patterns:
        if not isinstance(pat, dict):
            continue
        for side in ("old", "new"):
            for tok in pat.get(side) or []:
                t = str(tok or "")
                if t.startswith("field:") and t.count(":") >= 2:
                    parts = t.split(":", 2)
                    if parts[1]:
                        fields.add(parts[1])
            for cond in pat.get(f"{side}_conds") or []:
                if isinstance(cond, dict) and str(cond.get("type") or "") in ("", "field", "value"):
                    s = str(cond.get("field") or "").strip()
                    if s:
                        fields.add(s)
            for group in pat.get(f"{side}_groups") or []:
                if not isinstance(group, list):
                    continue
                for cond in group:
                    if isinstance(cond, dict) and str(cond.get("type") or "") in ("", "field", "value"):
                        s = str(cond.get("field") or "").strip()
                        if s:
                            fields.add(s)
    return fields


def _field_tokens_from_row(row: dict[str, Any] | None, fields: set[str]) -> set[str]:
    if not row or not fields:
        return set()
    out: set[str] = set()
    for f in fields:
        v = str(row.get(f) or "").strip()
        if v:
            out.add(field_token(f, v))
    return out


def _side_tokens(
    kind: str,
    status: str,
    *,
    row: dict[str, Any] | None = None,
    fields: set[str] | None = None,
) -> set[str]:
    toks: set[str] = set()
    k = str(kind or "").strip()
    if k:
        toks.add(k)
    s = str(status or "").strip()
    if s and s != "none":
        toks.add(s)
    toks |= _field_tokens_from_row(row, fields or set())
    return toks


def _norm_list_values(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(x).strip().lower() for x in raw if str(x).strip()]
    s = str(raw or "").strip()
    if not s:
        return []
    if "," in s:
        return [x.strip().lower() for x in s.split(",") if x.strip()]
    return [s.lower()]


def _eval_field_op(
    row: dict[str, Any] | None,
    field: str,
    op: str,
    value: Any,
    *,
    base_row: dict[str, Any] | None = None,
) -> bool:
    """Evaluate a value condition on current row (optionally vs baseline)."""
    f = str(field or "").strip()
    if not f:
        return False
    raw = "" if not row else str(row.get(f) or "").strip()
    val = raw.lower()
    o = str(op or "eq").strip().lower()
    if o in ("changed", "diff"):
        base = "" if not base_row else str(base_row.get(f) or "").strip().lower()
        if not row:
            return False
        return base != val
    if o in ("unchanged", "same"):
        if not row:
            return False
        base = "" if not base_row else str(base_row.get(f) or "").strip().lower()
        return base == val
    if o == "empty":
        return not val
    if o == "not_empty":
        return bool(val)
    need = _norm_list_values(value)
    if o == "eq":
        return bool(need) and val == need[0]
    if o == "ne":
        return bool(need) and val != need[0]
    if o == "in":
        return bool(need) and val in need
    if o == "not_in":
        return bool(need) and val not in need
    return False


def _normalize_cond_type(cond: dict[str, Any]) -> str:
    ctype = str(cond.get("type") or "").strip().lower()
    if ctype in ("kind", "presence"):
        return "presence"
    if ctype in ("field", "value"):
        return "value"
    if ctype == "status":
        # Legacy status class → treat as presence-of-status-token via status arg
        return "status"
    if cond.get("kind") is not None:
        return "presence"
    if cond.get("status") is not None:
        return "status"
    if cond.get("field"):
        return "value"
    return ctype


def _eval_condition(
    cond: Any,
    *,
    kind: str,
    status: str,
    row: dict[str, Any] | None,
    tokens: set[str],
    base_row: dict[str, Any] | None = None,
) -> bool:
    """One leaf: presence (no field) or value (needs field + strategy)."""
    if isinstance(cond, str):
        t = str(cond).strip()
        return bool(t) and t in tokens
    if not isinstance(cond, dict):
        return False
    ctype = _normalize_cond_type(cond)
    if ctype == "presence":
        want = str(
            cond.get("value")
            if cond.get("value") is not None
            else cond.get("kind")
            or ""
        ).strip()
        return bool(want) and want == str(kind or "").strip()
    if ctype == "status":
        want = str(
            cond.get("value")
            if cond.get("value") is not None
            else cond.get("status")
            or ""
        ).strip()
        return bool(want) and want == str(status or "").strip()
    if ctype == "value":
        field = str(cond.get("field") or "").strip()
        if not field:
            # Legacy status token with no field bound yet.
            need = _norm_list_values(cond.get("value"))
            return bool(need) and str(status or "").strip().lower() in need
        # Gone / missing row: value strategies do not apply (use presence instead).
        if str(kind or "").strip() == "removed" or not row:
            return str(cond.get("op") or "eq").strip().lower() == "empty"
        return _eval_field_op(
            row,
            field,
            str(cond.get("op") or "eq"),
            cond.get("value"),
            base_row=base_row,
        )
    return False


def _legacy_tokens_from_pat(pat: dict[str, Any], side: str) -> list[str]:
    return [str(x) for x in (pat.get(side) or []) if str(x).strip()]


def _conds_list_for_side(pat: dict[str, Any], side: str) -> list[Any]:
    key = f"{side}_conds"
    raw = pat.get(key)
    if isinstance(raw, list) and raw:
        return list(raw)
    return _legacy_tokens_from_pat(pat, side)


def _groups_for_side(pat: dict[str, Any], side: str) -> list[list[Any]]:
    """OR-of-AND groups. Prefer ``old_groups``/``new_groups``; migrate flat conds.

    Explicit empty list means don't-care for that side (no groups to match).
    """
    key = f"{side}_groups"
    if key in pat and isinstance(pat.get(key), list):
        raw = pat.get(key) or []
        if not raw:
            return []
        groups: list[list[Any]] = []
        for g in raw:
            if isinstance(g, list) and g:
                groups.append(list(g))
            elif g is not None and not isinstance(g, list):
                groups.append([g])
        return groups
    conds = _conds_list_for_side(pat, side)
    if not conds:
        return []
    mode = str(pat.get(f"{side}_mode") or "any").strip().lower()
    if mode == "all":
        return [list(conds)]
    # any → each cond is its own OR group
    return [[c] for c in conds]


def _match_groups(
    groups: list[list[Any]],
    *,
    kind: str,
    status: str,
    row: dict[str, Any] | None,
    tokens: set[str],
    base_row: dict[str, Any] | None = None,
) -> bool:
    """Group OR; within group AND."""
    if not groups:
        return False
    for group in groups:
        leaves = [c for c in group if c is not None]
        if not leaves:
            continue
        if all(
            _eval_condition(
                c,
                kind=kind,
                status=status,
                row=row,
                tokens=tokens,
                base_row=base_row,
            )
            for c in leaves
        ):
            return True
    return False


def _match_dual_pattern(
    pat: dict[str, Any],
    *,
    old_kind: str,
    new_kind: str,
    old_status: str,
    new_status: str,
    old_row: dict[str, Any] | None,
    new_row: dict[str, Any] | None,
    old_tokens: set[str],
    new_tokens: set[str],
    old_base: dict[str, Any] | None,
    new_base: dict[str, Any] | None,
    require_both_sides: bool,
) -> bool:
    """Match one dual pattern. Empty side groups = don't-care when not requiring both."""
    old_groups = _groups_for_side(pat, "old")
    new_groups = _groups_for_side(pat, "new")
    if require_both_sides:
        if not old_groups or not new_groups:
            return False
    elif not old_groups and not new_groups:
        return False
    old_ok = True if not old_groups else _match_groups(
        old_groups,
        kind=old_kind,
        status=old_status,
        row=old_row,
        tokens=old_tokens,
        base_row=old_base,
    )
    new_ok = True if not new_groups else _match_groups(
        new_groups,
        kind=new_kind,
        status=new_status,
        row=new_row,
        tokens=new_tokens,
        base_row=new_base,
    )
    return old_ok and new_ok


def _pattern_both_sides(pat: dict[str, Any]) -> bool:
    return bool(_groups_for_side(pat, "old")) and bool(_groups_for_side(pat, "new"))


def _match_patterns(
    patterns: list[dict[str, Any]] | None,
    *,
    old_kind: str = "",
    new_kind: str = "",
    old_status: str = "none",
    new_status: str = "none",
    old_row: dict[str, Any] | None = None,
    new_row: dict[str, Any] | None = None,
    old_tokens: set[str] | None = None,
    new_tokens: set[str] | None = None,
    old_base: dict[str, Any] | None = None,
    new_base: dict[str, Any] | None = None,
    require_both_sides: bool = True,
) -> int:
    """Return 1-based matched pattern index, or 0 if none match."""
    ot = old_tokens or set()
    nt = new_tokens or set()
    for i, pat in enumerate(patterns or [], start=1):
        if not isinstance(pat, dict):
            continue
        if _match_dual_pattern(
            pat,
            old_kind=old_kind,
            new_kind=new_kind,
            old_status=old_status,
            new_status=new_status,
            old_row=old_row,
            new_row=new_row,
            old_tokens=ot,
            new_tokens=nt,
            old_base=old_base,
            new_base=new_base,
            require_both_sides=require_both_sides,
        ):
            return i
    return 0


def _match_success(
    old_tokens: set[str],
    new_tokens: set[str],
    success_patterns: list[dict[str, Any]] | None,
    *,
    old_kind: str = "",
    new_kind: str = "",
    old_status: str = "none",
    new_status: str = "none",
    old_row: dict[str, Any] | None = None,
    new_row: dict[str, Any] | None = None,
    old_base: dict[str, Any] | None = None,
    new_base: dict[str, Any] | None = None,
) -> bool:
    return (
        _match_patterns(
            success_patterns,
            old_kind=old_kind,
            new_kind=new_kind,
            old_status=old_status,
            new_status=new_status,
            old_row=old_row,
            new_row=new_row,
            old_tokens=old_tokens,
            new_tokens=new_tokens,
            old_base=old_base,
            new_base=new_base,
            require_both_sides=True,
        )
        > 0
    )


def _match_field_token_rules(
    old_row: dict[str, Any] | None,
    new_row: dict[str, Any] | None,
    field_tokens: list[dict[str, Any]] | None,
) -> bool:
    """Optional AND constraints: each {side, field, in:[...]} must hold."""
    rules = [r for r in (field_tokens or []) if isinstance(r, dict)]
    if not rules:
        return True
    for ft in rules:
        side = str(ft.get("side") or "").strip().lower()
        field = str(ft.get("field") or "").strip()
        allowed = {str(x).strip().lower() for x in (ft.get("in") or []) if str(x).strip()}
        if not field or not allowed:
            continue
        row = old_row if side == "old" else new_row if side == "new" else None
        val = str((row or {}).get(field) or "").strip().lower()
        if val not in allowed:
            return False
    return True


def dual_verdict(
    *,
    old_kind: str,
    new_kind: str,
    in_expect: bool,
    window_active: bool,
    acceptance: bool = False,
    old_status: str = "none",
    new_status: str = "none",
    success_patterns: list[dict[str, Any]] | None = None,
    anomaly_patterns: list[dict[str, Any]] | None = None,
    out_of_expect: str = "strict",
    old_row: dict[str, Any] | None = None,
    new_row: dict[str, Any] | None = None,
    old_base: dict[str, Any] | None = None,
    new_base: dict[str, Any] | None = None,
    sheet_override: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Synthesize old+new into migration board verdict. See dual_verdict_ex."""
    verdict, color, _hit = dual_verdict_ex(
        old_kind=old_kind,
        new_kind=new_kind,
        in_expect=in_expect,
        window_active=window_active,
        acceptance=acceptance,
        old_status=old_status,
        new_status=new_status,
        success_patterns=success_patterns,
        anomaly_patterns=anomaly_patterns,
        out_of_expect=out_of_expect,
        old_row=old_row,
        new_row=new_row,
        old_base=old_base,
        new_base=new_base,
        sheet_override=sheet_override,
    )
    return verdict, color


def dual_verdict_ex(
    *,
    old_kind: str,
    new_kind: str,
    in_expect: bool,
    window_active: bool,
    acceptance: bool = False,
    old_status: str = "none",
    new_status: str = "none",
    success_patterns: list[dict[str, Any]] | None = None,
    anomaly_patterns: list[dict[str, Any]] | None = None,
    out_of_expect: str = "strict",
    old_row: dict[str, Any] | None = None,
    new_row: dict[str, Any] | None = None,
    old_base: dict[str, Any] | None = None,
    new_base: dict[str, Any] | None = None,
    sheet_override: dict[str, Any] | None = None,
) -> tuple[str, str, str]:
    """Like dual_verdict, plus rule_hit label (success#N / anomaly#N / heuristic).

    Patterns use OR-of-AND groups (``old_groups`` / ``new_groups``):
    within a group = AND, between groups = OR. Leaf types:
      - presence: row existence vs baseline (removed/added/unchanged/changed)
      - value: field strategy (eq/ne/in/empty/changed/…)

    Order (in expect): success → anomaly → heuristics.
    Anomaly may leave a side empty (= don't-care).
    During an active cutover window (not acceptance), side-only anomaly
    patterns are deferred so mid-migration downs do not false-red.
    When success or anomaly patterns are non-empty (rules mode), skip invented
    green-migrate and catch-all anomaly; keep lost / migrating only.
    """
    ov = sheet_override or {}
    if anomaly_patterns is None and isinstance(ov.get("anomaly"), list):
        anomaly_patterns = list(ov.get("anomaly") or [])
    fields = _fields_for_tokens(ov, success_patterns, anomaly_patterns)
    old_toks = _side_tokens(old_kind, old_status, row=old_row, fields=fields)
    new_toks = _side_tokens(new_kind, new_status, row=new_row, fields=fields)
    field_ok = _match_field_token_rules(old_row, new_row, list(ov.get("field_tokens") or []) or None)

    match_kw = dict(
        old_kind=old_kind,
        new_kind=new_kind,
        old_status=old_status,
        new_status=new_status,
        old_row=old_row,
        new_row=new_row,
        old_tokens=old_toks,
        new_tokens=new_toks,
        old_base=old_base,
        new_base=new_base,
    )

    hit = _match_patterns(success_patterns, require_both_sides=True, **match_kw)
    if in_expect and field_ok and hit:
        return "migrated", "green", f"success#{hit}"

    ooe = str(out_of_expect or "strict").strip().lower()
    if not in_expect:
        if ooe == "ignore":
            return "not_involved", "gray", "out_of_expect:ignore"
        if old_kind in ("removed", "changed") or new_kind in ("removed", "changed"):
            # Both gone / old gone with no new → lost; any other drift (incl. new-only
            # removed while old still present) → anomaly.
            if old_kind == "removed" and new_kind in ("", "removed"):
                label = "lost" if ooe == "warn" else "lost"
                color = "yellow" if ooe == "warn" else "red"
                return label, color, "out_of_expect:lost"
            return (
                ("anomaly", "yellow", "out_of_expect:anomaly")
                if ooe == "warn"
                else ("anomaly", "red", "out_of_expect:anomaly")
            )
        if old_kind == "added" or new_kind == "added":
            return "unexpected_new", "yellow", "out_of_expect:added"
        return "not_involved", "gray", "out_of_expect:none"

    # field_tokens gate success only — do not block anomaly on a gone side
    anomaly_list = list(anomaly_patterns or [])
    if window_active and not acceptance:
        # Side-only anomaly = don't-care other side; defer until acceptance / window closed.
        anomaly_list = [p for p in anomaly_list if isinstance(p, dict) and _pattern_both_sides(p)]
    ahit = _match_patterns(anomaly_list, require_both_sides=False, **match_kw)
    if in_expect and ahit:
        return "anomaly", "red", f"anomaly#{ahit}"

    # Explicit success/anomaly lists mean "rules mode": do not invent green migrate
    # or catch-all anomaly; keep lost / migrating as mid-state fallbacks only.
    rules_mode = bool(success_patterns) or bool(anomaly_patterns)

    if (
        not rules_mode
        and old_kind in ("removed", "changed")
        and new_kind in ("added", "unchanged", "changed")
    ):
        return "migrated", "green", "heuristic:migrate"
    if old_kind == "removed" and new_kind in ("", "removed"):
        return "lost", "red", "heuristic:lost"
    if old_kind in ("unchanged", "") and new_kind in ("unchanged", "added", "changed"):
        if acceptance:
            return "unfinished", "red", "heuristic:unfinished"
        return "migrating", "yellow", "heuristic:migrating"
    if old_kind == "unchanged" and new_kind in ("", "removed"):
        if acceptance:
            return "unfinished", "red", "heuristic:unfinished"
        return "migrating", "yellow", "heuristic:migrating"
    if not rules_mode:
        side_v, side_c = side_verdict(
            kind=old_kind or "unchanged", in_expect=True, window_active=window_active
        )
        if side_c == "red" or (new_kind in ("removed",) and old_kind != "removed"):
            return "anomaly", "red", "heuristic:anomaly"
    else:
        side_v, _side_c = side_verdict(
            kind=old_kind or "unchanged", in_expect=True, window_active=window_active
        )
    if acceptance:
        return "unfinished", "red", "heuristic:unfinished"
    if window_active or side_v.startswith("expected"):
        return "migrating", "yellow", "heuristic:migrating"
    return "migrating", "yellow", "heuristic:migrating"


def build_diff_index_from_compare(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Normalize compare_rows result diffs → key_str → {kind, before, after, key}."""
    out: dict[str, dict[str, Any]] = {}
    for d in result.get("diffs") or []:
        key = d.get("key")
        if isinstance(key, dict):
            key_list = [str(v) for v in key.values()]
            ks = _key_str(key_list)
        elif isinstance(key, (list, tuple)):
            key_list = [str(x) for x in key]
            ks = _key_str(key_list)
        else:
            ks = str(key or "")
            key_list = [ks] if ks else []
        before = dict(d.get("before") or {}) if d.get("before") else {}
        after = dict(d.get("after") or {}) if d.get("after") else {}
        out[ks] = {
            "kind": str(d.get("kind") or ""),
            "key": key_list,
            "before": before,
            "after": after,
            "mapped_before": dict(d.get("mapped_before") or {}) if d.get("mapped_before") else {},
        }
    return out


def expect_keys_for_metric(
    expect: dict[str, set[str]],
    *,
    metric_id: str,
    iface_fields: list[str],
    sheet_id: str = "",
) -> set[str]:
    """Resolve expect keys for a compare sheet.

    Prefer ``sheet_id`` (split tables); fall back to ``metric_id`` for legacy
    unsplit sheets / expect items that only carry metric_id.
    """
    sid = str(sheet_id or "").strip()
    mid = str(metric_id or "").strip()
    keys = set(expect.get(sid) or ()) if sid else set()
    if not keys and mid:
        keys = set(expect.get(mid) or ())
    # ports: shorthand is only for interface_brief, not every sheet with iface fields
    if mid == PORT_METRIC_ID and expect.get("_ports"):
        keys |= set(expect["_ports"])
    return keys


def _map_defines_iface_expect(metric_id: str, iface_fields: list[str], port_map: dict[str, str]) -> bool:
    """Iface sheets: a non-empty port map is the expect scope.

    BGP / PW keep the batch expect picker even if a column is marked iface.
    """
    if not port_map or not iface_fields:
        return False
    mid = str(metric_id or "").strip().lower()
    if "bgp" in mid or "pw" in mid or "pseudowire" in mid:
        return False
    return True


def _iface_values(key_str: str, *, key_fields: list[str], iface_fields: list[str]) -> list[str]:
    parts = str(key_str or "").split("|")
    iface_set = {str(f) for f in iface_fields}
    if key_fields and len(parts) == len(key_fields):
        return [parts[i] for i, f in enumerate(key_fields) if f in iface_set and parts[i]]
    if len(key_fields) == 1 and key_fields[0] in iface_set and key_str:
        return [key_str]
    return []


def _key_in_port_map(
    key_str: str,
    *,
    key_fields: list[str],
    iface_fields: list[str],
    port_map: dict[str, str],
) -> bool:
    """True when every interface segment of the key is covered by the map.

    Covered = exact map key, or parent (before last ``.``) is a map key.
    """
    vals = _iface_values(key_str, key_fields=key_fields, iface_fields=iface_fields)
    if not vals:
        return False
    for v in vals:
        if v in port_map:
            continue
        parent = v.rsplit(".", 1)[0] if "." in v else ""
        if parent and parent in port_map:
            continue
        return False
    return True


def _port_identity(key_fields: list[str], iface_fields: list[str]) -> bool:
    """Key is the interface itself (interface_brief), not a business row hanging off it."""
    if not key_fields or not iface_fields:
        return False
    iface_set = {str(f) for f in iface_fields}
    return all(str(f) in iface_set for f in key_fields)


def _unmapped_same_iface_anomaly(
    *,
    old_kind: str,
    new_kind: str,
    old_status: str,
    new_status: str,
) -> bool:
    """Same name, not in the map: old down/gone + new still up is not a migration."""
    old_bad = old_kind in ("removed", "changed") or old_status == "down"
    new_up = new_kind in ("added", "unchanged", "changed") and new_status != "down"
    return old_bad and new_up


def _remap_key_str(
    key_str: str,
    *,
    key_fields: list[str],
    iface_fields: list[str],
    port_map: dict[str, str],
) -> str:
    """Map old-side key_str → new-side using iface segments (not whole-string only)."""
    from ..biz_state.iface_normalize import resolve_mapped_iface

    if not key_str:
        return key_str
    if not port_map:
        return key_str
    if (not key_fields or len(key_fields) == 1) and (
        key_str in port_map or ("." in key_str and key_str.rsplit(".", 1)[0] in port_map)
    ):
        return resolve_mapped_iface(key_str, port_map)
    parts = str(key_str).split("|")
    if key_fields and len(parts) == len(key_fields):
        iface_set = {str(f) for f in iface_fields}
        out: list[str] = []
        for i, f in enumerate(key_fields):
            v = parts[i]
            if f in iface_set:
                out.append(resolve_mapped_iface(v, port_map))
            else:
                out.append(v)
        return "|".join(out)
    return resolve_mapped_iface(key_str, port_map)


def _reverse_remap_key_str(
    key_str: str,
    *,
    key_fields: list[str],
    iface_fields: list[str],
    rev_map: dict[str, str],
) -> str:
    """Map new-side key_str → old-side canon identity."""
    from ..biz_state.iface_normalize import resolve_mapped_iface

    if not key_str:
        return key_str
    if not rev_map:
        return key_str
    if (not key_fields or len(key_fields) == 1) and (
        key_str in rev_map or ("." in key_str and key_str.rsplit(".", 1)[0] in rev_map)
    ):
        return resolve_mapped_iface(key_str, rev_map)
    parts = str(key_str).split("|")
    if key_fields and len(parts) == len(key_fields):
        iface_set = {str(f) for f in iface_fields}
        out: list[str] = []
        for i, f in enumerate(key_fields):
            v = parts[i]
            if f in iface_set:
                out.append(resolve_mapped_iface(v, rev_map))
            else:
                out.append(v)
        return "|".join(out)
    return resolve_mapped_iface(key_str, rev_map)


def _current_row(diff: dict[str, Any] | None) -> dict[str, Any]:
    """Current-side snapshot from a compare diff (empty if removed / missing)."""
    if not diff:
        return {}
    kind = str(diff.get("kind") or "")
    if kind == "removed":
        return {}
    return dict(diff.get("after") or {})


def override_for_sheet(
    sheet_overrides: list[dict[str, Any]] | None,
    *,
    sheet_id: str,
    metric_id: str,
) -> dict[str, Any]:
    """Match a monitor override. sheet_id wins; a legacy metric-only override applies to every split."""
    sid = str(sheet_id or "").strip()
    mid = str(metric_id or "").strip()
    legacy: dict[str, Any] | None = None
    for ov in sheet_overrides or []:
        if not isinstance(ov, dict):
            continue
        ov_sid = str(ov.get("sheet_id") or "").strip()
        ov_mid = str(ov.get("metric_id") or "").strip()
        if ov_sid and ov_sid == sid:
            return ov
        if not ov_sid and ov_mid and ov_mid in (sid, mid) and legacy is None:
            legacy = ov
    return legacy or {}


def override_for_metric(
    sheet_overrides: list[dict[str, Any]] | None,
    metric_id: str,
) -> dict[str, Any]:
    mid = str(metric_id or "").strip()
    return override_for_sheet(sheet_overrides, sheet_id=mid, metric_id=mid)


def strip_netx(row: dict[str, Any] | None) -> dict[str, Any]:
    """Drop collector provenance key before compare / board row bodies."""
    if not row:
        return {}
    return {k: v for k, v in row.items() if k != "_netx"}


def row_match_key(
    row: dict[str, Any],
    *,
    key_fields: list[str],
    iface_fields: list[str],
    rules: list[dict[str, str]] | None,
) -> str:
    from ..biz_state.iface_normalize import apply_iface_normalize

    data = strip_netx(row)
    if iface_fields and rules:
        data = apply_iface_normalize(dict(data), iface_fields=iface_fields, rules=rules)
    return "|".join(str(data.get(k) or "").strip() for k in key_fields)


def index_raw_by_match_key(
    raw_rows: list[dict[str, Any]],
    *,
    key_fields: list[str],
    iface_fields: list[str],
    rules: list[dict[str, str]] | None,
) -> dict[str, dict[str, Any]]:
    """First raw row per internal match key (normalized iface, raw elsewhere)."""
    idx: dict[str, dict[str, Any]] = {}
    for raw in raw_rows:
        ks = row_match_key(raw, key_fields=key_fields, iface_fields=iface_fields, rules=rules)
        if ks and ks not in idx:
            idx[ks] = raw
    return idx


def display_key_from_raw(raw: dict[str, Any] | None, key_fields: list[str]) -> str:
    """Board / AI facing identity: as collected on the device (no normalize / map)."""
    if not raw or not key_fields:
        return ""
    data = strip_netx(raw)
    return "|".join(str(data.get(k) or "").strip() for k in key_fields)


def iface_lineage(
    raw: dict[str, Any] | None,
    *,
    iface_fields: list[str],
    rules: list[dict[str, str]] | None,
    port_map: dict[str, str] | None,
    apply_map: bool,
) -> list[dict[str, Any]]:
    """Per iface field: raw → normalized → (optional) mapped."""
    from ..biz_state.iface_normalize import normalize_iface_name, resolve_mapped_iface

    if not raw or not iface_fields:
        return []
    data = strip_netx(raw)
    pmap = port_map or {}
    out: list[dict[str, Any]] = []
    for f in iface_fields:
        raw_v = str(data.get(f) or "").strip()
        norm_v = normalize_iface_name(raw_v, rules) if rules else raw_v
        mapped = False
        map_to = ""
        if apply_map and pmap and norm_v:
            map_to = resolve_mapped_iface(norm_v, pmap)
            mapped = bool(map_to) and map_to != norm_v
        out.append(
            {
                "field": f,
                "raw": raw_v,
                "normalized": norm_v,
                "mapped": mapped,
                "map_from": norm_v if apply_map and pmap else "",
                "map_to": map_to if apply_map and pmap else "",
            }
        )
    return out


def evaluate_metric_dual(
    *,
    metric_id: str,
    key_fields: list[str],
    iface_fields: list[str],
    compare_fields: list[str],
    old_baseline_rows: list[dict[str, Any]],
    old_current_rows: list[dict[str, Any]],
    new_baseline_rows: list[dict[str, Any]] | None,
    new_current_rows: list[dict[str, Any]],
    port_map: dict[str, str],
    expect: dict[str, set[str]],
    window_active: bool,
    acceptance: bool = False,
    field_rules: list[dict[str, Any]] | None = None,
    sheet_override: dict[str, Any] | None = None,
    out_of_expect: str = "strict",
    sheet_id: str = "",
    iface_normalize_rules: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Run old vs old-baseline, new vs new-baseline (or mapped old baseline), dual merge.

    Matching uses normalized (+ port-mapped) keys internally.
    Board / AI fields ``old_key`` / ``new_key`` and ``old`` / ``new`` stay **raw**
    device values (A/B), never the post-map BB form.
    """
    from ..biz_state.iface_normalize import (
        apply_iface_normalize_rows,
        normalize_iface_rules,
    )

    sid = str(sheet_id or "").strip() or str(metric_id or "").strip()
    expect_keys = expect_keys_for_metric(
        expect, metric_id=metric_id, iface_fields=iface_fields, sheet_id=sid
    )
    ov = sheet_override or {}
    if ov.get("skip_dual"):
        return {
            "metric_id": metric_id,
            "old_summary": {},
            "new_summary": {},
            "progress_ok": 0,
            "progress_total": 0,
            "anomaly": 0,
            "rows": [],
            "skipped": True,
            "new_baseline_mode": "skipped",
            "new_baseline_missing": False,
            "anomaly_in_expect": 0,
        }
    success_patterns = list(ov.get("success") or []) if isinstance(ov.get("success"), list) else []
    anomaly_patterns = list(ov.get("anomaly") or []) if isinstance(ov.get("anomaly"), list) else []

    # Keep collector originals for display / evidence; compare on normalized copies.
    raw_old_base = [dict(r) for r in (old_baseline_rows or [])]
    raw_old_cur = [dict(r) for r in (old_current_rows or [])]
    raw_new_cur = [dict(r) for r in (new_current_rows or [])]
    raw_new_base = (
        [dict(r) for r in new_baseline_rows] if new_baseline_rows is not None else None
    )

    norm_rules = normalize_iface_rules(iface_normalize_rules)
    old_baseline_rows = apply_iface_normalize_rows(
        [strip_netx(r) for r in raw_old_base], iface_fields=iface_fields, rules=norm_rules
    )
    old_current_rows = apply_iface_normalize_rows(
        [strip_netx(r) for r in raw_old_cur], iface_fields=iface_fields, rules=norm_rules
    )
    new_current_rows = apply_iface_normalize_rows(
        [strip_netx(r) for r in raw_new_cur], iface_fields=iface_fields, rules=norm_rules
    )
    if raw_new_base is not None:
        new_baseline_rows = apply_iface_normalize_rows(
            [strip_netx(r) for r in raw_new_base],
            iface_fields=iface_fields,
            rules=norm_rules,
        )
    else:
        new_baseline_rows = None

    old_raw_cur_idx = index_raw_by_match_key(
        raw_old_cur, key_fields=key_fields, iface_fields=iface_fields, rules=norm_rules
    )
    old_raw_base_idx = index_raw_by_match_key(
        raw_old_base, key_fields=key_fields, iface_fields=iface_fields, rules=norm_rules
    )
    new_raw_cur_idx = index_raw_by_match_key(
        raw_new_cur, key_fields=key_fields, iface_fields=iface_fields, rules=norm_rules
    )
    new_raw_base_idx = index_raw_by_match_key(
        raw_new_base or [],
        key_fields=key_fields,
        iface_fields=iface_fields,
        rules=norm_rules,
    )

    old_cmp = compare_rows(
        before_rows=old_baseline_rows,
        after_rows=old_current_rows,
        key_fields=key_fields,
        iface_fields=[],
        compare_fields=compare_fields,
        port_map=None,
        field_rules=field_rules,
        iface_normalize_rules=None,  # already applied
    )
    old_idx = build_diff_index_from_compare(old_cmp)

    new_baseline_mode = "provided"
    new_baseline_missing = False
    if new_baseline_rows is not None:
        new_before = list(new_baseline_rows)
        new_baseline_mode = "provided"
        # Explicit empty batch is as unusable as "no baseline" for presence semantics.
        if not new_before and (old_baseline_rows or new_current_rows):
            if port_map and iface_fields:
                new_before = [
                    apply_port_map(r, iface_fields=iface_fields, port_map=port_map)
                    for r in old_baseline_rows
                ]
                new_baseline_mode = "port_mapped"
                new_baseline_missing = False
            else:
                new_baseline_missing = True
                new_baseline_mode = "empty"
    elif port_map and iface_fields:
        new_before = [
            apply_port_map(r, iface_fields=iface_fields, port_map=port_map)
            for r in old_baseline_rows
        ]
        new_baseline_mode = "port_mapped"
    else:
        new_before = []
        new_baseline_mode = "empty"
        # No new baseline and no map → every current new row looks like "added".
        new_baseline_missing = bool(old_baseline_rows or new_current_rows)

    new_cmp = compare_rows(
        before_rows=new_before,
        after_rows=new_current_rows,
        key_fields=key_fields,
        iface_fields=[],
        compare_fields=compare_fields,
        port_map=None,
        field_rules=field_rules,
    )
    new_idx = build_diff_index_from_compare(new_cmp)

    rev_map = {v: k for k, v in port_map.items()}
    map_scoped = _map_defines_iface_expect(metric_id, iface_fields, port_map)
    if map_scoped:
        scoped: set[str] = set()
        for ks in old_idx:
            if _key_in_port_map(
                ks, key_fields=key_fields, iface_fields=iface_fields, port_map=port_map
            ):
                scoped.add(ks)
        for ks in new_idx:
            canon = _reverse_remap_key_str(
                ks,
                key_fields=key_fields,
                iface_fields=iface_fields,
                rev_map=rev_map,
            )
            if _key_in_port_map(
                canon, key_fields=key_fields, iface_fields=iface_fields, port_map=port_map
            ):
                scoped.add(canon)
        expect_keys = scoped

    canon_keys: list[str] = []
    seen: set[str] = set()

    def _add(canon: str) -> None:
        if canon and canon not in seen:
            seen.add(canon)
            canon_keys.append(canon)

    for ok in sorted(expect_keys):
        _add(ok)
    for ks in sorted(old_idx):
        _add(ks)
    for ks in sorted(new_idx):
        _add(
            _reverse_remap_key_str(
                ks,
                key_fields=key_fields,
                iface_fields=iface_fields,
                rev_map=rev_map,
            )
        )

    rows_out: list[dict[str, Any]] = []
    progress_ok = 0
    progress_total = 0
    anomaly = 0
    anomaly_in_expect = 0

    for old_ks in canon_keys:
        new_ks = _remap_key_str(
            old_ks,
            key_fields=key_fields,
            iface_fields=iface_fields,
            port_map=port_map,
        )
        in_exp = old_ks in expect_keys
        if in_exp:
            progress_total += 1

        od = old_idx.get(old_ks)
        nd = new_idx.get(new_ks) or new_idx.get(old_ks)

        old_kind = str((od or {}).get("kind") or "")
        new_kind = str((nd or {}).get("kind") or "")
        if od is None and nd is None and not in_exp:
            continue
        if od is None:
            old_kind = ""
        if nd is None:
            new_kind = ""

        old_cur = _current_row(od)
        new_cur = _current_row(nd)
        old_base = dict((od or {}).get("before") or {})
        new_base = dict((nd or {}).get("before") or {})
        old_st = classify_status(old_cur, ov)
        new_st = classify_status(new_cur, ov)

        # Mapped iface rows are the expect set. Unmapped rows stay out of the
        # migration compare, except same-name ports where old went down and the
        # new device's same port is up — that is anomaly, not a successful cutover.
        if map_scoped and not in_exp:
            if not (
                _port_identity(key_fields, iface_fields)
                and _unmapped_same_iface_anomaly(
                    old_kind=old_kind,
                    new_kind=new_kind,
                    old_status=old_st,
                    new_status=new_st,
                )
            ):
                continue
            verdict, color, rule_hit = "anomaly", "red", "unmapped_same_iface"
        else:
            verdict, color, rule_hit = dual_verdict_ex(
                old_kind=old_kind or "",
                new_kind=new_kind or "",
                in_expect=in_exp,
                window_active=window_active,
                acceptance=acceptance,
                old_status=old_st,
                new_status=new_st,
                success_patterns=success_patterns or None,
                anomaly_patterns=anomaly_patterns or None,
                out_of_expect=out_of_expect,
                old_row=old_cur or None,
                new_row=new_cur or None,
                old_base=old_base or None,
                new_base=new_base or None,
                sheet_override=ov,
            )
        if in_exp and verdict == "migrated" and color == "green":
            progress_ok += 1
        if color == "red":
            anomaly += 1
            if in_exp:
                anomaly_in_expect += 1

        # Prefer live raw rows; fall back to baseline raw. Never use port-mapped
        # synthetic "before" as the new-side display row.
        raw_old = old_raw_cur_idx.get(old_ks) or old_raw_base_idx.get(old_ks)
        raw_new = new_raw_cur_idx.get(new_ks) or new_raw_cur_idx.get(old_ks)
        if not raw_new:
            raw_new = new_raw_base_idx.get(new_ks) or new_raw_base_idx.get(old_ks)

        display_old = display_key_from_raw(raw_old, key_fields) or old_ks
        display_new = display_key_from_raw(raw_new, key_fields) or (
            new_ks if raw_new is not None else ""
        )
        # If new side missing entirely, still show map target as hint only in match_*
        if not display_new and new_ks and new_ks != old_ks:
            display_new = ""

        old_disp = strip_netx(raw_old) if raw_old else {}
        new_disp = strip_netx(raw_new) if raw_new else {}
        old_base_raw = strip_netx(old_raw_base_idx.get(old_ks)) if old_raw_base_idx.get(old_ks) else {}
        new_base_raw = (
            strip_netx(new_raw_base_idx.get(new_ks) or new_raw_base_idx.get(old_ks))
            if (new_raw_base_idx.get(new_ks) or new_raw_base_idx.get(old_ks))
            else {}
        )

        evidence = {
            "old": {
                "netx": dict((raw_old or {}).get("_netx") or {}),
                "iface": iface_lineage(
                    raw_old,
                    iface_fields=iface_fields,
                    rules=norm_rules,
                    port_map=port_map,
                    apply_map=True,
                ),
            },
            "new": {
                "netx": dict((raw_new or {}).get("_netx") or {}),
                "iface": iface_lineage(
                    raw_new,
                    iface_fields=iface_fields,
                    rules=norm_rules,
                    port_map=None,
                    apply_map=False,
                ),
            },
            "port_map": {
                "applied": bool(port_map and iface_fields and old_ks != new_ks),
                "match_before": old_ks,
                "match_after": new_ks,
                "display_before": display_old,
                "display_after": display_new,
            },
        }

        rows_out.append(
            {
                "metric_id": metric_id,
                "key": (od or nd or {}).get("key") or [old_ks],
                # Display identity (A/B as collected) — board & MCP primary
                "old_key": display_old,
                "new_key": display_new,
                # Internal match keys (normalized / remapped)
                "match_old_key": old_ks,
                "match_new_key": new_ks,
                # Aliases: key_str = display old; new_key_str = display new
                "key_str": display_old,
                "new_key_str": display_new,
                "verdict": verdict,
                "color": color,
                "rule_hit": rule_hit,
                "in_expect": in_exp,
                "old_kind": old_kind,
                "new_kind": new_kind,
                "old": old_disp,
                "new": new_disp,
                "old_baseline": old_base_raw,
                "new_baseline": new_base_raw,
                "old_status": status_label(old_cur, ov)
                if old_cur
                else ("gone" if old_kind == "removed" else "—"),
                "new_status": status_label(new_cur, ov)
                if new_cur
                else ("gone" if new_kind == "removed" else "—"),
                "old_side": side_verdict(
                    kind=old_kind or "unchanged",
                    in_expect=in_exp,
                    window_active=window_active,
                )
                if old_kind
                else ("", "gray"),
                "new_side": side_verdict(
                    kind=new_kind or "unchanged",
                    in_expect=in_exp,
                    window_active=window_active,
                )
                if new_kind
                else ("", "gray"),
                "evidence": evidence,
            }
        )

    return {
        "metric_id": metric_id,
        "old_summary": old_cmp.get("summary") or {},
        "new_summary": new_cmp.get("summary") or {},
        "progress_ok": progress_ok,
        "progress_total": progress_total if progress_total else len(expect_keys),
        "anomaly": anomaly,
        "anomaly_in_expect": anomaly_in_expect,
        "rows": rows_out,
        "new_baseline_mode": new_baseline_mode,
        "new_baseline_missing": new_baseline_missing,
    }


def port_sheet_def() -> dict[str, Any]:
    """Default sheet for port-status cutover monitor (interface_brief only)."""
    return {
        "sheet_id": PORT_METRIC_ID,
        "title": PORT_METRIC_ID,
        "metric_id": PORT_METRIC_ID,
        "key_fields": ["interface"],
        "iface_fields": ["interface"],
        "compare_fields": ["admin", "phy", "prot"],
        "row_filters": [],
        "field_rules": [],
    }
