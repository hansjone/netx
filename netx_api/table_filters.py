"""Shared exact-value column filters, applied before pagination."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException, Query
from sqlalchemy import String, cast, func


@dataclass
class TableFilterParams:
    filters: dict[str, dict[str, Any]] = field(default_factory=dict)
    field: str = ""


def table_filter_params(
    column_filters: str = Query(default=""),
    filter_field: str = Query(default=""),
) -> TableFilterParams:
    try:
        filters = json.loads(column_filters) if column_filters else {}
        if not isinstance(filters, dict):
            raise ValueError()
        for value in filters.values():
            if not isinstance(value, dict) or value.get("mode") not in ("include", "exclude"):
                raise ValueError()
            values = value.get("values")
            if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="invalid_column_filters") from None
    return TableFilterParams(filters, filter_field)


def filter_query(query: Any, expressions: dict[str, Any], params: TableFilterParams | None):
    """Return the filtered query and, when requested, a distinct-value response."""
    params = params or TableFilterParams()
    if (set(params.filters) | ({params.field} if params.field else set())) - expressions.keys():
        raise HTTPException(status_code=400, detail="invalid_filter_field")
    for key, value in params.filters.items():
        if key == params.field:
            continue
        expression = func.coalesce(cast(expressions[key], String), "")
        values = value["values"]
        if value["mode"] == "include":
            query = query.filter(expression.in_(values)) if values else query.filter(False)
        elif values:
            query = query.filter(~expression.in_(values))
    if params.field:
        expression = func.coalesce(cast(expressions[params.field], String), "")
        rows = query.order_by(None).with_entities(expression).distinct().all()
        return query, {"field": params.field, "options": sorted({str(row[0]) for row in rows})}
    return query, None
