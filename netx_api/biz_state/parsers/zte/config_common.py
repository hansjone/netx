"""Helpers for ZTE MIM running-config section parsing."""

from __future__ import annotations

import re

_MIM_OPEN = re.compile(r"^!<([^/>][^>]*)>\s*$")
_MIM_CLOSE = re.compile(r"^!</([^>]+)>\s*$")


def extract_mim_section(raw_text: str, section: str) -> str:
    """Return body of ``!<section>`` … ``!</section>`` if present; else original text."""
    name = str(section or "").strip().lower()
    if not name:
        return str(raw_text or "")
    text = str(raw_text or "")
    lines = text.splitlines()
    start = -1
    for i, line in enumerate(lines):
        m = _MIM_OPEN.match(line.strip())
        if m and m.group(1).strip().lower() == name:
            start = i + 1
            break
    if start < 0:
        return text
    out: list[str] = []
    for line in lines[start:]:
        m = _MIM_CLOSE.match(line.strip())
        if m and m.group(1).strip().lower() == name:
            break
        out.append(line)
    return "\n".join(out)


def is_secret_line(line: str) -> bool:
    low = str(line or "").lower()
    return any(
        k in low
        for k in (
            "password",
            "secret",
            "encrypted",
            "authentication-key",
            "pre-shared-key",
        )
    )
