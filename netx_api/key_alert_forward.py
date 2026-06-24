from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .key_alert_matcher import match_key_alert_rule
from .models import UmeInventoryNE, UmeKeyAlertForwardLog
from .oclaw_alarm_forwarder import enqueue_alarm_forward, is_forwarder_operational
from .ume_sync_service import (
    _derive_ne_id_from_alarm,
    _pick,
    _s,
    notification_id_from_norm,
)

_log = logging.getLogger("netx.key_alert.forward")


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ne_payload(db: Session, ne_id: str) -> dict[str, str]:
    row = db.get(UmeInventoryNE, ne_id) if ne_id else None
    if row is None:
        return {}
    return {
        "ne_id": str(row.ne_id or ""),
        "ne_name": str(row.ne_name or ""),
        "user_label": str(row.user_label or ""),
        "host_name": str(row.host_name or ""),
        "ip_address": str(row.ip_address or ""),
        "ne_type": str(row.ne_type or ""),
        "device_level": str(row.device_level or ""),
    }


def _build_forward_payload(
    *,
    norm: dict[str, Any],
    alarm_key: str,
    action: str,
    rule_label: str,
) -> dict[str, Any]:
    ne_id = _s(_derive_ne_id_from_alarm(norm))
    return {
        "action": str(action or ""),
        "alarm_key": str(alarm_key or ""),
        "notification_id": notification_id_from_norm(norm),
        "rule_label": str(rule_label or ""),
        "event_type": _s(_pick(norm, "eventType", "event-type")),
        "native_probable_cause": _s(_pick(norm, "nativeProbableCause", "native-probable-cause")),
        "perceived_severity": _s(_pick(norm, "perceivedSeverity", "perceived-severity")),
        "is_cleared": _s(_pick(norm, "isCleared", "is-cleared")),
        "time_created": _s(_pick(norm, "timeCreated", "time-created")),
        "object_name": _s(_pick(norm, "objectName", "object-name")),
        "ne_id": ne_id,
    }


def maybe_forward_key_alert(
    db: Session,
    *,
    norm: dict[str, Any],
    alarm_key: str,
    action: str,
) -> bool:
    if not is_forwarder_operational():
        return False
    rule = match_key_alert_rule(db, norm=norm, action=action)
    if rule is None:
        return False
    act = str(action or "").strip().lower()
    existing = (
        db.query(UmeKeyAlertForwardLog)
        .filter(
            UmeKeyAlertForwardLog.alarm_key == str(alarm_key or ""),
            UmeKeyAlertForwardLog.action == act,
        )
        .first()
    )
    if existing is not None and int(existing.oclaw_ok or 0) == 1:
        return False

    ne_id = _s(_derive_ne_id_from_alarm(norm))
    payload = _build_forward_payload(
        norm=norm,
        alarm_key=alarm_key,
        action=action,
        rule_label=str(rule.label or ""),
    )
    payload["ne"] = _ne_payload(db, ne_id)
    payload["rule_key"] = str(rule.notification_id or "")

    queued = enqueue_alarm_forward(payload)
    if not queued:
        return False

    row = existing
    if row is None:
        row = UmeKeyAlertForwardLog(
            alarm_key=str(alarm_key or ""),
            action=act,
            rule_key=str(rule.notification_id or ""),
            notification_id=notification_id_from_norm(norm),
            forwarded_at=_utc_now_naive(),
            oclaw_ok=0,
            error="queued",
        )
        db.add(row)
    else:
        row.notification_id = notification_id_from_norm(norm)
        row.rule_key = str(rule.notification_id or "")
        row.forwarded_at = _utc_now_naive()
        row.oclaw_ok = 0
        row.error = "queued"
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
    return True


def record_forward_result(
    *,
    alarm_key: str,
    action: str,
    ok: bool,
    error: str = "",
    rule_key: str = "",
) -> None:
    from .db import SessionLocal

    key = str(alarm_key or "").strip()
    act = str(action or "").strip().lower()
    if not key or not act:
        return
    db = SessionLocal()
    try:
        row = (
            db.query(UmeKeyAlertForwardLog)
            .filter(UmeKeyAlertForwardLog.alarm_key == key, UmeKeyAlertForwardLog.action == act)
            .first()
        )
        if row is None:
            row = UmeKeyAlertForwardLog(
                alarm_key=key,
                action=act,
                rule_key=str(rule_key or "").strip(),
                forwarded_at=_utc_now_naive(),
                oclaw_ok=1 if ok else 0,
                error="" if ok else str(error or "forward_failed")[:240],
            )
            db.add(row)
        else:
            if rule_key and not str(row.rule_key or "").strip():
                row.rule_key = str(rule_key or "").strip()
            row.forwarded_at = _utc_now_naive()
            row.oclaw_ok = 1 if ok else 0
            row.error = "" if ok else str(error or "forward_failed")[:240]
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
