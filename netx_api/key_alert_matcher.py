from __future__ import annotations

import json
import re
import threading
import time
from typing import Any

from sqlalchemy.orm import Session

from .models import UmeInventoryNE, UmeKeyAlertRule
from .ume_sync_service import _derive_ne_id_from_alarm, _is_alarm_cleared, _pick, _s, notification_id_from_norm

_RULE_CACHE_LOCK = threading.Lock()
_RULE_CACHE: list[UmeKeyAlertRule] = []
_RULE_CACHE_LOADED_AT = 0.0
_RULE_CACHE_TTL_S = 30.0

_ITEM_SEP_RE = re.compile(r"[,，;；|\n]+")


def _fold(text: str) -> str:
    """Case-insensitive compare key for keyword matching."""
    return str(text or "").strip().casefold()


def parse_rule_items(text: str) -> list[str]:
    """Split batch rule input (comma/semicolon/newline separated)."""
    raw = str(text or "").strip()
    if not raw:
        return []
    parts = [p.strip() for p in _ITEM_SEP_RE.split(raw) if p.strip()]
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        key = _fold(part)
        if key in seen:
            continue
        seen.add(key)
        out.append(part)
    return out


def normalize_match_type(value: str) -> str:
    mt = str(value or "").strip().lower()
    if mt in {"keyword", "keywords", "desc", "description", "cause"}:
        return "keyword"
    return "notification_id"


def rule_storage_key(*, match_type: str, value: str) -> str:
    mt = normalize_match_type(match_type)
    v = str(value or "").strip()
    if not v:
        raise ValueError("match value is required")
    if mt == "keyword":
        return f"kw:{_fold(v)[:120]}"
    return v[:128]


def rule_match_value(row: UmeKeyAlertRule) -> str:
    mv = str(getattr(row, "match_value", "") or "").strip()
    if mv:
        return mv
    pk = str(row.notification_id or "").strip()
    if pk.startswith("kw:"):
        return pk[3:]
    return pk


def rule_match_type(row: UmeKeyAlertRule) -> str:
    mt = str(getattr(row, "match_type", "") or "").strip().lower()
    if mt in {"keyword", "notification_id"}:
        return mt
    pk = str(row.notification_id or "").strip()
    return "keyword" if pk.startswith("kw:") else "notification_id"


def serialize_rule_ne_types(ne_types: list[str]) -> str:
    cleaned = parse_rule_items(",".join(str(x or "").strip() for x in (ne_types or []) if str(x or "").strip()))
    return json.dumps(cleaned, ensure_ascii=False)


def rule_ne_types(row: UmeKeyAlertRule) -> list[str]:
    raw = str(getattr(row, "ne_types", "") or "").strip()
    if not raw or raw == "[]":
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parse_rule_items(",".join(str(x or "").strip() for x in parsed if str(x or "").strip()))
    except json.JSONDecodeError:
        pass
    return parse_rule_items(raw)


def parse_rule_ne_types_payload(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return parse_rule_items(",".join(str(x or "").strip() for x in raw if str(x or "").strip()))
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return parse_rule_items(",".join(str(x or "").strip() for x in parsed if str(x or "").strip()))
            except json.JSONDecodeError:
                pass
        return parse_rule_items(text)
    return []


def _alarm_ne_type(db: Session, norm: dict[str, Any]) -> str:
    ne_id = _s(_derive_ne_id_from_alarm(norm))
    if not ne_id:
        return ""
    row = db.get(UmeInventoryNE, ne_id)
    if row is None:
        return ""
    return str(row.ne_type or "").strip()


def _ne_type_matches_rule(db: Session, rule: UmeKeyAlertRule, norm: dict[str, Any]) -> bool:
    allowed = rule_ne_types(rule)
    if not allowed:
        return True
    ne_type = _alarm_ne_type(db, norm)
    if not ne_type:
        return False
    allowed_fold = {_fold(x) for x in allowed}
    return _fold(ne_type) in allowed_fold


def invalidate_key_alert_rule_cache() -> None:
    global _RULE_CACHE_LOADED_AT
    with _RULE_CACHE_LOCK:
        _RULE_CACHE.clear()
        _RULE_CACHE_LOADED_AT = 0.0


def _load_enabled_rules(db: Session) -> list[UmeKeyAlertRule]:
    global _RULE_CACHE_LOADED_AT
    now = time.time()
    with _RULE_CACHE_LOCK:
        if _RULE_CACHE and (now - _RULE_CACHE_LOADED_AT) < _RULE_CACHE_TTL_S:
            return list(_RULE_CACHE)
    rows = (
        db.query(UmeKeyAlertRule)
        .filter(UmeKeyAlertRule.enabled == 1)
        .all()
    )
    with _RULE_CACHE_LOCK:
        _RULE_CACHE.clear()
        _RULE_CACHE.extend(rows)
        _RULE_CACHE_LOADED_AT = now
    return list(rows)


def _alarm_search_text(norm: dict[str, Any]) -> str:
    parts = [
        notification_id_from_norm(norm),
        _s(_pick(norm, "nativeProbableCause", "native-probable-cause")),
        _s(_pick(norm, "objectName", "object-name")),
        _s(_pick(norm, "eventType", "event-type")),
    ]
    return " ".join(p for p in parts if p).casefold()


def _keyword_matches(norm: dict[str, Any], keyword: str) -> bool:
    kw = _fold(keyword)
    if not kw:
        return False
    return kw in _alarm_search_text(norm)


def _rule_matches_norm(rule: UmeKeyAlertRule, norm: dict[str, Any]) -> bool:
    mt = rule_match_type(rule)
    mv = rule_match_value(rule)
    if mt == "keyword":
        return _keyword_matches(norm, mv)
    nid = notification_id_from_norm(norm)
    return bool(nid) and nid == mv


def match_key_alert_rule(
    db: Session,
    *,
    norm: dict[str, Any],
    action: str,
) -> UmeKeyAlertRule | None:
    act = str(action or "").strip().lower()
    for rule in _load_enabled_rules(db):
        if not _rule_matches_norm(rule, norm):
            continue
        if not _ne_type_matches_rule(db, rule, norm):
            continue
        if act in {"inserted", "updated"}:
            if _is_alarm_cleared(norm):
                continue
            return rule
        if act == "deleted":
            from .key_alert_config import is_forward_on_clear_enabled

            if is_forward_on_clear_enabled(db):
                return rule
    return None
