"""Timezone helpers. DB columns store naive UTC; keep values naive for comparisons."""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
