"""UME TopoNodes + TopologicalLinks sync (phase-1: local tables only)."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from .models import UmeSyncJob, UmeTopoLink, UmeTopoNode
from .ume_client import UMEClient
from .ume_raw import dumps_ume_raw
from .ume_sync_common import _pick, _s, _utc_now_naive
from .ume_sync_pull import _build_sync_job

_sync_log = logging.getLogger("netx.ume.sync")

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_ME_BRACE_RE = re.compile(
    r"ME\{([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\}",
    re.IGNORECASE,
)
_PTP_RE = re.compile(r"PTP=\{([^}]*)\}", re.IGNORECASE)


def extract_me_uuid(text: str) -> str:
    """Extract managed-element uuid from TP ref or TOPO_NODE_ME* name."""
    s = str(text or "").strip()
    if not s:
        return ""
    m = _ME_BRACE_RE.search(s)
    if m:
        return m.group(1)
    # TOPO_NODE_ME<uuid> (no braces) or trailing uuid
    low = s.upper()
    if "TOPO_NODE_ME" in low:
        idx = low.find("TOPO_NODE_ME")
        rest = s[idx + len("TOPO_NODE_ME") :]
        m2 = _UUID_RE.search(rest)
        if m2:
            return m2.group(0)
    m3 = _UUID_RE.search(s)
    return m3.group(0) if m3 else ""


def extract_ptp(text: str) -> str:
    s = str(text or "").strip()
    if not s:
        return ""
    m = _PTP_RE.search(s)
    return (m.group(1) if m else "")[:256]


def _first_tp_ref(value: Any) -> str:
    if isinstance(value, list):
        for item in value:
            s = _s(item)
            if s:
                return s
        return ""
    return _s(value)


def _as_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


def _link_id_from_row(row: dict[str, Any]) -> str:
    lid = _s(_pick(row, "linkId", "link-id", "link_id", "id"))
    if lid:
        return lid[:128]
    name = _s(_pick(row, "name"))
    if not name:
        return ""
    digest = hashlib.sha1(name.encode("utf-8", errors="replace")).hexdigest()[:40]
    return f"name:{digest}"[:128]


def _node_id_from_row(row: dict[str, Any]) -> str:
    nid = _s(_pick(row, "nodeId", "node-id", "node_id", "id"))
    if nid:
        return nid[:128]
    name = _s(_pick(row, "name"))
    if not name:
        return ""
    digest = hashlib.sha1(name.encode("utf-8", errors="replace")).hexdigest()[:40]
    return f"name:{digest}"[:128]


def _ume_ne_id_for_topo_node(*, node_type: str, name: str) -> str:
    nt = str(node_type or "").strip().upper()
    if nt != "TOPO_NODE_ME":
        return ""
    return extract_me_uuid(name)


def sync_topology_full(db: Session, client: UMEClient, *, trigger_mode: str = "manual") -> UmeSyncJob:
    """Pull TopoNodes + TopologicalLinks into local tables (no Fabric apply)."""
    job = _build_sync_job("topology", trigger_mode)
    db.add(job)
    db.flush()
    db.commit()
    _sync_log.info("topology sync job %s committed as running (trigger=%s)", getattr(job, "id", "?"), trigger_mode)

    pulled = inserted = updated = 0
    nodes_pulled = nodes_ins = nodes_upd = nodes_del = 0
    links_pulled = links_ins = links_upd = links_del = 0
    nodes_skip = links_skip = 0

    try:
        now = _utc_now_naive()

        node_rows, node_diag = client.get_topo_nodes()
        nodes_pulled = len(node_rows)
        seen_nodes: set[str] = set()
        for row in node_rows:
            if not isinstance(row, dict):
                nodes_skip += 1
                continue
            node_id = _node_id_from_row(row)
            if not node_id:
                nodes_skip += 1
                continue
            seen_nodes.add(node_id)
            name = _s(_pick(row, "name"))
            node_type = _s(_pick(row, "nodeType", "node-type", "node_type"))
            existing = db.get(UmeTopoNode, node_id)
            if existing is None:
                existing = UmeTopoNode(node_id=node_id, first_seen_at=now)
                db.add(existing)
                nodes_ins += 1
            else:
                nodes_upd += 1
            existing.name = name[:512]
            existing.node_type = node_type[:64]
            existing.user_label = _s(_pick(row, "userLabel", "user-label", "user_label"))[:512]
            existing.owner = _s(_pick(row, "owner"))[:64]
            existing.parent_node = _s(_pick(row, "parentNode", "parent-node", "parent_node"))[:512]
            existing.x_pos = _as_optional_int(_pick(row, "xPos", "x-pos", "x_pos"))
            existing.y_pos = _as_optional_int(_pick(row, "yPos", "y-pos", "y_pos"))
            existing.ume_ne_id = _ume_ne_id_for_topo_node(node_type=node_type, name=name)[:128]
            existing.last_seen_at = now
            existing.raw_json = dumps_ume_raw(row)

        db.flush()
        if seen_nodes:
            nodes_del = int(
                db.query(UmeTopoNode)
                .filter(~UmeTopoNode.node_id.in_(list(seen_nodes)))
                .delete(synchronize_session=False)
            )
        else:
            # Successful empty snapshot → clear local table.
            nodes_del = int(db.query(UmeTopoNode).delete(synchronize_session=False))

        link_rows, link_diag = client.get_topological_links()
        links_pulled = len(link_rows)
        seen_links: set[str] = set()
        for row in link_rows:
            if not isinstance(row, dict):
                links_skip += 1
                continue
            link_id = _link_id_from_row(row)
            if not link_id:
                links_skip += 1
                continue
            seen_links.add(link_id)
            a_ref = _first_tp_ref(_pick(row, "aEndTpRefList", "a-end-tp-ref-list", "aEndTpRef"))
            z_ref = _first_tp_ref(_pick(row, "zEndTpRefList", "z-end-tp-ref-list", "zEndTpRef"))
            existing = db.get(UmeTopoLink, link_id)
            if existing is None:
                existing = UmeTopoLink(link_id=link_id, first_seen_at=now)
                db.add(existing)
                links_ins += 1
            else:
                links_upd += 1
            existing.name = _s(_pick(row, "name"))[:1024]
            existing.user_label = _s(_pick(row, "userLabel", "user-label", "user_label"))
            existing.owner = _s(_pick(row, "owner"))[:64]
            existing.direction = _s(_pick(row, "direction"))[:32]
            existing.layer_rate = _as_optional_int(_pick(row, "layerRate", "layer-rate", "layer_rate"))
            existing.connection_status = _s(
                _pick(row, "connection-status", "connectionStatus", "connection_status")
            )[:64]
            existing.a_end_tp_ref = a_ref
            existing.z_end_tp_ref = z_ref
            existing.a_ume_ne_id = extract_me_uuid(a_ref)[:128]
            existing.z_ume_ne_id = extract_me_uuid(z_ref)[:128]
            existing.a_ptp = extract_ptp(a_ref)
            existing.z_ptp = extract_ptp(z_ref)
            existing.last_seen_at = now
            existing.raw_json = dumps_ume_raw(row)

        db.flush()
        if seen_links:
            links_del = int(
                db.query(UmeTopoLink)
                .filter(~UmeTopoLink.link_id.in_(list(seen_links)))
                .delete(synchronize_session=False)
            )
        else:
            links_del = int(db.query(UmeTopoLink).delete(synchronize_session=False))

        pulled = nodes_pulled + links_pulled
        inserted = nodes_ins + links_ins
        updated = nodes_upd + links_upd

        job.details_json = json.dumps(
            {
                "nodes": {
                    "pulled": nodes_pulled,
                    "inserted": nodes_ins,
                    "updated": nodes_upd,
                    "deleted": nodes_del,
                    "skipped": nodes_skip,
                    "latency_ms": int(getattr(node_diag, "latency_ms", 0) or 0),
                },
                "links": {
                    "pulled": links_pulled,
                    "inserted": links_ins,
                    "updated": links_upd,
                    "deleted": links_del,
                    "skipped": links_skip,
                    "latency_ms": int(getattr(link_diag, "latency_ms", 0) or 0),
                },
                "deleted_topo_nodes": nodes_del,
                "deleted_topo_links": links_del,
            },
            ensure_ascii=False,
        )
        job.status = "done"
        _sync_log.info(
            "topology sync done nodes=%s/%s/%s links=%s/%s/%s deleted_n=%s deleted_l=%s",
            nodes_pulled,
            nodes_ins,
            nodes_upd,
            links_pulled,
            links_ins,
            links_upd,
            nodes_del,
            links_del,
        )
    except Exception as exc:
        job.status = "failed"
        job.error_message = str(exc)[:1024]
        _sync_log.exception("topology sync failed: %s", exc)
    finally:
        job.pulled_count = int(pulled)
        job.inserted_count = int(inserted)
        job.updated_count = int(updated)
        job.ended_at = _utc_now_naive()
        db.commit()
        db.refresh(job)
    return job
