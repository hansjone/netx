"""MCP 独立只读边界：后端开放 shell 策略也不能扩大模型的执行权限。"""
from __future__ import annotations

import re

_PREFIX = re.compile(r"(?i)^(show|display)[ \t]+\S")
_CONTROL = re.compile(r"[\x00-\x08\x0a-\x1f\x7f-\x9f\u2028\u2029]")
_SHELL = re.compile(r"[;&`$<>]")
_DESTRUCTIVE = re.compile(
    r"(?i)\b(reboot|reload|shutdown|erase|format|delete|commit|rollback|redirect|append|tee)\b"
    r"|\b(?:configure\s+terminal|conf\s+t|write\s+memory|system-view|undo\s+)"
)
_FILTER = re.compile(r"(?i)^(include|exclude|begin|section|count|match|grep|one-line|no-more)(?:[ \t]|$)")


def validate_readonly_cli(command: str) -> None:
    if not isinstance(command, str) or not command.strip():
        raise ValueError("MCP CLI requires a nonempty show/display command")
    if len(command) > 500 or _CONTROL.search(command) or _SHELL.search(command):
        raise ValueError("MCP CLI rejects control characters, shell operators and commands longer than 500 characters")
    cmd = command.strip()
    if not _PREFIX.match(cmd) or _DESTRUCTIVE.search(cmd):
        raise ValueError("MCP CLI allows read-only show/display only; open device policies do not override this boundary")
    parts = cmd.split("|")
    if any(not _FILTER.match(part.strip()) for part in parts[1:]):
        raise ValueError("MCP CLI pipes allow output filters only")
