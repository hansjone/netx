"""zlib + sha256 helpers for config blobs."""

from __future__ import annotations

import hashlib
import zlib


def compress_text(text: str) -> tuple[bytes, str, int, int]:
    """Return (zlib_bytes, sha256_hex, plain_size, zlib_size)."""
    raw = str(text or "").encode("utf-8", errors="replace")
    digest = hashlib.sha256(raw).hexdigest()
    blob = zlib.compress(raw, level=6)
    return blob, digest, len(raw), len(blob)


def decompress_text(blob: bytes | None) -> str:
    if not blob:
        return ""
    return zlib.decompress(bytes(blob)).decode("utf-8", errors="replace")
