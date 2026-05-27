from __future__ import annotations

from pathlib import Path

from .config import settings


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
