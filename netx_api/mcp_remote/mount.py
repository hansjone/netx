"""Mount Streamable HTTP MCP at /mcp on the FastAPI app."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from urllib.parse import urlparse

from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.types import ASGIApp, Receive, Scope, Send

from ..auth_deps import resolve_user_from_token
from ..config import settings
from ..db import SessionLocal
from . import server as server_mod
from .context import McpRequestContext, get_app, reset_request_context, set_app, set_request_context
from .registry import parse_groups_param
from .server import build_mcp_server

_MCP_ASGI: ASGIApp | None = None


def _mcp_transport_security() -> TransportSecuritySettings:
    """Host allow-list for MCP DNS-rebinding guard.

    Bearer auth is the real gate. Default: disable DNS-rebinding protection so
    LAN IPs / reverse-proxy Host headers work for remote MCP. Set
    ``NETX_MCP_ALLOWED_HOSTS`` (comma-separated, supports ``host:*``) to turn
    protection back on with an explicit list.
    """
    raw = str(getattr(settings, "mcp_allowed_hosts", "") or "").strip()
    if not raw:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)

    hosts: list[str] = []
    for part in raw.split(","):
        h = part.strip()
        if h:
            hosts.append(h)
    # Always keep loopback + ASGI test hosts when an allow-list is configured.
    for extra in (
        "localhost",
        "localhost:*",
        "127.0.0.1",
        "127.0.0.1:*",
        "[::1]",
        "[::1]:*",
        "test",
        "test:*",
        "testserver",
        "testserver:*",
        "mcp.internal",
        "mcp.internal:*",
    ):
        if extra not in hosts:
            hosts.append(extra)
    # Include configured bind / frontend hosts when they look like hostnames.
    for candidate in (settings.host, getattr(settings, "frontend_url", "")):
        try:
            parsed = urlparse(str(candidate) if "://" in str(candidate) else f"http://{candidate}")
            hostname = (parsed.hostname or "").strip()
        except Exception:
            hostname = ""
        if hostname and hostname not in hosts:
            hosts.append(hostname)
            hosts.append(f"{hostname}:*")
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
    )


def _header_map(scope: Scope) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in scope.get("headers") or []:
        out[key.decode("latin-1").lower()] = value.decode("latin-1")
    return out


def _bearer_from_scope(scope: Scope) -> str:
    auth = str(_header_map(scope).get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _groups_from_scope(scope: Scope) -> frozenset[str] | None:
    qs = (scope.get("query_string") or b"").decode("latin-1")
    from urllib.parse import parse_qs

    values = parse_qs(qs).get("groups")
    if not values:
        return parse_groups_param(None)
    return parse_groups_param(values[0])


def _resolve_lang_from_scope(scope: Scope) -> str:
    env = str(os.getenv("NETX_LANG") or "").strip().lower()
    if env.startswith("en"):
        return "en"
    if env.startswith("zh"):
        return "zh"
    al = str(_header_map(scope).get("accept-language") or "").strip().lower()
    if al.startswith("en"):
        return "en"
    return "zh"


async def _drain_body(receive: Receive) -> None:
    while True:
        message = await receive()
        if message["type"] != "http.request" or not message.get("more_body"):
            return


async def _send_json(scope: Scope, receive: Receive, send: Send, *, status: int, payload: dict) -> None:
    from starlette.responses import JSONResponse

    await JSONResponse(payload, status_code=status)(scope, receive, send)


class McpAuthMiddleware:
    """Pure ASGI Bearer gate wrapping only the MCP sub-app."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = (scope.get("method") or "GET").upper()
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        groups = _groups_from_scope(scope)
        lang = _resolve_lang_from_scope(scope)

        if not bool(settings.auth_enabled):
            ctx = McpRequestContext(
                token="disabled",
                scopes=None,
                groups=groups,
                lang=lang,
                auth_via="disabled",
            )
            token = set_request_context(ctx)
            try:
                await self.app(scope, receive, send)
            finally:
                reset_request_context(token)
            return

        raw = _bearer_from_scope(scope)
        if not raw:
            await _drain_body(receive)
            await _send_json(
                scope,
                receive,
                send,
                status=401,
                payload={"error": "unauthorized", "detail": "missing_bearer_token"},
            )
            return

        db = SessionLocal()
        try:
            resolved = resolve_user_from_token(db, raw)
        finally:
            db.close()

        if resolved is None:
            await _drain_body(receive)
            await _send_json(
                scope,
                receive,
                send,
                status=401,
                payload={"error": "unauthorized", "detail": "invalid_token"},
            )
            return

        user, via, scopes, _api_token_id, _jti = resolved
        ctx = McpRequestContext(
            token=raw,
            scopes=frozenset(str(s) for s in scopes),
            groups=groups,
            lang=lang,
            user_id=str(getattr(user, "id", "") or ""),
            auth_via=via,
        )
        token = set_request_context(ctx)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_request_context(token)


