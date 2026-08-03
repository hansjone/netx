"""Pre-TextFSM CLI wrap-line joiner (vendor-agnostic).

Many vendor CLIs wrap the last column (description / system name) onto the next
line with leading spaces. Pure TextFSM cannot reliably merge those mid-token wraps
without creating duplicate or incomplete records — especially when the wrap
breaks inside a hostname (``AL5458-ACC-612`` + ``0HS``).

This module flattens matching wrap lines **before** TextFSM runs. Rules are
registered per platform + command so indented multi-field blocks (e.g. Huawei
LLDP detail) are left alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CliWrapRule:
    """Join indented continuation lines into the previous table row."""

    id: str
    platforms: frozenset[str]
    # Match ntc / NetX command string (already the resolved show/display command).
    command: re.Pattern[str]
    # A data row that may be continued (typically starts with interface name).
    record_line: re.Pattern[str]
    # Continuation fragment: leading whitespace + non-empty payload.
    cont_line: re.Pattern[str]
    # ZTE column wraps usually split mid-token → join with "" ; use " " if needed.
    join_sep: str = ""
    # Lines that must never be treated as records or continuations.
    skip_line: re.Pattern[str] | None = None


# ZTE ZXROS table rows: interface-like token at column 0.
_ZTE_RECORD = re.compile(r"^(?P<head>[A-Za-z][\w./:-]*)\s+\S+")
_ZTE_CONT = re.compile(r"^\s{8,}(?P<tail>\S.*?)\s*$")
_ZTE_SKIP = re.compile(
    r"^(?:"
    r"-{3,}"
    r"|Local\s+Interface\b"
    r"|Interface\s+\S+\s+Mode\b"
    r"|Interface\s+Attribute\b"
    r"|Scope\s+codes\s*:"
    r"|Total\s+neighbors\b"
    r"|NB\s+=\s+"
    r"|NC\s+=\s+"
    r"|NTPMR\s+=\s+"
    r"|PHY:\s*"
    r"|.*#\s*$"
    r")",
    re.I,
)

CLI_WRAP_RULES: tuple[CliWrapRule, ...] = (
    CliWrapRule(
        id="zte_zxros_show_interface_brief",
        platforms=frozenset({"zte_zxros"}),
        command=re.compile(r"^show\s+interface\s+brief\b", re.I),
        record_line=_ZTE_RECORD,
        cont_line=_ZTE_CONT,
        join_sep="",
        skip_line=_ZTE_SKIP,
    ),
    CliWrapRule(
        id="zte_zxros_show_lldp_neighbor_brief",
        platforms=frozenset({"zte_zxros"}),
        command=re.compile(r"^show\s+lldp\s+neighbor(?:s)?\s+brief\b", re.I),
        record_line=_ZTE_RECORD,
        cont_line=_ZTE_CONT,
        join_sep="",
        skip_line=_ZTE_SKIP,
    ),
)


def _rules_for(*, platform: str, command: str) -> list[CliWrapRule]:
    plat = str(platform or "").strip().lower()
    cmd = str(command or "").strip()
    if not plat or not cmd:
        return []
    return [
        rule
        for rule in CLI_WRAP_RULES
        if plat in rule.platforms and rule.command.search(cmd)
    ]


def _is_skip(line: str, rule: CliWrapRule) -> bool:
    if not rule.skip_line:
        return False
    if rule.skip_line.match(line):
        return True
    stripped = line.strip()
    return bool(stripped and rule.skip_line.match(stripped))


def _apply_rule(text: str, rule: CliWrapRule) -> str:
    lines = str(text or "").splitlines()
    if not lines:
        return str(text or "")
    out: list[str] = []
    for ln in lines:
        if _is_skip(ln, rule):
            out.append(ln)
            continue
        cont = rule.cont_line.match(ln)
        if cont and out:
            prev = out[-1]
            if rule.record_line.match(prev) and not _is_skip(prev, rule):
                out[-1] = f"{prev.rstrip()}{rule.join_sep}{cont.group('tail')}"
                continue
        out.append(ln)
    return "\n".join(out)


def apply_cli_wrap(text: str, *, platform: str = "", command: str = "") -> str:
    """Return text with registered wrap continuations flattened for TextFSM."""
    raw = str(text or "")
    for rule in _rules_for(platform=platform, command=command):
        raw = _apply_rule(raw, rule)
    return raw
