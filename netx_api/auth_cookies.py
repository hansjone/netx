"""Auth cookie helpers (HttpOnly access + refresh)."""

from __future__ import annotations

from typing import Any

from fastapi import Response
from starlette.requests import Request

from .config import settings

COOKIE_ACCESS = "netx_at"
COOKIE_REFRESH = "netx_rt"


def _cookie_secure(request: Request | None = None) -> bool:
    configured = getattr(settings, "auth_cookie_secure", None)
    if configured is True:
        return True
    if configured is False:
        return False
    # Auto: secure when request is HTTPS (or behind TLS-terminating proxy).
    if request is None:
        return False
    if request.url.scheme == "https":
        return True
    fwd = str(request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return fwd == "https"


def _cookie_samesite() -> str:
    raw = str(getattr(settings, "auth_cookie_samesite", "lax") or "lax").strip().lower()
    if raw in ("lax", "strict", "none"):
        return raw
    return "lax"


def access_cookie_max_age() -> int:
    return max(300, int(getattr(settings, "auth_token_ttl_sec", 3600) or 3600))


def refresh_cookie_max_age() -> int:
    return max(3600, int(getattr(settings, "auth_refresh_ttl_sec", 604800) or 604800))


def set_auth_cookies(
    response: Response,
    *,
    access_token: str,
    refresh_token: str,
    request: Request | None = None,
) -> None:
    if not bool(getattr(settings, "auth_cookie_enabled", True)):
        return
    secure = _cookie_secure(request)
    samesite = _cookie_samesite()
    # SameSite=None requires Secure.
    if samesite == "none":
        secure = True
    common: dict[str, Any] = {
        "httponly": True,
        "secure": secure,
        "samesite": samesite,
        "path": "/",
    }
    response.set_cookie(
        COOKIE_ACCESS,
        access_token,
        max_age=access_cookie_max_age(),
        **common,
    )
    response.set_cookie(
        COOKIE_REFRESH,
        refresh_token,
        max_age=refresh_cookie_max_age(),
        **common,
    )


def clear_auth_cookies(response: Response, *, request: Request | None = None) -> None:
    secure = _cookie_secure(request)
    samesite = _cookie_samesite()
    if samesite == "none":
        secure = True
    for name in (COOKIE_ACCESS, COOKIE_REFRESH):
        response.delete_cookie(name, path="/", secure=secure, httponly=True, samesite=samesite)


def read_access_cookie(request: Request) -> str:
    return str(request.cookies.get(COOKIE_ACCESS) or "").strip()


def read_refresh_cookie(request: Request) -> str:
    return str(request.cookies.get(COOKIE_REFRESH) or "").strip()
