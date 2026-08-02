"""Bounded JSON persistence for UME raw payloads."""
from __future__ import annotations

import json
from typing import Any

from .config import settings


def dumps_ume_raw(obj: Any, *, max_bytes: int | None = None) -> str:
    """Serialize UME payloads with a hard byte cap to protect DB/RSS.

    Oversized payloads are truncated as UTF-8 bytes and annotated so operators
    can tell the raw blob is incomplete.
    """
    text = json.dumps(obj, ensure_ascii=False, default=str)
    limit = int(
        max_bytes
        if max_bytes is not None
        else (getattr(settings, "ume_raw_json_max_bytes", 65536) or 65536)
    )
    if limit <= 0:
        return text
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= limit:
        return text
    keep = max(64, limit - 96)
    truncated = raw[:keep].decode("utf-8", errors="replace")
    return f"{truncated}\n/* truncated raw_json {limit} bytes */"
