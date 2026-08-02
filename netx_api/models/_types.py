"""Shared SQLAlchemy column types for model modules."""
from __future__ import annotations

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

# JSONB on Postgres; plain JSON elsewhere (unit tests / sqlite).
JsonType = JSON().with_variant(JSONB(), "postgresql")
