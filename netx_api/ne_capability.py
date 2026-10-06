"""Agent-facing capability descriptor for managed NE exec.

Derived from device_type + exec_policy + runtime settings — not stored in DB.
Callers (MCP / UI) should read this before choosing short CLI vs on-device script vs async job.
"""

from __future__ import annotations

from typing import Any

from .ne_exec import _EXEC_BATCH_MAX_TARGETS, _EXEC_READ_TIMEOUT_DEFAULT, _EXEC_READ_TIMEOUT_MAX
from .ne_exec_guard import (
    EXEC_POLICY_LINUX_SHELL,
    EXEC_POLICY_READONLY,
    EXEC_POLICY_UNRESTRICTED,
    _LINUX_SHELL_MAX_LEN,
    _LINUX_SHELL_MAX_LINES,
    _READONLY_MAX_LEN,
    allows_open_exec_policy,
    effective_exec_policy,
    is_linux_device_type,
    is_mikrotik_device_type,
    normalize_exec_policy,
)


def _device_family(device_type: str | None) -> str:
    if is_linux_device_type(device_type):
        return "linux"
    if is_mikrotik_device_type(device_type):
        return "mikrotik"
    return "network_cli"


def _max_commands() -> int:
    from .config import settings
    from .ne_exec import _EXEC_MAX_COMMANDS_CAP

    raw = int(getattr(settings, "ne_exec_max_commands", 5) or 5)
    return max(1, min(_EXEC_MAX_COMMANDS_CAP, raw))


def build_ne_capability(
    *,
    device_type: str | None,
    exec_policy: str | None,
    vendor: str | None = None,
    hop_enabled: bool = False,
) -> dict[str, Any]:
    """Return a stable capability object for agents and ops UIs."""
    stored = normalize_exec_policy(exec_policy)
    effective = effective_exec_policy(exec_policy, device_type=device_type)
    family = _device_family(device_type)
    shell_ok = effective in (EXEC_POLICY_LINUX_SHELL, EXEC_POLICY_UNRESTRICTED)
    open_eligible = allows_open_exec_policy(device_type)
    max_cmds = _max_commands()

    if effective == EXEC_POLICY_READONLY:
        recommended = "show_only"
        hints = [
            "readonly: only show/display/ping/traceroute (optional pipe filters).",
            "For shell/scripts set exec_policy=linux_shell on a Linux or MikroTik NE "
            "(requires NETX_NE_EXEC_POLICY_ENABLED).",
        ]
    elif family == "linux":
        recommended = "script_on_device"
        hints = [
            "Prefer short on-device scripts for multi-step work; avoid huge inline heredocs in MCP args.",
            "Long / multi-NE work: execManagedNe async=true then poll getNeExecJob.",
            f"Max {max_cmds} commands per call; combine steps in one script when needed.",
        ]
    elif family == "mikrotik":
        recommended = "short_cli"
        hints = [
            "Prefer short RouterOS lines (comment=/find=); avoid one ultra-long line that may truncate.",
            "Multiline scripts allowed under linux_shell; keep each logical change small and idempotent.",
            "Long / multi-NE work: execManagedNe async=true then poll getNeExecJob.",
        ]
    else:
        recommended = "show_only"
        hints = ["network_cli: use vendor show/display; write changes are blocked under readonly."]

    if hop_enabled:
        hints.append("hop_enabled: expect higher latency; raise read_timeout_sec toward 90–120 if needed.")

    return {
        "vendor": str(vendor or "").strip() or None,
        "device_type": str(device_type or "").strip() or None,
        "device_family": family,
        "exec_policy_stored": stored,
        "exec_policy_effective": effective,
        "allows_shell_scripts": bool(shell_ok),
        "allows_multiline": bool(shell_ok),
        "open_policy_eligible": bool(open_eligible),
        "max_commands_per_call": max_cmds,
        "max_command_len": _LINUX_SHELL_MAX_LEN if shell_ok else _READONLY_MAX_LEN,
        "max_command_lines": _LINUX_SHELL_MAX_LINES if shell_ok else 1,
        "read_timeout_sec": {
            "default": _EXEC_READ_TIMEOUT_DEFAULT,
            "max": _EXEC_READ_TIMEOUT_MAX,
        },
        "supports_batch_exec": True,
        "batch_max_targets": _EXEC_BATCH_MAX_TARGETS,
        "supports_async_job": True,
        "recommended_mode": recommended,
        "hints": hints,
    }


__all__ = ["build_ne_capability"]
