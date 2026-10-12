"""Validate template writes without silently dropping parts of the comparison."""
from __future__ import annotations

import math
import re
from typing import Any

from fastapi import HTTPException

FILTER_OPS = {"eq", "==", "ci_eq", "ne", "!=", "in", "not_in", "nin", "contains", "empty", "not_empty", "nonempty", "regex", "age_timer"}
COMPARE_MODES = {"", "eq", "ignore", "skip", "off", "numeric", "number", "int", "float", "percent", "pct", "rel",
                 "not_worse", "numeric_min", "numeric_max"}
SIDES = {"", "both", "before", "after"}
MAX_VALUE_MAP = 500
NORMALIZE_MODES = {"", "none", "strip", "lower", "upper", "mac", "empty_as_blank"}


def invalid(path: str, reason: str) -> None:
    raise HTTPException(status_code=400, detail={"error": "invalid_template", "path": path, "reason": reason})


def _strings(value: Any, path: str, *, required: bool = False) -> None:
    if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
        invalid(path, "nonempty_strings_required")
    if required and not value:
        invalid(path, "key_fields_required")
    if len({x.strip() for x in value}) != len(value):
        invalid(path, "duplicate_field")


def validate_filters(value: Any, path: str, depth: int = 0) -> None:
    if not isinstance(value, list):
        invalid(path, "list_required")
    if depth > 12:
        invalid(path, "filter_nesting_too_deep")
    for i, node in enumerate(value):
        here = f"{path}[{i}]"
        if not isinstance(node, dict) or not node:
            invalid(here, "filter_required")
        if depth == 0 and str(node.get("side") or "").strip().lower() not in SIDES:
            invalid(here, "unknown_filter_side")
        groups = [key for key in ("any", "all") if key in node]
        if groups:
            if len(groups) != 1 or any(node.get(key) is not None for key in ("field", "op", "value")):
                invalid(here, "group_or_leaf_required")
            kids = node[groups[0]]
            if not isinstance(kids, list) or not kids:
                invalid(here, "nonempty_group_required")
            validate_filters(kids, f"{here}.{groups[0]}", depth + 1)
            continue
        if not isinstance(node.get("field"), str) or not node["field"].strip():
            invalid(here, "filter_field_required")
        op = str(node.get("op") or "eq").strip().lower()
        if op not in FILTER_OPS:
            invalid(here, "unknown_filter_operator")
        expect = node.get("value")
        if op in ("in", "not_in", "nin"):
            if not isinstance(expect, list) or not expect or any(not isinstance(x, (str, int, float, bool)) for x in expect):
                invalid(here, "nonempty_value_list_required")
        elif op not in ("empty", "not_empty", "nonempty", "age_timer"):
            if not isinstance(expect, (str, int, float, bool)):
                invalid(here, "scalar_value_required")
            if op in ("contains", "regex") and not str(expect).strip():
                invalid(here, "value_required")
        if op == "regex":
            try:
                re.compile(str(expect), re.I)
            except re.error:
                invalid(here, "invalid_regex")


