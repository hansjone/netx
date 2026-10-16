"""HTTP auth gate + request audit middleware (pure ASGI — safe with Mount /mcp)."""

from __future__ import annotations

import logging
import time

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .auth_deps import resolve_user_from_token
from .auth_scopes import has_scope, required_scope_for_request
from .config import settings
from .db import SessionLocal

_log = logging.getLogger("netx.auth.mw")

_PUBLIC_EXACT = frozenset(
    {
        "/",
        "/health",
        "/health/live",
        "/health/ready",
        "/metrics",
        "/metrics/json",
        "/v1/metrics/json",
        "/favicon.ico",
        "/v1/auth/login",
        "/v1/auth/refresh",
    }
)
_PUBLIC_PREFIXES = (
    "/assets",
)


def _is_api_auth_path(path: str) -> bool:
    p = str(path or "")
    if p.startswith("/v1/"):
        return True
    # /mcp has its own Bearer gate (mcp_remote.mount.McpAuthMiddleware).
    if p in ("/metrics", "/metrics/json", "/v1/metrics/json", "/openapi.json"):
        return True
    if p.startswith("/docs") or p.startswith("/redoc"):
        return True
    return False


_PASSWORD_CHANGE_ALLOW = frozenset(
    {
        ("GET", "/v1/auth/me"),
        ("POST", "/v1/auth/change-password"),
        ("POST", "/v1/auth/logout"),
        ("GET", "/v1/auth/sessions"),
    }
)


def _docs_public() -> bool:
    return bool(getattr(settings, "docs_enabled", False))


def _is_public(path: str) -> bool:
    p = str(path or "")
    if p in _PUBLIC_EXACT:
        return True
    if _docs_public() and (
        p in ("/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc")
        or p.startswith("/docs")
        or p.startswith("/redoc")
    ):
        return True
    if any(p.startswith(pref) for pref in _PUBLIC_PREFIXES):
        return True
    if not _is_api_auth_path(p):
        return True
    return False


def _is_mcp_path(path: str) -> bool:
    p = str(path or "")
    return p == "/mcp" or p.startswith("/mcp/")


def _client_ip(request: Request) -> str:
    from .client_ip import resolve_client_ip

    return resolve_client_ip(request)


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


class AuthAuditMiddleware:
    """Pure ASGI auth + audit. BaseHTTPMiddleware breaks Streamable HTTP under Mount."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        method = request.method.upper()
        path = request.url.path

        # MCP is authenticated inside the mounted sub-app; never wrap its body.
        if _is_mcp_path(path):
            await self.app(scope, receive, send)
            return

        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        if not bool(settings.auth_enabled) or _is_public(path):
            await self.app(scope, receive, send)
            return

        if path.startswith("/v1/webcrt/") and path.endswith("/ws"):
            await self.app(scope, receive, send)
            return

        token = ""
        auth = str(request.headers.get("authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        if not token:
            from .auth_cookies import read_access_cookie

            token = read_access_cookie(request)

        db = SessionLocal()
        try:
            resolved = resolve_user_from_token(db, token) if token else None
            if resolved is None:
                await JSONResponse(status_code=401, content={"detail": "unauthorized"})(
                    scope, receive, send
                )
                return
            user, via, scopes, token_id, jti = resolved
            if bool(getattr(user, "must_change_password", False)):
                allow_key = (method, path.rstrip("/") if len(path) > 1 else path)
                if allow_key not in _PASSWORD_CHANGE_ALLOW:
                    await JSONResponse(
                        status_code=403,
                        content={"detail": "password_change_required"},
                    )(scope, receive, send)
                    return
            need = required_scope_for_request(method, path)
            if need and not has_scope(scopes, need):
                await JSONResponse(
                    status_code=403,
                    content={
                        "detail": {
                            "error": "insufficient_scope",
                            "required": [need],
                            "granted": sorted(scopes),
                        }
                    },
                )(scope, receive, send)
                return
            request.state.auth_user = user
            request.state.auth_via = via
            request.state.auth_scopes = scopes
            request.state.auth_api_token_id = token_id
            request.state.auth_session_jti = jti
            actor_id = str(user.id)
            actor_name = str(user.username)
            auth_via = via
        except Exception:
            _log.exception("auth middleware failure path=%s", path)
            await JSONResponse(status_code=500, content={"detail": "auth_middleware_error"})(
                scope, receive, send
            )
            return
        finally:
            db.close()

        started = time.perf_counter()
        status_code = 0

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status") or 0)
            await send(message)

        await self.app(scope, receive, send_wrapper)
        try:
            from .audit_async import enqueue_audit

            enqueue_audit(
                action=_action_for(method, path),
                actor_user_id=actor_id,
                actor_username=actor_name,
                method=method,
                path=path,
                status_code=int(status_code or 0),
                client_ip=_client_ip(request),
                user_agent=str(request.headers.get("user-agent") or "")[:512],
                detail={
                    "auth_via": auth_via,
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                },
            )
        except Exception:
            _log.exception("audit enqueue after request failed path=%s", path)
