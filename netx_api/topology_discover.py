"""LLDP fabric discover jobs (background scan)."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .cli_resolve import get_default_profile
from .config import settings
from .db import SessionLocal
from .models import (
    LldpCollectPolicy,
    ManagedNE,
    TopoDiscoverJob,
    TopoDiscoverJobItem,
    TopoFabricNode,
    TopoFabricStats,
    UmeInventoryNE,
)
from .ne_exec import execute_managed_ne_commands
from .topology_common import (
    PAGE_DEFAULT,
    _DISCOVER_DEADLOCK_RETRIES,
    _JOB_LOCK,
    _RAW_PREVIEW_MAX,
    _RUNNING_JOBS,
    _is_deadlock_error,
    _sleep_deadlock_backoff,
    _utcnow,
)
from .topology_fabric import (
    _FabricPeerIndex,
    _apply_missing_and_purge,
    _mark_replaced_port_peers,
    _match_hit_to_fabric_node,
    ensure_fabric_node_for_managed,
    ensure_fabric_node_for_ume,
    ensure_lldp_discovered_managed_ne,
    merge_duplicate_fabric_nodes,
    refresh_fabric_stats,
    upsert_fabric_edge,
)
from .topology_lldp import (
    NeighborHit,
    parse_neighbor_output,
    parser_meta,
    pick_neighbor_command,
)
from .topology_schemas import (
    FabricDiscoverJobItemOut,
    FabricDiscoverJobOut,
    FabricDiscoverRequest,
    FabricDiscoverUnmatched,
)

def _raw_preview(raw: str, *, limit: int = _RAW_PREVIEW_MAX) -> str:
    text = str(raw or "")
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[truncated preview {limit}/{len(text)} chars]"


def _job_out(db: Session, job: TopoDiscoverJob, *, include_items: bool = True) -> FabricDiscoverJobOut:
    items_out: list[FabricDiscoverJobItemOut] = []
    if include_items:
        items = (
            db.query(TopoDiscoverJobItem)
            .filter(TopoDiscoverJobItem.job_id == job.id)
            .order_by(TopoDiscoverJobItem.created_at.asc())
            .all()
        )
        for it in items:
            unmatched = [
                FabricDiscoverUnmatched.model_validate(x) for x in (it.unmatched_json or [])[:40]
            ]
            items_out.append(
                FabricDiscoverJobItemOut(
                    id=it.id,
                    job_id=it.job_id,
                    ne_id=it.ne_id or "",
                    ume_ne_id=it.ume_ne_id or "",
                    fabric_node_id=it.fabric_node_id or "",
                    ne_name=it.ne_name or "",
                    ne_ip=it.ne_ip or "",
                    ok=bool(it.ok),
                    command=it.command or "",
                    neighbors=int(it.neighbors or 0),
                    edges_added=int(it.edges_added or 0),
                    edges_updated=int(it.edges_updated or 0),
                    unmatched_count=int(it.unmatched_count or 0),
                    unmatched=unmatched,
                    parser_key=it.parser_key or "",
                    parser_stub=bool(it.parser_stub),
                    error=it.error or "",
                    raw_preview=it.raw_preview or "",
                )
            )
    return FabricDiscoverJobOut(
        id=job.id,
        scope=job.scope,
        trigger_mode=str(getattr(job, "trigger_mode", None) or "manual"),
        status=job.status,
        total=int(job.total or 0),
        done=int(job.done or 0),
        edges_added=int(job.edges_added or 0),
        edges_updated=int(job.edges_updated or 0),
        edges_stale=int(job.edges_stale or 0),
        edges_missing=int(job.edges_stale or 0),
        error=job.error or "",
        started_at=job.started_at,
        ended_at=job.ended_at,
        items=items_out,
    )


def get_discover_job(db: Session, job_id: str) -> FabricDiscoverJobOut:
    job = db.get(TopoDiscoverJob, str(job_id or "").strip())
    if job is None:
        raise HTTPException(status_code=404, detail="discover_job_not_found")
    return _job_out(db, job)


def _ume_target_dict(db: Session, uid: str, default_profile: Any) -> dict[str, str] | None:
    ume = db.query(UmeInventoryNE).filter(UmeInventoryNE.ne_id == uid).one_or_none()
    if ume is None:
        return None
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


def _resolve_scan_targets(
    db: Session, body: FabricDiscoverRequest
) -> list[dict[str, str]]:
    scope = str(body.scope or "ne_ids").strip().lower() or "ne_ids"
    default_profile = get_default_profile(db)
    targets: list[dict[str, str]] = []
    if scope == "all_inventory":
        for ne in db.query(ManagedNE).all():
            targets.append(
                {
                    "ne_id": ne.id,
                    "ume_ne_id": "",
                    "ne_name": ne.name or "",
                    "ne_ip": ne.ip_address or "",
                    "vendor": ne.vendor or "",
                    "device_type": ne.device_type or "",
                }
            )
        return targets

    managed_ids = [str(x).strip() for x in (body.managed_ne_ids or []) if str(x).strip()]
    ume_ids = [str(x).strip() for x in (body.ume_ne_ids or []) if str(x).strip()]
    if managed_ids or ume_ids:
        seen: set[str] = set()
        for mid in managed_ids:
            if mid in seen:
                continue
            ne = db.get(ManagedNE, mid)
            if ne is None:
                continue
            seen.add(mid)
            targets.append(
                {
                    "ne_id": ne.id,
                    "ume_ne_id": "",
                    "ne_name": ne.name or "",
                    "ne_ip": ne.ip_address or "",
                    "vendor": ne.vendor or "",
                    "device_type": ne.device_type or "",
                }
            )
        for uid in ume_ids:
            key = f"ume:{uid}"
            if key in seen:
                continue
            row = _ume_target_dict(db, uid, default_profile)
            if row is None:
                continue
            seen.add(key)
            targets.append(row)
        if not targets:
            raise HTTPException(status_code=400, detail="ne_ids_required")
        return targets

    # Legacy mixed ne_ids: prefer ManagedNE, leftover treated as UME.
    filter_ids = {str(x).strip() for x in (body.ne_ids or []) if str(x).strip()}
    if not filter_ids:
        raise HTTPException(status_code=400, detail="ne_ids_required")
    for mid in list(filter_ids):
        ne = db.get(ManagedNE, mid)
        if ne is not None:
            targets.append(
                {
                    "ne_id": ne.id,
                    "ume_ne_id": "",
                    "ne_name": ne.name or "",
                    "ne_ip": ne.ip_address or "",
                    "vendor": ne.vendor or "",
                    "device_type": ne.device_type or "",
                }
            )
            filter_ids.discard(mid)
    for uid in list(filter_ids):
        row = _ume_target_dict(db, uid, default_profile)
        if row is not None:
            targets.append(row)
    return targets


def prune_discover_jobs(db: Session, *, keep: int = 30) -> int:
    """Delete finished discover jobs beyond ``keep`` (newest kept). Open jobs always retained."""
    keep = max(0, min(200, int(keep)))
    finished = (
        db.query(TopoDiscoverJob)
        .filter(TopoDiscoverJob.status.in_(["done", "failed"]))
        .order_by(TopoDiscoverJob.created_at.desc())
        .all()
    )
    to_drop = finished if keep == 0 else finished[keep:]
    if not to_drop:
        return 0
    dropped = 0
    for job in to_drop:
        db.query(TopoDiscoverJobItem).filter(TopoDiscoverJobItem.job_id == job.id).delete(
            synchronize_session=False
        )
        db.delete(job)
        dropped += 1
    if dropped:
        db.commit()
    return dropped


def _discover_one_target(
    target: dict[str, str],
    *,
    auto_add_unmatched: bool,
) -> dict[str, Any]:
    """Run LLDP for one NE in a fresh DB session.

    Keep the write txn short: resolve self fabric → commit → SSH → apply peers/edges
    (with deadlock retries). Holding inserts across SSH was a major deadlock source.
    """
    base = {
        "ne_id": target.get("ne_id") or "",
        "ume_ne_id": target.get("ume_ne_id") or "",
        "fabric_node_id": "",
        "ne_name": target.get("ne_name") or "",
        "ne_ip": target.get("ne_ip") or "",
    }
    db = SessionLocal()
    try:
        fabric_node: TopoFabricNode | None = None
        managed = db.get(ManagedNE, target["ne_id"]) if target.get("ne_id") else None
        if managed is not None:
            fabric_node = ensure_fabric_node_for_managed(db, managed)
        elif target.get("ume_ne_id"):
            ume = (
                db.query(UmeInventoryNE)
                .filter(UmeInventoryNE.ne_id == target["ume_ne_id"])
                .one_or_none()
            )
            if ume is not None:
                fabric_node = ensure_fabric_node_for_ume(
                    db,
                    ume,
                    device_type=target.get("device_type") or "",
                    vendor=target.get("vendor") or "",
                )
        if fabric_node is None:
            return {**base, "ok": False, "error": "fabric_node_resolve_failed"}

        fabric_node_id = fabric_node.id
        base["fabric_node_id"] = fabric_node_id
        # Release unique-index locks before slow SSH.
        db.commit()

        cmd, _proto = pick_neighbor_command(
            vendor=target.get("vendor") or "",
            device_type=target.get("device_type") or "",
        )
        exec_kwargs: dict[str, Any] = {"read_timeout_sec": 60}
        if target.get("ume_ne_id") and not db.get(ManagedNE, target["ne_id"]):
            exec_kwargs["ume_ne_id"] = target["ume_ne_id"]
        else:
            exec_kwargs["ne_id"] = target["ne_id"]
        try:
            exec_out = execute_managed_ne_commands(db, [cmd], **exec_kwargs)
        except HTTPException as exc:
            return {
                **base,
                "ok": False,
                "command": cmd,
                "error": str(exc.detail or "exec_failed")[:500],
            }
        if not exec_out.get("ok"):
            return {
                **base,
                "ok": False,
                "command": cmd,
                "error": str(exec_out.get("detail") or exec_out.get("error") or "exec_failed")[:500],
            }

        raw = str(exec_out.get("output") or "")
        pkey, is_stub = parser_meta(
            vendor=target.get("vendor") or "", device_type=target.get("device_type") or ""
        )
        hits = parse_neighbor_output(
            raw,
            protocol="lldp",
            vendor=target.get("vendor") or "",
            device_type=target.get("device_type") or "",
        )
        stub_flag = bool(is_stub and raw.strip() and not hits)

        apply_out = _apply_discover_hits(
            db,
            fabric_node_id=fabric_node_id,
            hits=hits,
            auto_add_unmatched=auto_add_unmatched,
        )
        if not apply_out.get("ok"):
            return {
                **base,
                "ok": False,
                "command": cmd,
                "parser_key": pkey,
                "parser_stub": stub_flag,
                "error": str(apply_out.get("error") or "apply_failed")[:500],
                "raw_preview": _raw_preview(raw),
            }

        return {
            **base,
            "ok": True,
            "command": cmd,
            "neighbors": len(hits),
            "edges_added": int(apply_out.get("edges_added") or 0),
            "edges_updated": int(apply_out.get("edges_updated") or 0),
            "unmatched_count": int(apply_out.get("unmatched_count") or 0),
            "unmatched": list(apply_out.get("unmatched") or []),
            "parser_key": pkey,
            "parser_stub": stub_flag,
            "error": "parser_stub" if stub_flag else "",
            "raw_preview": _raw_preview(raw),
            "touched_edge_ids": list(apply_out.get("touched_edge_ids") or []),
            "replaced_edge_ids": list(apply_out.get("replaced_edge_ids") or []),
            "scanned_node_id": fabric_node_id,
        }
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return {**base, "ok": False, "error": str(exc)[:500]}
    finally:
        db.close()


def _apply_discover_hits(
    db: Session,
    *,
    fabric_node_id: str,
    hits: list[NeighborHit],
    auto_add_unmatched: bool,
) -> dict[str, Any]:
    """Write peer fabric nodes + edges; retry on Postgres deadlocks."""
    last_err = ""
    for attempt in range(_DISCOVER_DEADLOCK_RETRIES):
        try:
            now = _utcnow()
            fabric_node = db.get(TopoFabricNode, fabric_node_id)
            if fabric_node is None:
                return {"ok": False, "error": "fabric_node_missing"}

            added = 0
            updated = 0
            unmatched: list[dict[str, str]] = []
            touched: list[str] = []
            replaced: list[str] = []
            peer_index = _FabricPeerIndex(db, fabric_node.id)
            for hit in hits:
                peer = peer_index.match(hit)
                if peer is None:
                    if auto_add_unmatched and (hit.remote_name or hit.remote_ip):
                        peer = peer_index.ensure_placeholder(
                            remote_name=(hit.remote_name or "").strip(),
                            remote_ip=(hit.remote_ip or "").strip(),
                        )
                        peer.attrs = dict(peer.attrs or {})
                        peer.attrs["from_lldp_unmatched"] = True
                        peer.last_seen_at = now
                        peer.updated_at = now
                    else:
                        unmatched.append(
                            {
                                "remote_name": (hit.remote_name or "").strip()[:256],
                                "remote_ip": (hit.remote_ip or "").strip()[:128],
                                "local_port": (hit.local_port or "").strip()[:128],
                                "remote_port": (hit.remote_port or "").strip()[:128],
                            }
                        )
                        continue
                edge, action = upsert_fabric_edge(
                    db,
                    a_node_id=fabric_node.id,
                    b_node_id=peer.id,
                    a_port=(hit.local_port or ""),
                    b_port=(hit.remote_port or ""),
                    source="lldp",
                    now=now,
                )
                touched.append(edge.id)
                replaced.extend(
                    _mark_replaced_port_peers(
                        db,
                        self_id=fabric_node.id,
                        local_port=(hit.local_port or ""),
                        peer_id=peer.id,
                        new_edge_id=edge.id,
                        now=now,
                    )
                )
                if action == "added":
                    added += 1
                elif action == "updated":
                    updated += 1
            fabric_node.last_seen_at = now
            fabric_node.updated_at = now
            db.commit()
            return {
                "ok": True,
                "edges_added": added,
                "edges_updated": updated,
                "unmatched_count": len(unmatched),
                "unmatched": unmatched[:40],
                "touched_edge_ids": touched,
                "replaced_edge_ids": replaced,
            }
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            last_err = str(exc)[:500]
            if _is_deadlock_error(exc) and attempt + 1 < _DISCOVER_DEADLOCK_RETRIES:
                _sleep_deadlock_backoff(attempt)
                continue
            return {"ok": False, "error": last_err}
    return {"ok": False, "error": last_err or "apply_failed"}


def _preensure_discover_targets(db: Session, targets: list[dict[str, str]]) -> None:
    """Create fabric rows for scan targets before parallel workers start."""
    for target in targets:
        managed = db.get(ManagedNE, target["ne_id"]) if target.get("ne_id") else None
        if managed is not None:
            ensure_fabric_node_for_managed(db, managed)
            continue
        if not target.get("ume_ne_id"):
            continue
        ume = (
            db.query(UmeInventoryNE)
            .filter(UmeInventoryNE.ne_id == target["ume_ne_id"])
            .one_or_none()
        )
        if ume is not None:
            ensure_fabric_node_for_ume(
                db,
                ume,
                device_type=target.get("device_type") or "",
                vendor=target.get("vendor") or "",
            )
    db.commit()


def _run_discover_job(job_id: str, body: FabricDiscoverRequest) -> None:
    db = SessionLocal()
    try:
        job = db.get(TopoDiscoverJob, job_id)
        if job is None:
            return
        job.status = "running"
        job.started_at = _utcnow()
        job.updated_at = job.started_at
        try:
            targets = _resolve_scan_targets(db, body)
        except HTTPException as exc:
            job.status = "failed"
            job.error = str(exc.detail or "resolve_failed")[:1024]
            job.ended_at = _utcnow()
            job.updated_at = job.ended_at
            db.commit()
            return
        job.total = len(targets)
        db.commit()

        # Reduce cross-worker races on self nodes before concurrent SSH/apply.
        try:
            _preensure_discover_targets(db, targets)
        except Exception:  # noqa: BLE001
            db.rollback()

        concurrency = max(1, min(32, int(body.concurrency or 4)))
        added = 0
        updated = 0
        stale = 0
        scanned_ok: set[str] = set()
        touched_edges: set[str] = set()

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futs = {
                pool.submit(
                    _discover_one_target, t, auto_add_unmatched=bool(body.auto_add_unmatched)
                ): t
                for t in targets
            }
            for fut in as_completed(futs):
                result = fut.result()
                item = TopoDiscoverJobItem(
                    id=uuid4().hex,
                    job_id=job_id,
                    ne_id=str(result.get("ne_id") or ""),
                    ume_ne_id=str(result.get("ume_ne_id") or ""),
                    fabric_node_id=str(result.get("fabric_node_id") or ""),
                    ne_name=str(result.get("ne_name") or "")[:256],
                    ne_ip=str(result.get("ne_ip") or "")[:128],
                    ok=bool(result.get("ok")),
                    command=str(result.get("command") or "")[:256],
                    neighbors=int(result.get("neighbors") or 0),
                    edges_added=int(result.get("edges_added") or 0),
                    edges_updated=int(result.get("edges_updated") or 0),
                    unmatched_count=int(result.get("unmatched_count") or 0),
                    unmatched_json=list(result.get("unmatched") or []),
                    parser_key=str(result.get("parser_key") or "")[:64],
                    parser_stub=bool(result.get("parser_stub")),
                    error=str(result.get("error") or "")[:1024],
                    raw_preview=str(result.get("raw_preview") or ""),
                    created_at=_utcnow(),
                )
                db.add(item)
                added += int(result.get("edges_added") or 0)
                updated += int(result.get("edges_updated") or 0)
                if result.get("ok") and result.get("scanned_node_id"):
                    scanned_ok.add(str(result["scanned_node_id"]))
                for eid in result.get("touched_edge_ids") or []:
                    touched_edges.add(str(eid))
                # Cutover edges already marked missing — skip same-job miss bump.
                for eid in result.get("replaced_edge_ids") or []:
                    touched_edges.add(str(eid))
                job.done = int(job.done or 0) + 1
                job.edges_added = added
                job.edges_updated = updated
                job.updated_at = _utcnow()
                db.commit()

        # Absent on a successfully scanned endpoint → missing; purge after N cycles.
        if scanned_ok:
            newly_missing, purged = _apply_missing_and_purge(
                db,
                scanned_ok=scanned_ok,
                touched_edge_ids=touched_edges,
            )
            stale = newly_missing + purged
            job.edges_stale = stale
            db.commit()

        stats = db.get(TopoFabricStats, "global")
        if stats is None:
            stats = TopoFabricStats(id="global")
            db.add(stats)
        stats.last_discover_at = _utcnow()
        db.commit()
        merge_duplicate_fabric_nodes(db)
        refresh_fabric_stats(db)

        job = db.get(TopoDiscoverJob, job_id)
        if job is not None:
            job.status = "done"
            job.ended_at = _utcnow()
            job.updated_at = job.ended_at
            job.edges_added = added
            job.edges_updated = updated
            job.edges_stale = stale
            db.commit()
        try:
            from .lldp_collect_service import DEFAULT_HISTORY_KEEP, ensure_policy

            keep = int(getattr(ensure_policy(db), "history_keep", DEFAULT_HISTORY_KEEP) or 0)
            prune_discover_jobs(db, keep=keep)
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        job = db.get(TopoDiscoverJob, job_id)
        if job is not None:
            job.status = "failed"
            job.error = str(exc)[:1024]
            job.ended_at = _utcnow()
            job.updated_at = job.ended_at
            db.commit()
    finally:
        db.close()
        with _JOB_LOCK:
            _RUNNING_JOBS.discard(job_id)


def reclaim_stale_discover_jobs(
    db: Session,
    *,
    force_all_open: bool = False,
    now: datetime | None = None,
) -> int:
    """Mark orphaned / hung discover jobs as failed so scheduling can proceed.

    - ``force_all_open``: process restart — all pending/running rows are dead.
    - Otherwise: pending older than pending_stale_sec, or running with stale updated_at.
    """
    now = now or _utcnow()
    run_sec = max(60, int(getattr(settings, "lldp_collect_stale_run_sec", 7200) or 7200))
    pend_sec = max(30, int(getattr(settings, "lldp_collect_pending_stale_sec", 300) or 300))
    open_jobs = (
        db.query(TopoDiscoverJob)
        .filter(TopoDiscoverJob.status.in_(["pending", "running"]))
        .all()
    )
    if not open_jobs:
        return 0
    closed = 0
    for job in open_jobs:
        status = str(job.status or "")
        if force_all_open:
            reason = "stale_running_reset_on_startup"
        elif status == "pending":
            created = job.created_at or job.updated_at or now
            if created > now - timedelta(seconds=pend_sec):
                continue
            reason = "pending_stale_timeout"
        else:
            touched = job.updated_at or job.started_at or job.created_at or now
            if touched > now - timedelta(seconds=run_sec):
                continue
            reason = "running_stale_timeout"
        job.status = "failed"
        job.ended_at = now
        job.updated_at = now
        msg = str(job.error or "").strip()
        job.error = (msg + ("; " if msg else "") + reason)[:1024]
        closed += 1
        with _JOB_LOCK:
            _RUNNING_JOBS.discard(job.id)
    if closed:
        db.commit()
    return closed


def start_discover_job(
    db: Session,
    body: FabricDiscoverRequest,
    *,
    trigger_mode: str = "manual",
) -> FabricDiscoverJobOut:
    reclaim_stale_discover_jobs(db)
    # Serialize multi-worker starts via singleton policy row lock (PG/SQLite FOR UPDATE).
    pol = db.get(LldpCollectPolicy, 1)
    if pol is None:
        pol = LldpCollectPolicy(
            id=1,
            enabled=False,
            interval_days=1,
            interval_hours=24,
            concurrency=4,
            scope_mode="all",
            selected_targets=[],
            auto_add_unmatched=True,
            history_keep=30,
            updated_at=_utcnow(),
        )
        db.add(pol)
        db.commit()
    db.query(LldpCollectPolicy).filter(LldpCollectPolicy.id == 1).with_for_update().one()
    if (
        db.query(TopoDiscoverJob)
        .filter(TopoDiscoverJob.status.in_(["pending", "running"]))
        .first()
        is not None
    ):
        raise HTTPException(status_code=409, detail="lldp_collect_already_running")
    scope = str(body.scope or "ne_ids").strip().lower() or "ne_ids"
    if scope not in {"all_inventory", "ne_ids"}:
        raise HTTPException(status_code=400, detail="invalid_scope")
    trig = str(trigger_mode or getattr(body, "trigger_mode", None) or "manual").strip().lower() or "manual"
    if trig not in {"manual", "schedule", "topology"}:
        trig = "manual"
    now = _utcnow()
    # Persist explicit source lists when present; keep legacy ne_ids for older clients.
    stored_ids = list(body.ne_ids or [])
    if body.managed_ne_ids or body.ume_ne_ids:
        stored_ids = [
            *(f"managed:{x}" for x in (body.managed_ne_ids or []) if str(x).strip()),
            *(f"ume:{x}" for x in (body.ume_ne_ids or []) if str(x).strip()),
        ]
    job = TopoDiscoverJob(
        id=uuid4().hex,
        scope=scope,
        trigger_mode=trig,
        ne_ids_json=stored_ids,
        status="pending",
        total=0,
        done=0,
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    with _JOB_LOCK:
        _RUNNING_JOBS.add(job.id)
    thread = threading.Thread(
        target=_run_discover_job,
        args=(job.id, body),
        name=f"topo-discover-{job.id[:8]}",
        daemon=True,
    )
    thread.start()
    return _job_out(db, job, include_items=False)
