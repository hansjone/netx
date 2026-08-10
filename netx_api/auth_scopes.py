"""Capability scopes for RBAC and API tokens.

Roles map to default scopes; API tokens may further restrict via intersection.
"""

from __future__ import annotations

from typing import Iterable

# Canonical scope names (keep stable for MCP / UI).
SCOPE_ALARMS_READ = "alarms:read"
SCOPE_NE_READ = "ne:read"
SCOPE_NE_WRITE = "ne:write"
SCOPE_NE_EXEC = "ne:exec"
SCOPE_WEBCRT = "webcrt:session"
SCOPE_SQL = "sql:query"
SCOPE_ADMIN_USERS = "admin:users"
SCOPE_OPS_WRITE = "ops:write"

ALL_SCOPES: frozenset[str] = frozenset(
    {
        SCOPE_ALARMS_READ,
        SCOPE_NE_READ,
        SCOPE_NE_WRITE,
        SCOPE_NE_EXEC,
        SCOPE_WEBCRT,
        SCOPE_SQL,
        SCOPE_ADMIN_USERS,
        SCOPE_OPS_WRITE,
    }
)

ROLE_DEFAULT_SCOPES: dict[str, frozenset[str]] = {
    "admin": ALL_SCOPES,
    # Read-only operator by default (alarms + inventory).
    "user": frozenset({SCOPE_ALARMS_READ, SCOPE_NE_READ}),
}

# Default MCP bootstrap token: diagnostics CLI allowed; no interactive shell / SQL / writes.
MCP_DEFAULT_SCOPES: tuple[str, ...] = (
    SCOPE_ALARMS_READ,
    SCOPE_NE_READ,
    SCOPE_NE_EXEC,
)


def normalize_scopes(raw: Iterable[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in raw or []:
        s = str(item or "").strip().lower()
        if not s or s not in ALL_SCOPES or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return sorted(out)


def scopes_for_role(role: str) -> frozenset[str]:
    role_n = str(role or "user").strip().lower()
    return ROLE_DEFAULT_SCOPES.get(role_n, ROLE_DEFAULT_SCOPES["user"])


def effective_user_scopes(*, role: str, override: Iterable[str] | None = None) -> frozenset[str]:
    """User scopes = role defaults, optionally replaced by a non-empty override list."""
    ov = normalize_scopes(override)
    if ov:
        # Admin role always keeps admin:users even if override omits it? No — override is authoritative
        # when set; UI should only allow admins to set overrides.
        return frozenset(ov)
    return scopes_for_role(role)


def effective_token_scopes(
    *,
    user_scopes: Iterable[str],
    token_scopes: Iterable[str] | None,
) -> frozenset[str]:
    """Token cannot grant more than the owning user. Empty token scopes inherit user scopes."""
    user = frozenset(normalize_scopes(user_scopes))
    tok = normalize_scopes(token_scopes)
    if not tok:
        return user
    return frozenset(tok) & user


def has_scope(granted: Iterable[str], required: str) -> bool:
    need = str(required or "").strip().lower()
    if not need:
        return True
    return need in frozenset(normalize_scopes(granted))


def has_all_scopes(granted: Iterable[str], required: Iterable[str]) -> bool:
    g = frozenset(normalize_scopes(granted))
    return all(str(r).strip().lower() in g for r in required if str(r).strip())


def required_scope_for_request(method: str, path: str) -> str | None:
    """Return a single required scope for the HTTP request, or None if any auth is enough.

    Paths already gated as public by middleware are not called here.
    """
    m = (method or "GET").upper()
    p = str(path or "")

    if p.startswith("/v1/users"):
        return SCOPE_ADMIN_USERS

    if p.startswith("/v1/sql"):
        return SCOPE_SQL

    if p.startswith("/v1/webcrt"):
        return SCOPE_WEBCRT

    if p.startswith("/v1/managed-ne"):
        if p.rstrip("/").endswith("/exec") and m == "POST":
            return SCOPE_NE_EXEC
        if m in ("POST", "PUT", "PATCH", "DELETE"):
            return SCOPE_NE_WRITE
        return SCOPE_NE_READ

    if p.startswith("/v1/cli"):
        if m in ("POST", "PUT", "PATCH", "DELETE"):
            return SCOPE_NE_WRITE
        return SCOPE_NE_READ

    if p.startswith("/v1/ne-collections"):
        if m in ("POST", "PUT", "PATCH", "DELETE"):
            return SCOPE_NE_WRITE
        return SCOPE_NE_READ

    if p.startswith("/v1/config-sync"):
        if m in ("POST", "PUT", "PATCH", "DELETE"):
            return SCOPE_NE_WRITE
        return SCOPE_NE_READ

    if p.startswith("/v1/port-traffic"):
        if m in ("POST", "PUT", "PATCH", "DELETE"):
            return SCOPE_NE_WRITE
        return SCOPE_NE_READ

    if p.startswith("/v1/topology"):
        # Read-only path search uses POST for a structured body.
        if m == "POST" and p.rstrip("/").endswith("/fabric/paths"):
            return SCOPE_NE_READ
        if m in ("POST", "PUT", "PATCH", "DELETE"):
            return SCOPE_NE_WRITE
        return SCOPE_NE_READ

    # UME / alarms / diagnostics / AI analyze / import
    if (
        p.startswith("/v1/ume")
        or p.startswith("/v1/alarms")
        or p.startswith("/v1/batches")
        or p.startswith("/v1/diagnostics")
        or p.startswith("/v1/ap/")
        or p.startswith("/v1/import")
        or p.startswith("/v1/key-alert")
    ):
        if m in ("POST", "PUT", "PATCH", "DELETE") and (
            "/runtime/" in p or p.endswith("/pause") or p.endswith("/resume") or "subscription" in p
        ):
            return SCOPE_OPS_WRITE
        if m in ("POST", "PUT", "PATCH", "DELETE") and not p.startswith("/v1/ap/"):
            # Sync triggers / rule edits need ops write; pure analyze stays read.
            if "/analyze" in p:
                return SCOPE_ALARMS_READ
            return SCOPE_OPS_WRITE
        return SCOPE_ALARMS_READ

    if p.startswith("/v1/ops"):
        if m in ("POST", "PUT", "PATCH", "DELETE"):
            return SCOPE_OPS_WRITE
        return SCOPE_NE_READ

    if p.startswith("/v1/integrations"):
        return SCOPE_ALARMS_READ

    # Auth self-service, api-tokens, audit: any authenticated user
    return None
