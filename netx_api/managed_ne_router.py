from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from .auth_deps import AuthContext, require_user
from .auth_service import write_audit
from .db import get_db
from .device_types import SUPPORTED_VENDORS
from .ne_connect import schedule_connect_tests
from .ne_crypto import credentials_configured
from .ne_exec import execute_managed_ne_commands, execute_managed_ne_commands_batch
from .ne_schemas import (
    BatchAccountApplyRequest,
    BatchHopApplyRequest,
    ConnectTestRequest,
    ManagedNeCreate,
    ManagedNeExecBatchRequest,
    ManagedNeExecRequest,
    ManagedNeUpdate,
)
from .ne_service import (
    batch_apply_account,
    batch_apply_hop_proxy,
    build_managed_ne_import_template,
    create_managed_ne,
    batch_delete_managed_ne,
    delete_ume_synced_managed_ne,
    delete_managed_ne,
    get_ids_by_tag,
    get_managed_ne,
    get_managed_ne_stats,
    import_managed_ne,
    list_managed_ne,
    sync_ume_inventory_to_managed_ne,
    update_managed_ne,
)
from .models import ManagedNE

router = APIRouter(prefix="/v1/managed-ne", tags=["managed-ne"])


def _actor(ctx: AuthContext | None = None, request: Request | None = None) -> tuple[str, str]:
    if ctx is not None and ctx.user is not None:
        return str(ctx.user.id or ""), str(ctx.user.username or "")
    if request is not None:
        user = getattr(request.state, "auth_user", None)
        if user:
            return str(getattr(user, "id", "") or ""), str(getattr(user, "username", "") or "")
    return "", ""


