from __future__ import annotations

import json
import logging
import threading
import hashlib
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any

_sync_log = logging.getLogger("netx.ume.sync")
_ALARMS_CURRENT_SYNC_LOCK = threading.Lock()

from sqlalchemy import func
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .models import (
    UmeAlarmBatch,
    UmeAlarmCurrent,
    UmeAlarmHistory,
    UmeInventoryNE,
    UmeSyncJob,
)
from .ume_client import UMEClient


def _s(v: Any) -> str:
    if v is None:
        return ""
    text = str(v).strip()
    if text.lower() == "nan":
        return ""
    return text


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


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


def _alarm_key(alarm: dict[str, Any]) -> str:
    key = _s(_pick(alarm, "alarmKey", "alarm-key","alarmkey","id"))
    if key:
        # Keep full upstream key; use a stable digest only for pathological ultra-long keys.
        if len(key) > 512:
            return "sha256:" + hashlib.sha256(key.encode("utf-8", errors="ignore")).hexdigest()
        return key
    parts = [
        _s(_pick(alarm, "objectName", "object-name")),
        _s(_pick(alarm, "eventType", "event-type")),
        _s(_pick(alarm, "timeCreated", "time-created")),
        _s(_pick(alarm, "nativeProbableCause", "native-probable-cause")),
    ]
    merged = "|".join(x for x in parts if x)
    raw = merged or f"fallback-{datetime.utcnow().timestamp()}"
    if len(raw) > 512:
        return "sha256:" + hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()
    return raw


def _derive_ne_id_from_alarm(alarm: dict[str, Any]) -> str:
    ne_id = _s(_pick(alarm, "ne-id", "neId", "ne_id"))
    if ne_id:
        return ne_id

    def _uuid_like(s: str) -> bool:
        return bool(
            re.fullmatch(
                r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
                str(s or "").strip(),
            )
        )

    # Prefer UUID net_id in objectName, e.g. "... ME{33a3e8f4-a76e-40fd-a0ba-045371a5f234} ..."
    object_name = _s(_pick(alarm, "objectName", "object-name"))
    if object_name:
        m_obj = re.search(r"ME\{([^}]+)\}", object_name, flags=re.IGNORECASE)
        if m_obj:
            candidate = _s(m_obj.group(1))
            if _uuid_like(candidate):
                return candidate

    alarm_key = _s(_pick(alarm, "alarmKey", "alarm-key", "alarmkey"))
    if not alarm_key:
        return ""

    # If alarmkey contains ME{...}, only accept it when it looks like a UUID.
    m0 = re.search(r"ME\{([^}]+)\}", alarm_key, flags=re.IGNORECASE)
    if m0:
        candidate = _s(m0.group(1))
        if _uuid_like(candidate):
            return candidate

    # Common UME formats observed:
    # 1) "<net_id>#<suffix>"
    # 2) "<net_id>, <x>, <y>"
    # 3) "<net_id> <x> <y>"
    if "#" in alarm_key:
        candidate = _s(alarm_key.split("#", 1)[0])
        return candidate if _uuid_like(candidate) else ""
    if "," in alarm_key:
        candidate = _s(alarm_key.split(",", 1)[0])
        return candidate if _uuid_like(candidate) else ""
    parts = [p for p in alarm_key.split() if p]
    if len(parts) >= 2 and ":" not in parts[0]:
        candidate = _s(parts[0])
        return candidate if _uuid_like(candidate) else ""
    return ""


def _normalize_yang_key(key: str) -> str:
    raw = str(key or "").strip()
    if not raw:
        return ""
    if ":" in raw:
        return raw.rsplit(":", 1)[-1]
    return raw


def normalize_yang_alarm(raw: dict[str, Any]) -> dict[str, Any]:
    """Flatten YANG namespace-prefixed keys (e.g. zte-alarms:alarmkey) for _pick()."""
    out: dict[str, Any] = {}
    for k, v in raw.items():
        nk = _normalize_yang_key(str(k))
        if not nk:
            continue
        if nk in out and out[nk] not in (None, ""):
            continue
        out[nk] = v
    return out


