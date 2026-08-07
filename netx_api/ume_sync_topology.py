"""UME TopoNodes + TopologicalLinks sync, then apply into Fabric world map."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from datetime import timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError, OperationalError, InvalidRequestError
from sqlalchemy.orm import Session, sessionmaker

from .config import settings
from .db import SessionLocal
from .models import UmeSyncJob, UmeTopoLink, UmeTopoNode
from .ume_client import UMEClient
from .ume_raw import dumps_ume_raw
from .ume_port_normalize import resolve_link_ifnames
from .ume_sync_common import _pick, _s, _utc_now_naive
from .ume_sync_pull import _build_sync_job

_sync_log = logging.getLogger("netx.ume.sync")
_TOPOLOGY_SYNC_LOCK = threading.Lock()

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
# Onsite FDN: MD=ZTE/UME(BN);ME=0ab0b408-... or ;SBN=47173499-...
_ME_EQ_RE = re.compile(
    r"(?:^|[;,/])ME=([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
    re.IGNORECASE,
)
_SBN_EQ_RE = re.compile(
    r"(?:^|[;,/])SBN=([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
    re.IGNORECASE,
)
_ME_BRACE_RE = re.compile(
    r"ME\{([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\}",
    re.IGNORECASE,
)
_SBN_BRACE_RE = re.compile(
    r"SBN\{([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\}",
    re.IGNORECASE,
)
_PTP_RE = re.compile(r"PTP=\{([^}]*)\}", re.IGNORECASE)


def extract_me_uuid(text: str) -> str:
    """Extract managed-element uuid from FDN / TP ref / TOPO_NODE_ME* name."""
    s = str(text or "").strip()
    if not s:
        return ""
    m = _ME_EQ_RE.search(s)
    if m:
        return m.group(1)
    m = _ME_BRACE_RE.search(s)
    if m:
        return m.group(1)
    low = s.upper()
    if "TOPO_NODE_ME" in low:
        idx = low.find("TOPO_NODE_ME")
        rest = s[idx + len("TOPO_NODE_ME") :]
        m2 = _UUID_RE.search(rest)
        if m2:
            return m2.group(0)
    return ""


def extract_topo_object_uuid(text: str) -> str:
    """Extract ME/SBN uuid from onsite FDN name (preferred stable node_id)."""
    s = str(text or "").strip()
    if not s:
        return ""
    for cre in (_ME_EQ_RE, _SBN_EQ_RE, _ME_BRACE_RE, _SBN_BRACE_RE):
        m = cre.search(s)
        if m:
            return m.group(1)
    low = s.upper()
    for prefix in ("TOPO_NODE_ME", "TOPO_NODE_SBN"):
        if prefix in low:
            idx = low.find(prefix)
            rest = s[idx + len(prefix) :]
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
    # Prefer endpoint ME uuids embedded in TL name when linkId is absent.
    obj = extract_topo_object_uuid(name)
    if obj:
        # Keep uniqueness for nameless links by hashing full name but avoid bare name: when possible
        digest = hashlib.sha1(name.encode("utf-8", errors="replace")).hexdigest()[:40]
        return f"tl:{digest}"[:128]
    digest = hashlib.sha1(name.encode("utf-8", errors="replace")).hexdigest()[:40]
    return f"name:{digest}"[:128]


def _node_id_from_row(row: dict[str, Any]) -> str:
    """Stable PK: nodeId if present, else ME=/SBN= uuid from onsite FDN ``name``."""
    nid = _s(_pick(row, "nodeId", "node-id", "node_id", "id"))
    if nid:
        return nid[:128]
    name = _s(_pick(row, "name"))
    if not name:
        return ""
    obj = extract_topo_object_uuid(name)
    if obj:
        return obj[:128]
    digest = hashlib.sha1(name.encode("utf-8", errors="replace")).hexdigest()[:40]
    return f"name:{digest}"[:128]


def _ume_ne_id_for_topo_node(*, node_type: str, name: str) -> str:
    nt = str(node_type or "").strip().upper()
    if nt != "TOPO_NODE_ME":
        return ""
    return extract_me_uuid(name) or extract_topo_object_uuid(name)


def _normalize_parent_node(raw: str) -> str:
    """Store parent as ME/SBN uuid when parentNode is an onsite FDN string."""
    s = _s(raw)
    if not s:
        return ""
    obj = extract_topo_object_uuid(s)
    return (obj or s)[:512]


def _stale_running_sec() -> int:
    return max(3600, int(getattr(settings, "ume_sync_topology_stale_running_sec", 86400) or 86400))


def fail_stale_topology_running_jobs(db: Session | None = None) -> int:
    """Close topology jobs stuck in running (hung HTTP / dead session finalize)."""
    own = db is None
    sess = db if db is not None else SessionLocal()
    try:
        cutoff = _utc_now_naive() - timedelta(seconds=_stale_running_sec())
        rows = (
            sess.query(UmeSyncJob)
            .filter(
                UmeSyncJob.domain == "topology",
                UmeSyncJob.status == "running",
                UmeSyncJob.ended_at.is_(None),
                UmeSyncJob.started_at < cutoff,
            )
            .all()
        )
        if not rows:
            return 0
        now = _utc_now_naive()
        for row in rows:
            row.status = "failed"
            row.ended_at = now
            msg = str(row.error_message or "").strip()
            suffix = "stale_running_topology_reaped"
            row.error_message = (msg + ("; " if msg else "") + suffix)[:1024]
        sess.commit()
        _sync_log.warning("topology sync: reaped %s stale running jobs", len(rows))
        return len(rows)
    except Exception:
        _sync_log.exception("topology sync: stale reap failed")
        try:
            sess.rollback()
        except Exception:
            pass
        return 0
    finally:
        if own:
            sess.close()


def _active_topology_running(db: Session) -> UmeSyncJob | None:
    cutoff = _utc_now_naive() - timedelta(seconds=_stale_running_sec())
    return (
        db.query(UmeSyncJob)
        .filter(
            UmeSyncJob.domain == "topology",
            UmeSyncJob.status == "running",
            UmeSyncJob.ended_at.is_(None),
            UmeSyncJob.started_at >= cutoff,
        )
        .order_by(UmeSyncJob.id.desc())
        .first()
    )


def _session_factory_for(db: Session):
    """Open a sibling session on the same bind (tests use sqlite; prod uses pool)."""
    bind = db.get_bind()
    return sessionmaker(bind=bind, autoflush=False, autocommit=False, expire_on_commit=False)


def _finalize_topology_job(
    job_id: int,
    *,
    status: str,
    pulled: int,
    inserted: int,
    updated: int,
    error_message: str = "",
    details_json: str = "{}",
    db: Session | None = None,
) -> UmeSyncJob | None:
    """Persist job end state on a fresh session so a dead pull-session cannot leave running forever."""
    if db is not None:
        sess = _session_factory_for(db)()
    else:
        sess = SessionLocal()
    try:
        row = sess.get(UmeSyncJob, int(job_id))
        if row is None:
            return None
        row.status = status
        row.pulled_count = int(pulled)
        row.inserted_count = int(inserted)
        row.updated_count = int(updated)
        row.error_message = str(error_message or "")[:1024]
        row.details_json = str(details_json or "{}")
        row.ended_at = _utc_now_naive()
        sess.commit()
        sess.refresh(row)
        return row
    except Exception:
        _sync_log.exception("topology sync: finalize job %s failed", job_id)
        try:
            sess.rollback()
        except Exception:
            pass
        return None
    finally:
        sess.close()


def _safe_rollback(sess: Session) -> None:
    try:
        sess.rollback()
    except Exception:
        pass


def _dedupe_rows_by_id(
    rows: list[Any], id_fn
) -> tuple[list[tuple[str, dict[str, Any]]], int]:
    """Keep last row per id; drop non-dicts / missing ids."""
    by_id: dict[str, dict[str, Any]] = {}
    skipped = 0
    for row in rows:
        if not isinstance(row, dict):
            skipped += 1
            continue
        rid = str(id_fn(row) or "").strip()
        if not rid:
            skipped += 1
            continue
        by_id[rid] = row
    return list(by_id.items()), skipped


def _load_node(work: Session, node_id: str) -> UmeTopoNode | None:
    row = work.get(UmeTopoNode, node_id)
    if row is not None:
        return row
    return work.query(UmeTopoNode).filter(UmeTopoNode.node_id == node_id).one_or_none()


def _load_link(work: Session, link_id: str) -> UmeTopoLink | None:
    row = work.get(UmeTopoLink, link_id)
    if row is not None:
        return row
    return work.query(UmeTopoLink).filter(UmeTopoLink.link_id == link_id).one_or_none()


def _upsert_topo_node(work: Session, node_id: str, row: dict[str, Any], *, now) -> str:
    """Return 'inserted' | 'updated'."""
    name = _s(_pick(row, "name"))
    node_type = _s(_pick(row, "nodeType", "node-type", "node_type"))

    def _apply(existing: UmeTopoNode) -> None:
        existing.name = name[:512]
        existing.node_type = node_type[:64]
        existing.user_label = _s(_pick(row, "userLabel", "user-label", "user_label"))[:512]
        existing.owner = _s(_pick(row, "owner"))[:64]
        existing.parent_node = _normalize_parent_node(
            _s(_pick(row, "parentNode", "parent-node", "parent_node"))
        )
        existing.x_pos = _as_optional_int(_pick(row, "xPos", "x-pos", "x_pos"))
        existing.y_pos = _as_optional_int(_pick(row, "yPos", "y-pos", "y_pos"))
        existing.ume_ne_id = _ume_ne_id_for_topo_node(node_type=node_type, name=name)[:128]
        existing.last_seen_at = now
        existing.raw_json = dumps_ume_raw(row)

    existing = _load_node(work, node_id)
    if existing is not None:
        _apply(existing)
        return "updated"

    existing = UmeTopoNode(node_id=node_id, first_seen_at=now)
    _apply(existing)
    try:
        with work.begin_nested():
            work.add(existing)
            work.flush()
        return "inserted"
    except IntegrityError:
        try:
            work.expunge(existing)
        except Exception:
            pass
        existing = _load_node(work, node_id)
        if existing is None:
            raise
        _apply(existing)
        return "updated"


def _upsert_topo_link(work: Session, link_id: str, row: dict[str, Any], *, now) -> str:
    """Return 'inserted' | 'updated'."""
    a_ref = _first_tp_ref(_pick(row, "aEndTpRefList", "a-end-tp-ref-list", "aEndTpRef"))
    z_ref = _first_tp_ref(_pick(row, "zEndTpRefList", "z-end-tp-ref-list", "zEndTpRef"))

    def _apply(existing: UmeTopoLink) -> None:
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
        label = _s(_pick(row, "userLabel", "user-label", "user_label"))
        a_if, z_if = resolve_link_ifnames(
            a_end_tp_ref=a_ref, z_end_tp_ref=z_ref, user_label=label
        )
        existing.a_ifname = a_if[:128]
        existing.z_ifname = z_if[:128]
        existing.last_seen_at = now
        existing.raw_json = dumps_ume_raw(row)

    existing = _load_link(work, link_id)
    if existing is not None:
        _apply(existing)
        return "updated"

    existing = UmeTopoLink(link_id=link_id, first_seen_at=now)
    _apply(existing)
    try:
        with work.begin_nested():
            work.add(existing)
            work.flush()
        return "inserted"
    except IntegrityError:
        try:
            work.expunge(existing)
        except Exception:
            pass
        existing = _load_link(work, link_id)
        if existing is None:
            raise
        _apply(existing)
        return "updated"


def _delete_missing_ids(work: Session, model, pk_col, seen_ids: set[str]) -> int:
    if not seen_ids:
        return int(work.query(model).delete(synchronize_session=False))
    all_pks = [str(x[0]) for x in work.query(pk_col).all() if str(x[0] or "").strip()]
    missing = [pk for pk in all_pks if pk not in seen_ids]
    deleted = 0
    chunk = 5000
    for i in range(0, len(missing), chunk):
        part = missing[i : i + chunk]
        deleted += int(work.query(model).filter(pk_col.in_(part)).delete(synchronize_session=False))
    return deleted


def sync_topology_full(db: Session, client: UMEClient, *, trigger_mode: str = "manual") -> UmeSyncJob:
    """Pull TopoNodes + TopologicalLinks into dock tables, then apply into Fabric."""
    if not _TOPOLOGY_SYNC_LOCK.acquire(blocking=False):
        raise RuntimeError("topology_sync_busy")

    try:
        fail_stale_topology_running_jobs(db)
        active = _active_topology_running(db)
        if active is not None:
            raise RuntimeError(f"topology_sync_busy:job_id={active.id}")

        job = _build_sync_job("topology", trigger_mode)
        db.add(job)
        db.flush()
        db.commit()
        job_id = int(job.id)
        _sync_log.info(
            "topology sync job %s committed as running (trigger=%s)",
            job_id,
            trigger_mode,
        )

        pulled = inserted = updated = 0
        nodes_pulled = nodes_ins = nodes_upd = nodes_del = 0
        links_pulled = links_ins = links_upd = links_del = 0
        nodes_skip = links_skip = 0
        details = "{}"
        err = ""
        status = "done"

        try:
            try:
                db.expire_all()
            except Exception:
                pass

            work = _session_factory_for(db)()
            try:
                now = _utc_now_naive()
                _sync_log.info("topology sync job %s: pulling TopoNodes…", job_id)
                node_rows, node_diag = client.get_topo_nodes()
                nodes_pulled = len(node_rows)
                pairs, nodes_skip = _dedupe_rows_by_id(node_rows, _node_id_from_row)
                _sync_log.info(
                    "topology sync job %s: TopoNodes rows=%s unique=%s latency_ms=%s",
                    job_id,
                    nodes_pulled,
                    len(pairs),
                    int(getattr(node_diag, "latency_ms", 0) or 0),
                )
                seen_nodes: set[str] = set()
                for i, (node_id, row) in enumerate(pairs):
                    seen_nodes.add(node_id)
                    try:
                        action = _upsert_topo_node(work, node_id, row, now=now)
                    except (IntegrityError, OperationalError, InvalidRequestError):
                        _safe_rollback(work)
                        action = _upsert_topo_node(work, node_id, row, now=now)
                    if action == "inserted":
                        nodes_ins += 1
                    else:
                        nodes_upd += 1
                    if (i + 1) % 2000 == 0:
                        work.commit()
                        _sync_log.info(
                            "topology sync job %s: nodes progress %s/%s",
                            job_id,
                            i + 1,
                            len(pairs),
                        )

                work.flush()
                nodes_del = _delete_missing_ids(work, UmeTopoNode, UmeTopoNode.node_id, seen_nodes)
                work.commit()

                _sync_log.info("topology sync job %s: pulling TopologicalLinks…", job_id)
                link_rows, link_diag = client.get_topological_links()
                links_pulled = len(link_rows)
                link_pairs, links_skip = _dedupe_rows_by_id(link_rows, _link_id_from_row)
                _sync_log.info(
                    "topology sync job %s: TopologicalLinks rows=%s unique=%s latency_ms=%s",
                    job_id,
                    links_pulled,
                    len(link_pairs),
                    int(getattr(link_diag, "latency_ms", 0) or 0),
                )
                seen_links: set[str] = set()
                for i, (link_id, row) in enumerate(link_pairs):
                    seen_links.add(link_id)
                    try:
                        action = _upsert_topo_link(work, link_id, row, now=now)
                    except (IntegrityError, OperationalError, InvalidRequestError):
                        _safe_rollback(work)
                        action = _upsert_topo_link(work, link_id, row, now=now)
                    if action == "inserted":
                        links_ins += 1
                    else:
                        links_upd += 1
                    if (i + 1) % 2000 == 0:
                        work.commit()
                        _sync_log.info(
                            "topology sync job %s: links progress %s/%s",
                            job_id,
                            i + 1,
                            len(link_pairs),
                        )

                work.flush()
                links_del = _delete_missing_ids(work, UmeTopoLink, UmeTopoLink.link_id, seen_links)
                work.commit()

                apply_stats: dict[str, Any] = {}
                apply_ok = False
                apply_err = ""
                try:
                    from .ume_topology_apply import apply_ume_topology_to_fabric
                    from .ume_topology_world import ensure_ume_world_and_sbn_folders

                    _sync_log.info("topology sync job %s: applying dock → fabric…", job_id)
                    apply_db = _session_factory_for(db)()
                    try:
                        apply_stats = apply_ume_topology_to_fabric(apply_db)
                        world_stats = ensure_ume_world_and_sbn_folders(apply_db)
                        apply_stats = {**apply_stats, "world": world_stats}
                        apply_ok = True
                    finally:
                        try:
                            apply_db.close()
                        except Exception:
                            pass
                except Exception as apply_exc:
                    apply_err = str(apply_exc)[:500]
                    apply_stats = {"error": apply_err, "ok": False}
                    _sync_log.exception(
                        "topology sync job %s: fabric apply failed (dock sync kept)",
                        job_id,
                    )

                if not apply_ok:
                    status = "partial"
                    err = f"dock_ok_fabric_apply_failed:{apply_err}"[:1024]

                pulled = nodes_pulled + links_pulled
                inserted = nodes_ins + links_ins
                updated = nodes_upd + links_upd
                details = json.dumps(
                    {
                        "nodes": {
                            "pulled": nodes_pulled,
                            "unique": len(seen_nodes),
                            "inserted": nodes_ins,
                            "updated": nodes_upd,
                            "deleted": nodes_del,
                            "skipped": nodes_skip,
                            "latency_ms": int(getattr(node_diag, "latency_ms", 0) or 0),
                        },
                        "links": {
                            "pulled": links_pulled,
                            "unique": len(seen_links),
                            "inserted": links_ins,
                            "updated": links_upd,
                            "deleted": links_del,
                            "skipped": links_skip,
                            "latency_ms": int(getattr(link_diag, "latency_ms", 0) or 0),
                        },
                        "deleted_topo_nodes": nodes_del,
                        "deleted_topo_links": links_del,
                        "fabric_apply": apply_stats,
                        "fabric_apply_ok": apply_ok,
                    },
                    ensure_ascii=False,
                )
                _sync_log.info(
                    "topology sync done job=%s status=%s nodes=%s/%s/%s links=%s/%s/%s deleted_n=%s deleted_l=%s apply_ok=%s",
                    job_id,
                    status,
                    nodes_pulled,
                    nodes_ins,
                    nodes_upd,
                    links_pulled,
                    links_ins,
                    links_upd,
                    nodes_del,
                    links_del,
                    apply_ok,
                )
            finally:
                try:
                    work.close()
                except Exception:
                    pass
        except Exception as exc:
            status = "failed"
            err = str(exc)[:1024]
            _sync_log.exception("topology sync failed job=%s: %s", job_id, exc)

        finalized = _finalize_topology_job(
            job_id,
            status=status,
            pulled=pulled,
            inserted=inserted,
            updated=updated,
            error_message=err,
            details_json=details,
            db=db,
        )
        if finalized is not None:
            return finalized
        orphan = UmeSyncJob(
            domain="topology",
            status=status,
            trigger_mode=trigger_mode,
            pulled_count=pulled,
            inserted_count=inserted,
            updated_count=updated,
            error_message=(err or "finalize_failed")[:1024],
            details_json=details,
            ended_at=_utc_now_naive(),
        )
        orphan.id = job_id
        return orphan
    finally:
        _TOPOLOGY_SYNC_LOCK.release()