@router.get("")
def api_list_managed_ne(
    keyword: str | None = Query(default=None),
    vendor: str | None = Query(default=None),
    connect_status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    return list_managed_ne(
        db,
        keyword=keyword,
        vendor=vendor,
        connect_status=connect_status,
        page=page,
        page_size=page_size,
    )


@router.get("/meta/device-types")
def api_device_types():
    # Include generic/linux so LLDP/WebCRT placeholders can be edited without a bogus select value.
    from .device_types import WEBCRT_DEVICE_TYPES

    return {"device_types": list(WEBCRT_DEVICE_TYPES), "vendors": list(SUPPORTED_VENDORS)}


@router.get("/meta/credentials-configured")
def api_credentials_configured():
    return {"configured": credentials_configured()}


@router.get("/meta/stats")
def api_managed_ne_stats(db: Session = Depends(get_db)):
    return get_managed_ne_stats(db)


@router.get("/meta/ids-by-tag")
def api_ids_by_tag(tag: str | None = Query(default=None), db: Session = Depends(get_db)):
    """Return all NE ids that carry the given tag (or every id when tag is omitted)."""
    return {"ids": get_ids_by_tag(db, tag)}


@router.post("")
def api_create_managed_ne(body: ManagedNeCreate, db: Session = Depends(get_db)):
    return create_managed_ne(db, body).model_dump()


@router.get("/import/template")
def api_managed_ne_import_template(format: str = Query(default="xlsx")):
    filename, payload, media_type = build_managed_ne_import_template(format)
    return Response(
        content=payload,
        media_type=media_type,
        headers={"content-disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import")
async def api_import_managed_ne(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty_file")
    return import_managed_ne(db, content, file.filename or "import.xlsx").model_dump()


@router.post("/batch-hop")
def api_batch_apply_hop(body: BatchHopApplyRequest, db: Session = Depends(get_db)):
    return batch_apply_hop_proxy(db, body.ids, body.hop)


@router.post("/batch-account")
def api_batch_apply_account(body: BatchAccountApplyRequest, db: Session = Depends(get_db)):
    return batch_apply_account(db, body.ids, body.account)


@router.post("/batch-delete")
def api_batch_delete_managed_ne(body: ConnectTestRequest, db: Session = Depends(get_db)):
    return batch_delete_managed_ne(db, body.ids)


@router.post("/ume-sync")
def api_sync_ume_inventory_to_managed_ne(db: Session = Depends(get_db)):
    return sync_ume_inventory_to_managed_ne(db).model_dump()


@router.delete("/ume-sync")
def api_delete_ume_synced_managed_ne(db: Session = Depends(get_db)):
    return delete_ume_synced_managed_ne(db).model_dump()


@router.post("/exec")
def api_exec_managed_ne(
    body: ManagedNeExecRequest,
    ctx: Annotated[AuthContext, Depends(require_user)],
    db: Session = Depends(get_db),
):
    """Login to a managed NE or UME inventory NE and run read-only CLI (show/display/ping/traceroute)."""
    uid, uname = _actor(ctx)
    out = execute_managed_ne_commands(
        db,
        body.commands,
        ne_id=body.ne_id,
        ume_ne_id=body.ume_ne_id,
        read_timeout_sec=body.read_timeout_sec,
    )
    device = out.get("device") if isinstance(out.get("device"), dict) else {}
    write_audit(
        db,
        action="ne.exec",
        actor_user_id=uid,
        actor_username=uname,
        method="POST",
        path="/v1/managed-ne/exec",
        status_code=200 if out.get("ok") else 502,
        detail={
            "ne_id": body.ne_id or "",
            "ume_ne_id": body.ume_ne_id or "",
            "ne_name": str(device.get("name") or device.get("ne_name") or ""),
            "ne_ip": str(device.get("ip_address") or device.get("ip") or device.get("mgmt_ip") or ""),
            "commands": list(out.get("commands") or body.commands or [])[:20],
            "ok": bool(out.get("ok")),
            "error": str(out.get("error") or "")[:500],
            "output_len": len(str(out.get("output") or "")),
        },
    )
    return out


@router.post("/exec-batch")
def api_exec_managed_ne_batch(
    body: ManagedNeExecBatchRequest,
    ctx: Annotated[AuthContext, Depends(require_user)],
    db: Session = Depends(get_db),
):
    """Run read-only CLI on many NEs concurrently (field multi-NE sweeps)."""
    uid, uname = _actor(ctx)
    targets = None
    if body.targets:
        targets = [t.model_dump() for t in body.targets]
    out = execute_managed_ne_commands_batch(
        targets=targets,
        ne_ids=body.ne_ids,
        ume_ne_ids=body.ume_ne_ids,
        commands=body.commands,
        read_timeout_sec=body.read_timeout_sec,
        concurrency=body.concurrency,
    )
    items = out.get("items") if isinstance(out, dict) else None
    ok_n = 0
    fail_n = 0
    if isinstance(items, list):
        for row in items:
            if isinstance(row, dict) and row.get("ok"):
                ok_n += 1
            else:
                fail_n += 1
    write_audit(
        db,
        action="ne.exec_batch",
        actor_user_id=uid,
        actor_username=uname,
        method="POST",
        path="/v1/managed-ne/exec-batch",
        status_code=200,
        detail={
            "ne_ids": list(body.ne_ids or [])[:100],
            "ume_ne_ids": list(body.ume_ne_ids or [])[:100],
            "target_count": len(targets or []) + len(body.ne_ids or []) + len(body.ume_ne_ids or []),
            "commands": list(body.commands or [])[:20],
            "ok_count": ok_n,
            "fail_count": fail_n,
        },
    )
    return out


@router.post("/connect-test")
def api_connect_test(body: ConnectTestRequest, db: Session = Depends(get_db)):
    ids = [str(x).strip() for x in body.ids if str(x).strip()]
    if not ids:
        raise HTTPException(status_code=400, detail="ids_required")
    rows = db.query(ManagedNE).filter(ManagedNE.id.in_(ids)).all()
    found_ids = {str(r.id) for r in rows}
    missing = [x for x in ids if x not in found_ids]
    if missing:
        raise HTTPException(status_code=404, detail=f"managed_ne_not_found: {','.join(missing[:5])}")
    submitted = schedule_connect_tests(ids)
    return {"ok": True, "submitted": submitted}


@router.get("/{ne_id}")
def api_get_managed_ne(ne_id: str, db: Session = Depends(get_db)):
    return get_managed_ne(db, ne_id).model_dump()


@router.patch("/{ne_id}")
def api_update_managed_ne(ne_id: str, body: ManagedNeUpdate, db: Session = Depends(get_db)):
    return update_managed_ne(db, ne_id, body).model_dump()


@router.delete("/{ne_id}")
def api_delete_managed_ne(ne_id: str, db: Session = Depends(get_db)):
    return delete_managed_ne(db, ne_id)
