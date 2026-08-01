"""Topology map CRUD, graph save, and LLDP/CDP edge discovery."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4
import re

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .cli_resolve import get_default_profile, infer_device_type_vendor
from .models import ManagedNE, TopologyEdge, TopologyMap, TopologyNode, UmeInventoryNE
from .ne_exec import execute_managed_ne_commands
from .topology_lldp import NeighborHit, normalize_ifname, parse_neighbor_output, pick_neighbor_command
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


def _ume_lookup(db: Session, ume_ids: set[str]) -> dict[str, UmeInventoryNE]:
    ids = {str(x).strip() for x in ume_ids if str(x).strip()}
    if not ids:
        return {}
    rows = db.query(UmeInventoryNE).filter(UmeInventoryNE.ne_id.in_(list(ids))).all()
    return {str(r.ne_id): r for r in rows}


def _node_out(
    n: TopologyNode,
    ne: ManagedNE | None,
    ume: UmeInventoryNE | None = None,
) -> TopologyNodeOut:
    label = (n.label or "").strip()
    ne_name = ""
    ne_ip = ""
    vendor = ""
    protocol = ""
    connect_status = ""
    if ne is not None:
        if not label:
            label = (ne.name or ne.ip_address or n.id)[:256]
        ne_name = ne.name or ""
        ne_ip = ne.ip_address or ""
        vendor = ne.vendor or ""
        protocol = ne.protocol or ""
        connect_status = ne.connect_status or ""
    elif ume is not None:
        ume_name = (ume.host_name or ume.ne_name or ume.user_label or "").strip()
        if not label:
            label = (ume_name or ume.ip_address or n.id)[:256]
        ne_name = ume_name
        ne_ip = ume.ip_address or ""
        vendor = (ume.vendor or "ZTE").strip() or "ZTE"
        connect_status = ume.connection_status or ""
    return TopologyNodeOut(
        id=n.id,
        map_id=n.map_id,
        managed_ne_id=n.managed_ne_id or "",
        ume_ne_id=n.ume_ne_id or "",
        label=label,
        x=float(n.x or 0),
        y=float(n.y or 0),
        ne_name=ne_name,
        ne_ip=ne_ip,
        vendor=vendor,
        protocol=protocol,
        connect_status=connect_status,
    )


_HEX_COLOR_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_LINE_STYLES = {"", "solid", "dashed", "dotted"}


def _normalize_edge_style(
    *,
    stroke_color: str = "",
    stroke_width: int = 0,
    line_style: str = "",
) -> tuple[str, int, str]:
    color = str(stroke_color or "").strip()
    if color and not _HEX_COLOR_RE.match(color):
        raise HTTPException(status_code=400, detail="invalid_stroke_color")
    try:
        width = int(stroke_width or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid_stroke_width") from exc
    if width < 0 or width > 12:
        raise HTTPException(status_code=400, detail="invalid_stroke_width")
    style = str(line_style or "").strip().lower()
    if style not in _LINE_STYLES:
        raise HTTPException(status_code=400, detail="invalid_line_style")
    return color, width, style


def _edge_out(e: TopologyEdge) -> TopologyEdgeOut:
    return TopologyEdgeOut(
        id=e.id,
        map_id=e.map_id,
        source_node_id=e.source_node_id,
        target_node_id=e.target_node_id,
        source_port=e.source_port or "",
        target_port=e.target_port or "",
        source=e.source or "manual",
        stroke_color=getattr(e, "stroke_color", None) or "",
        stroke_width=int(getattr(e, "stroke_width", 0) or 0),
        line_style=getattr(e, "line_style", None) or "",
        discovered_at=e.discovered_at,
    )


def get_graph(db: Session, map_id: str) -> TopologyGraphOut:
    row = _get_map_or_404(db, map_id)
    nodes = db.query(TopologyNode).filter(TopologyNode.map_id == row.id).all()
    edges = db.query(TopologyEdge).filter(TopologyEdge.map_id == row.id).all()
    nes = _ne_lookup(db, {str(n.managed_ne_id or "") for n in nodes if n.managed_ne_id})
    umes = _ume_lookup(db, {str(n.ume_ne_id or "") for n in nodes if n.ume_ne_id})
    return TopologyGraphOut(
        map=_map_out(row, node_count=len(nodes), edge_count=len(edges)),
        nodes=[
            _node_out(
                n,
                nes.get(str(n.managed_ne_id or "")),
                umes.get(str(n.ume_ne_id or "")),
            )
            for n in nodes
        ],
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

    normalized_edges: list[tuple[TopologyEdgeIn, str, str, int, str]] = []
    for e in edges_in:
        sid = str(e.source_node_id or "").strip()
        tid = str(e.target_node_id or "").strip()
        if sid not in node_ids or tid not in node_ids:
            raise HTTPException(status_code=400, detail="edge_endpoint_not_in_nodes")
        if sid == tid:
            raise HTTPException(status_code=400, detail="edge_self_loop")
        src = str(e.source or "manual").strip().lower() or "manual"
        if src not in {"manual", "lldp", "cdp", "stale"}:
            raise HTTPException(status_code=400, detail="invalid_edge_source")
        color, width, line = _normalize_edge_style(
            stroke_color=getattr(e, "stroke_color", "") or "",
            stroke_width=int(getattr(e, "stroke_width", 0) or 0),
            line_style=getattr(e, "line_style", "") or "",
        )
        normalized_edges.append((e, src, color, width, line))

    now = _utcnow()
    prev_nodes = {
        str(n.id): {"created_at": n.created_at}
        for n in db.query(TopologyNode).filter(TopologyNode.map_id == row.id).all()
    }
    prev_edges = {
        str(e.id): {"created_at": e.created_at, "discovered_at": e.discovered_at}
        for e in db.query(TopologyEdge).filter(TopologyEdge.map_id == row.id).all()
    }
    db.query(TopologyEdge).filter(TopologyEdge.map_id == row.id).delete(synchronize_session=False)
    db.query(TopologyNode).filter(TopologyNode.map_id == row.id).delete(synchronize_session=False)
    db.expire_all()

    for n in nodes_in:
        nid = str(n.id).strip()
        prev = prev_nodes.get(nid) or {}
        created = getattr(n, "created_at", None) or prev.get("created_at") or now
        db.add(
            TopologyNode(
                id=nid,
                map_id=row.id,
                managed_ne_id=str(n.managed_ne_id or "").strip(),
                ume_ne_id=str(n.ume_ne_id or "").strip(),
                label=str(n.label or "").strip()[:256],
                x=float(n.x or 0),
                y=float(n.y or 0),
                created_at=created,
                updated_at=now,
            )
        )
    for e, src, color, width, line in normalized_edges:
        eid = str(e.id).strip() or uuid4().hex
        prev = prev_edges.get(eid) or {}
        created = getattr(e, "created_at", None) or prev.get("created_at") or now
        client_discovered = getattr(e, "discovered_at", None)
        if src in {"lldp", "cdp", "stale"}:
            discovered = client_discovered or prev.get("discovered_at") or now
        else:
            discovered = None
        db.add(
            TopologyEdge(
                id=eid,
                map_id=row.id,
                source_node_id=str(e.source_node_id).strip(),
                target_node_id=str(e.target_node_id).strip(),
                source_port=str(e.source_port or "").strip()[:128],
                target_port=str(e.target_port or "").strip()[:128],
                source=src,
                stroke_color=color,
                stroke_width=width,
                line_style=line,
                discovered_at=discovered,
                created_at=created,
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
    umes: dict[str, UmeInventoryNE],
    self_node_id: str,
) -> TopologyNode | None:
    name_key = _norm_key(hit.remote_name)
    ip_key = str(hit.remote_ip or "").strip()
    for n in nodes:
        if n.id == self_node_id:
            continue
        ne = nes.get(str(n.managed_ne_id or ""))
        ume = umes.get(str(n.ume_ne_id or ""))
        candidates = [
            _norm_key(n.label or ""),
            _norm_key(ne.name if ne else ""),
            str(ne.ip_address if ne else "").strip(),
            _norm_key(ume.host_name if ume else ""),
            _norm_key(ume.ne_name if ume else ""),
            _norm_key(ume.user_label if ume else ""),
            str(ume.ip_address if ume else "").strip(),
        ]
        cand_set = {_norm_key(c) for c in candidates if str(c or "").strip()}
        if ip_key and ip_key in {str(c).strip() for c in candidates if str(c or "").strip()}:
            return n
        if name_key and name_key in cand_set:
            return n
    return None


def _edge_pair_key(a: str, b: str, local_port: str, remote_port: str) -> tuple[str, str, str, str]:
    lp = normalize_ifname(local_port)
    rp = normalize_ifname(remote_port)
    if a <= b:
        return (a, b, lp, rp)
    return (b, a, rp, lp)


def _discover_target_for_node(
    n: TopologyNode,
    *,
    nes: dict[str, ManagedNE],
    umes: dict[str, UmeInventoryNE],
    filter_ids: set[str],
    default_profile,
) -> dict[str, str] | None:
    """Resolve CLI target for a topology node (managed preferred, else UME)."""
    mid = str(n.managed_ne_id or "").strip()
    uid = str(n.ume_ne_id or "").strip()
    if filter_ids and mid not in filter_ids and uid not in filter_ids:
        return None
    if mid and mid in nes:
        ne = nes[mid]
        return {
            "ne_id": ne.id,
            "ume_ne_id": "",
            "ne_name": ne.name or "",
            "ne_ip": ne.ip_address or "",
            "vendor": ne.vendor or "",
            "device_type": ne.device_type or "",
        }
    if uid and uid in umes:
        ume = umes[uid]
        if default_profile is not None:
            dtype, vendor = infer_device_type_vendor(str(ume.ne_type or ""), default_profile)
        else:
            dtype, vendor = "zte_zxros", (ume.vendor or "ZTE")
        name = (ume.host_name or ume.ne_name or ume.user_label or ume.ip_address or uid).strip()
        return {
            "ne_id": uid,
            "ume_ne_id": uid,
            "ne_name": name,
            "ne_ip": ume.ip_address or "",
            "vendor": vendor or (ume.vendor or "ZTE"),
            "device_type": dtype or "zte_zxros",
        }
    return None


def iter_discover_neighbors(
    db: Session,
    map_id: str,
    body: TopologyDiscoverRequest,
):
    """Yield discovery progress events: start / ne_start / ne_result / done / error."""
    row = _get_map_or_404(db, map_id)
    nodes = db.query(TopologyNode).filter(TopologyNode.map_id == row.id).all()
    edges = db.query(TopologyEdge).filter(TopologyEdge.map_id == row.id).all()
    nes = _ne_lookup(db, {str(n.managed_ne_id or "") for n in nodes if n.managed_ne_id})
    umes = _ume_lookup(db, {str(n.ume_ne_id or "") for n in nodes if n.ume_ne_id})
    default_profile = get_default_profile(db)

    filter_ids = {str(x).strip() for x in (body.ne_ids or []) if str(x).strip()}
    scan_targets: list[tuple[TopologyNode, dict[str, str]]] = []
    for n in nodes:
        target = _discover_target_for_node(
            n, nes=nes, umes=umes, filter_ids=filter_ids, default_profile=default_profile
        )
        if target is not None:
            scan_targets.append((n, target))

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
    stale_count = 0
    now = _utcnow()
    proto_req = str(body.protocol or "auto").strip().lower() or "auto"
    total = len(scan_targets)
    touched_edge_ids: set[str] = set()
    scanned_ok_node_ids: set[str] = set()

    yield {
        "type": "start",
        "map_id": row.id,
        "protocol": proto_req,
        "total": total,
    }

    for index, (n, target) in enumerate(scan_targets, start=1):
        yield {
            "type": "ne_start",
            "index": index,
            "total": total,
            "ne_id": target["ne_id"],
            "ne_name": target["ne_name"],
            "ne_ip": target["ne_ip"],
        }
        cmd, proto_tag = pick_neighbor_command(
            protocol=proto_req,
            vendor=target["vendor"],
            device_type=target["device_type"],
        )
        if not cmd:
            result = TopologyDiscoverNeResult(
                ne_id=target["ne_id"],
                ne_name=target["ne_name"],
                ne_ip=target["ne_ip"],
                ok=False,
                error="no_command_for_vendor",
            )
            results.append(result)
            yield {
                "type": "ne_result",
                "index": index,
                "total": total,
                "result": result.model_dump(mode="json"),
                "edges_added": added,
                "edges_updated": updated,
                "edges_stale": stale_count,
            }
            continue

        exec_kwargs: dict[str, Any] = {"read_timeout_sec": 60}
        if target["ume_ne_id"] and not str(n.managed_ne_id or "").strip():
            exec_kwargs["ume_ne_id"] = target["ume_ne_id"]
        else:
            exec_kwargs["ne_id"] = target["ne_id"]
        try:
            exec_out = execute_managed_ne_commands(db, [cmd], **exec_kwargs)
        except HTTPException as exc:
            result = TopologyDiscoverNeResult(
                ne_id=target["ne_id"],
                ne_name=target["ne_name"],
                ne_ip=target["ne_ip"],
                ok=False,
                command=cmd,
                error=str(exc.detail or "exec_failed")[:500],
            )
            results.append(result)
            yield {
                "type": "ne_result",
                "index": index,
                "total": total,
                "result": result.model_dump(mode="json"),
                "edges_added": added,
                "edges_updated": updated,
                "edges_stale": stale_count,
            }
            continue
        if not exec_out.get("ok"):
            result = TopologyDiscoverNeResult(
                ne_id=target["ne_id"],
                ne_name=target["ne_name"],
                ne_ip=target["ne_ip"],
                ok=False,
                command=cmd,
                error=str(exec_out.get("detail") or exec_out.get("error") or "exec_failed")[:500],
            )
            results.append(result)
            yield {
                "type": "ne_result",
                "index": index,
                "total": total,
                "result": result.model_dump(mode="json"),
                "edges_added": added,
                "edges_updated": updated,
                "edges_stale": stale_count,
            }
            continue

        scanned_ok_node_ids.add(n.id)
        raw = str(exec_out.get("output") or "")
        hits = parse_neighbor_output(
            raw,
            protocol=proto_tag,
            vendor=target["vendor"],
            device_type=target["device_type"],
        )
        ne_added = 0
        ne_updated = 0
        for hit in hits:
            peer = _match_neighbor_to_node(
                hit, nodes=nodes, nes=nes, umes=umes, self_node_id=n.id
            )
            if peer is None:
                continue
            local_port = (hit.local_port or "").strip()[:128]
            remote_port = (hit.remote_port or "").strip()[:128]
            key = _edge_pair_key(n.id, peer.id, local_port, remote_port)
            edge_proto = hit.protocol if hit.protocol in {"lldp", "cdp"} else proto_tag
            cur = existing.get(key)
            if cur is not None:
                if (cur.source or "manual") == "manual":
                    continue
                cur.source = edge_proto
                cur.source_port = local_port if cur.source_node_id == n.id else remote_port
                cur.target_port = remote_port if cur.source_node_id == n.id else local_port
                cur.discovered_at = now
                cur.updated_at = now
                touched_edge_ids.add(cur.id)
                ne_updated += 1
                updated += 1
                continue
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
            touched_edge_ids.add(new_edge.id)
            ne_added += 1
            added += 1

        result = TopologyDiscoverNeResult(
            ne_id=target["ne_id"],
            ne_name=target["ne_name"],
            ne_ip=target["ne_ip"],
            ok=True,
            command=cmd,
            neighbors=len(hits),
            edges_added=ne_added,
            edges_updated=ne_updated,
            raw_preview=raw[:800],
        )
        results.append(result)
        yield {
            "type": "ne_result",
            "index": index,
            "total": total,
            "result": result.model_dump(mode="json"),
            "edges_added": added,
            "edges_updated": updated,
            "edges_stale": stale_count,
        }

    # Mark previously discovered edges not refreshed by this run as stale
    # (only when at least one endpoint was successfully scanned).
    if scanned_ok_node_ids:
        for e in edges:
            src = (e.source or "manual").strip().lower()
            if src not in {"lldp", "cdp", "stale"}:
                continue
            if e.id in touched_edge_ids:
                continue
            if e.source_node_id not in scanned_ok_node_ids and e.target_node_id not in scanned_ok_node_ids:
                continue
            e.source = "stale"
            e.updated_at = now
            stale_count += 1

    row.updated_at = now
    db.commit()
    graph = get_graph(db, row.id)
    report = TopologyDiscoverOut(
        map_id=row.id,
        protocol=proto_req,
        scanned=len(results),
        edges_added=added,
        edges_updated=updated,
        edges_stale=stale_count,
        results=results,
        graph=graph,
    )
    yield {"type": "done", "report": report.model_dump(mode="json")}


def discover_neighbors(
    db: Session,
    map_id: str,
    body: TopologyDiscoverRequest,
) -> TopologyDiscoverOut:
    report: TopologyDiscoverOut | None = None
    for event in iter_discover_neighbors(db, map_id, body):
        if event.get("type") == "done":
            report = TopologyDiscoverOut.model_validate(event.get("report") or {})
    if report is None:
        raise HTTPException(status_code=500, detail="discover_failed")
    return report