def _is_alarm_cleared(alarm: dict[str, Any]) -> bool:
    val = _pick(alarm, "isCleared", "is-cleared")
    if isinstance(val, bool):
        return val
    text = _s(val).lower()
    return text in {"true", "1", "yes"}


_cleared_tombstone_lock = threading.Lock()
_cleared_tombstones: dict[str, float] = {}


def _mark_alarm_cleared_tombstone(alarm_key: str) -> None:
    key = str(alarm_key or "").strip()
    if not key:
        return
    ttl_s = max(60, int(getattr(settings, "ume_alarm_cleared_tombstone_s", 300) or 300))
    expires = time.time() + ttl_s
    with _cleared_tombstone_lock:
        _cleared_tombstones[key] = expires
        if len(_cleared_tombstones) > 50000:
            now = time.time()
            stale = [k for k, exp in _cleared_tombstones.items() if exp <= now]
            for k in stale[:10000]:
                _cleared_tombstones.pop(k, None)


def _is_alarm_cleared_tombstone(alarm_key: str) -> bool:
    key = str(alarm_key or "").strip()
    if not key:
        return False
    now = time.time()
    with _cleared_tombstone_lock:
        exp = _cleared_tombstones.get(key)
        if exp is None:
            return False
        if exp <= now:
            _cleared_tombstones.pop(key, None)
            return False
        return True


def notification_id_from_norm(norm: dict[str, Any]) -> str:
    return _s(_pick(norm, "notificationId", "notification-id"))


def _alarm_row_from_norm(key: str, norm: dict[str, Any], *, touch_ts: datetime, first_seen_at: datetime) -> dict[str, Any]:
    return {
        "alarm_key": key,
        "ne_id": _s(_derive_ne_id_from_alarm(norm)),
        "host_name": "",
        "object_name": _s(_pick(norm, "objectName", "object-name")),
        "event_type": _s(_pick(norm, "eventType", "event-type")),
        "native_probable_cause": _s(_pick(norm, "nativeProbableCause", "native-probable-cause")),
        "perceived_severity": _s(_pick(norm, "perceivedSeverity", "perceived-severity")),
        "is_cleared": _s(_pick(norm, "isCleared", "is-cleared")),
        "time_created": _s(_pick(norm, "timeCreated", "time-created")),
        "root_cause_alarm_indication": _s(
            _pick(norm, "rootCauseAlarmIndication", "root-cause-alarm-indication")
        ),
        "notification_id": notification_id_from_norm(norm),
        "first_seen_at": first_seen_at,
        "last_seen_at": touch_ts,
        "raw_json": json.dumps(norm, ensure_ascii=False, default=str),
    }


def _apply_row_to_model(db: Session, existing: UmeAlarmCurrent, norm: dict[str, Any], *, touch_ts: datetime) -> None:
    existing.ne_id = _s(_derive_ne_id_from_alarm(norm))
    existing.object_name = _s(_pick(norm, "objectName", "object-name"))
    existing.event_type = _s(_pick(norm, "eventType", "event-type"))
    existing.native_probable_cause = _s(_pick(norm, "nativeProbableCause", "native-probable-cause"))
    existing.perceived_severity = _s(_pick(norm, "perceivedSeverity", "perceived-severity"))
    existing.is_cleared = _s(_pick(norm, "isCleared", "is-cleared"))
    existing.time_created = _s(_pick(norm, "timeCreated", "time-created"))
    existing.root_cause_alarm_indication = _s(
        _pick(norm, "rootCauseAlarmIndication", "root-cause-alarm-indication")
    )
    existing.notification_id = notification_id_from_norm(norm)
    prev_seen = existing.last_seen_at
    if prev_seen is None or touch_ts >= prev_seen:
        existing.last_seen_at = touch_ts
    existing.raw_json = json.dumps(norm, ensure_ascii=False, default=str)


