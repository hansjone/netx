"""Shared topology constants and low-level helpers."""
from __future__ import annotations

import random
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from .models import TopoFabricEdge, TopoViewEdgeStyle
from .timeutil import utcnow_naive

ROOT_FOLDER_NAME = "Network"
PHYSICAL_VIEW_NAME = "Physical topology"
# Legacy system region name (no longer auto-created; stripped on bootstrap when empty).
_LEGACY_UNASSIGNED_NAME = "Unassigned"

PAGE_DEFAULT = 100
PAGE_MAX = 2000
VIEW_GRAPH_NODE_HARD_CAP = 2000
VIEW_GRAPH_EDGE_HARD_CAP = 5000
_RAW_PREVIEW_MAX = 12_000
_JOB_LOCK = threading.Lock()
_RUNNING_JOBS: set[str] = set()

# Fabric link lifecycle: absent once → missing; still absent for N cycles → purge.
_EDGE_STATUS_MISSING = "missing"
_EDGE_STATUS_MISSING_COMPAT = frozenset({"missing", "stale"})
_MISS_PURGE_AFTER_CYCLES = 4


def _normalize_edge_status(status: str) -> str:
    s = str(status or "").strip().lower() or "active"
    if s in _EDGE_STATUS_MISSING_COMPAT:
        return _EDGE_STATUS_MISSING
    return s


def _edge_attrs(e: TopoFabricEdge) -> dict[str, Any]:
    return dict(e.attrs or {})


def _clear_miss_attrs(attrs: dict[str, Any]) -> dict[str, Any]:
    out = dict(attrs or {})
    out.pop("miss_count", None)
    out.pop("first_missing_at", None)
    out.pop("replaced_by_edge_id", None)
    return out


def _set_edge_missing(
    e: TopoFabricEdge,
    now: datetime,
    *,
    replaced_by_edge_id: str = "",
) -> bool:
    """Mark edge missing and bump miss_count. Returns True if newly became missing."""
    prev = _normalize_edge_status(e.status or "")
    attrs = _edge_attrs(e)
    miss_count = int(attrs.get("miss_count") or 0) + 1
    attrs["miss_count"] = miss_count
    if not attrs.get("first_missing_at"):
        attrs["first_missing_at"] = now.isoformat(timespec="seconds")
    if replaced_by_edge_id:
        attrs["replaced_by_edge_id"] = str(replaced_by_edge_id)
    e.attrs = attrs
    e.status = _EDGE_STATUS_MISSING
    # Keep observational source; never leave source stuck on legacy "stale".
    if str(e.source or "").strip().lower() in {"", "stale"}:
        e.source = "lldp"
    e.updated_at = now
    return prev != _EDGE_STATUS_MISSING


def _purge_edge_if_due(db: Session, e: TopoFabricEdge) -> bool:
    """Physically delete missing edge after enough consecutive miss cycles."""
    attrs = _edge_attrs(e)
    if int(attrs.get("miss_count") or 0) < _MISS_PURGE_AFTER_CYCLES:
        return False
    if _normalize_edge_status(e.status or "") != _EDGE_STATUS_MISSING:
        return False
    if str(e.source or "").strip().lower() == "manual":
        return False
    db.query(TopoViewEdgeStyle).filter(TopoViewEdgeStyle.fabric_edge_id == e.id).delete(
        synchronize_session=False
    )
    db.delete(e)
    return True


def _utcnow() -> datetime:
    return utcnow_naive()


def _norm_host(s: str) -> str:
    t = str(s or "").strip().lower().split(".")[0]
    return t.rstrip(".,;:")


def _empty_to_none(s: str | None) -> str | None:
    v = str(s or "").strip()
    return v or None


# Postgres advisory-lock namespaces for fabric ensure (avoid cross-feature collisions).
_ADV_NS_FABRIC_MANAGED = 710001
_ADV_NS_FABRIC_UME = 710002
_DISCOVER_DEADLOCK_RETRIES = 4


def _is_postgres(db: Session) -> bool:
    bind = db.get_bind()
    return bind is not None and str(bind.dialect.name).lower() == "postgresql"


def _advisory_xact_lock(db: Session, namespace: int, key: str) -> None:
    """Serialize concurrent creates for the same unique key (Postgres only)."""
    k = str(key or "").strip()
    if not k or not _is_postgres(db):
        return
    db.execute(
        text("SELECT pg_advisory_xact_lock(:ns, hashtext(:key))"),
        {"ns": int(namespace), "key": k},
    )


def _is_deadlock_error(exc: BaseException) -> bool:
    """True for Postgres 40P01 / SQLite 'database is locked' style races."""
    cur: BaseException | None = exc
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        pgcode = getattr(cur, "pgcode", None) or getattr(cur, "sqlstate", None)
        if str(pgcode or "") == "40P01":
            return True
        msg = str(cur).lower()
        if "deadlock" in msg or "40p01" in msg:
            return True
        orig = getattr(cur, "orig", None)
        if isinstance(orig, BaseException) and id(orig) not in seen:
            cur = orig
            continue
        cur = cur.__cause__ or cur.__context__  # type: ignore[assignment]
    return False


def _sleep_deadlock_backoff(attempt: int) -> None:
    # attempt is 0-based; jitter avoids thundering herd across workers.
    base = 0.05 * (2**attempt)
    time.sleep(base + random.uniform(0.0, 0.05))


