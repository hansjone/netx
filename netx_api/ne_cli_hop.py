"""Vendor CLI hop return-to-proxy detection and session guard metadata."""
from __future__ import annotations

import re
from typing import Any

from netmiko import ConnectHandler

# Nested stelnet/telnet/ssh on vendor hops ends with messages like these; outer hop stays up.
_CLI_HOP_NESTED_END_RE = re.compile(
    r"(?is)"
    r"(?:^|\n)\s*(?:"
    r"connection\s+closed(?:\s+by\s+(?:foreign|remote)\s+host)?"
    r"|closed\s+by\s+foreign\s+host"
    r"|connection\s+to\s+\S+\s+closed"
    r"|%\s*connection\s+closed(?:\s+by\s+(?:foreign|remote)\s+host)?"
    r"|\[connection\s+to\s+[^\]]+closed\]"
    r"|remote\s+host\s+closed\s+the\s+connection"
    r")[^\n]*\s*(?:\n|$)"
)

# Last-line CLI prompts: <HW>  [HW]  Router#  Router>
_CLI_PROMPT_LINE_RE = re.compile(
    r"^(?:"
    r"<[^>\r\n]{1,64}>|"
    r"\[[^\]\r\n]{1,64}\]|"
    r"[A-Za-z0-9][\w.\-:/]{0,62}[#>]"
    r")\s*$"
)


def extract_cli_prompt_marker(text: str) -> str:
    """Return the last recognizable CLI prompt line from channel text."""
    s = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    for line in reversed(s.split("\n")):
        # Strip common ANSI CSI sequences so markers match live reader bytes.
        cleaned = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", line).strip()
        if cleaned and _CLI_PROMPT_LINE_RE.match(cleaned):
            return cleaned
    return ""


def cli_hop_nested_session_ended(text: str) -> bool:
    """True when nested jump (stelnet/telnet/ssh) reports connection closed."""
    return bool(_CLI_HOP_NESTED_END_RE.search(str(text or "")))


def cli_hop_returned_to_proxy(text: str, hop_prompt: str) -> bool:
    """True when output ends on the hop prompt captured before the jump command."""
    marker = str(hop_prompt or "").strip()
    if not marker:
        return False
    last = extract_cli_prompt_marker(text)
    return bool(last) and last == marker


def should_close_cli_hop_session(
    recent: str,
    hop_prompt: str = "",
    *,
    seen_other_prompt: bool = False,
) -> bool:
    """Policy: end WebCRT when nested target session drops back to the hop CLI.

    Nested-close messages are matched only in a trailing window so a mid-session
    ``display log`` that reprints old "Connection closed" text does not trip.

    Prompt-only return requires ``seen_other_prompt`` so identical default sysnames
    (e.g. hop and target both ``<HUAWEI>``) do not close immediately after jump.
    """
    text = str(recent or "")
    if cli_hop_nested_session_ended(text[-800:]):
        return True
    if not seen_other_prompt:
        return False
    return cli_hop_returned_to_proxy(text, hop_prompt)


def get_cli_hop_guard(conn: ConnectHandler | None) -> dict[str, Any] | None:
    """Metadata attached by CLI hop connect; None when not a vendor CLI hop session."""
    if conn is None:
        return None
    guard = getattr(conn, "_netx_cli_hop", None)
    if not isinstance(guard, dict) or not guard.get("enabled"):
        return None
    return guard


def _attach_cli_hop_guard(
    conn: ConnectHandler,
    *,
    hop_prompt: str,
    hop_vendor: str,
    hop_host: str,
) -> None:
    conn._netx_cli_hop = {  # type: ignore[attr-defined]
        "enabled": True,
        "hop_prompt": str(hop_prompt or "").strip(),
        "hop_vendor": str(hop_vendor or "").strip().lower(),
        "hop_host": str(hop_host or "").strip(),
    }


