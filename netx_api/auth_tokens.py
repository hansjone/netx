"""JWT access tokens and opaque API token hashing."""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from .config import settings

_log = logging.getLogger("netx.auth")

_DEFAULT_DEV_SECRET = "netx-dev-auth-secret-change-me-in-production-32b"
_warned_default_secret = False


def auth_secret() -> str:
    """Return configured secret (lab default is set in Settings)."""
    global _warned_default_secret
    configured = str(settings.auth_secret or "").strip() or _DEFAULT_DEV_SECRET
    if configured == _DEFAULT_DEV_SECRET and not _warned_default_secret:
        _warned_default_secret = True
        _log.warning(
            "using default NETX_AUTH_SECRET; set a unique secret for production deployments"
        )
    return configured


def issue_access_token(*, user_id: str, username: str, role: str) -> str:
    ttl = max(300, int(settings.auth_token_ttl_sec or 86400))
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "username": str(username),
        "role": str(role),
        "typ": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
    }
    return jwt.encode(payload, auth_secret(), algorithm="HS256")


def decode_access_token(token: str) -> dict[str, Any]:
    return jwt.decode(
        str(token or ""),
        auth_secret(),
        algorithms=["HS256"],
        options={"require": ["exp", "sub"]},
    )


def new_api_token_plaintext() -> str:
    """Generate opaque API token (shown once). Prefix helps ops identify netx tokens."""
    return "nxt_" + secrets.token_urlsafe(32)


def hash_api_token(plaintext: str) -> str:
    return hashlib.sha256(str(plaintext or "").encode("utf-8")).hexdigest()
