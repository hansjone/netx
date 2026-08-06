"""One-shot: ensure schema, then load field CSVs from ume/ into local DB."""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import netx_api.models  # noqa: F401
from netx_api.db import Base, SessionLocal, engine
from netx_api.schema_patches import apply_all_legacy_startup_ddl
from sqlalchemy import text

UME_DIR = ROOT / "ume"

FILES = [
    ("ume_inventory_ne", "ume_inventory_ne_202608061843.csv"),
    ("ume_topo_node", "ume_topo_node_202608061841.csv"),
    ("ume_topo_link", "ume_topo_link_202608061841.csv"),
    ("topo_fabric_node", "topo_fabric_node_202608061847.csv"),
    ("topo_fabric_edge", "topo_fabric_edge_202608061847.csv"),
]

INT_COLS = {"x_pos", "y_pos", "layer_rate"}
JSON_COLS = {"attrs"}
DT_COLS = {
    "first_seen_at",
    "last_seen_at",
    "discovered_at",
    "created_at",
    "updated_at",
}
NULLABLE_EMPTY = {
    "managed_ne_id",
    "region_folder_id",
    "discovered_at",
    "x_pos",
    "y_pos",
    "layer_rate",
}
# Unique nullable on fabric nodes: empty string would violate UNIQUE; use NULL.
FABRIC_NULL_EMPTY = {"ume_ne_id", "managed_ne_id", "region_folder_id"}


def _parse_dt(v: str):
    s = (v or "").strip()
    if not s:
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return s


def _coerce(col: str, raw: str, *, table: str):
    if raw is None:
        return None
    v = raw
    if isinstance(v, str):
        v = v.strip()
    null_empty = set(NULLABLE_EMPTY)
    if table == "topo_fabric_node":
        null_empty |= FABRIC_NULL_EMPTY
    if v == "" and col in null_empty:
        return None
    if col in INT_COLS:
        if v == "":
            return None
        return int(v)
    if col in JSON_COLS:
        if v == "":
            return {}
        if isinstance(v, (dict, list)):
            return v
        try:
            return json.loads(v)
        except Exception:
            return {}
    if col in DT_COLS:
        return _parse_dt(v if isinstance(v, str) else str(v))
    return v if v is not None else ""


def _load_csv(path: Path, *, table: str) -> tuple[list[str], list[dict]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        cols = list(reader.fieldnames or [])
        rows = []
        for row in reader:
            rows.append({c: _coerce(c, row.get(c, ""), table=table) for c in cols})
    return cols, rows


def main() -> None:
    print("ensure schema…")
    Base.metadata.create_all(bind=engine)
    apply_all_legacy_startup_ddl(engine)

    # FK-safe truncate order
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE TABLE topo_fabric_edge, topo_fabric_node, "
                "ume_topo_link, ume_topo_node, ume_inventory_ne RESTART IDENTITY CASCADE"
            )
        )
        print("truncated ok")

    db = SessionLocal()
    try:
        for table, fname in FILES:
            path = UME_DIR / fname
            if not path.exists():
                raise FileNotFoundError(path)
            cols, rows = _load_csv(path, table=table)
            print(f"load {table}: {len(rows)} rows from {fname}")
            if not rows:
                continue
            # chunked insert via core
            chunk = 1000
            meta = Base.metadata.tables[table]
            for i in range(0, len(rows), chunk):
                part = rows[i : i + chunk]
                # drop unknown cols just in case
                clean = [{k: r[k] for k in cols if k in meta.c} for r in part]
                db.execute(meta.insert(), clean)
                db.commit()
                print(f"  … {min(i + chunk, len(rows))}/{len(rows)}")
        print("counts:")
        for table, _ in FILES:
            n = db.execute(text(f"SELECT count(*) FROM {table}")).scalar()
            print(f"  {table}: {n}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
