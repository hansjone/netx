from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from .config import settings

_log = logging.getLogger("netx.ne.collection.paths")


def collection_data_root() -> Path:
    root = Path(str(settings.ne_collection_data_dir or "data/ne_collections"))
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def run_output_dir(job_id: str, run_id: str) -> Path:
    return collection_data_root() / job_id / run_id


def clear_run_output_files(job_id: str, run_id: str) -> None:
    """Remove prior log files for this run so re-collection overwrites in place."""
    out_dir = run_output_dir(job_id, run_id).resolve()
    root = collection_data_root()
    if not str(out_dir).startswith(str(root)) or not out_dir.is_dir():
        return
    for path in out_dir.iterdir():
        if path.is_file():
            path.unlink(missing_ok=True)


def prune_old_collection_dirs(*, keep_days: int | None = None) -> int:
    """Remove top-level job directories older than ``keep_days``.

    Returns number of job directories removed. Safe no-op when keep_days <= 0.
    """
    days = int(
        keep_days
        if keep_days is not None
        else (getattr(settings, "ne_collection_keep_days", 14) or 0)
    )
    if days <= 0:
        return 0
    root = collection_data_root()
    cutoff = time.time() - (days * 86400)
    removed = 0
    try:
        children = list(root.iterdir())
    except Exception:  # noqa: BLE001
        _log.exception("list collection root failed path=%s", root)
        return 0
    for path in children:
        if not path.is_dir():
            continue
        try:
            mtime = path.stat().st_mtime
        except Exception:  # noqa: BLE001
            continue
        if mtime >= cutoff:
            continue
        try:
            shutil.rmtree(path, ignore_errors=False)
            removed += 1
        except Exception:  # noqa: BLE001
            _log.warning("prune collection dir failed path=%s", path, exc_info=True)
    if removed:
        _log.info("pruned %s ne_collection job dir(s) older than %s day(s)", removed, days)
    return removed
