"""Password hashing helpers (bcrypt)."""

from __future__ import annotations

import bcrypt


def hash_password(password: str) -> str:
    raw = str(password or "").encode("utf-8")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(
            str(password or "").encode("utf-8"),
            str(password_hash or "").encode("ascii"),
        )
    except Exception:
        return False
