"""Thread-safe CLI parse: NetX cli_templates -> community ntc-templates.

CliTable/ParseCmd is not safe to share across threads. Each call builds a fresh
CliTable under a process-wide lock. Template files on disk are read-only and
safe for multi-process workers.
"""

from __future__ import annotations

import logging
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, TypeVar

_log = logging.getLogger("netx.ntc_parse")

_PARSE_LOCK = threading.Lock()

CUSTOM_TEMPLATE_DIR = Path(__file__).resolve().parent / "cli_templates"

T = TypeVar("T")


@lru_cache(maxsize=1)
def _community_template_dir() -> str:
    import ntc_templates

    return str(Path(ntc_templates.__file__).resolve().parent / "templates")


def resolve_cli_platform(*, vendor: str = "", device_type: str = "", vendor_key: str = "") -> str:
    """Map NetX inventory fields to an ntc / NetX TextFSM platform id."""
    dt = str(device_type or "").strip().lower()
    key = str(vendor_key or "").strip().lower()
    vend = str(vendor or "").strip().lower()

    if dt.startswith("cisco_nxos") or dt == "cisco_nxos":
        return "cisco_nxos"
    if dt.startswith("cisco_xr") or dt == "cisco_xr":
        return "cisco_xr"
    if dt.startswith("cisco_"):
        return "cisco_ios"
    if dt.startswith("huawei"):
        return "huawei_vrp"
    if dt.startswith("hp_comware") or dt.startswith("h3c_"):
        return "hp_comware"
    if dt.startswith("juniper"):
        return "juniper_junos"
    if dt.startswith("zte_"):
        return "zte_zxros"
    if dt.startswith("alcatel_aos"):
        return "alcatel_aos"
    if dt.startswith("alcatel_sros") or dt.startswith("nokia_sros"):
        return "alcatel_sros"
    if dt.startswith("nokia_"):
        # SRL etc.: best-effort SROS templates until dedicated ones exist.
        return "alcatel_sros"
    if dt.startswith("alcatel"):
        return "alcatel_sros"
    if dt.startswith("ericsson_"):
        return "ericsson_ipos"
    if dt.startswith("mikrotik"):
        return "mikrotik_routeros"

    blob = key or vend
    if "nxos" in blob:
        return "cisco_nxos"
    if "cisco" in blob:
        return "cisco_ios"
    if "huawei" in blob:
        return "huawei_vrp"
    if "h3c" in blob or "comware" in blob:
        return "hp_comware"
    if "juniper" in blob or "junos" in blob:
        return "juniper_junos"
    if "zte" in blob:
        return "zte_zxros"
    if "aos" in blob and "alcatel" in blob:
        return "alcatel_aos"
    if "nokia" in blob or "alcatel" in blob or "sros" in blob:
        return "alcatel_sros"
    if "ericsson" in blob:
        return "ericsson_ipos"
    if "mikrotik" in blob or "routeros" in blob:
        return "mikrotik_routeros"
    return ""


def row_get(row: dict[str, Any], *names: str) -> str:
    if not row:
        return ""
    lower_map = {str(k).lower(): v for k, v in row.items()}
    for name in names:
        val = lower_map.get(name.lower())
        if val is None:
            continue
        text = str(val).strip()
        if text:
            return text
    return ""


def parse_cli(
    *,
    platform: str,
    command: str,
    text: str,
) -> list[dict[str, Any]]:
    """Parse CLI text: custom templates first, then community ntc-templates."""
    plat = str(platform or "").strip()
    cmd = str(command or "").strip()
    raw = str(text or "")
    if not plat or not cmd or not raw.strip():
        return []

    with _PARSE_LOCK:
        rows = _parse_dir(CUSTOM_TEMPLATE_DIR, plat, cmd, raw)
        if rows:
            return rows
        try:
            community = Path(_community_template_dir())
        except Exception:
            _log.debug("community ntc template dir unavailable", exc_info=True)
            return []
        return _parse_dir(community, plat, cmd, raw)


def _parse_dir(template_dir: Path, platform: str, command: str, text: str) -> list[dict[str, Any]]:
    index_path = template_dir / "index"
    if not index_path.is_file():
        return []
    try:
        from ntc_templates.parse import ParsingException, parse_output
    except Exception:
        _log.debug("ntc_templates unavailable", exc_info=True)
        return []

    try:
        # Fresh CliTable inside parse_output each call; we still hold _PARSE_LOCK.
        rows = parse_output(
            platform=platform,
            command=command,
            data=text,
            template_dir=str(template_dir),
            try_fallback=False,
        )
    except ParsingException:
        return []
    except Exception:
        _log.debug(
            "cli parse failed platform=%s command=%s dir=%s",
            platform,
            command,
            template_dir,
            exc_info=True,
        )
        return []

    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


def parse_cli_mapped(
    *,
    platform: str,
    command: str,
    text: str,
    map_rows: Callable[[list[dict[str, Any]]], list[T]],
) -> list[T]:
    """Parse CLI and map rows; empty list if no TextFSM match."""
    rows = parse_cli(platform=platform, command=command, text=text)
    if not rows:
        return []
    return map_rows(rows)
