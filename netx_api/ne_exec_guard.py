"""NE CLI command allow/deny gates (execManagedNe / ops tools).

Policies (per managed NE ``exec_policy``):

- ``readonly`` (default): network CLI only — show/display/ping/traceroute.
- ``linux_shell``: open shell/script on capable NEs (Linux, MikroTik RouterOS) —
  pipes/&&/;/quotes/heredoc / RouterOS multiline scripts allowed;
  no network prefix/pipe rules; no write-deny list.
- ``unrestricted``: same as linux_shell (lab open); kept distinct for audit/UI.
"""

from __future__ import annotations

import re

from fastapi import HTTPException

EXEC_POLICY_READONLY = "readonly"
EXEC_POLICY_LINUX_SHELL = "linux_shell"
EXEC_POLICY_UNRESTRICTED = "unrestricted"
EXEC_POLICIES = frozenset(
    {EXEC_POLICY_READONLY, EXEC_POLICY_LINUX_SHELL, EXEC_POLICY_UNRESTRICTED}
)

# readonly: short show/ping lines. linux_shell: scripts / heredoc file writes for agents.
_READONLY_MAX_LEN = 500
_LINUX_SHELL_MAX_LEN = 65_536
_LINUX_SHELL_MAX_LINES = 2_000

# Block obvious config-change / destructive patterns (case-insensitive).
_BLOCKED_RE = re.compile(
    r"(?i)("
    r"configure\s+terminal|conf\s+t\b|"
    r"\bwrite\s+(memory|erase)|\bcopy\s+run|\bcopy\s+startup|"
    r"\breload\b|\breboot\b|\berase\b|\bformat\b|\bdelete\b|"
    r"\bcommit\b|\brollback\b|startup-config|"
    r"\bsystem-view\b|\bip\s+address\b|\bvlan\s+\d|"
    # Extra vendor / destructive surface (avoid words that appear in show output filters)
    r"\bclear\s+configuration\b|\breset\s+saved-configuration\b|"
    r"\bundo\s+|\bsave\s*$|\bsave\s+\S|"
    r"\bfile\s+delete\b|\bftp\s+put\b|\btftp\s+put\b|"
    r"\bdebug\s+all\b|\bundebug\s+all\b|"
    r"\brequest\s+system\s+(reboot|halt|power-off|zeroize)\b|"
    r"\bset\s+system\s+reboot\b"
    r")"
)

# Read-only CLI: show/display plus ping/traceroute reachability checks.
_ALLOWED_PREFIX_RE = re.compile(
    r"(?i)^(show\s|display\s|ping\s|ping6\s|traceroute\s|tracert\s|trace\s|trace6\s)"
)

# Unicode / C1 line separators that can smuggle a second CLI after a show prefix.
_FORBIDDEN_LINE_SEPARATORS = ("\u2028", "\u2029", "\x85", "\x0b", "\x0c")

# Pipe segments allowed after show/display (output filtering only).
_ALLOWED_PIPE_SEGMENT_RE = re.compile(
    r"(?i)^(include|exclude|begin|section|count|match|grep|one-line|no-more)(\s|$)"
)
_BLOCKED_PIPE_SEGMENT_RE = re.compile(r"(?i)\b(redirect|append|tee|send)\b")


def normalize_exec_policy(raw: str | None) -> str:
    p = str(raw or "").strip().lower()
    return p if p in EXEC_POLICIES else EXEC_POLICY_READONLY


def is_linux_device_type(device_type: str | None) -> bool:
    low = str(device_type or "").strip().lower()
    return low in ("linux", "linux_ssh", "linux_telnet") or low.startswith("linux_")


def is_mikrotik_device_type(device_type: str | None) -> bool:
    low = str(device_type or "").strip().lower()
    return low in ("mikrotik_routeros", "mikrotik_switchos") or low.startswith("mikrotik_")


def allows_open_exec_policy(device_type: str | None) -> bool:
    """Device types that may use linux_shell / unrestricted (multiline scripts)."""
    return is_linux_device_type(device_type) or is_mikrotik_device_type(device_type)


