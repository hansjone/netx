"""Managed NE service facade — CRUD / WebCRT hosts / import / credentials."""
from __future__ import annotations

from .ne_service_common import (
    IMPORT_COLUMNS,
    UME_SYNC_SOURCE,
    UME_SYNC_TAG,
    WEBCRT_SOURCE,
    _normalize_hop_target_auth_mode,
    _normalize_hop_vendor,
    _normalize_protocol,
    _require_crypto,
    get_device_credentials,
    row_to_out,
)
from .ne_service_crud import (
    batch_apply_account,
    batch_apply_hop_proxy,
    batch_delete_managed_ne,
    create_managed_ne,
    delete_managed_ne,
    get_ids_by_tag,
    get_managed_ne,
    get_managed_ne_stats,
    list_managed_ne,
    update_managed_ne,
)
from .ne_service_import import (
    build_managed_ne_import_template,
    delete_ume_synced_managed_ne,
    import_managed_ne,
    sync_ume_inventory_to_managed_ne,
)
from .ne_service_webcrt import (
    upsert_webcrt_managed_ne,
    upsert_webcrt_session_host,
)

__all__ = [
    "IMPORT_COLUMNS",
    "UME_SYNC_SOURCE",
    "UME_SYNC_TAG",
    "WEBCRT_SOURCE",
    "_normalize_hop_target_auth_mode",
    "_normalize_hop_vendor",
    "_normalize_protocol",
    "_require_crypto",
    "batch_apply_account",
    "batch_apply_hop_proxy",
    "batch_delete_managed_ne",
    "build_managed_ne_import_template",
    "create_managed_ne",
    "delete_managed_ne",
    "delete_ume_synced_managed_ne",
    "get_device_credentials",
    "get_ids_by_tag",
    "get_managed_ne",
    "get_managed_ne_stats",
    "import_managed_ne",
    "list_managed_ne",
    "row_to_out",
    "sync_ume_inventory_to_managed_ne",
    "update_managed_ne",
    "upsert_webcrt_managed_ne",
    "upsert_webcrt_session_host",
]
