from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .cli_schemas import (
    CliConnectProfileCreate,
    CliConnectProfileUpdate,
    UmeCliOverrideUpdate,
    UmeConnectTestRequest,
)
from .cli_service import (
    cli_meta,
    create_cli_profile,
    delete_cli_profile,
    get_cli_profile,
    get_ume_cli_override,
    list_cli_target_filter_options,
    list_cli_profiles,
    list_cli_targets,
    set_default_cli_profile,
    update_cli_profile,
    upsert_ume_cli_override,
)
from .table_filters import TableFilterParams, table_filter_params
from .db import get_db
from .ne_connect import schedule_ume_connect_tests

router = APIRouter(prefix="/v1/cli", tags=["cli"])


@router.get("/meta")
def api_cli_meta(db: Session = Depends(get_db)):
    return cli_meta(db)


@router.get("/profiles")
def api_list_cli_profiles(db: Session = Depends(get_db)):
    return {"items": [x.model_dump() for x in list_cli_profiles(db)]}


@router.post("/profiles")
def api_create_cli_profile(body: CliConnectProfileCreate, db: Session = Depends(get_db)):
    return create_cli_profile(db, body).model_dump()


@router.get("/profiles/{profile_id}")
def api_get_cli_profile(profile_id: str, db: Session = Depends(get_db)):
    return get_cli_profile(db, profile_id).model_dump()


@router.patch("/profiles/{profile_id}")
def api_update_cli_profile(profile_id: str, body: CliConnectProfileUpdate, db: Session = Depends(get_db)):
    return update_cli_profile(db, profile_id, body).model_dump()


@router.post("/profiles/{profile_id}/default")
def api_set_default_cli_profile(profile_id: str, db: Session = Depends(get_db)):
    return set_default_cli_profile(db, profile_id).model_dump()


@router.delete("/profiles/{profile_id}")
def api_delete_cli_profile(profile_id: str, db: Session = Depends(get_db)):
    return delete_cli_profile(db, profile_id)


@router.get("/targets")
def api_list_cli_targets(
    source: str = Query(default="all"),
    keyword: str | None = Query(default=None),
    source_filter: list[str] | None = Query(default=None),
    source_filter_mode: str | None = Query(default=None),
    name_filter: list[str] | None = Query(default=None),
    name_filter_mode: str | None = Query(default=None),
    ip_filter: list[str] | None = Query(default=None),
    ip_filter_mode: str | None = Query(default=None),
    vendor_filter: list[str] | None = Query(default=None),
    vendor_filter_mode: str | None = Query(default=None),
    device_type_filter: list[str] | None = Query(default=None),
    device_type_filter_mode: str | None = Query(default=None),
    connect_status_filter: list[str] | None = Query(default=None),
    connect_status_filter_mode: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    table_filters: TableFilterParams = Depends(table_filter_params),
    db: Session = Depends(get_db),
):
    if table_filters.field:
        return list_cli_target_filter_options(db, field=table_filters.field, source=source, keyword=keyword)
    filters = table_filters.filters
    if set(filters) - {"source", "name", "ip", "vendor", "deviceType", "connectStatus"}:
        raise HTTPException(status_code=400, detail="invalid_filter_field")
    return list_cli_targets(
        db,
        source=source,
        keyword=keyword,
        source_filter=filters.get("source", {}).get("values", source_filter),
        source_filter_mode=filters.get("source", {}).get("mode", source_filter_mode),
        name_filter=filters.get("name", {}).get("values", name_filter),
        name_filter_mode=filters.get("name", {}).get("mode", name_filter_mode),
        ip_filter=filters.get("ip", {}).get("values", ip_filter),
        ip_filter_mode=filters.get("ip", {}).get("mode", ip_filter_mode),
        vendor_filter=filters.get("vendor", {}).get("values", vendor_filter),
        vendor_filter_mode=filters.get("vendor", {}).get("mode", vendor_filter_mode),
        device_type_filter=filters.get("deviceType", {}).get("values", device_type_filter),
        device_type_filter_mode=filters.get("deviceType", {}).get("mode", device_type_filter_mode),
        connect_status_filter=filters.get("connectStatus", {}).get("values", connect_status_filter),
        connect_status_filter_mode=filters.get("connectStatus", {}).get("mode", connect_status_filter_mode),
        page=page,
        page_size=page_size,
    )


@router.get("/targets/filter-options")
def api_list_cli_target_filter_options(
    field: str = Query(),
    source: str = Query(default="inventory"),
    keyword: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    return list_cli_target_filter_options(db, field=field, source=source, keyword=keyword)


@router.get("/ume-overrides/{ume_ne_id}")
def api_get_ume_cli_override(ume_ne_id: str, db: Session = Depends(get_db)):
    row = get_ume_cli_override(db, ume_ne_id)
    return row.model_dump() if row else None


@router.patch("/ume-overrides/{ume_ne_id}")
def api_upsert_ume_cli_override(ume_ne_id: str, body: UmeCliOverrideUpdate, db: Session = Depends(get_db)):
    return upsert_ume_cli_override(db, ume_ne_id, body).model_dump()


@router.post("/ume-connect-test")
def api_ume_connect_test(body: UmeConnectTestRequest, db: Session = Depends(get_db)):
    ids = [str(x).strip() for x in body.ume_ne_ids if str(x).strip()]
    submitted = schedule_ume_connect_tests(ids)
    return {"ok": True, "submitted": submitted}
