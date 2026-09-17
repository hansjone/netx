"""Thread-safe CLI parse: NetX cli_templates -> community ntc-templates.

CliTable/ParseCmd is not safe to share across threads. Each call builds a fresh
CliTable under a process-wide lock. Template files on disk are read-only and
safe for multi-process workers.

Rule pipeline (biz_state):
  apply_rule / apply_rules — run TextFSM by template stem (filename without .textfsm)
  rules_for_command — list stems matching platform + CLI command via index
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Sequence, TypeVar

from .cli_wrap import apply_cli_wrap

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


@dataclass(frozen=True)
class _IndexEntry:
    stem: str
    rel_path: str
    platform: str
    command_pat: str
    template_dir: Path


def _stem_of_template_path(rel: str) -> str:
    name = Path(str(rel or "").replace("\\", "/")).name
    if name.lower().endswith(".textfsm"):
        name = name[: -len(".textfsm")]
    return name


def _parse_index_file(template_dir: Path) -> list[_IndexEntry]:
    index_path = template_dir / "index"
    if not index_path.is_file():
        return []
    out: list[_IndexEntry] = []
    try:
        lines = index_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        _log.debug("read index failed dir=%s", template_dir, exc_info=True)
        return []
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        # Template, Hostname, Platform, Command
        parts = [p.strip() for p in s.split(",")]
        if len(parts) < 4:
            continue
        if parts[0].lower() == "template":
            continue
        rel, _host, platform, cmd_pat = parts[0], parts[1], parts[2], ",".join(parts[3:]).strip()
        stem = _stem_of_template_path(rel)
        if not stem or not platform:
            continue
        out.append(
            _IndexEntry(
                stem=stem,
                rel_path=rel.replace("\\", "/"),
                platform=str(platform).strip(),
                command_pat=cmd_pat,
                template_dir=template_dir,
            )
        )
    return out


@lru_cache(maxsize=4)
def _all_index_entries(include_community: bool = True) -> tuple[_IndexEntry, ...]:
    entries = list(_parse_index_file(CUSTOM_TEMPLATE_DIR))
    if include_community:
        try:
            entries.extend(_parse_index_file(Path(_community_template_dir())))
        except Exception:
            _log.debug("community index unavailable", exc_info=True)
    return tuple(entries)


def _index_cmd_to_regex(pat: str) -> re.Pattern[str] | None:
    """Convert ntc index Command column (sh[[ow]] foo) to an anchored regex."""
    raw = str(pat or "").strip()
    if not raw:
        return None
    out: list[str] = [r"^\s*"]
    i = 0
    while i < len(raw):
        if raw.startswith("[[", i):
            i += 2
            continue
        m = re.match(r"([A-Za-z0-9_.|/\-]+)(?:\[\[([A-Za-z0-9_.|/\-]+)\]\])?", raw[i:])
        if m:
            base, opt = m.group(1), m.group(2)
            if opt:
                out.append(re.escape(base) + r"(?:" + re.escape(opt) + r")?")
            else:
                out.append(re.escape(base))
            i += m.end()
            ws = re.match(r"\s+", raw[i:])
            if ws:
                out.append(r"\s+")
                i += ws.end()
            continue
        ch = raw[i]
        if ch in ".$^?*+()[]{}|\\":
            out.append(re.escape(ch))
        else:
            out.append(re.escape(ch))
        i += 1
    out.append(r"\s*$")
    try:
        return re.compile("".join(out), re.I)
    except re.error:
        return None


def _command_matches_index(pat: str, command: str) -> bool:
    cmd = str(command or "").strip()
    if not cmd:
        return False
    rx = _index_cmd_to_regex(pat)
    if rx and rx.match(cmd):
        return True
    # Fallback: loose token containment after expanding [[opt]]
    flat = re.sub(r"\[\[([^\]]+)\]\]", r"\1", pat)
    flat = re.sub(r"\s+", " ", flat).strip().lower()
    norm = re.sub(r"\s+", " ", cmd).strip().lower()
    return bool(flat) and flat in norm


def rules_for_command(platform: str, command: str) -> list[str]:
    """Return TextFSM template stems matching platform + CLI command (index order)."""
    plat = str(platform or "").strip()
    cmd = str(command or "").strip()
    if not plat or not cmd:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for e in _all_index_entries(True):
        if e.platform != plat:
            continue
        if not _command_matches_index(e.command_pat, cmd):
            continue
        if e.stem in seen:
            continue
        seen.add(e.stem)
        out.append(e.stem)
    return out


def _find_entries_for_stem(rule_key: str) -> list[_IndexEntry]:
    stem = str(rule_key or "").strip()
    if stem.lower().endswith(".textfsm"):
        stem = stem[: -len(".textfsm")]
    if not stem:
        return []
    return [e for e in _all_index_entries(True) if e.stem == stem]


def _parse_textfsm_file(template_path: Path, text: str) -> list[dict[str, Any]]:
    try:
        from textfsm import TextFSM
    except Exception:
        _log.debug("textfsm unavailable", exc_info=True)
        return []
    try:
        with template_path.open("r", encoding="utf-8", errors="ignore") as fh:
            fsm = TextFSM(fh)
            rows = fsm.ParseTextToDicts(str(text or ""))
    except Exception:
        _log.debug("textfsm parse failed path=%s", template_path, exc_info=True)
        return []
    if not isinstance(rows, list):
        return []
    # Match ntc-templates parse_output: lowercase dict keys.
    out: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        out.append({str(k).lower(): v for k, v in r.items()})
    return out


def apply_rule(*, platform: str, rule_key: str, text: str, command: str = "") -> list[dict[str, Any]]:
    """Run one TextFSM rule by template stem; empty list if missing/failed."""
    plat = str(platform or "").strip()
    key = str(rule_key or "").strip()
    raw = str(text or "")
    if not key or not raw.strip():
        return []
    wrap_cmd = str(command or "").strip() or key.replace("_", " ")
    if plat:
        raw = apply_cli_wrap(raw, platform=plat, command=wrap_cmd)

    entries = _find_entries_for_stem(key)
    if plat:
        plat_entries = [e for e in entries if e.platform == plat]
        if plat_entries:
            entries = plat_entries
    with _PARSE_LOCK:
        for e in entries:
            path = e.template_dir / e.rel_path
            if not path.is_file():
                alt = e.template_dir / Path(e.rel_path).name
                path = alt if alt.is_file() else path
            if not path.is_file():
                continue
            rows = _parse_textfsm_file(path, raw)
            if rows:
                return rows
        # Fallback: search directories by filename even if not in index
        fname = f"{key}.textfsm" if not key.endswith(".textfsm") else key
        try:
            community = Path(_community_template_dir())
        except Exception:
            community = None
        roots = [CUSTOM_TEMPLATE_DIR] + ([community] if community else [])
        for base in roots:
            if not base or not base.is_dir():
                continue
            for path in base.rglob(fname):
                rows = _parse_textfsm_file(path, raw)
                if rows:
                    return rows
    return []


def apply_rules(
    *,
    platform: str,
    text: str,
    rule_keys: Sequence[str],
    command: str = "",
) -> dict[str, list[dict[str, Any]]]:
    """Run multiple TextFSM rules → ``{stem: rows}``. Missing/failed keys map to ``[]``."""
    out: dict[str, list[dict[str, Any]]] = {}
    for key in rule_keys or ():
        k = str(key or "").strip()
        if not k:
            continue
        if k.lower().endswith(".textfsm"):
            k = k[: -len(".textfsm")]
        if k in out:
            continue
        out[k] = apply_rule(platform=platform, rule_key=k, text=text, command=command)
    return out


def parse_cli(
    *,
    platform: str,
    command: str,
    text: str,
) -> list[dict[str, Any]]:
    """Parse CLI text: wrap-join → custom templates → community ntc-templates."""
    plat = str(platform or "").strip()
    cmd = str(command or "").strip()
    raw = apply_cli_wrap(str(text or ""), platform=plat, command=cmd)
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
