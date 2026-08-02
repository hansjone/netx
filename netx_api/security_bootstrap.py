"""Startup security checks for bind address vs insecure lab defaults."""

from __future__ import annotations

import logging
import sys

from .auth_tokens import ensure_auth_secret, is_legacy_insecure_secret
from .config import settings

_log = logging.getLogger("netx.security")


def _is_loopback_host(host: str) -> bool:
    h = str(host or "").strip().lower()
    return h in {"127.0.0.1", "::1", "localhost"}


def assert_secure_defaults_or_exit() -> None:
    """Refuse non-loopback bind when lab secrets / default admin password remain."""
    # Always materialize per-install JWT secret before bind checks.
    ensure_auth_secret()

    if bool(getattr(settings, "allow_insecure_defaults", False)):
        _log.warning("NETX_ALLOW_INSECURE_DEFAULTS=1 — skipping insecure-default bind check")
        return
    host = str(settings.host or "127.0.0.1")
    if _is_loopback_host(host):
        return
    problems: list[str] = []
    explicit = str(settings.auth_secret or "").strip()
    if is_legacy_insecure_secret(explicit):
        problems.append(
            "NETX_AUTH_SECRET is still the legacy shared development value "
            "(unset it to use data/auth/jwt_secret, or set a unique secret)"
        )
    pwd = str(settings.bootstrap_admin_password or "").strip()
    if pwd in {"", "admin123"}:
        problems.append("NETX_BOOTSTRAP_ADMIN_PASSWORD is still the lab default (admin123)")
    if not bool(getattr(settings, "ume_verify_tls", True)):
        problems.append("NETX_UME_VERIFY_TLS=false while binding on a non-loopback interface")
    if problems:
        for p in problems:
            _log.error("insecure default: %s", p)
        _log.error(
            "Refusing to start on host=%s. Set unique secrets, or bind 127.0.0.1, "
            "or set NETX_ALLOW_INSECURE_DEFAULTS=1 for lab only.",
            host,
        )
        sys.exit(2)
