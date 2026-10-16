"""Per-request context for built-in remote MCP (token, scopes, groups, lang)."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass
class McpRequestContext:
    token: str = ""
    scopes: frozenset[str] | None = None
    groups: frozenset[str] = field(default_factory=frozenset)
    lang: str = "zh"
    user_id: str = ""
    auth_via: str = ""


_CTX: ContextVar[McpRequestContext | None] = ContextVar("netx_mcp_request_ctx", default=None)
_ASGI_APP: ContextVar[Any] = ContextVar("netx_mcp_asgi_app", default=None)

# Process-wide FastAPI app reference for in-process ASGI calls (set at mount).
_APP_REF: Any = None


def set_app(app: Any) -> None:
    global _APP_REF
    _APP_REF = app


def get_app() -> Any:
    return _ASGI_APP.get() or _APP_REF


def set_request_context(ctx: McpRequestContext | None) -> Any:
    return _CTX.set(ctx)


def reset_request_context(token: Any) -> None:
    _CTX.reset(token)


def get_request_context() -> McpRequestContext | None:
    return _CTX.get()


def require_request_context() -> McpRequestContext:
    ctx = _CTX.get()
    if ctx is None or not ctx.token:
        raise RuntimeError("mcp_request_context_missing")
    return ctx
