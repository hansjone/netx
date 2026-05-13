from __future__ import annotations

import json
import logging
import hashlib
from datetime import datetime, timezone
from typing import Any

_sync_log = logging.getLogger("netx.ume.sync")

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

    alarm_key = _s(_pick(alarm, "alarmKey", "alarm-key", "alarmkey"))
    if not alarm_key:
        return ""

    # Common UME formats observed:
    # 1) "<net_id>#<suffix>"
    # 2) "<net_id>, <x>, <y>"
    if "#" in alarm_key:
        return _s(alarm_key.split("#", 1)[0])
    if "," in alarm_key:
        return _s(alarm_key.split(",", 1)[0])
    return ""


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


def _sync_alarms_common(
    db: Session,
    client: UMEClient,
    *,
    is_uncleared: bool,
    trigger_mode: str,
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
    paging_mode = "marker"
    paging_note = ""
    page_no = 0
    next_marker = ""
    is_end_of_reply = False
    graceful_end_by_iterator_error = False
    warnings: list[str] = []
    meta: dict[str, Any] = {}
    try:
        limit_max = int(getattr(settings, "ume_limit_max", 5000) or 5000)
        limit_max = max(1, limit_max)
        page_size = int(getattr(settings, "ume_marker_page_limit", getattr(settings, "ume_page_size", 1000)) or 1000)
        page_size = max(1, min(page_size, limit_max))
        max_pages = int(getattr(settings, "ume_marker_max_pages", getattr(settings, "ume_max_pages", 2000)) or 2000)
        max_pages = max(1, min(max_pages, 20000))

        def upsert_alarm(alarm: dict[str, Any], *, touch_ts: datetime) -> None:
            nonlocal inserted, updated
            key = _alarm_key(alarm)
            if is_uncleared:
                existing = db.get(UmeAlarmHistory, key)
                if existing is None:
                    existing = UmeAlarmHistory(alarm_key=key, first_seen_at=touch_ts)
                    db.add(existing)
                    inserted += 1
                else:
                    updated += 1
            else:
                existing = db.get(UmeAlarmCurrent, key)
                if existing is None:
                    existing = UmeAlarmCurrent(alarm_key=key, first_seen_at=touch_ts)
                    db.add(existing)
                    inserted += 1
                else:
                    updated += 1
            existing.ne_id = _s(_derive_ne_id_from_alarm(alarm))
            existing.object_name = _s(_pick(alarm, "objectName", "object-name"))
            existing.event_type = _s(_pick(alarm, "eventType", "event-type"))
            existing.native_probable_cause = _s(_pick(alarm, "nativeProbableCause", "native-probable-cause"))
            existing.perceived_severity = _s(_pick(alarm, "perceivedSeverity", "perceived-severity"))
            existing.is_cleared = _s(_pick(alarm, "isCleared", "is-cleared"))
            existing.time_created = _s(_pick(alarm, "timeCreated", "time-created"))
            existing.root_cause_alarm_indication = _s(
                _pick(alarm, "rootCauseAlarmIndication", "root-cause-alarm-indication")
            )
            existing.last_seen_at = touch_ts
            existing.raw_json = json.dumps(alarm, ensure_ascii=False, default=str)

        iterator_500_as_end = bool(getattr(settings, "ume_iterator_500_as_end", True))
        pages, meta = _collect_marker_pages(
            lambda marker: client.get_alarms(is_uncleared=is_uncleared, limit=page_size, marker=marker),
            max_pages=max_pages,
            iterator_500_as_end=iterator_500_as_end,
        )
        sync_batch_ts = _utc_now_naive()
        for rows in pages:
            pulled += len(rows)
            for alarm in rows:
                upsert_alarm(alarm, touch_ts=sync_batch_ts)
        db.flush()
        page_no = int(meta.get("page_count") or 0)
        next_marker = str(meta.get("last_marker") or "")
        is_end_of_reply = bool(meta.get("is_end_of_reply"))
        graceful_end_by_iterator_error = bool(meta.get("graceful_end_by_iterator_error"))
        paging_note = str(meta.get("paging_note") or "")
        warnings = [str(x) for x in (meta.get("warnings") or []) if str(x)]

        if not is_uncleared and _snapshot_reconcile_ok(meta):
            deleted_stale_current = int(
                db.query(UmeAlarmCurrent)
                .filter(UmeAlarmCurrent.last_seen_at < sync_batch_ts)
                .delete(synchronize_session=False)
            )

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
                "current_snapshot_reconcile": (not is_uncleared) and _snapshot_reconcile_ok(meta),
            },
            ensure_ascii=False,
        )

        job.status = "done"
    except Exception as exc:
        msg = str(exc)[:1024]
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
                "current_snapshot_reconcile": (not is_uncleared) and _snapshot_reconcile_ok(meta),
            },
            ensure_ascii=False,
        )
        db.commit()
        db.refresh(job)
        db.refresh(batch)
    return job, batch


def sync_alarms_current(db: Session, client: UMEClient, *, trigger_mode: str = "manual") -> tuple[UmeSyncJob, UmeAlarmBatch]:
    return _sync_alarms_common(db, client, is_uncleared=False, trigger_mode=trigger_mode)


def sync_alarms_history_full(
    db: Session, client: UMEClient, *, trigger_mode: str = "manual"
) -> tuple[UmeSyncJob, UmeAlarmBatch]:
    return _sync_alarms_common(db, client, is_uncleared=True, trigger_mode=trigger_mode)