def exec_policy_feature_enabled() -> bool:
    """Global kill-switch: off → always readonly (UI hidden, API rejects open policies)."""
    from .config import settings

    return bool(getattr(settings, "ne_exec_policy_enabled", False))


def effective_exec_policy(raw: str | None, *, device_type: str | None = None) -> str:
    """Policy used at exec time (forces readonly when feature off or ineligible type)."""
    if not exec_policy_feature_enabled():
        return EXEC_POLICY_READONLY
    pol = normalize_exec_policy(raw)
    if pol != EXEC_POLICY_READONLY and not allows_open_exec_policy(device_type):
        return EXEC_POLICY_READONLY
    return pol


def require_exec_policy_writable(
    raw: str | None,
    *,
    device_type: str | None = None,
) -> str:
    """Normalize for create/update; reject open policies when feature off or ineligible type."""
    pol = normalize_exec_policy(raw)
    if pol == EXEC_POLICY_READONLY:
        return pol
    if not exec_policy_feature_enabled():
        raise HTTPException(status_code=400, detail="exec_policy_feature_disabled")
    if not allows_open_exec_policy(device_type):
        raise HTTPException(status_code=400, detail="exec_policy_requires_shell_device_type")
    return pol


def _validate_pipe_segments(cmd: str) -> None:
    if "|" not in cmd:
        return
    parts = [p.strip() for p in cmd.split("|")]
    if len(parts) < 2 or not parts[0] or any(not p for p in parts[1:]):
        raise HTTPException(status_code=400, detail="command_pipe_not_allowed")
    for segment in parts[1:]:
        if _BLOCKED_PIPE_SEGMENT_RE.search(segment):
            raise HTTPException(status_code=400, detail="command_pipe_not_allowed")
        if not _ALLOWED_PIPE_SEGMENT_RE.match(segment):
            raise HTTPException(status_code=400, detail="command_pipe_not_allowed")


def _validate_linux_shell_command(cmd: str) -> None:
    """Shell policy: allow multiline (heredoc / scripts); still reject exotic separators."""
    if any(sep in cmd for sep in _FORBIDDEN_LINE_SEPARATORS):
        raise HTTPException(status_code=400, detail="command_chars_not_allowed")
    # Count lines after normalizing CRLF; blank trailing newline from strip() is already gone.
    line_count = cmd.count("\n") + 1
    if line_count > _LINUX_SHELL_MAX_LINES:
        raise HTTPException(status_code=400, detail="command_too_many_lines")


def _validate_readonly_command(cmd: str) -> None:
    if any(ch in cmd for ch in (";", "\n", "\r", "`")):
        raise HTTPException(status_code=400, detail="command_chars_not_allowed")
    if any(sep in cmd for sep in _FORBIDDEN_LINE_SEPARATORS):
        raise HTTPException(status_code=400, detail="command_chars_not_allowed")
    if _BLOCKED_RE.search(cmd):
        raise HTTPException(status_code=400, detail="command_blocked")
    if not _ALLOWED_PREFIX_RE.match(cmd):
        raise HTTPException(status_code=400, detail="command_not_allowed_prefix")
    _validate_pipe_segments(cmd)


def validate_ne_exec_command(command: str, *, policy: str = EXEC_POLICY_READONLY) -> None:
    """Raise HTTPException if command is empty, smuggled, blocked, or not allowlisted."""
    cmd = str(command or "").strip()
    if not cmd:
        raise HTTPException(status_code=400, detail="empty_command")
    pol = normalize_exec_policy(policy)
    max_len = (
        _LINUX_SHELL_MAX_LEN
        if pol in (EXEC_POLICY_LINUX_SHELL, EXEC_POLICY_UNRESTRICTED)
        else _READONLY_MAX_LEN
    )
    if len(cmd) > max_len:
        raise HTTPException(status_code=400, detail="command_too_long")
    if pol in (EXEC_POLICY_LINUX_SHELL, EXEC_POLICY_UNRESTRICTED):
        # Shell metacharacters (|;&&`$'"<<) and newlines (heredoc) allowed.
        _validate_linux_shell_command(cmd)
        return
    _validate_readonly_command(cmd)


# Back-compat alias used by tests / callers.
_validate_command = validate_ne_exec_command