class _McpSlashRewrite:
    """Rewrite bare ``/mcp`` → ``/mcp/`` so Starlette Mount does not 307 POST."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") == "/mcp":
            scope = dict(scope)
            scope["path"] = "/mcp/"
            raw = scope.get("raw_path")
            if isinstance(raw, (bytes, bytearray)) and raw == b"/mcp":
                scope["raw_path"] = b"/mcp/"
        await self.app(scope, receive, send)


class _PreferChunkedEncoding:
    """Strip ``Content-Length`` so the ASGI server emits proper chunked framing.

    Some WinSock LSPs (observed: path matching ``*/mcp*``) rewrite
    ``Content-Length`` responses into ``Transfer-Encoding: chunked`` **without**
    framing the body. Pre-chunked responses pass through. This is not a response
    buffer/rewrite hack — it only drops CL so h11/uvicorn chunk-encode correctly.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (k, v)
                    for k, v in (message.get("headers") or [])
                    if k.lower() != b"content-length"
                ]
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _build_streamable_asgi() -> ASGIApp:
    app = server_mod.mcp_server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        transport_security=_mcp_transport_security(),
    )
    if isinstance(app, Starlette):
        app.add_middleware(McpAuthMiddleware)
    # Outermost on the sub-app: survive refresh_mcp_for_lifespan remounts.
    return _PreferChunkedEncoding(app)


def build_mcp_asgi_app(*, force_new: bool = False) -> ASGIApp:
    """Stateless Streamable HTTP ASGI app mounted at ``/mcp``."""
    global _MCP_ASGI
    if _MCP_ASGI is not None and not force_new:
        return _MCP_ASGI
    _MCP_ASGI = _build_streamable_asgi()
    return _MCP_ASGI


def ensure_mcp_session_manager() -> None:
    """Create streamable app so ``mcp_server.session_manager`` exists for lifespan."""
    build_mcp_asgi_app()


def _replace_mcp_mount(host_app: Any, mcp_asgi: ASGIApp) -> None:
    for route in getattr(host_app, "routes", []) or []:
        if isinstance(route, Mount) and str(getattr(route, "path", "")) == "/mcp":
            route.app = mcp_asgi
            return


def refresh_mcp_for_lifespan(host_app: Any | None = None) -> Any:
    """Rebuild MCP Server + Streamable app (session manager can only run() once)."""
    global _MCP_ASGI
    server_mod.mcp_server = build_mcp_server()
    _MCP_ASGI = None
    mcp_asgi = build_mcp_asgi_app(force_new=True)
    app = host_app or get_app()
    if app is not None:
        _replace_mcp_mount(app, mcp_asgi)
        app.state.mcp_server = server_mod.mcp_server
        app.state.mcp_asgi = mcp_asgi
        set_app(app)
    return server_mod.mcp_server


@asynccontextmanager
async def mcp_lifespan_cm(*, host_app: Any | None = None) -> AsyncIterator[None]:
    """Enter a fresh MCP session manager for this lifespan cycle."""
    mcp = refresh_mcp_for_lifespan(host_app if host_app is not None else get_app())
    async with mcp.session_manager.run():
        yield


def mount_mcp(app: Any) -> None:
    """``app.mount('/mcp', ...)`` + slash rewrite; auth is on the sub-app only."""
    set_app(app)
    mcp_asgi = build_mcp_asgi_app()
    app.mount("/mcp", mcp_asgi)
    # Outermost: clients may POST http://host:port/mcp without a trailing slash.
    app.add_middleware(_McpSlashRewrite)
    app.state.mcp_server = server_mod.mcp_server
    app.state.mcp_mounted = True
    app.state.mcp_asgi = mcp_asgi
