"""Coalesced collect-completion events for live cutover evaluation.

One worker per process bounds concurrency. Project row locks in run_evaluate
serialize persistence across processes. No device collection runs here.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading

from sqlalchemy.orm import Session

from ..models import BizMigrationBatch, BizMigrationProject, BizMigrationRun, BizMonitorTemplate, BizStateBatch
from . import service

_log = logging.getLogger(__name__)
_lock = threading.Lock()
_pending: set[str] = set()
_worker_running = False


def try_auto_monitor_for_task(db: Session, task_id: str) -> int:
    count = 0
    # Bindings are JSON for multiple sampling intervals; match in Python after
    # limiting to projects with an active operation batch.
    projects = db.query(BizMigrationProject).filter(
        BizMigrationProject.status != "done",
        BizMigrationProject.id.in_(db.query(BizMigrationBatch.project_id).filter(
            BizMigrationBatch.status == "active")),
    ).all()
    for candidate in projects:
        tids = service._all_hf_task_ids(candidate, "old") + service._all_hf_task_ids(candidate, "new")
        if task_id not in tids or not candidate.old_baseline_batch_id or not candidate.new_baseline_batch_id:
            continue
        # Lock before deduplication so two worker processes cannot save the same
        # snapshot. run_evaluate holds the same lock through the transaction.
        proj = db.query(BizMigrationProject).filter(
            BizMigrationProject.id == candidate.id).with_for_update().populate_existing().first()
        batch = db.query(BizMigrationBatch).filter(
            BizMigrationBatch.project_id == proj.id, BizMigrationBatch.status == "active"
        ).order_by(BizMigrationBatch.started_at.desc()).first()
        if not batch or proj.status == "done":
            db.rollback()
            continue
        mt = db.get(BizMonitorTemplate, proj.monitor_template_id) if proj.monitor_template_id else None
        if not mt:
            db.rollback()
            _log.error("cutover monitor template missing project=%s", proj.id)
            continue
        sheets, overrides, defaults, norms = service.resolve_evaluate_sheets(db, mt)
        samples = []
        for tid in tids:
            sample = db.query(BizStateBatch).filter(
                BizStateBatch.task_id == tid, BizStateBatch.status.notin_(("running", "queued"))
            ).order_by(BizStateBatch.started_at.desc(), BizStateBatch.id.desc()).first()
            samples.append([tid, sample.id if sample else "", sample.status if sample else ""])
        payload = [samples, proj.old_baseline_batch_id, proj.new_baseline_batch_id,
                   batch.expect_set_json, service._port_map_dict(db, proj.mapping_id),
                   sheets, overrides, defaults, norms, service.resolve_collect_metric_ids(db, proj)]
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        last = db.query(BizMigrationRun).filter(BizMigrationRun.batch_id == batch.id).order_by(
            BizMigrationRun.created_at.desc()).first()
        if last and (last.summary_json or {}).get("auto_fingerprint") == fingerprint:
            db.rollback()
            continue
        previous_samples = {s[0]: (s[1], s[2]) for s in (last.summary_json or {}).get("auto_samples", [])} if last else {}
        if last and not previous_samples:
            for sample in (last.summary_json or {}).get("metric_batches", {}).values():
                for side in ("old", "new"):
                    previous_samples[sample.get(f"{side}_task_id", "")] = (
                        sample.get(f"{side}_batch_id", ""), sample.get(f"{side}_status", "success"))
        changed_tasks = {tid for tid, bid, status in samples if previous_samples.get(tid) != (bid, status)}
        changed_metrics = {mid for side in ("old", "new") for binding in service._hf_bindings(proj, side)
                           if binding["task_id"] in changed_tasks for mid in binding.get("metric_ids") or payload[-1]}
        try:
            result = service.run_evaluate(db, batch_id=batch.id, purpose="auto", _commit=False,
                                          _metric_ids=changed_metrics)
            run = db.get(BizMigrationRun, result["id"])
            run.summary_json = {**run.summary_json, "auto_fingerprint": fingerprint, "auto_samples": samples}
            db.commit()
            count += 1
        except Exception:
            db.rollback()
            _log.exception("cutover monitor evaluate failed project=%s", proj.id)
    return count


def schedule_auto_monitor_for_task(task_id: str) -> None:
    global _worker_running
    with _lock:
        _pending.add(str(task_id))
        if _worker_running:
            return
        _worker_running = True

    def work() -> None:
        global _worker_running
        from ..db import SessionLocal

        while True:
            with _lock:
                if not _pending:
                    _worker_running = False
                    return
                tid = _pending.pop()
            try:
                with SessionLocal() as db:
                    try_auto_monitor_for_task(db, tid)
            except Exception:
                _log.exception("cutover monitor worker failed task=%s", tid)

    threading.Thread(target=work, name="biz-cutover-monitor", daemon=True).start()
