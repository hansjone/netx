from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import settings


def _normalize_key(s: str) -> str:
    return "".join(ch for ch in str(s).strip().lower() if ch not in {" ", "_", "-"})


@dataclass
class ParserConfig:
    parser_version: str
    dict_version: str
    aliases: dict[str, list[str]]
    severity_map: dict[str, str]

    def resolve_col(self, headers: list[str], field: str) -> str | None:
        alias_set = {_normalize_key(x) for x in self.aliases.get(field, [])}
        for h in headers:
            if _normalize_key(h) in alias_set:
                return h
        return None

    def normalize_severity(self, raw: str) -> str:
        key = _normalize_key(raw)
        return self.severity_map.get(key, "unknown")


def load_parser_config(path: str | None = None) -> ParserConfig:
    cfg_path = Path(path or settings.parser_config)
    data: dict[str, Any] = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    aliases = data.get("aliases") or {}
    severity = data.get("severity_map") or {}
    severity_map = {_normalize_key(k): str(v) for k, v in severity.items()}
    return ParserConfig(
        parser_version=str(data.get("parser_version") or "zte_alarm_monitor_v1"),
        dict_version=str(data.get("dict_version") or "v1"),
        aliases={str(k): [str(x) for x in (v or [])] for k, v in aliases.items()},
        severity_map=severity_map,
    )
