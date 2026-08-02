from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import QueuePool

from .config import settings


class Base(DeclarativeBase):
    pass


def _engine_kwargs() -> dict[str, Any]:
    url = str(settings.database_url or "")
    kwargs: dict[str, Any] = {"future": True, "pool_pre_ping": True}
    # SQLite (tests / local) does not use QueuePool the same way.
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        return kwargs
    kwargs.update(
        {
            "poolclass": QueuePool,
            "pool_size": max(1, int(getattr(settings, "db_pool_size", 20) or 20)),
            "max_overflow": max(0, int(getattr(settings, "db_max_overflow", 20) or 20)),
            "pool_recycle": max(60, int(getattr(settings, "db_pool_recycle_sec", 1800) or 1800)),
            "pool_timeout": max(1, int(getattr(settings, "db_pool_timeout_sec", 30) or 30)),
        }
    )
    return kwargs


engine = create_engine(settings.database_url, **_engine_kwargs())
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def db_pool_status() -> dict[str, Any]:
    """Best-effort QueuePool snapshot for readiness / metrics."""
    pool = getattr(engine, "pool", None)
    if pool is None:
        return {"backend": "none"}
    out: dict[str, Any] = {"backend": type(pool).__name__}
    for key, meth in (
        ("size", "size"),
        ("checked_in", "checkedin"),
        ("checked_out", "checkedout"),
        ("overflow", "overflow"),
    ):
        fn = getattr(pool, meth, None)
        if callable(fn):
            try:
                out[key] = int(fn())
            except Exception:  # noqa: BLE001
                pass
    out["pool_size_cfg"] = int(getattr(settings, "db_pool_size", 20) or 20)
    out["max_overflow_cfg"] = int(getattr(settings, "db_max_overflow", 20) or 20)
    return out
