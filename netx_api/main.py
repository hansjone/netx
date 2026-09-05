"""FastAPI application entry — routers + thin lifecycle hooks."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI

from .auth_middleware import AuthAuditMiddleware
from .auth_router import router as auth_router
from .alarms_router import router as alarms_router
from .cli_router import router as cli_router
from .collection_router import router as collection_router
from .config import settings
from .config_sync_router import router as config_sync_router
from .integrations_router import router as integrations_router
from .lldp_collect_router import router as lldp_collect_router
from .managed_ne_router import router as managed_ne_router
from .ops_router import router as ops_router
from .parser_config import load_parser_config
from .port_traffic_router import router as port_traffic_router
from .sql_router import router as sql_router
from .sql_router import sql_query, sql_ume_query  # noqa: F401 — tests import from main
from .topology_router import router as topology_router
from .ume_router import router as ume_router
from .ume_router import (  # noqa: F401 — tests import from main
    _extract_ume_raw_group_field,
    _serialize_ume_alarm_raw_row,
    ume_alarms_fields,
)
from .ume_support import (  # noqa: F401 — tests import from main
    _classify_protocol_bucket,
    _protocol_bucket_label,
)
from .webcrt_router import router as webcrt_router
from .metrics_router import router as metrics_router

_schedule_log = logging.getLogger("netx.ume.schedule")
_BOOT_MONO = time.monotonic()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    import asyncio

    from .app_startup import run_api_startup
    from .app_shutdown import shutdown_runtime
    from .dsh_alarm_hub import bind_event_loop

    run_api_startup()
    bind_event_loop(asyncio.get_running_loop())
    try:
        yield
    finally:
        shutdown_runtime(reason="api_lifespan")


app = FastAPI(
    title="netx ops tool",
    version="0.1.0",
    docs_url="/docs" if bool(settings.docs_enabled) else None,
    redoc_url="/redoc" if bool(settings.docs_enabled) else None,
    openapi_url="/openapi.json" if bool(settings.docs_enabled) else None,
    lifespan=lifespan,
)
app.add_middleware(AuthAuditMiddleware)
app.include_router(auth_router)
app.include_router(managed_ne_router)
app.include_router(cli_router)
app.include_router(collection_router)
app.include_router(config_sync_router)
app.include_router(port_traffic_router)
app.include_router(webcrt_router)
app.include_router(topology_router)
app.include_router(lldp_collect_router)
app.include_router(ops_router)
app.include_router(sql_router)
app.include_router(integrations_router)
app.include_router(metrics_router)
app.include_router(ume_router)
app.include_router(alarms_router)
parser_cfg = load_parser_config()


@app.get("/health", status_code=200)
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> dict:
    return {
        "ok": True,
        "mode": "api_only",
        "message": "netx UI is served by Vite frontend only",
        "frontend_url": settings.frontend_url,
        "api_health": "/health",
        "api_status": "/v1/integrations/status",
    }


if __name__ == "__main__":
    uvicorn.run("netx_api.main:app", host=settings.host, port=settings.port, reload=False)
