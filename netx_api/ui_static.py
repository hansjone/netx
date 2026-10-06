"""Optional SPA hosting from a Vite build (web/dist)."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request
from starlette.responses import Response

from .config import settings

_log = logging.getLogger("netx.ui")

# Paths that must never fall through to index.html.
_API_PREFIXES = (
    "/v1/",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/metrics",
    "/health",
)


def resolve_ui_dist() -> Path | None:
    raw = str(getattr(settings, "ui_dist_dir", "") or "").strip()
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = Path(__file__).resolve().parents[1] / p
    index = p / "index.html"
    if index.is_file():
        return p
    return None


def _is_api_or_infra(path: str) -> bool:
    p = path or "/"
    if p == "/health" or p.startswith("/health/"):
        return True
    if p in ("/metrics", "/metrics/json", "/openapi.json"):
        return True
    return any(p == pref.rstrip("/") or p.startswith(pref) for pref in _API_PREFIXES)


def mount_ui_if_present(app: FastAPI) -> bool:
    """Mount /assets + SPA fallback when ui_dist_dir has index.html. Returns True if mounted."""
    dist = resolve_ui_dist()
    if dist is None:
        return False

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="ui_assets")

    index_path = dist / "index.html"

    @app.get("/")
    async def spa_root() -> FileResponse:
        return FileResponse(index_path)

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def spa_fallback(full_path: str, request: Request) -> Response:
        # Let API / infra 404s stay as API 404s (this route is last).
        path = "/" + (full_path or "").lstrip("/")
        if _is_api_or_infra(path):
            return Response(status_code=404, content=b'{"detail":"Not Found"}', media_type="application/json")
        # Prefer real files under dist (favicon, robots, etc.)
        candidate = dist / full_path
        try:
            candidate.resolve().relative_to(dist.resolve())
        except ValueError:
            return FileResponse(index_path)
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index_path)

    _log.info("UI mounted from %s", dist)
    return True
