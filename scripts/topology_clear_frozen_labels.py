"""Clear view-node labels that merely freeze the current fabric name/ip.

Default is dry-run. Pass --apply to write.

A placement label is considered frozen when it equals the fabric node's current
name or IP (case-insensitive, stripped). Clearing it restores follow-device-name
behavior on the next graph read.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import or_

from netx_api.db import SessionLocal
from netx_api.models import TopoFabricNode, TopoViewNode


def _norm(s: str | None) -> str:
    return str(s or "").strip().casefold()


def clear_frozen_labels(db, *, apply: bool) -> tuple[int, int]:
    """Return (scanned_nonempty_labels, frozen_cleared). Caller commits/rolls back."""
    rows = (
        db.query(TopoViewNode, TopoFabricNode)
        .outerjoin(TopoFabricNode, TopoFabricNode.id == TopoViewNode.fabric_node_id)
        .filter(TopoViewNode.label != "")
        .filter(or_(TopoViewNode.label.isnot(None)))
        .all()
    )
    scanned = 0
    cleared = 0
    for vn, fn in rows:
        scanned += 1
        label = _norm(vn.label)
        if not label or fn is None:
            continue
        if label not in {_norm(fn.name), _norm(fn.ip)}:
            continue
        cleared += 1
        if apply:
            vn.label = ""
    return scanned, cleared


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes (default: dry-run only)",
    )
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        scanned, cleared = clear_frozen_labels(db, apply=args.apply)
        if args.apply:
            db.commit()
        else:
            db.rollback()
        mode = "apply" if args.apply else "dry-run"
        print(f"mode={mode} scanned={scanned} frozen_labels={cleared}")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
