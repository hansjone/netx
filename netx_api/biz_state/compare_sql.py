"""PostgreSQL-backed sheet compare (FULL OUTER JOIN) for pushdown-safe sheets.

Falls back to the Python engine when port-map / complex rules cannot be expressed
in SQL. See ``can_sql_compare``.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from .compare_rules import effective_compare_fields, field_rule_map

_log = logging.getLogger("netx.biz_state.compare_sql")

_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SQL_FILTER_OPS = frozenset(
    {"eq", "==", "ne", "!=", "in", "not_in", "nin", "contains", "empty", "not_empty", "nonempty", "ci_eq"}
)
_SQL_NORMALIZE = frozenset({"", "none", "strip", "lower", "upper", "empty_as_blank"})
_SQL_COMPARE_MODES = frozenset({"", "eq", "ignore", "skip", "off"})
_EMPTY_AS_BLANK = ("n/a", "na", "-", "--", "none", "null")


def _dialect_is_postgres(db: Session) -> bool:
    bind = db.get_bind()
    if bind is None:
        return False
    return str(getattr(bind.dialect, "name", "") or "").lower() in ("postgresql", "postgres")


def _safe_field(name: str) -> str:
    f = str(name or "").strip()
    if not f or not _FIELD_RE.match(f):
        raise ValueError(f"unsafe_json_field:{name!r}")
    return f


def _json_text_expr(alias: str, field: str) -> str:
    """SQL expression: trimmed text from JSONB column ``alias.data`` / ``data_json``."""
    f = _safe_field(field)
    # alias is caller-controlled (_b / data_json) — not user input
    return f"trim(both from coalesce({alias}->>'{f}', ''))"


def _norm_expr(alias: str, field: str, normalize: str) -> str:
    base = _json_text_expr(alias, field)
    mode = str(normalize or "strip").strip().lower() or "strip"
    if mode in ("", "none", "strip"):
        return base
    if mode == "lower":
        return f"lower({base})"
    if mode == "upper":
        return f"upper({base})"
    if mode == "empty_as_blank":
        opts = ", ".join(f"'{x}'" for x in _EMPTY_AS_BLANK)
        return (
            f"CASE WHEN lower({base}) IN ({opts}) THEN '' ELSE {base} END"
        )
    raise ValueError(f"unsupported_normalize:{mode}")


def _filters_sql_compatible(filters: Sequence[Mapping[str, Any]] | None) -> bool:
    fl = [f for f in (filters or []) if isinstance(f, dict)]
    return all(_filter_node_compatible(f) for f in fl)


def _filter_node_compatible(filt: Mapping[str, Any]) -> bool:
    if "any" in filt:
        kids = filt.get("any") or []
        return isinstance(kids, list) and all(
            isinstance(k, dict) and _filter_node_compatible(k) for k in kids
        )
    if "all" in filt:
        kids = filt.get("all") or []
        return isinstance(kids, list) and all(
            isinstance(k, dict) and _filter_node_compatible(k) for k in kids
        )
    field = str(filt.get("field") or "").strip()
    op = str(filt.get("op") or "eq").strip().lower()
    if op not in _SQL_FILTER_OPS:
        return False
    if op in ("empty", "not_empty", "nonempty"):
        return bool(field) and bool(_FIELD_RE.match(field))
    if not field or not _FIELD_RE.match(field):
        return False
    if op in ("in", "not_in", "nin"):
        vals = filt.get("value")
        if vals is None:
            return True
        if not isinstance(vals, (list, tuple)):
            vals = [vals]
        return all(isinstance(x, (str, int, float, bool)) or x is None for x in vals)
    return True


def _field_rules_sql_compatible(rules: Sequence[Mapping[str, Any]] | None) -> bool:
    for raw in rules or []:
        if not isinstance(raw, dict):
            return False
        name = str(raw.get("field") or "").strip()
        if name and not _FIELD_RE.match(name):
            return False
        mode = str(raw.get("compare") or "eq").strip().lower() or "eq"
        if mode not in _SQL_COMPARE_MODES:
            return False
        if raw.get("ignore") is True:
            continue
        if mode in ("ignore", "skip", "off"):
            continue
        norm = str(raw.get("normalize") or "strip").strip().lower() or "strip"
        if norm not in _SQL_NORMALIZE:
            return False
    return True


def can_sql_compare(
    db: Session,
    sheet: Mapping[str, Any],
    *,
    port_map: Mapping[str, str] | None = None,
    iface_normalize_rules: Sequence[Mapping[str, str]] | None = None,
) -> bool:
    """True when this sheet can run entirely as a PostgreSQL JOIN."""
    if not _dialect_is_postgres(db):
        return False
    if port_map:
        return False
    key_fields = [str(k).strip() for k in (sheet.get("key_fields") or []) if str(k).strip()]
    if not key_fields or any(not _FIELD_RE.match(k) for k in key_fields):
        return False
    iface_fields = [str(f).strip() for f in (sheet.get("iface_fields") or []) if str(f).strip()]
    iface_set = set(iface_fields)
    # Port-rename heuristic / map rewrite cannot be expressed here
    ignore_ports = sheet.get("ignore_port_changes")
    if ignore_ports is True:
        return False
    if ignore_ports is None and iface_set and any(k in iface_set for k in key_fields):
        # Auto path may drop iface from match key — stay on Python
        return False
    field_rules = list(sheet.get("field_rules") or [])
    if not _field_rules_sql_compatible(field_rules):
        return False
    compare_fields = effective_compare_fields(
        list(sheet.get("compare_fields") or []),
        field_rules,
    )
    if any(not _FIELD_RE.match(str(f).strip()) for f in compare_fields if str(f).strip()):
        return False
    used = set(key_fields) | {str(f).strip() for f in compare_fields if str(f).strip()}
    if iface_normalize_rules and (used & iface_set):
        return False
    if not _filters_sql_compatible(list(sheet.get("row_filters") or [])):
        return False
    if not str(sheet.get("metric_id") or "").strip():
        return False
    return True


def compile_row_filters_sql(
    filters: Sequence[Mapping[str, Any]] | None,
    *,
    json_col: str = "data_json",
    param_prefix: str = "f",
) -> tuple[str, dict[str, Any]]:
    """Compile template row_filters to SQL AND-clause + bind params.

    Returns ``(sql_fragment, params)``. Empty filters → ``(\"TRUE\", {})``.
    ``json_col`` is the JSONB column/expression name (trusted).
    """
    fl = [f for f in (filters or []) if isinstance(f, dict)]
    if not fl:
        return "TRUE", {}
    params: dict[str, Any] = {}
    counter = {"n": 0}

    def _next(name: str) -> str:
        counter["n"] += 1
        return f"{param_prefix}_{counter['n']}_{name}"

    def _node(filt: Mapping[str, Any]) -> str:
        if "any" in filt:
            kids = [k for k in (filt.get("any") or []) if isinstance(k, dict)]
            if not kids:
                return "TRUE"
            return "(" + " OR ".join(_node(k) for k in kids) + ")"
        if "all" in filt:
            kids = [k for k in (filt.get("all") or []) if isinstance(k, dict)]
            if not kids:
                return "TRUE"
            return "(" + " AND ".join(_node(k) for k in kids) + ")"
        field = _safe_field(str(filt.get("field") or ""))
        op = str(filt.get("op") or "eq").strip().lower()
        expr = _json_text_expr(json_col, field)
        # _json_text_expr uses alias->> ; for bare column use data_json directly
        if json_col == "data_json":
            expr = f"trim(both from coalesce(data_json->>'{field}', ''))"
        if op in ("empty",):
            return f"({expr} = '')"
        if op in ("not_empty", "nonempty"):
            return f"({expr} <> '')"
        expect = filt.get("value")
        if op in ("eq", "==", "ci_eq"):
            key = _next("v")
            params[key] = str(expect or "").strip()
            if op == "ci_eq" or op in ("eq", "=="):
                # Python eq is case-insensitive via .lower()
                params[key] = str(expect or "").strip().lower()
                return f"(lower({expr}) = :{key})"
        if op in ("ne", "!="):
            key = _next("v")
            params[key] = str(expect or "").strip().lower()
            return f"(lower({expr}) <> :{key})"
        if op == "contains":
            key = _next("v")
            needle = str(expect or "").strip().lower()
            params[key] = f"%{needle}%"
            return f"(lower({expr}) LIKE :{key})"
        if op in ("in", "not_in", "nin"):
            vals = expect
            if vals is None:
                vals = []
            if not isinstance(vals, (list, tuple)):
                vals = [vals]
            clean = [str(x).strip().lower() for x in vals if str(x).strip()]
            if not clean:
                return "FALSE" if op == "in" else "TRUE"
            keys = []
            for i, v in enumerate(clean):
                k = _next(f"in{i}")
                params[k] = v
                keys.append(f":{k}")
            inside = f"lower({expr}) IN ({', '.join(keys)})"
            return f"({inside})" if op == "in" else f"(NOT {inside})"
        raise ValueError(f"unsupported_filter_op:{op}")

    parts = [_node(f) for f in fl]
    return "(" + " AND ".join(parts) + ")", params


def _rk_sql(key_fields: list[str], *, json_col: str = "data_json") -> str:
    parts = []
    for k in key_fields:
        f = _safe_field(k)
        if json_col == "data_json":
            parts.append(f"trim(both from coalesce(data_json->>'{f}', ''))")
        else:
            parts.append(f"trim(both from coalesce({json_col}->>'{f}', ''))")
    if len(parts) == 1:
        return parts[0]
    return "concat_ws('|', " + ", ".join(parts) + ")"


def _changed_predicate(
    compare_fields: list[str],
    rules: dict[str, dict[str, Any]],
    *,
    before_alias: str = "b",
    after_alias: str = "a",
) -> str:
    """SQL boolean: True when any compare field differs (IS DISTINCT FROM)."""
    if not compare_fields:
        return "FALSE"
    clauses: list[str] = []
    for f in compare_fields:
        name = str(f).strip()
        if not name:
            continue
        rule = rules.get(name) or {}
        mode = str(rule.get("compare") or "eq").strip().lower() or "eq"
        if mode in ("ignore", "skip", "off") or rule.get("ignore") is True:
            continue
        norm = str(rule.get("normalize") or "strip").strip().lower() or "strip"
        bv = _norm_expr(f"{before_alias}.data", name, norm)
        av = _norm_expr(f"{after_alias}.data", name, norm)
        clauses.append(f"({bv} IS DISTINCT FROM {av})")
    if not clauses:
        return "FALSE"
    return "(" + " OR ".join(clauses) + ")"


def _key_obj_from_data(data: dict[str, Any] | None, key_fields: list[str]) -> dict[str, Any]:
    row = data if isinstance(data, dict) else {}
    return {f: row.get(f, "") for f in key_fields}


def _changes_from_rows(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    compare_fields: list[str],
    rules: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    from .compare_rules import explain_diff, values_equal

    out: dict[str, dict[str, Any]] = {}
    b = before or {}
    a = after or {}
    for f in compare_fields:
        rule = rules.get(f)
        bv = b.get(f, "")
        av = a.get(f, "")
        if values_equal(bv, av, rule=rule):
            continue
        entry: dict[str, Any] = {"before": bv, "after": av}
        reason = explain_diff(bv, av, rule=rule)
        if reason:
            entry["reason"] = reason
        out[f] = entry
    return out


def run_sql_sheet_compare(
    db: Session,
    *,
    sheet: Mapping[str, Any],
    before_batch_id: str,
    after_batch_id: str,
    store_unchanged: str = "auto",
    on_progress: Callable[[str, int], None] | None = None,
) -> dict[str, Any]:
    """Compare one sheet via PostgreSQL TEMP tables + FULL OUTER JOIN.

    Returns the same ``{summary, diffs, mapping_stats}`` shape as ``compare_rows``.
    """
    if not _dialect_is_postgres(db):
        raise RuntimeError("sql_compare_requires_postgres")

    key_fields = [str(k).strip() for k in (sheet.get("key_fields") or []) if str(k).strip()]
    field_rules = list(sheet.get("field_rules") or [])
    rules = field_rule_map(field_rules)
    compare_fields = effective_compare_fields(
        list(sheet.get("compare_fields") or []),
        field_rules,
    )
    row_filters = list(sheet.get("row_filters") or [])
    mid = str(sheet.get("metric_id") or "").strip()
    bid_b = str(before_batch_id or "").strip()
    bid_a = str(after_batch_id or "").strip()
    if not mid or not bid_b or not bid_a or not key_fields:
        raise ValueError("sql_compare_missing_args")

    filter_sql, filter_params = compile_row_filters_sql(row_filters)
    rk = _rk_sql(key_fields)
    tag = uuid4().hex[:8]
    tb = f"_netx_cmp_b_{tag}"
    ta = f"_netx_cmp_a_{tag}"

    # Raw counts (no row_filters)
    raw_b = int(
        db.execute(
            text(
                "SELECT count(*) FROM biz_state_metric_row "
                "WHERE batch_id = :bid AND metric_id = :mid"
            ),
            {"bid": bid_b, "mid": mid},
        ).scalar()
        or 0
    )
    if on_progress:
        on_progress("before", raw_b)
    raw_a = int(
        db.execute(
            text(
                "SELECT count(*) FROM biz_state_metric_row "
                "WHERE batch_id = :bid AND metric_id = :mid"
            ),
            {"bid": bid_a, "mid": mid},
        ).scalar()
        or 0
    )
    if on_progress:
        on_progress("after", raw_a)

    base_params = {"bid": bid_b, "mid": mid, **filter_params}
    # Build TEMP sides
    for tname, batch_id in ((tb, bid_b), (ta, bid_a)):
        db.execute(text(f"DROP TABLE IF EXISTS {tname}"))
        params = {**base_params, "bid": batch_id}
        # PRESERVE ROWS: compare progress commits must not drop temps mid-run
        db.execute(
            text(
                f"""
                CREATE TEMP TABLE {tname} ON COMMIT PRESERVE ROWS AS
                SELECT
                  id,
                  ({rk}) AS rk,
                  data_json AS data,
                  row_number() OVER (
                    PARTITION BY ({rk})
                    ORDER BY seq ASC, id ASC
                  ) AS dup_rn
                FROM biz_state_metric_row
                WHERE batch_id = :bid
                  AND metric_id = :mid
                  AND ({filter_sql})
                """
            ),
            params,
        )
        db.execute(text(f"CREATE INDEX ON {tname} (rk) WHERE dup_rn = 1"))

    before_n = int(
        db.execute(text(f"SELECT count(*) FROM {tb}")).scalar() or 0
    )
    after_n = int(
        db.execute(text(f"SELECT count(*) FROM {ta}")).scalar() or 0
    )
    if on_progress:
        on_progress("after", after_n)

    changed_pred = _changed_predicate(compare_fields, rules)
    kind_expr = f"""
      CASE
        WHEN b.id IS NULL THEN 'added'
        WHEN a.id IS NULL THEN 'removed'
        WHEN {changed_pred} THEN 'changed'
        ELSE 'unchanged'
      END
    """

    # Aggregate primary match kinds (first-wins keys only)
    agg_rows = db.execute(
        text(
            f"""
            SELECT {kind_expr} AS kind, count(*)::bigint AS n
            FROM (SELECT * FROM {tb} WHERE dup_rn = 1) b
            FULL OUTER JOIN (SELECT * FROM {ta} WHERE dup_rn = 1) a
              ON b.rk = a.rk
            GROUP BY 1
            """
        )
    ).mappings().all()
    counts = {str(r["kind"]): int(r["n"] or 0) for r in agg_rows}
    added = counts.get("added", 0)
    removed = counts.get("removed", 0)
    changed = counts.get("changed", 0)
    unchanged = counts.get("unchanged", 0)

    dup_b = int(
        db.execute(text(f"SELECT count(*) FROM {tb} WHERE dup_rn > 1")).scalar() or 0
    )
    dup_a = int(
        db.execute(text(f"SELECT count(*) FROM {ta} WHERE dup_rn > 1")).scalar() or 0
    )
    duplicate = dup_b + dup_a

    # Lazy import — avoid circular import with compare_service
    from .compare_service import resolve_unchanged_policy

    policy = resolve_unchanged_policy(
        store_unchanged, before_n=before_n, after_n=after_n
    )
    diffs: list[dict[str, Any]] = []

    # Fail + duplicate rows (stream into Python — should be << million)
    fail_sql = text(
        f"""
        SELECT
          {kind_expr} AS kind,
          b.id AS before_row_id,
          a.id AS after_row_id,
          b.data AS before_data,
          a.data AS after_data,
          COALESCE(b.rk, a.rk) AS rk
        FROM (SELECT * FROM {tb} WHERE dup_rn = 1) b
        FULL OUTER JOIN (SELECT * FROM {ta} WHERE dup_rn = 1) a
          ON b.rk = a.rk
        WHERE {kind_expr} IN ('added', 'removed', 'changed')
        """
    )
    for row in db.execute(fail_sql).mappings():
        kind = str(row["kind"] or "")
        before = dict(row["before_data"] or {}) if row["before_data"] is not None else None
        after = dict(row["after_data"] or {}) if row["after_data"] is not None else None
        key_src = after if kind == "added" else (before or after or {})
        item: dict[str, Any] = {
            "kind": kind,
            "key": _key_obj_from_data(key_src, key_fields),
            "before": before,
            "after": after,
            "mapped_before": before,
            "changes": {},
            "before_row_id": str(row["before_row_id"] or ""),
            "after_row_id": str(row["after_row_id"] or ""),
        }
        if kind == "changed":
            item["changes"] = _changes_from_rows(before, after, compare_fields, rules)
        diffs.append(item)

    # Duplicate extras
    for side, tname in (("before", tb), ("after", ta)):
        q = text(
            f"""
            SELECT id, data, rk FROM {tname} WHERE dup_rn > 1
            """
        )
        for row in db.execute(q).mappings():
            data = dict(row["data"] or {})
            diffs.append(
                {
                    "kind": "duplicate",
                    "side": side,
                    "key": _key_obj_from_data(data, key_fields),
                    "before": data if side == "before" else None,
                    "after": data if side == "after" else None,
                    "mapped_before": data if side == "before" else None,
                    "changes": {},
                    "before_row_id": str(row["id"] or "") if side == "before" else "",
                    "after_row_id": str(row["id"] or "") if side == "after" else "",
                }
            )

    unchanged_listed = 0
    include_u = bool(policy.get("include"))
    compact = bool(policy.get("compact"))
    limit_n = policy.get("limit")
    if include_u and unchanged > 0:
        lim_sql = ""
        params_u: dict[str, Any] = {}
        if limit_n is not None:
            lim_sql = " LIMIT :lim"
            params_u["lim"] = max(0, int(limit_n))
        u_sql = text(
            f"""
            SELECT
              b.id AS before_row_id,
              a.id AS after_row_id,
              b.data AS before_data,
              a.data AS after_data
            FROM (SELECT * FROM {tb} WHERE dup_rn = 1) b
            INNER JOIN (SELECT * FROM {ta} WHERE dup_rn = 1) a
              ON b.rk = a.rk
            WHERE NOT ({changed_pred})
            {lim_sql}
            """
        )
        for row in db.execute(u_sql, params_u).mappings():
            before = dict(row["before_data"] or {})
            after = dict(row["after_data"] or {})
            unchanged_listed += 1
            if compact:
                diffs.append(
                    {
                        "kind": "unchanged",
                        "key": _key_obj_from_data(before, key_fields),
                        "before": {},
                        "after": {},
                        "mapped_before": {},
                        "changes": {},
                        "compact": True,
                        "before_row_id": str(row["before_row_id"] or ""),
                        "after_row_id": str(row["after_row_id"] or ""),
                    }
                )
            else:
                diffs.append(
                    {
                        "kind": "unchanged",
                        "key": _key_obj_from_data(before, key_fields),
                        "before": before,
                        "after": after,
                        "mapped_before": before,
                        "changes": {},
                        "before_row_id": str(row["before_row_id"] or ""),
                        "after_row_id": str(row["after_row_id"] or ""),
                    }
                )

    # Cleanup (also ON COMMIT DROP)
    db.execute(text(f"DROP TABLE IF EXISTS {tb}"))
    db.execute(text(f"DROP TABLE IF EXISTS {ta}"))

    dup_key_list: list[str] = []
    summary = {
        "before_count": before_n,
        "after_count": after_n,
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged": unchanged,
        "duplicate": duplicate,
        "match_key_fields": list(key_fields),
        "duplicate_keys_before": dup_b,
        "duplicate_keys_after": dup_a,
        "duplicate_key_list": dup_key_list,
        "unchanged_listed": unchanged_listed,
        "unchanged_truncated": bool(
            include_u and limit_n is not None and unchanged > unchanged_listed
        ),
        "unchanged_compact": bool(compact and unchanged_listed > 0),
        "engine": "sql",
        "before_raw_count": raw_b,
        "after_raw_count": raw_a,
        "row_filters": len(row_filters),
        "unchanged_policy": policy,
    }
    mapping_stats = {
        "before_iface_count": 0,
        "after_iface_count": 0,
        "map_pairs": 0,
        "hit_before": [],
        "miss_before": [],
        "hit_after": [],
        "miss_after": [],
        "unused_before_keys": [],
        "ok": True,
        "ignore_port_changes": False,
        "engine": "sql",
    }
    return {"summary": summary, "diffs": diffs, "mapping_stats": mapping_stats}
