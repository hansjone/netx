"""Topology HTTP routes."""

from __future__ import annotations

import json
from typing import Any, Iterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .db import get_db
from .topology_schemas import (
    TopologyDiscoverRequest,
    TopologyGraphPut,
    TopologyMapCreate,
    TopologyMapUpdate,
)
from .topology_service import (
    create_map,
    delete_map,
    discover_neighbors,
    get_graph,
    iter_discover_neighbors,
    list_maps,
    put_graph,
    update_map,
)

router = APIRouter(prefix="/v1/topology", tags=["topology"])


def _sse_pack(event: dict[str, Any]) -> str:
    etype = str(event.get("type") or "message")
    return f"event: {etype}\ndata: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


@router.get("/maps")
def api_list_maps(db: Session = Depends(get_db)) -> dict[str, Any]:
    return list_maps(db)


@router.post("/maps")
def api_create_map(body: TopologyMapCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    return create_map(db, body).model_dump()


@router.get("/maps/{map_id}")
def api_get_map(map_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return get_graph(db, map_id).model_dump()


@router.patch("/maps/{map_id}")
def api_patch_map(
    map_id: str, body: TopologyMapUpdate, db: Session = Depends(get_db)
) -> dict[str, Any]:
    return update_map(db, map_id, body).model_dump()


@router.delete("/maps/{map_id}")
def api_delete_map(map_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return delete_map(db, map_id)


@router.put("/maps/{map_id}/graph")
def api_put_graph(
    map_id: str, body: TopologyGraphPut, db: Session = Depends(get_db)
) -> dict[str, Any]:
    return put_graph(db, map_id, body).model_dump()


@router.post("/maps/{map_id}/discover")
def api_discover(
    map_id: str,
    body: TopologyDiscoverRequest | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    req = body or TopologyDiscoverRequest()
    return discover_neighbors(db, map_id, req).model_dump()


@router.post("/maps/{map_id}/discover/stream")
def api_discover_stream(
    map_id: str,
    body: TopologyDiscoverRequest | None = None,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """SSE stream: start → ne_start/ne_result (per NE) → done."""
    req = body or TopologyDiscoverRequest()

    def generate() -> Iterator[str]:
        try:
            for event in iter_discover_neighbors(db, map_id, req):
                yield _sse_pack(event)
        except Exception as exc:  # noqa: BLE001 — surface to client then end stream
            yield _sse_pack({"type": "error", "detail": str(exc)[:800]})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