def validate_sheet(sheet: Any, path: str, *, partial: bool = False) -> None:
    if not isinstance(sheet, dict):
        invalid(path, "sheet_required")
    if not partial or "metric_id" in sheet:
        if not isinstance(sheet.get("metric_id"), str) or not sheet["metric_id"].strip():
            invalid(path, "metric_id_required")
    for key in ("key_fields", "iface_fields", "compare_fields", "display_fields", "ignore_fields"):
        if key in sheet or (key == "key_fields" and not partial):
            _strings(sheet.get(key), f"{path}.{key}", required=key == "key_fields")
    for opt, allowed in (("unmapped_port_policy", {"by_name", "ignore_port"}),
                         ("duplicate_pairing", {"sorted", "ordered"})):
        if str(sheet.get(opt) or "").strip() and str(sheet[opt]).strip().lower() not in allowed:
            invalid(path, f"unknown_{opt}")
    if sheet.get("builtin_vendor_canonical") is not None and not isinstance(sheet["builtin_vendor_canonical"], bool):
        invalid(path, "boolean_builtin_vendor_canonical_required")
    if "ignore_port_changes" in sheet and sheet["ignore_port_changes"] is not None and not isinstance(sheet["ignore_port_changes"], bool):
        invalid(path, "boolean_port_policy_required")
    if "row_filters" in sheet:
        validate_filters(sheet["row_filters"], f"{path}.row_filters")
    if "field_rules" in sheet:
        rules = sheet["field_rules"]
        if not isinstance(rules, list):
            invalid(path, "field_rules_list_required")
        seen = set()
        for i, rule in enumerate(rules):
            here = f"{path}.field_rules[{i}]"
            if not isinstance(rule, dict) or not isinstance(rule.get("field"), str) or not rule["field"].strip():
                invalid(here, "rule_field_required")
            field = rule["field"].strip()
            rule_side = str(rule.get("side") or "").strip().lower()
            if rule_side not in SIDES:
                invalid(here, "unknown_rule_side")
            marker = (field, "" if rule_side == "both" else rule_side)
            if marker in seen:
                invalid(here, "duplicate_field_rule")
            seen.add(marker)
            vmap = rule.get("value_map")
            if vmap is not None:
                if not isinstance(vmap, dict) or any(
                    not isinstance(k, str) or not isinstance(v, str) for k, v in vmap.items()
                ):
                    invalid(here, "value_map_string_pairs_required")
                if len(vmap) > MAX_VALUE_MAP:
                    invalid(here, "value_map_too_large")
            if rule.get("apply_to_key") is not None and not isinstance(rule["apply_to_key"], bool):
                invalid(here, "boolean_apply_to_key_required")
            mode = str(rule.get("compare") or "").strip().lower()
            if mode == "not_worse":
                rank = rule.get("rank")
                if not isinstance(rank, list) or len(rank) < 2 or any(not isinstance(x, str) or not x.strip() for x in rank):
                    invalid(here, "rank_list_required")
            elif rule.get("rank") is not None:
                invalid(here, "rank_only_for_not_worse")
            if str(rule.get("compare") or "").strip().lower() not in COMPARE_MODES:
                invalid(here, "unknown_compare_mode")
            if str(rule.get("normalize") or "").strip().lower() not in NORMALIZE_MODES:
                invalid(here, "unknown_normalize_mode")
            tol = rule.get("tolerance")
            if tol is not None:
                if isinstance(tol, bool) or not isinstance(tol, (int, float)) or not math.isfinite(tol) or tol < 0:
                    invalid(here, "finite_nonnegative_tolerance_required")


def validate_template_body(body: dict[str, Any], *, partial: bool = False) -> None:
    metrics = body.get("metrics")
    if metrics is not None:
        if not isinstance(metrics, list) or not metrics:
            invalid("metrics", "metrics_required")
        seen = set()
        for i, sheet in enumerate(metrics):
            path = f"metrics[{i}]"
            validate_sheet(sheet, path)
            sid = str(sheet.get("sheet_id") or sheet["metric_id"]).strip()
            if sid in seen:
                invalid(path, "duplicate_sheet_id")
            seen.add(sid)
    else:
        validate_sheet(body, "template", partial=partial)
    if "iface_normalize_rules" in body and body["iface_normalize_rules"] is not None:
        rules = body["iface_normalize_rules"]
        if not isinstance(rules, list):
            invalid("iface_normalize_rules", "list_required")
        seen = set()
        for i, rule in enumerate(rules):
            path = f"iface_normalize_rules[{i}]"
            if not isinstance(rule, dict) or any(not isinstance(rule.get(k), str) or not rule[k].strip() for k in ("from", "to")):
                invalid(path, "alias_pair_required")
            rside = str(rule.get("side") or "").strip().lower()
            if rside not in SIDES:
                invalid(path, "unknown_alias_side")
            if rule.get("vendor") is not None and not isinstance(rule["vendor"], str):
                invalid(path, "alias_vendor_string_required")
            key = (rule["from"].strip().lower(), "" if rside == "both" else rside,
                   str(rule.get("vendor") or "").strip().lower())
            if key in seen:
                invalid(path, "duplicate_alias")
            seen.add(key)
