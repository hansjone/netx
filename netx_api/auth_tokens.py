"""JWT access tokens and opaque API token hashing."""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jwt

from .config import settings

_log = logging.getLogger("netx.auth")

# Legacy hard-coded value (pre auto-file). Treated as insecure if still set via env.
_LEGACY_INSECURE_SECRET = "netx-dev-auth-secret-change-me-in-production-32b"
_cached_secret: str | None = None


def auth_secret_file_path() -> Path:
    raw = str(getattr(settings, "auth_secret_file", None) or "data/auth/jwt_secret").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def is_legacy_insecure_secret(value: str) -> bool:
    return str(value or "").strip() == _LEGACY_INSECURE_SECRET


def ensure_auth_secret() -> str:
    """Resolve JWT signing secret: explicit env > persisted file > generate once.

    Empty / legacy built-in ``NETX_AUTH_SECRET`` falls through to a per-install
    file under ``data/auth/jwt_secret`` (same idea as ``mcp_token``).
    """
    global _cached_secret
    configured = str(settings.auth_secret or "").strip()
    if configured and not is_legacy_insecure_secret(configured):
        return configured
    if configured and is_legacy_insecure_secret(configured):
        _log.warning(
            "NETX_AUTH_SECRET is the legacy shared default; ignoring it and using "
            "per-install file %s (existing JWTs will need re-login)",
            auth_secret_file_path(),
        )
    if _cached_secret:
        return _cached_secret

    path = auth_secret_file_path()
    try:
        if path.is_file():
            existing = path.read_text(encoding="utf-8").strip()
            if len(existing) >= 32 and not is_legacy_insecure_secret(existing):
                _cached_secret = existing
                return _cached_secret
    except Exception:
        _log.exception("read auth secret file failed path=%s", path)

    generated = secrets.token_urlsafe(48)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(generated + "\n", encoding="utf-8")
        try:
            path.chmod(0o600)
        except Exception:
            pass
        _log.info("wrote per-install JWT secret to %s", path)
    except Exception:
        _log.exception("write auth secret file failed path=%s; using in-memory secret only", path)
    _cached_secret = generated
    return _cached_secret


def auth_secret() -> str:
    """Return JWT HMAC secret (ensures file exists on first use)."""
    return ensure_auth_secret()


def issue_access_token(
    *,
    user_id: str,
    username: str,
    role: str,
    jti: str | None = None,
) -> tuple[str, str, int]:
    """Return (token, jti, ttl_sec). jti is required for server-side revocation."""
    ttl = max(300, int(settings.auth_token_ttl_sec or 86400))
    now = datetime.now(timezone.utc)
    sid = str(jti or secrets.token_urlsafe(24)).strip()
    if not sid:
        sid = secrets.token_urlsafe(24)
    payload = {
        "sub": str(user_id),
        "username": str(username),
        "role": str(role),
        "typ": "access",
        "jti": sid,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
    }
    return jwt.encode(payload, auth_secret(), algorithm="HS256"), sid, ttl


def decode_access_token(token: str) -> dict[str, Any]:
    return jwt.decode(
        str(token or ""),
        auth_secret(),
        algorithms=["HS256"],
        options={"require": ["exp", "sub", "jti"]},
    )


def new_api_token_plaintext() -> str:
    """Generate opaque API token (shown once). Prefix helps ops identify netx tokens."""
    return "nxt_" + secrets.token_urlsafe(32)


def new_refresh_token_plaintext() -> str:
    """Opaque refresh token (shown once). Prefix distinguishes from API tokens."""
    return "nxr_" + secrets.token_urlsafe(32)


def hash_api_token(plaintext: str) -> str:
    return hashlib.sha256(str(plaintext or "").encode("utf-8")).hexdigest()
