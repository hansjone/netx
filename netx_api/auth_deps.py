"""FastAPI dependencies for authenticated / admin-only / scope-gated routes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Callable

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .auth_cookies import read_access_cookie
from .auth_scopes import (
    ALL_SCOPES,
    effective_token_scopes,
    effective_user_scopes,
    has_all_scopes,
    has_scope,
    normalize_scopes,
)
from .auth_service import get_auth_session, get_user_by_id, resolve_api_token_row, touch_auth_session
from .auth_tokens import decode_access_token
from .config import settings
from .db import get_db
from .models import AppUser


@dataclass
class AuthContext:
    user: AppUser
    auth_via: str  # jwt | api_token | disabled
    scopes: frozenset[str] = field(default_factory=frozenset)
    api_token_id: str = ""
    session_jti: str = ""


def _extract_bearer(request: Request) -> str:
    auth = str(request.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    # Prefer Authorization; fall back to HttpOnly access cookie for browser sessions.
    return read_access_cookie(request)


def user_scopes(user: AppUser) -> frozenset[str]:
    return effective_user_scopes(role=str(user.role or "user"), override=getattr(user, "scopes", None) or [])


def resolve_user_from_token(
    db: Session, token: str
) -> tuple[AppUser, str, frozenset[str], str, str] | None:
    """Return (user, via, scopes, api_token_id, session_jti) or None."""
    raw = str(token or "").strip()
    if not raw:
        return None
    if raw.startswith("nxt_"):
        row = resolve_api_token_row(db, raw)
        if row is None:
            return None
        user = get_user_by_id(db, row.user_id)
        if user is None or not user.is_active:
            return None
        scopes = effective_token_scopes(
            user_scopes=user_scopes(user),
            token_scopes=getattr(row, "scopes", None) or [],
        )
        return user, "api_token", scopes, str(row.id), ""
    try:
        payload = decode_access_token(raw)
    except Exception:
        return None
    if str(payload.get("typ") or "") not in ("", "access"):
        return None
    jti = str(payload.get("jti") or "").strip()
    if not jti:
        return None
    sess = get_auth_session(db, jti)
    if sess is None:
        return None
    user = get_user_by_id(db, str(payload.get("sub") or ""))
    if user is None or not user.is_active:
        return None
    if str(sess.user_id) != str(user.id):
        return None
    touch_auth_session(db, jti)
    return user, "jwt", user_scopes(user), "", jti


def get_optional_user(
    request: Request,
    db: Session = Depends(get_db),
) -> AuthContext | None:
    if not bool(settings.auth_enabled):
        return None
    cached = getattr(request.state, "auth_user", None)
    if isinstance(cached, AppUser):
        via = str(getattr(request.state, "auth_via", "") or "jwt")
        scopes = getattr(request.state, "auth_scopes", None)
        if not isinstance(scopes, frozenset):
            scopes = user_scopes(cached)
        token_id = str(getattr(request.state, "auth_api_token_id", "") or "")
        jti = str(getattr(request.state, "auth_session_jti", "") or "")
        return AuthContext(
            user=cached, auth_via=via, scopes=scopes, api_token_id=token_id, session_jti=jti
        )
    token = _extract_bearer(request)
    if not token:
        return None
    resolved = resolve_user_from_token(db, token)
    if resolved is None:
        return None
    user, via, scopes, token_id, jti = resolved
    request.state.auth_user = user
    request.state.auth_via = via
    request.state.auth_scopes = scopes
    request.state.auth_api_token_id = token_id
    request.state.auth_session_jti = jti
    return AuthContext(
        user=user, auth_via=via, scopes=scopes, api_token_id=token_id, session_jti=jti
    )


def require_user(
    request: Request,
    db: Session = Depends(get_db),
) -> AuthContext:
    if not bool(settings.auth_enabled):
        fake = AppUser(
            id="system",
            username="system",
            password_hash="",
            role="admin",
            is_active=True,
            created_by="auth_disabled",
        )
        return AuthContext(user=fake, auth_via="disabled", scopes=ALL_SCOPES)
    ctx = get_optional_user(request, db)
    if ctx is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    return ctx


def require_admin(ctx: Annotated[AuthContext, Depends(require_user)]) -> AuthContext:
    if ctx.user.role != "admin" and not has_scope(ctx.scopes, "admin:users"):
        raise HTTPException(status_code=403, detail="admin_required")
    return ctx


def require_scopes(*needed: str) -> Callable[..., AuthContext]:
    required = normalize_scopes(needed)

    def _dep(ctx: Annotated[AuthContext, Depends(require_user)]) -> AuthContext:
        if not has_all_scopes(ctx.scopes, required):
            raise HTTPException(
                status_code=403,
                detail={"error": "insufficient_scope", "required": required, "granted": sorted(ctx.scopes)},
            )
        return ctx

    return _dep
