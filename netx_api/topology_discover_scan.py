"""Per-target LLDP collect and fabric peer/edge apply."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import ManagedNE, TopoFabricNode, UmeInventoryNE
from .cli_creds import cli_creds_skip_reason
from .cli_resolve import resolve_cli_target
from .ne_exec import execute_managed_ne_commands
from .topology_common import (
    _DISCOVER_DEADLOCK_RETRIES,
    _is_deadlock_error,
    _sleep_deadlock_backoff,
    _utcnow,
)
from .topology_discover_common import _raw_preview
from .topology_fabric import (
    _FabricPeerIndex,
    _mark_replaced_port_peers,
    ensure_fabric_node_for_managed,
    ensure_fabric_node_for_ume,
    upsert_fabric_edge,
)
from .topology_lldp import (
    NeighborHit,
    can_discover_lldp,
    parse_neighbor_output,
    parser_meta,
    pick_neighbor_command,
)


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

        vendor = target.get("vendor") or ""
        device_type = target.get("device_type") or ""
        pkey, is_stub = parser_meta(vendor=vendor, device_type=device_type)
        if not can_discover_lldp(vendor=vendor, device_type=device_type):
            return {
                **base,
                "ok": False,
                "command": "",
                "parser_key": pkey,
                "parser_stub": True,
                "error": "vendor_or_device_type_required",
                "raw_preview": "",
            }

        cmd, _proto = pick_neighbor_command(vendor=vendor, device_type=device_type)
        exec_kwargs: dict[str, Any] = {"read_timeout_sec": 60}
        if target.get("ume_ne_id") and not db.get(ManagedNE, target["ne_id"]):
            exec_kwargs["ume_ne_id"] = target["ume_ne_id"]
        else:
            exec_kwargs["ne_id"] = target["ne_id"]
        try:
            creds, _device = resolve_cli_target(
                db,
                managed_ne_id=exec_kwargs.get("ne_id"),
                ume_ne_id=exec_kwargs.get("ume_ne_id"),
            )
        except HTTPException as exc:
            return {
                **base,
                "ok": False,
                "command": cmd,
                "error": str(exc.detail or "resolve_failed")[:500],
            }
        skip = cli_creds_skip_reason(creds, interactive=False)
        if skip:
            return {**base, "ok": False, "command": cmd, "error": skip}
        try:
            from .cli_budget import acquire_cli_slot

            with acquire_cli_slot() as ok:
                if not ok:
                    return {**base, "ok": False, "command": cmd, "error": "cli_budget_unavailable"}
                exec_out = execute_managed_ne_commands(db, [cmd], **exec_kwargs)
        except HTTPException as exc:
            detail = str(exc.detail or "exec_failed")[:500]
            return {
                **base,
                "ok": False,
                "command": cmd,
                "error": detail,
            }
        if not exec_out.get("ok"):
            err = str(exec_out.get("error") or exec_out.get("detail") or "exec_failed")[:500]
            return {
                **base,
                "ok": False,
                "command": cmd,
                "error": err,
            }

        raw = str(exec_out.get("output") or "")
        hits = parse_neighbor_output(
            raw,
            protocol="lldp",
            vendor=vendor,
            device_type=device_type,
            command=cmd,
        )
        # Stub / empty CLI body = maybe logged in, but not trustworthy LLDP evidence.
        # Miss marking requires a real parser + non-empty command output.
        stub_flag = bool(is_stub)
        evidence_ok = (not stub_flag) and bool(raw.strip())

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
                "lldp_evidence_ok": False,
                "error": str(apply_out.get("error") or "apply_failed")[:500],
                "raw_preview": _raw_preview(raw),
            }

        err = ""
        if stub_flag:
            err = "parser_stub"
        elif not raw.strip():
            err = "empty_cli_output"

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
            "lldp_evidence_ok": evidence_ok,
            "error": err,
            "raw_preview": _raw_preview(raw),
            "touched_edge_ids": list(apply_out.get("touched_edge_ids") or []),
            "replaced_edge_ids": list(apply_out.get("replaced_edge_ids") or []),
            # Only evidence-ok scans participate in miss/purge judgment.
            "scanned_node_id": fabric_node_id if evidence_ok else "",
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
                if edge is None or action == "skipped_self_loop":
                    # Device advertising itself (or peer resolved to same fabric node).
                    continue
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
