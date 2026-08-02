"""UME alarm normalize / upsert / notification apply."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from datetime import datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .models import UmeAlarmCurrent
from .ume_sync_common import _lookup_host_name, _pick, _s, _utc_now_naive

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
    raw = merged or f"fallback-{_utc_now_naive().timestamp()}"
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
        now = time.time()
        # Always sweep expired keys; hard-cap with oldest-first eviction.
        stale = [k for k, exp in _cleared_tombstones.items() if exp <= now]
        for k in stale:
            _cleared_tombstones.pop(k, None)
        max_keys = 50000
        if len(_cleared_tombstones) > max_keys:
            overflow = len(_cleared_tombstones) - max_keys
            oldest = sorted(_cleared_tombstones.items(), key=lambda kv: kv[1])[:overflow]
            for k, _ in oldest:
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

