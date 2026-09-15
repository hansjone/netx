from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .dsh_alarm_hub import publish_alarm
from .key_alert_matcher import match_key_alert_rule
from .models import UmeInventoryNE, UmeKeyAlertForwardLog
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
    # Column name is historical (oclaw_ok); now means DSH hub delivery succeeded.
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

    hub_sent = publish_alarm(payload)
    if hub_sent <= 0:
        return False

    status_text = f"dsh_hub:{hub_sent}"
    row = existing
    if row is None:
        row = UmeKeyAlertForwardLog(
            alarm_key=str(alarm_key or ""),
            action=act,
            rule_key=str(rule.notification_id or ""),
            notification_id=notification_id_from_norm(norm),
            forwarded_at=_utc_now_naive(),
            oclaw_ok=1,
            error="",
        )
        db.add(row)
    else:
        row.notification_id = notification_id_from_norm(norm)
        row.rule_key = str(rule.notification_id or "")
        row.forwarded_at = _utc_now_naive()
        row.oclaw_ok = 1
        row.error = ""
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        _log.debug("key_alert forward log race alarm_key=%s action=%s", alarm_key, act)
    else:
        _log.debug("key_alert forwarded via DSH hub=%s status=%s", hub_sent, status_text)
    return True
