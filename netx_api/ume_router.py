"""UME REST routes aggregator (token / key-alert / sync / inventory / alarms)."""
from __future__ import annotations

from fastapi import APIRouter

from .ume_alarms_router import (
    _extract_ume_raw_group_field,
    _serialize_ume_alarm_raw_row,
    router as alarms_router,
    ume_alarms_fields,
)
from .ume_inventory_router import router as inventory_router
from .ume_key_alert_router import router as key_alert_router
from .ume_sync_router import router as sync_router
from .ume_token_router import router as token_router

router = APIRouter(tags=["ume"])
router.include_router(token_router)
router.include_router(key_alert_router)
router.include_router(sync_router)
router.include_router(inventory_router)
router.include_router(alarms_router)

__all__ = [
    "_extract_ume_raw_group_field",
    "_serialize_ume_alarm_raw_row",
    "router",
    "ume_alarms_fields",
]
