"""FastAPI dependencies for authenticated / admin-only routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .auth_service import get_user_by_id, resolve_api_token_user
from .auth_tokens import decode_access_token
from .config import settings
from .db import get_db
from .models import AppUser


@dataclass
class AuthContext:
    user: AppUser
    auth_via: str  # jwt | api_token | disabled


def _extract_bearer(request: Request) -> str:
    auth = str(request.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    # WebCRT / tools may pass access_token query
    q = request.query_params.get("access_token")
    return str(q or "").strip()


def resolve_user_from_token(db: Session, token: str) -> tuple[AppUser, str] | None:
    raw = str(token or "").strip()
    if not raw:
        return None
    if raw.startswith("nxt_"):
        user = resolve_api_token_user(db, raw)
        if user is None:
            return None
        return user, "api_token"
    try:
        payload = decode_access_token(raw)
    except Exception:
        return None
    if str(payload.get("typ") or "") not in ("", "access"):
        return None
    user = get_user_by_id(db, str(payload.get("sub") or ""))
    if user is None or not user.is_active:
        return None
    return user, "jwt"


def get_optional_user(
    request: Request,
    db: Session = Depends(get_db),
) -> AuthContext | None:
    if not bool(settings.auth_enabled):
        return None
    token = _extract_bearer(request)
    if not token:
        # Middleware may have already attached user
        cached = getattr(request.state, "auth_user", None)
        if isinstance(cached, AppUser):
            via = str(getattr(request.state, "auth_via", "") or "jwt")
            return AuthContext(user=cached, auth_via=via)
        return None
    resolved = resolve_user_from_token(db, token)
    if resolved is None:
        return None
    user, via = resolved
    request.state.auth_user = user
    request.state.auth_via = via
    return AuthContext(user=user, auth_via=via)


def require_user(
    request: Request,
    db: Session = Depends(get_db),
) -> AuthContext:
    if not bool(settings.auth_enabled):
        # Auth disabled: synthesize a system principal for Depends callers.
        fake = AppUser(
            id="system",
            username="system",
            password_hash="",
            role="admin",
            is_active=True,
            created_by="auth_disabled",
        )
        return AuthContext(user=fake, auth_via="disabled")
    ctx = get_optional_user(request, db)
    if ctx is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    return ctx


def require_admin(ctx: Annotated[AuthContext, Depends(require_user)]) -> AuthContext:
    if ctx.user.role != "admin":
        raise HTTPException(status_code=403, detail="admin_required")
    return ctx