def _upsert_alarm_current(db: Session, key: str, norm: dict[str, Any], *, touch_ts: datetime) -> tuple[str, bool]:
    bind = db.get_bind()
    dialect = str(getattr(getattr(bind, "dialect", None), "name", "") or "").lower()
    existing = db.get(UmeAlarmCurrent, key)
    if existing is not None:
        _apply_row_to_model(db, existing, norm, touch_ts=touch_ts)
        existing.host_name = _lookup_host_name(db, existing.ne_id)
        return "updated", True

    if dialect == "postgresql":
        row = _alarm_row_from_norm(key, norm, touch_ts=touch_ts, first_seen_at=touch_ts)
        row["host_name"] = _lookup_host_name(db, row["ne_id"])
        ins = pg_insert(UmeAlarmCurrent).values(**row)
        excluded = ins.excluded
        stmt = ins.on_conflict_do_update(
            index_elements=[UmeAlarmCurrent.alarm_key],
            set_={
                "ne_id": excluded.ne_id,
                "host_name": excluded.host_name,
                "object_name": excluded.object_name,
                "event_type": excluded.event_type,
                "native_probable_cause": excluded.native_probable_cause,
                "perceived_severity": excluded.perceived_severity,
                "is_cleared": excluded.is_cleared,
                "time_created": excluded.time_created,
                "root_cause_alarm_indication": excluded.root_cause_alarm_indication,
                "notification_id": excluded.notification_id,
                "last_seen_at": func.greatest(UmeAlarmCurrent.last_seen_at, excluded.last_seen_at),
                "raw_json": excluded.raw_json,
            },
        )
        db.execute(stmt)
        return "inserted", True

    try:
        with db.begin_nested():
            model = UmeAlarmCurrent(alarm_key=key, first_seen_at=touch_ts)
            db.add(model)
            db.flush()
        _apply_row_to_model(db, model, norm, touch_ts=touch_ts)
        model.host_name = _lookup_host_name(db, model.ne_id)
        return "inserted", True
    except IntegrityError:
        existing = db.get(UmeAlarmCurrent, key)
        if existing is None:
            return "skipped", False
        _apply_row_to_model(db, existing, norm, touch_ts=touch_ts)
        existing.host_name = _lookup_host_name(db, existing.ne_id)
        return "updated", True


