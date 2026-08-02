"""UME sync shared helpers (string/pick/host-name)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from .models import UmeAlarmCurrent, UmeAlarmHistory, UmeInventoryNE
from .timeutil import utcnow_naive


def _utc_now_naive() -> datetime:
    return utcnow_naive()


def _s(v: Any) -> str:
    if v is None:
        return ""
    text = str(v).strip()
    if text.lower() == "nan":
        return ""
    return text


def _pick(d: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in d:
            return d.get(key)
    return None


def _lookup_host_name(db: Session, ne_id: str) -> str:
    nid = _s(ne_id)
    if not nid:
        return ""
    row = db.get(UmeInventoryNE, nid)
    if row is None:
        return ""
    return _s(row.host_name)


def _propagate_host_name_to_alarms(db: Session, ne_id: str, host_name: str) -> None:
    nid = _s(ne_id)
    if not nid:
        return
    hn = _s(host_name)
    db.query(UmeAlarmCurrent).filter(UmeAlarmCurrent.ne_id == nid).update(
        {UmeAlarmCurrent.host_name: hn},
        synchronize_session=False,
    )
    db.query(UmeAlarmHistory).filter(UmeAlarmHistory.ne_id == nid).update(
        {UmeAlarmHistory.host_name: hn},
        synchronize_session=False,
    )


def _backfill_alarm_host_names(db: Session, model: type[UmeAlarmCurrent] | type[UmeAlarmHistory]) -> int:
    """Set alarm.host_name from ume_inventory_ne for all rows with matching ne_id."""
    table = str(getattr(model, "__tablename__", "") or "")
    if not table:
        return 0
    bind = db.get_bind()
    if bind is not None and str(bind.dialect.name).lower() == "postgresql":
        res = db.execute(
            sql_text(
                f"""
                UPDATE {table} AS a
                SET host_name = COALESCE(NULLIF(TRIM(ne.host_name), ''), '')
                FROM ume_inventory_ne AS ne
                WHERE a.ne_id <> '' AND a.ne_id = ne.ne_id
                """
            )
        )
        return int(res.rowcount or 0)
    ne_map = {str(r.ne_id or ""): _s(r.host_name) for r in db.query(UmeInventoryNE).all() if str(r.ne_id or "")}
    updated = 0
    for alarm in db.query(model).filter(model.ne_id != "").all():  # type: ignore[arg-type]
        hn = ne_map.get(str(alarm.ne_id or ""), "")
        if str(alarm.host_name or "") != hn:
            alarm.host_name = hn
            updated += 1
    return updated


