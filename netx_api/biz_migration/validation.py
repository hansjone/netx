"""Validate monitor correction rules before they can affect live verdicts."""
from typing import Any

from fastapi import HTTPException

from .evaluate import _groups_for_side, _normalize_cond_type


def validate_monitor_rules(defaults: Any, overrides: Any) -> None:
    def fail(path: str) -> None:
        raise HTTPException(status_code=400, detail=f"monitor_rule_invalid:{path}")

    if not isinstance(defaults, dict) or not isinstance(overrides, list):
        fail("defaults/sheet_overrides")
    if defaults.get("out_of_expect", "strict") not in ("strict", "warn", "ignore"):
        fail("defaults.out_of_expect")
    seen = set()
    for i, ov in enumerate(overrides):
        path = f"sheet_overrides[{i}]"
        if not isinstance(ov, dict) or not (ov.get("sheet_id") or ov.get("metric_id")):
            fail(path)
        ident = ov.get("sheet_id") or ov.get("metric_id")
        if ident in seen:
            fail(path + ".duplicate")
        seen.add(ident)
        for key in ("status_fields", "up_values", "down_values"):
            if key in ov and (not isinstance(ov[key], list) or any(not str(v).strip() for v in ov[key])):
                fail(path + "." + key)
        for key in ("success", "anomaly"):
            patterns = ov.get(key, [])
            if not isinstance(patterns, list):
                fail(path + "." + key)
            for j, pat in enumerate(patterns):
                pp = f"{path}.{key}[{j}]"
                if not isinstance(pat, dict):
                    fail(pp)
                for side in ("old", "new"):
                    for attr in (side, side + "_conds", side + "_groups"):
                        if attr in pat and not isinstance(pat[attr], list):
                            fail(pp + "." + attr)
                    if any(isinstance(g, list) and not g for g in pat.get(side + "_groups", [])):
                        fail(pp + "." + side + "_groups.empty_group")
                    groups = _groups_for_side(pat, side)
                    if key == "success" and not groups:
                        fail(pp + "." + side + ".required")
                    for group in groups:
                        for cond in group:
                            cp = pp + "." + side
                            if isinstance(cond, str):
                                if cond not in ("added", "removed", "changed", "unchanged", "up", "down", "other", "none") and not (cond.startswith("field:") and cond.count(":") >= 2):
                                    fail(cp + ".token")
                            elif isinstance(cond, dict):
                                typ = _normalize_cond_type(cond)
                                if typ not in ("presence", "status", "value"):
                                    fail(cp + ".type")
                                val = cond.get("value", cond.get("kind", cond.get("status")))
                                if typ == "presence" and val not in ("added", "removed", "changed", "unchanged"):
                                    fail(cp + ".presence")
                                if typ == "status" and val not in ("up", "down", "other", "none"):
                                    fail(cp + ".status")
                                if typ == "value":
                                    op = cond.get("op", "eq")
                                    if op not in ("eq", "ne", "in", "not_in", "empty", "not_empty", "changed", "unchanged", "same", "diff"):
                                        fail(cp + ".op")
                                    if op in ("eq", "ne", "in", "not_in") and (val is None or val == "" or val == []):
                                        fail(cp + ".value")
                            else:
                                fail(cp + ".condition")
                if key == "anomaly" and not any(_groups_for_side(pat, s) for s in ("old", "new")):
                    fail(pp + ".empty")