def extract_alarm_from_notification(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Parse alarm-notification from a WS/REST notification envelope."""
    if not isinstance(payload, dict):
        return None

    def _find_alarm_notification(node: Any) -> dict[str, Any] | None:
        if isinstance(node, dict):
            for k, v in node.items():
                key = str(k).lower()
                if key in {"alarm-notification", "alarm_notification"} and isinstance(v, dict):
                    return normalize_yang_alarm(v)
                found = _find_alarm_notification(v)
                if found is not None:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = _find_alarm_notification(item)
                if found is not None:
                    return found
        return None

    direct = _find_alarm_notification(payload)
    if direct is not None:
        return direct
    return normalize_yang_alarm(payload) if payload else None


def apply_alarm_to_current(
    db: Session,
    alarm: dict[str, Any],
    *,
    touch_ts: datetime,
    source: str = "",
) -> tuple[str, bool]:
    """
    Apply one alarm to ume_alarms_current.
    Returns (action, changed) where action is inserted|updated|deleted|skipped.
    """
    norm = normalize_yang_alarm(alarm) if alarm else {}
    if not norm:
        return "skipped", False

    if _is_alarm_cleared(norm):
        key = _alarm_key(norm)
        if not key:
            return "skipped", False
        existing = db.get(UmeAlarmCurrent, key)
        if existing is None:
            _mark_alarm_cleared_tombstone(key)
            return "deleted", False
        db.delete(existing)
        _mark_alarm_cleared_tombstone(key)
        return "deleted", True

    key = _alarm_key(norm)
    if not key:
        return "skipped", False
    if str(source or "").strip().lower() == "rest" and _is_alarm_cleared_tombstone(key):
        return "skipped", False

    return _upsert_alarm_current(db, key, norm, touch_ts=touch_ts)


def _build_sync_job(domain: str, trigger_mode: str) -> UmeSyncJob:
    return UmeSyncJob(
        domain=domain,
        status="running",
        trigger_mode=trigger_mode,
        started_at=_utc_now_naive(),
    )


def _snapshot_reconcile_ok(meta: dict[str, Any]) -> bool:
    """True when paging finished normally (full snapshot); avoid deleting local rows on partial pulls."""
    if not bool(meta.get("is_end_of_reply")):
        return False
    if bool(meta.get("graceful_end_by_iterator_error")):
        return False
    warnings = meta.get("warnings") or []
    if not isinstance(warnings, list):
        return False
    if "duplicate_page_detected" in [str(w) for w in warnings]:
        return False
    if str(meta.get("paging_note") or "").strip() == "duplicate_page_detected":
        return False
    return True


def _collect_marker_pages(
    fetch_page: Any,
    *,
    max_pages: int,
    iterator_500_as_end: bool = False,
) -> tuple[list[list[dict[str, Any]]], dict[str, Any]]:
    page_no = 0
    next_marker = ""
    is_end_of_reply = False
    graceful_end_by_iterator_error = False
    paging_note = ""
    warnings: list[str] = []
    last_page_signature = ""
    pages: list[list[dict[str, Any]]] = []

    while True:
        page_no += 1
        if page_no > max_pages:
            raise RuntimeError(f"ume_alarms_pagination_exceeded:max_pages={max_pages}")

        try:
            rows, diag = fetch_page(next_marker or None)
        except Exception as exc:
            msg = str(exc or "")
            low = msg.lower()
            if iterator_500_as_end and pages and "ume_request_failed:500" in low and "iterator" in low and "null" in low:
                graceful_end_by_iterator_error = True
                paging_note = msg[:240]
                break
            raise

        rows = [x for x in rows if isinstance(x, dict)]
        pages.append(rows)

        if page_no == 1 or page_no % 25 == 0:
            _sync_log.info(
                "ume marker page=%s rows=%s is_end=%s marker_len=%s",
                page_no,
                len(rows),
                getattr(diag, "is_end_of_reply", None),
                len(str(getattr(diag, "marker", "") or "")),
            )

        # Protection against repeated pages causing infinite loops.
        cur_sig = "|".join(sorted(_alarm_key(x) for x in rows))
        if cur_sig and cur_sig == last_page_signature:
            warnings.append("duplicate_page_detected")
            paging_note = "duplicate_page_detected"
            break
        last_page_signature = cur_sig

        has_is_end = diag.is_end_of_reply is not None
        is_end_of_reply = bool(diag.is_end_of_reply) if has_is_end else False
        next_marker = str(diag.marker or "").strip()

        if has_is_end and is_end_of_reply:
            break
        if has_is_end and (not is_end_of_reply) and (not next_marker):
            warnings.append("marker_missing_when_not_end")
            paging_note = "marker_missing_when_not_end"
            break
        if (not has_is_end) and (not next_marker):
            if rows:
                warnings.append("marker_missing_stop")
                paging_note = "marker_missing_stop"
            break

    meta = {
        "page_count": page_no,
        "last_marker": next_marker,
        "is_end_of_reply": is_end_of_reply,
        "graceful_end_by_iterator_error": graceful_end_by_iterator_error,
        "paging_note": paging_note,
        "warnings": warnings,
    }
    return pages, meta


def sync_inventory_full(db: Session, client: UMEClient, *, trigger_mode: str = "manual") -> UmeSyncJob:
    job = _build_sync_job("inventory", trigger_mode)
    db.add(job)
    db.flush()
    db.commit()
    _sync_log.info("inventory sync job %s committed as running (trigger=%s)", getattr(job, "id", "?"), trigger_mode)
    pulled = inserted = updated = 0
    try:
        limit_max = int(getattr(settings, "ume_limit_max", 5000) or 5000)
        limit_max = max(1, limit_max)
        page_size = int(getattr(settings, "ume_marker_page_limit", getattr(settings, "ume_page_size", 1000)) or 1000)
        page_size = max(1, min(page_size, limit_max))
        max_pages = int(getattr(settings, "ume_marker_max_pages", getattr(settings, "ume_max_pages", 2000)) or 2000)
        max_pages = max(1, min(max_pages, 20000))

        pages, inv_meta = _collect_marker_pages(
            lambda marker: client.get_network_elements(limit=page_size, marker=marker),
            max_pages=max_pages,
            iterator_500_as_end=False,
        )
        ne_rows = [row for page in pages for row in page]
        now = _utc_now_naive()
        pulled = len(ne_rows)
        seen_ne_ids: set[str] = set()
        for row in ne_rows:
            ne_id = _s(_pick(row, "ne-id", "ne_id", "id"))
            if not ne_id:
                continue
            seen_ne_ids.add(ne_id)
            existing = db.get(UmeInventoryNE, ne_id)
            if existing is None:
                existing = UmeInventoryNE(
                    ne_id=ne_id,
                    first_seen_at=now,
                )
                db.add(existing)
                inserted += 1
            else:
                updated += 1
            existing.ne_name = _s(_pick(row, "name", "ne-name"))
            existing.user_label = _s(_pick(row, "user-label", "user_label"))
            existing.ip_address = _s(_pick(row, "ip-Address", "ip-address", "ip"))
            existing.ipv6_address = _s(_pick(row, "ipv6-address", "ipv6_address"))
            existing.ne_type = _s(_pick(row, "type", "ne-type"))
            existing.device_level = _s(_pick(row, "device-level"))
            existing.host_name = _s(_pick(row, "host-name"))
            existing.location = _s(_pick(row, "location"))
            existing.hardware_version = _s(_pick(row, "hardware-version"))
            existing.loopback = _s(_pick(row, "loopback"))
            existing.consistent_state = _s(_pick(row, "consistent-state"))
            existing.interface_version = _s(_pick(row, "interface-version"))
            existing.mac = _s(_pick(row, "mac"))
            existing.admin_status = _s(_pick(row, "admin-status"))
            existing.address_type = _s(_pick(row, "address-type"))
            existing.connection_status = _s(_pick(row, "connection-status"))
            existing.maintain_status = _s(_pick(row, "maintain-status"))
            existing.net_mask = _s(_pick(row, "net-mask"))
            existing.create_time = _s(_pick(row, "create-time"))
            existing.creator = _s(_pick(row, "creator"))
            existing.vendor = _s(_pick(row, "vendor-name")) or "ZTE"
            existing.last_seen_at = now
            existing.raw_json = json.dumps(row, ensure_ascii=False, default=str)
            _propagate_host_name_to_alarms(db, ne_id, existing.host_name)

        db.flush()
        deleted_ne = 0
        if _snapshot_reconcile_ok(inv_meta):
            if seen_ne_ids:
                deleted_ne = int(
                    db.query(UmeInventoryNE)
                    .filter(~UmeInventoryNE.ne_id.in_(list(seen_ne_ids)))
                    .delete(synchronize_session=False)
                )
            else:
                deleted_ne = int(db.query(UmeInventoryNE).delete(synchronize_session=False))

        job.details_json = json.dumps(
            {
                "inventory_reconcile": _snapshot_reconcile_ok(inv_meta),
                "deleted_inventory_ne": deleted_ne,
                "paging": {
                    "is_end_of_reply": bool(inv_meta.get("is_end_of_reply")),
                    "warnings": list(inv_meta.get("warnings") or []),
                },
            },
            ensure_ascii=False,
        )

        job.status = "done"
    except Exception as exc:
        job.status = "failed"
        job.error_message = str(exc)[:1024]
    finally:
        job.pulled_count = int(pulled)
        job.inserted_count = int(inserted)
        job.updated_count = int(updated)
        job.ended_at = _utc_now_naive()
        db.commit()
        db.refresh(job)
    return job


def _reconcile_stale_current_alarms(
    db: Session,
    *,
    sync_batch_ts: datetime,
    seen_keys: set[str],
    wss_active: bool,
) -> int:
    """Remove local current alarms missing from REST snapshot.

    When WSS is active, skip deletes (WSS may have keys not yet in REST); manual sync only upserts.
    """
    del seen_keys
    if wss_active:
        return 0
    return int(
        db.query(UmeAlarmCurrent)
        .filter(UmeAlarmCurrent.last_seen_at < sync_batch_ts)
        .delete(synchronize_session=False)
    )


def _sync_alarms_common(
    db: Session,
    client: UMEClient,
    *,
    is_uncleared: bool,
    trigger_mode: str,
    wss_active: bool = False,
) -> tuple[UmeSyncJob, UmeAlarmBatch]:
    domain = "alarms_history" if is_uncleared else "alarms_current"
    job = _build_sync_job(domain, trigger_mode)
    db.add(job)
    batch = UmeAlarmBatch(
        kind="history" if is_uncleared else "current",
        status="running",
        started_at=_utc_now_naive(),
    )
    db.add(batch)
    db.flush()
    db.commit()
    _sync_log.info(
        "alarms sync job domain=%s id=%s committed as running (trigger=%s)",
        domain,
        getattr(job, "id", "?"),
        trigger_mode,
    )
    pulled = inserted = updated = 0
    deleted_stale_current = 0
    host_names_backfilled = 0
    reconcile_mode = ""
    seen_keys: set[str] = set()
    paging_mode = "marker"
    paging_note = ""
    page_no = 0
    next_marker = ""
    is_end_of_reply = False
    graceful_end_by_iterator_error = False
    warnings: list[str] = []
    meta: dict[str, Any] = {}
    sync_batch_ts = _utc_now_naive()
    try:
        limit_max = int(getattr(settings, "ume_limit_max", 5000) or 5000)
        limit_max = max(1, limit_max)
        page_size = int(getattr(settings, "ume_marker_page_limit", getattr(settings, "ume_page_size", 1000)) or 1000)
        page_size = max(1, min(page_size, limit_max))
        max_pages = int(getattr(settings, "ume_marker_max_pages", getattr(settings, "ume_max_pages", 2000)) or 2000)
        max_pages = max(1, min(max_pages, 20000))

        def upsert_alarm_history(alarm: dict[str, Any], *, touch_ts: datetime) -> None:
            nonlocal inserted, updated
            key = _alarm_key(alarm)
            existing = db.get(UmeAlarmHistory, key)
            if existing is None:
                existing = UmeAlarmHistory(alarm_key=key, first_seen_at=touch_ts)
                db.add(existing)
                inserted += 1
            else:
                updated += 1
            existing.ne_id = _s(_derive_ne_id_from_alarm(alarm))
            existing.host_name = _lookup_host_name(db, existing.ne_id)
            existing.object_name = _s(_pick(alarm, "objectName", "object-name"))
            existing.event_type = _s(_pick(alarm, "eventType", "event-type"))
            existing.native_probable_cause = _s(_pick(alarm, "nativeProbableCause", "native-probable-cause"))
            existing.perceived_severity = _s(_pick(alarm, "perceivedSeverity", "perceived-severity"))
            existing.is_cleared = _s(_pick(alarm, "isCleared", "is-cleared"))
            existing.time_created = _s(_pick(alarm, "timeCreated", "time-created"))
            existing.root_cause_alarm_indication = _s(
                _pick(alarm, "rootCauseAlarmIndication", "root-cause-alarm-indication")
            )
            existing.notification_id = notification_id_from_norm(alarm)
            existing.last_seen_at = touch_ts
            existing.raw_json = json.dumps(alarm, ensure_ascii=False, default=str)

        iterator_500_as_end = bool(getattr(settings, "ume_iterator_500_as_end", True))
        pages, meta = _collect_marker_pages(
            lambda marker: client.get_alarms(is_uncleared=is_uncleared, limit=page_size, marker=marker),
            max_pages=max_pages,
            iterator_500_as_end=iterator_500_as_end,
        )
        sync_batch_ts = _utc_now_naive()
        seen_keys = set()
        for rows in pages:
            pulled += len(rows)
            for alarm in rows:
                if is_uncleared:
                    upsert_alarm_history(alarm, touch_ts=sync_batch_ts)
                else:
                    key = _alarm_key(alarm)
                    if key:
                        seen_keys.add(key)
                    action, _changed = apply_alarm_to_current(
                        db,
                        alarm,
                        touch_ts=sync_batch_ts,
                        source="rest",
                    )
                    if action == "inserted":
                        inserted += 1
                    elif action == "updated":
                        updated += 1
        db.flush()
        page_no = int(meta.get("page_count") or 0)
        next_marker = str(meta.get("last_marker") or "")
        is_end_of_reply = bool(meta.get("is_end_of_reply"))
        graceful_end_by_iterator_error = bool(meta.get("graceful_end_by_iterator_error"))
        paging_note = str(meta.get("paging_note") or "")
        warnings = [str(x) for x in (meta.get("warnings") or []) if str(x)]

        reconcile_mode = "full"
        if not is_uncleared and _snapshot_reconcile_ok(meta):
            if wss_active:
                reconcile_mode = "upsert_only"
            deleted_stale_current = _reconcile_stale_current_alarms(
                db,
                sync_batch_ts=sync_batch_ts,
                seen_keys=seen_keys,
                wss_active=wss_active,
            )

        alarm_model = UmeAlarmHistory if is_uncleared else UmeAlarmCurrent
        host_names_backfilled = _backfill_alarm_host_names(db, alarm_model)

        batch.total_rows = int(pulled)
        batch.success_rows = int(inserted + updated)
        batch.failed_rows = max(0, int(pulled) - int(inserted + updated))
        batch.status = "done"
        batch.ended_at = _utc_now_naive()
        batch.raw_json = json.dumps(
            {
                "pulled": pulled,
                "inserted": inserted,
                "updated": updated,
                "paging_mode": paging_mode,
                "page_count": page_no,
                "last_marker": next_marker,
                "is_end_of_reply": is_end_of_reply,
                "graceful_end_by_iterator_error": graceful_end_by_iterator_error,
                "warnings": warnings,
                "deleted_stale_current_alarms": int(deleted_stale_current),
                "host_names_backfilled": int(host_names_backfilled),
                "current_snapshot_reconcile": (not is_uncleared) and _snapshot_reconcile_ok(meta),
                "reconcile_mode": reconcile_mode if not is_uncleared else "",
                "wss_active_during_sync": bool(wss_active) if not is_uncleared else False,
                "seen_keys_count": len(seen_keys) if not is_uncleared else 0,
            },
            ensure_ascii=False,
        )

        job.status = "done"
    except Exception as exc:
        msg = str(exc)[:1024]
        reconcile_mode = "failed"
        batch.status = "failed"
        batch.error_message = msg
        batch.ended_at = _utc_now_naive()
        job.status = "failed"
        job.error_message = msg
    finally:
        job.pulled_count = int(pulled)
        job.inserted_count = int(inserted)
        job.updated_count = int(updated)
        job.ended_at = _utc_now_naive()
        job.details_json = json.dumps(
            {
                "batch_id": batch.batch_id,
                "kind": batch.kind,
                "status": batch.status,
                "paging_mode": paging_mode,
                "paging_note": paging_note,
                "page_count": page_no,
                "last_marker": next_marker,
                "is_end_of_reply": is_end_of_reply,
                "graceful_end_by_iterator_error": graceful_end_by_iterator_error,
                "warnings": warnings,
                "deleted_stale_current_alarms": int(deleted_stale_current),
                "host_names_backfilled": int(host_names_backfilled),
                "current_snapshot_reconcile": (not is_uncleared) and _snapshot_reconcile_ok(meta),
                "reconcile_mode": reconcile_mode if not is_uncleared else "",
                "wss_active_during_sync": bool(wss_active) if not is_uncleared else False,
                "seen_keys_count": len(seen_keys) if not is_uncleared else 0,
            },
            ensure_ascii=False,
        )
        db.commit()
        db.refresh(job)
        db.refresh(batch)
    return job, batch


def sync_alarms_current(
    db: Session,
    client: UMEClient,
    *,
    trigger_mode: str = "manual",
    wss_active: bool | None = None,
) -> tuple[UmeSyncJob, UmeAlarmBatch]:
    if not _ALARMS_CURRENT_SYNC_LOCK.acquire(blocking=False):
        _sync_log.warning("alarms_current sync skipped: another sync is in progress")
        raise RuntimeError("alarms_current_sync_busy")
    try:
        if wss_active is None:
            from .ume_alarm_ws import is_wss_active_for_current_alarms

            wss_active = is_wss_active_for_current_alarms()
        return _sync_alarms_common(
            db,
            client,
            is_uncleared=False,
            trigger_mode=trigger_mode,
            wss_active=bool(wss_active),
        )
    finally:
        _ALARMS_CURRENT_SYNC_LOCK.release()


def sync_alarms_history_full(
    db: Session, client: UMEClient, *, trigger_mode: str = "manual"
) -> tuple[UmeSyncJob, UmeAlarmBatch]:
    return _sync_alarms_common(db, client, is_uncleared=True, trigger_mode=trigger_mode)
