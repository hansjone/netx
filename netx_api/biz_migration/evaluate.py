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
            if not mid:
                continue
            bucket = out.setdefault(mid, set())
            if it.get("key") is not None:
                bucket.add(str(it.get("key") or "").strip())
            keys = it.get("keys")
            if isinstance(keys, list):
                if all(not isinstance(x, (list, tuple, dict)) for x in keys):
                    bucket.add("|".join(str(x).strip() for x in keys))
                else:
                    for x in keys:
                        bucket.add(str(x).strip())
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


def _side_tokens(kind: str, status: str) -> set[str]:
    toks: set[str] = set()
    k = str(kind or "").strip()
    if k:
        toks.add(k)
    s = str(status or "").strip()
    if s and s != "none":
        toks.add(s)
    return toks


def _match_success(
    old_tokens: set[str],
    new_tokens: set[str],
    success_patterns: list[dict[str, Any]] | None,
) -> bool:
    for pat in success_patterns or []:
        if not isinstance(pat, dict):
            continue
        old_need = {str(x) for x in (pat.get("old") or []) if str(x)}
        new_need = {str(x) for x in (pat.get("new") or []) if str(x)}
        if not old_need or not new_need:
            continue
        if (old_need & old_tokens) and (new_need & new_tokens):
            return True
    return False


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
) -> tuple[str, str]:
    """Synthesize old+new into migration board verdict.

    ``acceptance=True`` (本批完成终验): unfinished expect items become red
    (``unfinished`` / ``lost``), not yellow migrating.

    When ``success_patterns`` is set (from monitor sheet_overrides), tokens may
    include kinds and status classes (up/down) so e.g. old down + new up → migrated.
    """
    if in_expect and _match_success(
        _side_tokens(old_kind, old_status),
        _side_tokens(new_kind, new_status),
        success_patterns,
    ):
        return "migrated", "green"

    if not in_expect:
        if old_kind in ("removed", "changed") or new_kind in ("removed", "changed"):
            if old_kind == "removed" and new_kind in ("", "removed"):
                return "lost", "red"
            if old_kind in ("removed", "changed") or new_kind in ("changed",):
                return "anomaly", "red"
        if old_kind == "added" or new_kind == "added":
            return "unexpected_new", "yellow"
        return "not_involved", "gray"

    if old_kind in ("removed", "changed") and new_kind in ("added", "unchanged", "changed"):
        return "migrated", "green"
    if old_kind == "removed" and new_kind in ("", "removed"):
        return "lost", "red"
    if old_kind in ("unchanged", "") and new_kind in ("unchanged", "added", "changed"):
        if acceptance:
            return "unfinished", "red"
        return "migrating", "yellow"
    if old_kind == "unchanged" and new_kind in ("", "removed"):
        if acceptance:
            return "unfinished", "red"
        return "migrating", "yellow"
    ov, oc = side_verdict(kind=old_kind or "unchanged", in_expect=True, window_active=window_active)
    if oc == "red" or (new_kind in ("removed",) and old_kind != "removed"):
        return "anomaly", "red"
    if acceptance:
        return "unfinished", "red"
    if window_active or ov.startswith("expected"):
        return "migrating", "yellow"
    return "migrating", "yellow"


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
) -> set[str]:
    keys = set(expect.get(metric_id) or ())
    if iface_fields and expect.get("_ports"):
        keys |= set(expect["_ports"])
    return keys


def _current_row(diff: dict[str, Any] | None) -> dict[str, Any]:
    """Current-side snapshot from a compare diff (empty if removed / missing)."""
    if not diff:
        return {}
    kind = str(diff.get("kind") or "")
    if kind == "removed":
        return {}
    return dict(diff.get("after") or {})


def override_for_metric(
    sheet_overrides: list[dict[str, Any]] | None,
    metric_id: str,
) -> dict[str, Any]:
    mid = str(metric_id or "").strip()
    for ov in sheet_overrides or []:
        if isinstance(ov, dict) and str(ov.get("metric_id") or "").strip() == mid:
            return ov
    return {}


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
) -> dict[str, Any]:
    """Run old vs old-baseline, new vs new-baseline (or mapped old baseline), dual merge."""
    expect_keys = expect_keys_for_metric(expect, metric_id=metric_id, iface_fields=iface_fields)
    ov = sheet_override or {}
    success_patterns = list(ov.get("success") or []) if isinstance(ov.get("success"), list) else []

    old_cmp = compare_rows(
        before_rows=old_baseline_rows,
        after_rows=old_current_rows,
        key_fields=key_fields,
        iface_fields=[],
        compare_fields=compare_fields,
        port_map=None,
        field_rules=field_rules,
    )
    old_idx = build_diff_index_from_compare(old_cmp)

    if new_baseline_rows is not None:
        new_before = new_baseline_rows
    elif port_map and iface_fields:
        new_before = [
            apply_port_map(r, iface_fields=iface_fields, port_map=port_map)
            for r in old_baseline_rows
        ]
    else:
        new_before = []

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
        _add(rev_map.get(ks, ks))

    rows_out: list[dict[str, Any]] = []
    progress_ok = 0
    progress_total = 0
    anomaly = 0

    for old_ks in canon_keys:
        new_ks = port_map.get(old_ks, old_ks)
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
        old_st = classify_status(old_cur, ov)
        new_st = classify_status(new_cur, ov)

        verdict, color = dual_verdict(
            old_kind=old_kind or "",
            new_kind=new_kind or "",
            in_expect=in_exp,
            window_active=window_active,
            acceptance=acceptance,
            old_status=old_st,
            new_status=new_st,
            success_patterns=success_patterns or None,
        )
        if in_exp and verdict == "migrated" and color == "green":
            progress_ok += 1
        if color == "red":
            anomaly += 1

        rows_out.append(
            {
                "metric_id": metric_id,
                "key": (od or nd or {}).get("key") or [old_ks],
                "key_str": old_ks,
                "new_key_str": new_ks,
                "verdict": verdict,
                "color": color,
                "in_expect": in_exp,
                "old_kind": old_kind,
                "new_kind": new_kind,
                "old": old_cur,
                "new": new_cur,
                "old_baseline": dict((od or {}).get("before") or {}),
                "new_baseline": dict((nd or {}).get("before") or {}),
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
            }
        )

    return {
        "metric_id": metric_id,
        "old_summary": old_cmp.get("summary") or {},
        "new_summary": new_cmp.get("summary") or {},
        "progress_ok": progress_ok,
        "progress_total": progress_total if progress_total else len(expect_keys),
        "anomaly": anomaly,
        "rows": rows_out,
    }


def port_sheet_def() -> dict[str, Any]:
    """Default sheet for port-status cutover monitor (interface_brief only)."""
    return {
        "metric_id": PORT_METRIC_ID,
        "key_fields": ["interface"],
        "iface_fields": ["interface"],
        "compare_fields": ["admin", "phy", "prot"],
        "row_filters": [],
        "field_rules": [],
    }
