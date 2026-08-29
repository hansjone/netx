"""Classify CLI/Netmiko failures so auth rejects are not mistaken for read timeouts."""

from __future__ import annotations

import re
from typing import Any

# Prefer specific auth signals over generic Netmiko prompt timeouts.
_AUTH_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.I)
    for p in (
        r"permission denied\s*\([^)]*password[^)]*\)",
        r"permission denied\s*\([^)]*publickey[^)]*\)",
        r"permission denied",
        r"authentication failed",
        r"authentication failure",
        r"auth(?:entication)?\s*fail",
        r"login\s*(?:invalid|failed|incorrect|rejected)",
        r"access denied",
        r"bad (?:secret|password|secrets)",
        r"incorrect password",
        r"%\s*(?:error|login):\s*authentication",
        r"username or password is (?:wrong|incorrect|invalid)",
        r"wrong password",
        r"password rejected",
    )
)

# Huawei / ZTE post-login security banner (success path via SSH or bastion hop).
# Example: "Afterwards, 0 authentication failure occurred."
_LOGIN_SUCCESS_NOTICE = re.compile(
    r"^.*(?:"
    r"last\s+successful\s+login\s+was\s+performed"
    r"|afterwards,\s*\d+\s+authentication\s+failures?\s+occurred"
    r"|上次成功登录"
    r"|之后发生了?\s*\d+\s*次认证失败"
    r").*$",
    re.I | re.M,
)

_PROMPT_TIMEOUT = re.compile(r"pattern not detected|readtimeout|read timeout", re.I)


def find_auth_failure_snippet(text: str, *, max_len: int = 220) -> str | None:
    """Return a short matching auth-failure line/snippet, or None."""
    blob = _LOGIN_SUCCESS_NOTICE.sub("", str(text or ""))
    if not blob.strip():
        return None
    for pat in _AUTH_PATTERNS:
        m = pat.search(blob)
        if not m:
            continue
        # Prefer the whole line containing the match.
        start = blob.rfind("\n", 0, m.start()) + 1
        end = blob.find("\n", m.end())
        if end < 0:
            end = len(blob)
        line = blob[start:end].strip()
        if not line:
            line = m.group(0).strip()
        return line[:max_len]
    return None


def format_cli_failure(exc: BaseException | str, transcript: str = "", *, limit: int = 1020) -> str:
    """Human/ops-facing failure message; promote auth rejects above Pattern/ReadTimeout."""
    if isinstance(exc, BaseException):
        exc_text = f"{type(exc).__name__}: {exc}"
        # Paramiko/Netmiko auth exceptions may carry little/no message text.
        try:
            import paramiko

            if isinstance(exc, paramiko.AuthenticationException):
                detail = str(exc).strip() or type(exc).__name__
                return f"auth_rejected: {detail}"[:limit]
        except Exception:
            pass
        if "AuthenticationException" in type(exc).__name__:
            detail = str(exc).strip() or type(exc).__name__
            return f"auth_rejected: {detail}"[:limit]
    else:
        exc_text = str(exc or "")
    combined = f"{exc_text}\n{transcript or ''}"
    auth = find_auth_failure_snippet(combined)
    if auth:
        # Keep enough of the original class for searchability when it was a timeout wrapper.
        if _PROMPT_TIMEOUT.search(exc_text):
            msg = f"auth_rejected: {auth} (reported_as_prompt_timeout)"
        else:
            msg = f"auth_rejected: {auth}"
        return msg[:limit]
    # Non-auth: keep a session-log tail so operators can see where the CLI stuck
    # (More prompt, half-auth, wrong command echo, etc.).
    tail = str(transcript or "").strip()
    if not tail:
        return exc_text[:limit]
    sep = "\n--- session log ---\n"
    budget = max(120, int(limit) - len(exc_text) - len(sep) - 8)
    clipped = tail[-budget:]
    if len(tail) > budget:
        clipped = "…\n" + clipped
    return f"{exc_text}{sep}{clipped}"[:limit]


def session_log_text(session_log: Any) -> str:
    """Decode Netmiko session_log file/BytesIO into text."""
    if session_log is None:
        return ""
    try:
        if hasattr(session_log, "getvalue"):
            raw = session_log.getvalue()
        elif hasattr(session_log, "read"):
            try:
                session_log.seek(0)
            except Exception:
                pass
            raw = session_log.read()
        else:
            return ""
    except Exception:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw or "")
