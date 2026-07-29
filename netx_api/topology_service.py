"""Topology map CRUD, graph save, and LLDP/CDP edge discovery."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .models import ManagedNE, TopologyEdge, TopologyMap, TopologyNode
from .ne_exec import execute_managed_ne_commands
from .topology_lldp import NeighborHit, parse_neighbor_output, pick_neighbor_command
from .topology_schemas import (
    TopologyDiscoverNeResult,
    TopologyDiscoverOut,
    TopologyDiscoverRequest,
    TopologyEdgeIn,
    TopologyEdgeOut,
    TopologyGraphOut,
    TopologyGraphPut,
    TopologyMapCreate,
    TopologyMapOut,
    TopologyMapUpdate,
    TopologyNodeIn,
    TopologyNodeOut,
)


def _utcnow() -> datetime:
    return datetime.utcnow()


def _map_out(m: TopologyMap, *, node_count: int = 0, edge_count: int = 0) -> TopologyMapOut:
    return TopologyMapOut(
        id=m.id,
        name=m.name,
        remark=m.remark or "",
        node_count=node_count,
        edge_count=edge_count,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


def _get_map_or_404(db: Session, map_id: str) -> TopologyMap:
    mid = str(map_id or "").strip()
    row = db.get(TopologyMap, mid) if mid else None
    if row is None:
        raise HTTPException(status_code=404, detail="topology_map_not_found")
    return row


def list_maps(db: Session) -> dict[str, Any]:
    rows = db.query(TopologyMap).order_by(TopologyMap.updated_at.desc()).all()
    items: list[TopologyMapOut] = []
    for m in rows:
        nc = db.query(TopologyNode).filter(TopologyNode.map_id == m.id).count()
        ec = db.query(TopologyEdge).filter(TopologyEdge.map_id == m.id).count()
        items.append(_map_out(m, node_count=nc, edge_count=ec))
    return {"total": len(items), "items": [i.model_dump() for i in items]}


def create_map(db: Session, body: TopologyMapCreate) -> TopologyMapOut:
    name = str(body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name_required")
    now = _utcnow()
    row = TopologyMap(
        id=uuid4().hex,
        name=name[:256],
        remark=str(body.remark or "")[:1024],
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _map_out(row)


def update_map(db: Session, map_id: str, body: TopologyMapUpdate) -> TopologyMapOut:
    row = _get_map_or_404(db, map_id)
    if body.name is not None:
        name = str(body.name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name_required")
        row.name = name[:256]
    if body.remark is not None:
        row.remark = str(body.remark or "")[:1024]
    row.updated_at = _utcnow()
    db.commit()
    db.refresh(row)
    nc = db.query(TopologyNode).filter(TopologyNode.map_id == row.id).count()
    ec = db.query(TopologyEdge).filter(TopologyEdge.map_id == row.id).count()
    return _map_out(row, node_count=nc, edge_count=ec)


def delete_map(db: Session, map_id: str) -> dict[str, Any]:
    row = _get_map_or_404(db, map_id)
    db.query(TopologyEdge).filter(TopologyEdge.map_id == row.id).delete(synchronize_session=False)
    db.query(TopologyNode).filter(TopologyNode.map_id == row.id).delete(synchronize_session=False)
    db.delete(row)
    db.commit()
    return {"ok": True, "map_id": map_id, "deleted": True}


def _ne_lookup(db: Session, ne_ids: set[str]) -> dict[str, ManagedNE]:
    if not ne_ids:
        return {}
    rows = db.query(ManagedNE).filter(ManagedNE.id.in_(list(ne_ids))).all()
    return {r.id: r for r in rows}


def _node_out(n: TopologyNode, ne: ManagedNE | None) -> TopologyNodeOut:
    label = (n.label or "").strip()
    if not label and ne is not None:
        label = (ne.name or ne.ip_address or n.id)[:256]
    return TopologyNodeOut(
        id=n.id,
        map_id=n.map_id,
        managed_ne_id=n.managed_ne_id or "",
        ume_ne_id=n.ume_ne_id or "",
        label=label,
        x=float(n.x or 0),
        y=float(n.y or 0),
        ne_name=(ne.name if ne else ""),
        ne_ip=(ne.ip_address if ne else ""),
        vendor=(ne.vendor if ne else ""),
        protocol=(ne.protocol if ne else ""),
        connect_status=(ne.connect_status if ne else ""),
    )


def _edge_out(e: TopologyEdge) -> TopologyEdgeOut:
    return TopologyEdgeOut(
        id=e.id,
        map_id=e.map_id,
        source_node_id=e.source_node_id,
        target_node_id=e.target_node_id,
        source_port=e.source_port or "",
        target_port=e.target_port or "",
        source=e.source or "manual",
        discovered_at=e.discovered_at,
    )


def get_graph(db: Session, map_id: str) -> TopologyGraphOut:
    row = _get_map_or_404(db, map_id)
    nodes = db.query(TopologyNode).filter(TopologyNode.map_id == row.id).all()
    edges = db.query(TopologyEdge).filter(TopologyEdge.map_id == row.id).all()
    nes = _ne_lookup(db, {str(n.managed_ne_id or "") for n in nodes if n.managed_ne_id})
    return TopologyGraphOut(
        map=_map_out(row, node_count=len(nodes), edge_count=len(edges)),
        nodes=[_node_out(n, nes.get(str(n.managed_ne_id or ""))) for n in nodes],
        edges=[_edge_out(e) for e in edges],
    )


def put_graph(db: Session, map_id: str, body: TopologyGraphPut) -> TopologyGraphOut:
    row = _get_map_or_404(db, map_id)
    nodes_in = list(body.nodes or [])
    edges_in = list(body.edges or [])
    if len(nodes_in) > 2000:
        raise HTTPException(status_code=400, detail="too_many_nodes")
    if len(edges_in) > 5000:
        raise HTTPException(status_code=400, detail="too_many_edges")

    node_ids = set()
    for n in nodes_in:
        nid = str(n.id or "").strip()
        if not nid:
            raise HTTPException(status_code=400, detail="node_id_required")
        if nid in node_ids:
            raise HTTPException(status_code=400, detail=f"duplicate_node_id:{nid}")
        node_ids.add(nid)

    for e in edges_in:
        sid = str(e.source_node_id or "").strip()
        tid = str(e.target_node_id or "").strip()
        if sid not in node_ids or tid not in node_ids:
            raise HTTPException(status_code=400, detail="edge_endpoint_not_in_nodes")
        src = str(e.source or "manual").strip().lower() or "manual"
        if src not in {"manual", "lldp", "cdp"}:
            raise HTTPException(status_code=400, detail="invalid_edge_source")

    now = _utcnow()
    db.query(TopologyEdge).filter(TopologyEdge.map_id == row.id).delete(synchronize_session=False)
    db.query(TopologyNode).filter(TopologyNode.map_id == row.id).delete(synchronize_session=False)

    for n in nodes_in:
        db.add(
            TopologyNode(
                id=str(n.id).strip(),
                map_id=row.id,
                managed_ne_id=str(n.managed_ne_id or "").strip(),
                ume_ne_id=str(n.ume_ne_id or "").strip(),
                label=str(n.label or "").strip()[:256],
                x=float(n.x or 0),
                y=float(n.y or 0),
                created_at=now,
                updated_at=now,
            )
        )
    for e in edges_in:
        db.add(
            TopologyEdge(
                id=str(e.id).strip() or uuid4().hex,
                map_id=row.id,
                source_node_id=str(e.source_node_id).strip(),
                target_node_id=str(e.target_node_id).strip(),
                source_port=str(e.source_port or "").strip()[:128],
                target_port=str(e.target_port or "").strip()[:128],
                source=str(e.source or "manual").strip().lower() or "manual",
                discovered_at=now if str(e.source or "").lower() in {"lldp", "cdp"} else None,
                created_at=now,
                updated_at=now,
            )
        )
    row.updated_at = now
    db.commit()
    return get_graph(db, row.id)


def _norm_key(s: str) -> str:
    return re_sub_host(str(s or "").strip().lower())


def re_sub_host(s: str) -> str:
    # Strip domain / trailing punctuation for hostname matching.
    t = s.split(".")[0].strip().lower()
    return t.rstrip(".,;:")


def _match_neighbor_to_node(
    hit: NeighborHit,
    *,
    nodes: list[TopologyNode],
    nes: dict[str, ManagedNE],
    self_node_id: str,
) -> TopologyNode | None:
    name_key = _norm_key(hit.remote_name)
    ip_key = str(hit.remote_ip or "").strip()
    for n in nodes:
        if n.id == self_node_id:
            continue
        ne = nes.get(str(n.managed_ne_id or ""))
        candidates = [
            _norm_key(n.label or ""),
            _norm_key(ne.name if ne else ""),
            str(ne.ip_address if ne else "").strip(),
        ]
        if ip_key and ip_key in candidates:
            return n
        if name_key and name_key in {_norm_key(c) for c in candidates if c}:
            return n
        # Also match managed NE name without case
        if ne and name_key and name_key == _norm_key(ne.name):
            return n
    return None


def _edge_pair_key(a: str, b: str, local_port: str, remote_port: str) -> tuple[str, str, str, str]:
    if a <= b:
        return (a, b, local_port, remote_port)
    return (b, a, remote_port, local_port)


def discover_neighbors(
    db: Session,
    map_id: str,
    body: TopologyDiscoverRequest,
) -> TopologyDiscoverOut:
    row = _get_map_or_404(db, map_id)
    nodes = db.query(TopologyNode).filter(TopologyNode.map_id == row.id).all()
    edges = db.query(TopologyEdge).filter(TopologyEdge.map_id == row.id).all()
    nes = _ne_lookup(db, {str(n.managed_ne_id or "") for n in nodes if n.managed_ne_id})

    filter_ids = {str(x).strip() for x in (body.ne_ids or []) if str(x).strip()}
    scan_nodes = [
        n
        for n in nodes
        if n.managed_ne_id
        and n.managed_ne_id in nes
        and (not filter_ids or n.managed_ne_id in filter_ids)
    ]

    # Index existing edges for upsert (undirected + ports).
    existing: dict[tuple[str, str, str, str], TopologyEdge] = {}
    for e in edges:
        key = _edge_pair_key(
            e.source_node_id,
            e.target_node_id,
            (e.source_port or "").strip(),
            (e.target_port or "").strip(),
        )
        existing[key] = e

    results: list[TopologyDiscoverNeResult] = []
    added = 0
    updated = 0
    now = _utcnow()
    proto_req = str(body.protocol or "auto").strip().lower() or "auto"

    for n in scan_nodes:
        ne = nes.get(n.managed_ne_id)
        if ne is None:
            continue
        cmd, proto_tag = pick_neighbor_command(
            protocol=proto_req,
            vendor=ne.vendor or "",
            device_type=ne.device_type or "",
        )
        if not cmd:
            results.append(
                TopologyDiscoverNeResult(
                    ne_id=ne.id,
                    ne_name=ne.name or "",
                    ne_ip=ne.ip_address or "",
                    ok=False,
                    error="no_command_for_vendor",
                )
            )
            continue
        exec_out = execute_managed_ne_commands(
            db,
            [cmd],
            ne_id=ne.id,
            read_timeout_sec=60,
        )
        if not exec_out.get("ok"):
            results.append(
                TopologyDiscoverNeResult(
                    ne_id=ne.id,
                    ne_name=ne.name or "",
                    ne_ip=ne.ip_address or "",
                    ok=False,
                    command=cmd,
                    error=str(exec_out.get("detail") or exec_out.get("error") or "exec_failed")[:500],
                )
            )
            continue

        raw = str(exec_out.get("output") or "")
        hits = parse_neighbor_output(
            raw,
            protocol=proto_tag,
            vendor=ne.vendor or "",
            device_type=ne.device_type or "",
        )
        ne_added = 0
        ne_updated = 0
        for hit in hits:
            peer = _match_neighbor_to_node(
                hit, nodes=nodes, nes=nes, self_node_id=n.id
            )
            if peer is None:
                continue
            local_port = (hit.local_port or "").strip()[:128]
            remote_port = (hit.remote_port or "").strip()[:128]
            key = _edge_pair_key(n.id, peer.id, local_port, remote_port)
            edge_proto = hit.protocol if hit.protocol in {"lldp", "cdp"} else proto_tag
            cur = existing.get(key)
            if cur is not None:
                # Never downgrade manual edges; refresh discovery metadata only for discovered.
                if (cur.source or "manual") == "manual":
                    continue
                cur.source = edge_proto
                cur.source_port = local_port if cur.source_node_id == n.id else remote_port
                cur.target_port = remote_port if cur.source_node_id == n.id else local_port
                cur.discovered_at = now
                cur.updated_at = now
                ne_updated += 1
                updated += 1
                continue
            # Prefer orientation: scanning node as source.
            new_edge = TopologyEdge(
                id=uuid4().hex,
                map_id=row.id,
                source_node_id=n.id,
                target_node_id=peer.id,
                source_port=local_port,
                target_port=remote_port,
                source=edge_proto,
                discovered_at=now,
                created_at=now,
                updated_at=now,
            )
            db.add(new_edge)
            existing[key] = new_edge
            ne_added += 1
            added += 1

        results.append(
            TopologyDiscoverNeResult(
                ne_id=ne.id,
                ne_name=ne.name or "",
                ne_ip=ne.ip_address or "",
                ok=True,
                command=cmd,
                neighbors=len(hits),
                edges_added=ne_added,
                edges_updated=ne_updated,
                raw_preview=raw[:800],
            )
        )

    row.updated_at = now
    db.commit()
    graph = get_graph(db, row.id)
    return TopologyDiscoverOut(
        map_id=row.id,
        protocol=proto_req,
        scanned=len(results),
        edges_added=added,
        edges_updated=updated,
        results=results,
        graph=graph,
    )
