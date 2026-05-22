from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .models import UmeAlarmSubscription

DEFAULT_SUBSCRIPTION_KEY = "default"


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def load_subscription(db: Session, *, cache_key: str = DEFAULT_SUBSCRIPTION_KEY) -> tuple[str, str, str] | None:
    row = db.get(UmeAlarmSubscription, str(cache_key or DEFAULT_SUBSCRIPTION_KEY))
    if row is None:
        return None
    sub_id = str(row.subscription_id or "").strip()
    uri = str(row.wss_uri or "").strip()
    if not sub_id or not uri:
        return None
    return sub_id, uri, str(row.topic or "ALARM")


def save_subscription(
    db: Session,
    *,
    subscription_id: str,
    wss_uri: str,
    topic: str = "ALARM",
    cache_key: str = DEFAULT_SUBSCRIPTION_KEY,
) -> UmeAlarmSubscription:
    key = str(cache_key or DEFAULT_SUBSCRIPTION_KEY)
    now = _utc_now_naive()
    row = db.get(UmeAlarmSubscription, key)
    if row is None:
        row = UmeAlarmSubscription(
            cache_key=key,
            subscription_id=str(subscription_id or "").strip(),
            wss_uri=str(wss_uri or "").strip(),
            topic=str(topic or "ALARM").strip() or "ALARM",
            established_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        row.subscription_id = str(subscription_id or "").strip()
        row.wss_uri = str(wss_uri or "").strip()
        row.topic = str(topic or "ALARM").strip() or "ALARM"
        row.updated_at = now
    db.flush()
    return row


def clear_subscription(db: Session, *, cache_key: str = DEFAULT_SUBSCRIPTION_KEY) -> None:
    key = str(cache_key or DEFAULT_SUBSCRIPTION_KEY)
    row = db.get(UmeAlarmSubscription, key)
    if row is not None:
        db.delete(row)
        db.flush()
