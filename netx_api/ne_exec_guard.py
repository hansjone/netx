"""NE CLI command allow/deny gates (read-only exec for ops tools)."""

from __future__ import annotations

import re

from fastapi import HTTPException

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


def validate_ne_exec_command(command: str) -> None:
    """Raise HTTPException if command is empty, smuggled, blocked, or not allowlisted."""
    cmd = str(command or "").strip()
    if not cmd:
        raise HTTPException(status_code=400, detail="empty_command")
    if len(cmd) > 500:
        raise HTTPException(status_code=400, detail="command_too_long")
    if any(ch in cmd for ch in (";", "\n", "\r", "`")):
        raise HTTPException(status_code=400, detail="command_chars_not_allowed")
    if any(sep in cmd for sep in _FORBIDDEN_LINE_SEPARATORS):
        raise HTTPException(status_code=400, detail="command_chars_not_allowed")
    if _BLOCKED_RE.search(cmd):
        raise HTTPException(status_code=400, detail="command_blocked")
    if not _ALLOWED_PREFIX_RE.match(cmd):
        raise HTTPException(status_code=400, detail="command_not_allowed_prefix")
    _validate_pipe_segments(cmd)


# Back-compat alias used by tests / callers.
_validate_command = validate_ne_exec_command
