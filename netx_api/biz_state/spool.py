"""Filesystem spool for biz_state collect: CLI/raw + parsed records before DB flush."""

from __future__ import annotations

import json
import logging
import re
import shutil
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import settings

_log = logging.getLogger("netx.biz_state.spool")

_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def spool_root() -> Path:
    root = Path(str(getattr(settings, "biz_state_spool_dir", None) or "data/biz_state_spool"))
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def batch_spool_dir(batch_id: str) -> Path:
    bid = _SAFE_RE.sub("_", str(batch_id or "").strip())[:64] or "unknown"
    path = spool_root() / bid
    path.mkdir(parents=True, exist_ok=True)
    return path


def clear_batch_spool(batch_id: str) -> None:
    bid = _SAFE_RE.sub("_", str(batch_id or "").strip())[:64]
    if not bid:
        return
    path = (spool_root() / bid).resolve()
    root = spool_root()
    if not str(path).startswith(str(root)) or path == root:
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def _cmd_paths(batch_id: str, cmd_id: str) -> tuple[Path, Path, Path]:
    base = batch_spool_dir(batch_id)
    cid = _SAFE_RE.sub("_", str(cmd_id or "").strip())[:64] or "cmd"
    return base / f"{cid}.raw.txt", base / f"{cid}.meta.json", base / f"{cid}.records.jsonl"


def write_raw_text(batch_id: str, cmd_id: str, text: str) -> str:
    """Write CLI output; return path relative to spool root (posix)."""
    raw_path, _, _ = _cmd_paths(batch_id, cmd_id)
    data = str(text or "").encode("utf-8", errors="replace")
    raw_path.write_bytes(data)
    rel = raw_path.resolve().relative_to(spool_root())
    return str(rel).replace("\\", "/")


def count_file_lines(rel_path: str) -> int:
    """Count lines in a spool raw file (full file, not DB-truncated)."""
    if not rel_path:
        return 0
    path = (spool_root() / str(rel_path)).resolve()
    if not str(path).startswith(str(spool_root())) or not path.is_file():
        return 0
    n = 0
    with path.open("rb") as fh:
        for _ in fh:
            n += 1
    return n


def count_text_lines(text: str | None) -> int:
    s = text or ""
    if not s:
        return 0
    return s.count("\n") + (0 if s.endswith("\n") else 1)


def write_records(
    batch_id: str,
    cmd_id: str,
    records: Iterable[Mapping[str, Any]] | None,
) -> tuple[str, int]:
    """Stream parsed records as JSONL; return (relative path, row count)."""
    _, _, rec_path = _cmd_paths(batch_id, cmd_id)
    n = 0
    with rec_path.open("w", encoding="utf-8", errors="replace") as fh:
        for rec in records or ():
            if not isinstance(rec, Mapping):
                continue
            fh.write(json.dumps(dict(rec), ensure_ascii=False, default=str, separators=(",", ":")))
            fh.write("\n")
            n += 1
    rel = rec_path.resolve().relative_to(spool_root())
    return str(rel).replace("\\", "/"), n


def write_meta(batch_id: str, cmd_id: str, meta: dict[str, Any]) -> str:
    _, meta_path, _ = _cmd_paths(batch_id, cmd_id)
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, default=str),
        encoding="utf-8",
        errors="replace",
    )
    rel = meta_path.resolve().relative_to(spool_root())
    return str(rel).replace("\\", "/")


def read_raw_text(rel_path: str, *, max_bytes: int = 0) -> str:
    if not rel_path:
        return ""
    path = (spool_root() / str(rel_path)).resolve()
    if not str(path).startswith(str(spool_root())) or not path.is_file():
        return ""
    data = path.read_bytes()
    cap = int(max_bytes or 0)
    if cap > 0 and len(data) > cap:
        text = data[:cap].decode("utf-8", errors="replace")
        return text + f"\n...[truncated {cap} bytes cap]\n"
    return data.decode("utf-8", errors="replace")


def spool_file_size(rel_path: str) -> int:
    """Byte size of a spool file; 0 if missing."""
    if not rel_path:
        return 0
    path = (spool_root() / str(rel_path)).resolve()
    if not str(path).startswith(str(spool_root())) or not path.is_file():
        return 0
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


def iter_records(rel_path: str) -> Iterator[dict[str, Any]]:
    """Yield one record dict at a time from JSONL (never loads full file)."""
    if not rel_path:
        return
    path = (spool_root() / str(rel_path)).resolve()
    if not str(path).startswith(str(spool_root())) or not path.is_file():
        return
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                yield rec


def iter_record_chunks(
    rel_path: str, *, chunk_size: int = 2000
) -> Iterator[list[dict[str, Any]]]:
    """Yield lists of up to ``chunk_size`` records from JSONL."""
    size = max(1, int(chunk_size or 2000))
    buf: list[dict[str, Any]] = []
    for rec in iter_records(rel_path):
        buf.append(rec)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf


def read_records(rel_path: str) -> list[dict[str, Any]]:
    """Load all records (tests / small payloads only — prefer iter_record_chunks)."""
    return list(iter_records(rel_path))


@dataclass
class SpooledCommand:
    """One command (primary or aux) collected on disk, awaiting DB flush."""

    id: str
    batch_id: str
    task_item_id: str = ""
    profile_id: str = ""
    parser_id: str = ""
    metric_id: str = ""
    raw_command: str = ""
    params_json: dict[str, Any] = field(default_factory=dict)
    parse_status: str = ""
    message: str = ""
    raw_rel_path: str = ""
    records_rel_path: str = ""
    row_count: int = 0
    # Full CLI line count (before DB raw_text truncate).
    raw_line_count: int = 0
    # Device-declared total when present (e.g. BGP "Total number of routes").
    declared_total: int = 0
    # True when raw_text stored in DB was truncated by raw_max_bytes.
    raw_truncated: bool = False
    # "" | "metric" | "lldp"
    persist_kind: str = ""

    def to_meta(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "batch_id": self.batch_id,
            "task_item_id": self.task_item_id,
            "profile_id": self.profile_id,
            "parser_id": self.parser_id,
            "metric_id": self.metric_id,
            "raw_command": self.raw_command,
            "params_json": dict(self.params_json or {}),
            "parse_status": self.parse_status,
            "message": self.message,
            "raw_rel_path": self.raw_rel_path,
            "records_rel_path": self.records_rel_path,
            "row_count": self.row_count,
            "raw_line_count": self.raw_line_count,
            "declared_total": self.declared_total,
            "raw_truncated": self.raw_truncated,
            "persist_kind": self.persist_kind,
        }


def persist_every_cmds() -> int:
    return max(1, int(getattr(settings, "biz_state_persist_every_cmds", 8) or 8))


def raw_max_bytes() -> int:
    return max(0, int(getattr(settings, "biz_state_raw_max_bytes", 8 * 1024 * 1024) or 0))
