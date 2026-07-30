"""HTTP auth gate + request audit middleware."""

from __future__ import annotations

import logging
import time
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .auth_deps import resolve_user_from_token
from .auth_service import write_audit
from .config import settings
from .db import SessionLocal

_log = logging.getLogger("netx.auth.mw")

_PUBLIC_EXACT = frozenset(
    {
        "/",
        "/health",
        "/openapi.json",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
        "/favicon.ico",
        "/v1/auth/login",
    }
)
_PUBLIC_PREFIXES = (
    "/docs",
    "/redoc",
    "/assets",
)


def _is_public(path: str) -> bool:
    p = str(path or "")
    if p in _PUBLIC_EXACT:
        return True
    return any(p.startswith(pref) for pref in _PUBLIC_PREFIXES)


def _client_ip(request: Request) -> str:
    return str(request.client.host if request.client else "")


def _action_for(method: str, path: str) -> str:
    m = method.upper()
    p = path
    if p.startswith("/v1/auth/"):
        return f"auth.{p.rsplit('/', 1)[-1]}"
    if p.startswith("/v1/users"):
        return f"users.{m.lower()}"
    if p.startswith("/v1/audit-logs"):
        return "audit.list"
    if p.startswith("/v1/api-tokens"):
        return f"api_tokens.{m.lower()}"
    if p.startswith("/v1/webcrt"):
        return f"webcrt.{m.lower()}"
    if "/token" in p:
        return f"ume.token.{m.lower()}"
    return f"http.{m.lower()}"


class AuthAuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.method.upper() == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        if not bool(settings.auth_enabled) or _is_public(path):
            return await call_next(request)

        # WebSocket upgrades are authenticated inside the WS endpoint.
        if path.startswith("/v1/webcrt/") and path.endswith("/ws"):
            return await call_next(request)

        token = ""
        auth = str(request.headers.get("authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        if not token:
            token = str(request.query_params.get("access_token") or "").strip()

        db = SessionLocal()
        try:
            resolved = resolve_user_from_token(db, token) if token else None
            if resolved is None:
                write_audit(
                    db,
                    action="auth.unauthorized",
                    method=request.method,
                    path=path,
                    status_code=401,
                    client_ip=_client_ip(request),
                    user_agent=str(request.headers.get("user-agent") or "")[:512],
                    detail={},
                )
                return JSONResponse(status_code=401, content={"detail": "unauthorized"})
            user, via = resolved
            request.state.auth_user = user
            request.state.auth_via = via
            actor_id = str(user.id)
            actor_name = str(user.username)
            auth_via = via
        except Exception:
            _log.exception("auth middleware failure path=%s", path)
            return JSONResponse(status_code=500, content={"detail": "auth_middleware_error"})
        finally:
            db.close()

        started = time.perf_counter()
        response = await call_next(request)
        try:
            db2 = SessionLocal()
            try:
                write_audit(
                    db2,
                    action=_action_for(request.method, path),
                    actor_user_id=actor_id,
                    actor_username=actor_name,
                    method=request.method,
                    path=path,
                    status_code=int(response.status_code),
                    client_ip=_client_ip(request),
                    user_agent=str(request.headers.get("user-agent") or "")[:512],
                    detail={
                        "auth_via": auth_via,
                        "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    },
                )
            finally:
                db2.close()
        except Exception:
            _log.exception("audit write after request failed path=%s", path)
        return response
