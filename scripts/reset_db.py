from __future__ import annotations

"""
Dangerous: drop all netx tables and recreate schema.

Usage (PowerShell):
  Set-Location D:\\project\\chatgpt\\netx
  python scripts\\reset_db.py

It uses NETX_DATABASE_URL (via netx_api.config settings).
"""

import sys
from pathlib import Path

# Ensure `import netx_api` works when executed as a script.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import netx_api.models  # noqa: F401,E402  # ensure SQLAlchemy models are registered to Base.metadata
from netx_api.db import Base, engine  # noqa: E402


def main() -> None:
    # Drop all tables first (data loss).
    Base.metadata.drop_all(bind=engine)
    # Recreate with current models.
    Base.metadata.create_all(bind=engine)
    print("ok: dropped and recreated all tables")


if __name__ == "__main__":
    main()

