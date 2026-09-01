"""Auth, users, audit logs, and API token routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from .auth_cookies import clear_auth_cookies, read_refresh_cookie, set_auth_cookies
from .auth_deps import AuthContext, require_admin, require_user
from .auth_schemas import (
    ApiTokenCreateRequest,
    ApiTokenUpdateRequest,
    ChangePasswordRequest,
    LoginRequest,
    RefreshRequest,
    UserCreateRequest,
    UserUpdateRequest,
)
from .auth_service import (
    authenticate_user,
    change_password,
    create_api_token,
    create_user,
    list_api_tokens,
    list_audit_logs,
    list_auth_sessions,
    list_users,
    login_issue_token,
    refresh_login_tokens,
    revoke_api_token,
    revoke_auth_session_for_user,
    revoke_auth_sessions,
    update_api_token,
    update_user,
    user_public,
    write_audit,
)
from .auth_rate_limit import (
    clear_login_failures,
    login_lock_remaining,
    register_login_failure,
)
from .db import get_db

router = APIRouter(tags=["auth"])


def _client_meta(request: Request) -> tuple[str, str]:
    ip = str(request.client.host if request.client else "")
    ua = str(request.headers.get("user-agent") or "")[:512]
    return ip, ua


def _token_response(response: Response, request: Request, out: dict[str, Any]) -> dict[str, Any]:
    access = str(out.get("access_token") or "")
    refresh = str(out.get("refresh_token") or "")
    if access and refresh:
        set_auth_cookies(response, access_token=access, refresh_token=refresh, request=request)
    # Keep tokens in JSON for API clients / scripts; browsers rely on HttpOnly cookies.
    return out


@router.post("/v1/auth/login")
def api_login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    from fastapi import HTTPException

    ip, ua = _client_meta(request)
    locked = login_lock_remaining(body.username, ip)
    if locked > 0:
        write_audit(
            db,
            action="auth.login_locked",
            actor_username=str(body.username or "").strip(),
            method="POST",
            path="/v1/auth/login",
            status_code=429,
            client_ip=ip,
            user_agent=ua,
            detail={"retry_after_sec": int(locked)},
        )
        raise HTTPException(
            status_code=429,
            detail={"error": "login_locked", "retry_after_sec": int(locked)},
        )
    user = authenticate_user(db, body.username, body.password)
    if user is None:
        remaining = register_login_failure(body.username, ip)
        write_audit(
            db,
            action="auth.login_failed",
            actor_username=str(body.username or "").strip(),
            method="POST",
            path="/v1/auth/login",
            status_code=401,
            client_ip=ip,
            user_agent=ua,
            detail={"locked": remaining > 0, "retry_after_sec": int(remaining)},
        )
        if remaining > 0:
            raise HTTPException(
                status_code=429,
                detail={"error": "login_locked", "retry_after_sec": int(remaining)},
            )
        raise HTTPException(status_code=401, detail="invalid_credentials")
    clear_login_failures(body.username, ip)
    out = login_issue_token(db, user, client_ip=ip, user_agent=ua)
    write_audit(
        db,
        action="auth.login",
        actor_user_id=user.id,
        actor_username=user.username,
        method="POST",
        path="/v1/auth/login",
        status_code=200,
        client_ip=ip,
        user_agent=ua,
        detail={"role": user.role},
    )
    return _token_response(response, request, out)


@router.post("/v1/auth/refresh")
def api_refresh(
    body: RefreshRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    from fastapi import HTTPException

    ip, ua = _client_meta(request)
    refresh = str(body.refresh_token or "").strip() or read_refresh_cookie(request)
    if not refresh:
        raise HTTPException(status_code=401, detail="invalid_refresh_token")
    try:
        out = refresh_login_tokens(db, refresh_token=refresh, client_ip=ip, user_agent=ua)
    except Exception:
        write_audit(
            db,
            action="auth.refresh_failed",
            method="POST",
            path="/v1/auth/refresh",
            status_code=401,
            client_ip=ip,
            user_agent=ua,
            detail={},
        )
        raise
    # Successful silent token refresh is not an operator action — skip audit.
    return _token_response(response, request, out)


@router.post("/v1/auth/logout")
def api_logout(
    request: Request,
    response: Response,
    ctx: Annotated[AuthContext, Depends(require_user)],
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    ip, ua = _client_meta(request)
    revoked = 0
    if ctx.auth_via == "jwt" and ctx.session_jti:
        revoked = revoke_auth_sessions(db, user_id=str(ctx.user.id), only_jti=ctx.session_jti)
    closed_webcrt = 0
    try:
        from .webcrt_session_registry import close_sessions_for_user

        closed_webcrt = close_sessions_for_user(str(ctx.user.id), reason="auth_logout")
    except Exception:
        pass
    clear_auth_cookies(response, request=request)
    write_audit(
        db,
        action="auth.logout",
        actor_user_id=ctx.user.id,
        actor_username=ctx.user.username,
        method="POST",
        path="/v1/auth/logout",
        status_code=200,
        client_ip=ip,
        user_agent=ua,
        detail={"auth_via": ctx.auth_via, "revoked": revoked, "webcrt_closed": closed_webcrt},
    )
    return {"ok": True, "revoked": revoked, "webcrt_closed": closed_webcrt}


@router.get("/v1/auth/sessions")
def api_list_sessions(ctx: Annotated[AuthContext, Depends(require_user)], db: Session = Depends(get_db)) -> dict[str, Any]:
    items = list_auth_sessions(db, user_id=str(ctx.user.id), current_jti=ctx.session_jti)
    return {"items": items, "total": len(items)}


@router.delete("/v1/auth/sessions/{session_id}")
def api_revoke_session(
    session_id: str,
    request: Request,
    ctx: Annotated[AuthContext, Depends(require_user)],
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    from fastapi import HTTPException

    ok = revoke_auth_session_for_user(
        db,
        user_id=str(ctx.user.id),
        session_id=session_id,
        current_jti=ctx.session_jti,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="session_not_found")
    ip, ua = _client_meta(request)
    write_audit(
        db,
        action="auth.session_revoke",
        actor_user_id=ctx.user.id,
        actor_username=ctx.user.username,
        method="DELETE",
        path=f"/v1/auth/sessions/{session_id}",
        status_code=200,
        client_ip=ip,
        user_agent=ua,
        detail={"session_id": session_id, "current": session_id == ctx.session_jti},
    )
    return {"ok": True, "revoked": True, "session_id": session_id}


@router.post("/v1/auth/sessions/revoke-others")
def api_revoke_other_sessions(
    request: Request,
    ctx: Annotated[AuthContext, Depends(require_user)],
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if not ctx.session_jti:
        # API-token auth has no JWT session to keep.
        n = revoke_auth_sessions(db, user_id=str(ctx.user.id))
    else:
        n = revoke_auth_sessions(db, user_id=str(ctx.user.id), except_jti=ctx.session_jti)
    ip, ua = _client_meta(request)
    write_audit(
        db,
        action="auth.session_revoke_others",
        actor_user_id=ctx.user.id,
        actor_username=ctx.user.username,
        method="POST",
        path="/v1/auth/sessions/revoke-others",
        status_code=200,
        client_ip=ip,
        user_agent=ua,
        detail={"revoked": n},
    )
    return {"ok": True, "revoked": n}


@router.get("/v1/auth/me")
def api_me(ctx: Annotated[AuthContext, Depends(require_user)]) -> dict[str, Any]:
    from .auth_scopes import ALL_SCOPES

    return {
        "user": user_public(ctx.user),
        "auth_via": ctx.auth_via,
        "scopes": sorted(ctx.scopes),
        "all_scopes": sorted(ALL_SCOPES),
    }


@router.post("/v1/auth/change-password")
def api_change_password(
    body: ChangePasswordRequest,
    request: Request,
    ctx: Annotated[AuthContext, Depends(require_user)],
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    change_password(
        db,
        user=ctx.user,
        old_password=body.old_password,
        new_password=body.new_password,
        keep_jti=ctx.session_jti or None,
    )
    ip, ua = _client_meta(request)
    write_audit(
        db,
        action="auth.change_password",
        actor_user_id=ctx.user.id,
        actor_username=ctx.user.username,
        method="POST",
        path="/v1/auth/change-password",
        status_code=200,
        client_ip=ip,
        user_agent=ua,
        detail={},
    )
    return {"ok": True}


@router.get("/v1/users")
def api_list_users(ctx: Annotated[AuthContext, Depends(require_admin)], db: Session = Depends(get_db)) -> dict[str, Any]:
    del ctx
    return {"items": list_users(db)}


@router.post("/v1/users")
def api_create_user(
    body: UserCreateRequest,
    request: Request,
    ctx: Annotated[AuthContext, Depends(require_admin)],
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    user = create_user(
        db,
        username=body.username,
        password=body.password,
        role=body.role,
        actor=ctx.user,
        scopes=body.scopes,
    )
    ip, ua = _client_meta(request)
    write_audit(
        db,
        action="users.create",
        actor_user_id=ctx.user.id,
        actor_username=ctx.user.username,
        method="POST",
        path="/v1/users",
        status_code=200,
        client_ip=ip,
        user_agent=ua,
        detail={"target_username": user.username, "role": user.role},
    )
    return {"user": user_public(user)}


@router.patch("/v1/users/{user_id}")
def api_update_user(
    user_id: str,
    body: UserUpdateRequest,
    request: Request,
    ctx: Annotated[AuthContext, Depends(require_admin)],
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    user = update_user(
        db,
        user_id=user_id,
        actor=ctx.user,
        is_active=body.is_active,
        role=body.role,
        password=body.password,
        scopes=body.scopes,
    )
    ip, ua = _client_meta(request)
    write_audit(
        db,
        action="users.update",
        actor_user_id=ctx.user.id,
        actor_username=ctx.user.username,
        method="PATCH",
        path=f"/v1/users/{user_id}",
        status_code=200,
        client_ip=ip,
        user_agent=ua,
        detail={
            "target_username": user.username,
            "is_active": user.is_active,
            "role": user.role,
            "password_reset": body.password is not None,
        },
    )
    return {"user": user_public(user)}


@router.get("/v1/audit-logs")
def api_audit_logs(
    ctx: Annotated[AuthContext, Depends(require_user)],
    db: Session = Depends(get_db),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    username: str = Query(default=""),
    action: str = Query(default=""),
    exclude_noise: bool = Query(default=True),
) -> dict[str, Any]:
    return list_audit_logs(
        db,
        actor=ctx.user,
        page=page,
        page_size=page_size,
        username=username,
        action=action,
        exclude_noise=exclude_noise,
    )


@router.get("/v1/api-tokens")
def api_list_tokens(
    ctx: Annotated[AuthContext, Depends(require_user)],
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    user_id = None if ctx.user.role == "admin" else ctx.user.id
    return {"items": list_api_tokens(db, user_id=user_id)}


@router.post("/v1/api-tokens")
def api_create_token(
    body: ApiTokenCreateRequest,
    request: Request,
    ctx: Annotated[AuthContext, Depends(require_user)],
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    from .auth_service import get_user_by_id

    target = ctx.user
    target_user_id = str(body.user_id or "").strip()
    if target_user_id and target_user_id != ctx.user.id:
        if ctx.user.role != "admin":
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail="admin_required")
        other = get_user_by_id(db, target_user_id)
        if other is None or not other.is_active:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="user_not_found")
        target = other

    expires_in_days = body.expires_in_days
    if expires_in_days is None:
        expires_in_days = 90
    row, plaintext = create_api_token(
        db,
        user=target,
        name=body.name,
        expires_in_days=expires_in_days,
        scopes=body.scopes,
    )
    ip, ua = _client_meta(request)
    write_audit(
        db,
        action="api_tokens.create",
        actor_user_id=ctx.user.id,
        actor_username=ctx.user.username,
        method="POST",
        path="/v1/api-tokens",
        status_code=200,
        client_ip=ip,
        user_agent=ua,
        detail={
            "token_id": row.id,
            "name": row.name,
            "owner_user_id": target.id,
            "owner_username": target.username,
            "scopes": getattr(row, "scopes", None) or [],
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        },
    )
    return {
        "token": {
            "id": row.id,
            "name": row.name,
            "user_id": row.user_id,
            "username": target.username,
            "scopes": getattr(row, "scopes", None) or [],
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "token": plaintext,
        }
    }


@router.patch("/v1/api-tokens/{token_id}")
def api_update_token(
    token_id: str,
    body: ApiTokenUpdateRequest,
    request: Request,
    ctx: Annotated[AuthContext, Depends(require_user)],
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if body.name is None and body.scopes is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="nothing_to_update")
    row = update_api_token(
        db,
        token_id=token_id,
        actor=ctx.user,
        name=body.name,
        scopes=body.scopes,
    )
    ip, ua = _client_meta(request)
    write_audit(
        db,
        action="api_tokens.update",
        actor_user_id=ctx.user.id,
        actor_username=ctx.user.username,
        method="PATCH",
        path=f"/v1/api-tokens/{token_id}",
        status_code=200,
        client_ip=ip,
        user_agent=ua,
        detail={
            "token_id": row.id,
            "name": row.name,
            "scopes": getattr(row, "scopes", None) or [],
        },
    )
    from .auth_service import _token_public

    return {"token": _token_public(db, row)}


@router.delete("/v1/api-tokens/{token_id}")
def api_revoke_token(
    token_id: str,
    request: Request,
    ctx: Annotated[AuthContext, Depends(require_user)],
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = revoke_api_token(db, token_id=token_id, actor=ctx.user)
    ip, ua = _client_meta(request)
    write_audit(
        db,
        action="api_tokens.revoke",
        actor_user_id=ctx.user.id,
        actor_username=ctx.user.username,
        method="DELETE",
        path=f"/v1/api-tokens/{token_id}",
        status_code=200,
        client_ip=ip,
        user_agent=ua,
        detail={"token_id": row.id, "name": row.name},
    )
    return {"ok": True, "id": row.id}
