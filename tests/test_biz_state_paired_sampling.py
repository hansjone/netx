"""Paired admission/dispatch, actual cadence, and per-metric isolation."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from netx_api.db import Base
from netx_api.models import BizStateTask, BizStateTaskItem, BizStateBatch
from netx_api.timeutil import utcnow_naive
from netx_api.biz_state import claim
from netx_api import biz_state_scheduler as scheduler


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(claim, "SessionLocal", factory)
    monkeypatch.setattr(scheduler, "SessionLocal", factory)
    with factory() as session:
        for tid in ("old", "new"):
            session.add(BizStateTask(id=tid, source="managed", ne_id=tid,
                status="running", interval_sec=60, collect_group_id="pair"))
            session.add(BizStateTaskItem(id="item-" + tid, task_id=tid, source_profile_id="zte.arp", enabled=True))
        session.commit()
        yield session
    scheduler._in_flight.clear()


def test_individual_trigger_enqueues_both_with_one_identity(db):
    result = claim.enqueue_collect("new", manual=True)
    assert result["queued"] and result["task_id"] == "new"
    rows = db.query(BizStateBatch).all()
    assert len(rows) == 2
    assert {b.task_id for b in rows} == {"old", "new"}
    assert len({b.collect_round_id for b in rows}) == 1
    assert len({b.queued_at for b in rows}) == 1
    assert not claim.enqueue_collect("old", manual=True)["queued"]


@pytest.mark.parametrize("problem", ["busy", "no_items", "paused", "same_device"])
def test_pair_admission_never_creates_half_round(db, problem):
    peer = db.get(BizStateTask, "new")
    if problem == "busy":
        peer.collect_running = True
    elif problem == "no_items":
        db.delete(db.get(BizStateTaskItem, "item-new"))
    elif problem == "paused":
        peer.status = "paused"
    else:
        peer.ne_id = "old"
    db.commit()
    assert not claim.enqueue_collect("old")["queued"]
    assert db.query(BizStateBatch).count() == 0


def test_pair_claim_requires_two_slots(db):
    claim.enqueue_collect("old")
    assert claim.claim_queued_batches(1) == []
    assert len(claim.claim_queued_batches(2)) == 2


def test_busy_device_keeps_both_queued_and_no_ssh_overlap(db, monkeypatch):
    # These cases are about one collection per device; the cap defaults to 2.
    monkeypatch.setattr(claim, "max_sessions_per_ne", lambda: 1)
    db.add(BizStateBatch(id="portrait", task_id="other", ne_id="old", status="running"))
    db.commit()
    claim.enqueue_collect("old")
    assert claim.claim_queued_batches(8) == []
    assert db.query(BizStateBatch).filter(BizStateBatch.status == "queued").count() == 2


def test_capacity_one_has_actionable_admission_error(db, monkeypatch):
    monkeypatch.setattr(claim, "max_concurrent_tasks", lambda: 1)
    assert claim.enqueue_collect("old")["reason"] == "collect_pair_capacity"
    assert db.query(BizStateBatch).count() == 0


def test_manual_routes_are_excluded_from_periodic_scheduler(db):
    for t in db.query(BizStateTask).all():
        t.collect_manual_only = True
    db.commit()
    assert scheduler._enqueue_due_tasks() == 0
    assert claim.enqueue_collect("old", manual=True)["queued"]


def test_pair_cadence_uses_later_start_and_slow_peer_blocks_restart(db):
    now = utcnow_naive()
    db.get(BizStateTask, "old").last_collect_started_at = now - timedelta(seconds=100)
    db.get(BizStateTask, "new").last_collect_started_at = now - timedelta(seconds=10)
    db.commit()
    assert scheduler._enqueue_due_tasks() == 0
    db.get(BizStateTask, "new").last_collect_started_at = now - timedelta(seconds=100)
    db.get(BizStateTask, "new").collect_running = True
    db.commit()
    assert scheduler._enqueue_due_tasks() == 0
    db.get(BizStateTask, "new").collect_running = False
    db.commit()
    assert scheduler._enqueue_due_tasks() == 1
    assert db.query(BizStateBatch).count() == 2


def test_inline_pair_uses_two_workers_without_waiting_for_one_side(db, monkeypatch):
    from netx_api.biz_state import collect_runner

    both_started, release = Barrier(2), Event()
    started, synchronized = [], []
    def fake_collect(**job):
        started.append(job["task_id"])
        both_started.wait(timeout=3)
        synchronized.append(job["task_id"])
        release.wait(timeout=3)
    monkeypatch.setattr(scheduler, "execute_claimed_batch", fake_collect)
    monkeypatch.setattr(collect_runner, "_should_execute_inline", lambda: True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        monkeypatch.setattr(scheduler, "_dispatch_pool_get", lambda: pool)
        result = collect_runner.dispatch_collect("old", manual=True)
        assert result["queued"]
        # Both worker entries must reach a barrier; serial old→new would fail it.
        release.set()
    assert set(started) == {"old", "new"}
    assert set(synchronized) == {"old", "new"}


def test_faster_metrics_claim_before_manual_route_round(db, monkeypatch):
    # These cases are about one collection per device; the cap defaults to 2.
    monkeypatch.setattr(claim, "max_sessions_per_ne", lambda: 1)
    for tid in ("ro", "rn"):
        db.add(BizStateTask(id=tid, ne_id="old" if tid == "ro" else "new",
            status="paused", collect_group_id="routes", collect_manual_only=True))
        db.add(BizStateTaskItem(id="i-" + tid, task_id=tid, source_profile_id="zte.ip_route", enabled=True))
    db.commit()
    claim.enqueue_collect("ro", manual=True)
    claim.enqueue_collect("old")
    jobs = claim.claim_queued_batches(4)
    assert {job["task_id"] for job in jobs} == {"old", "new"}


def test_manual_route_round_cannot_starve_behind_continuous_fast_metrics(db, monkeypatch):
    # These cases are about one collection per device; the cap defaults to 2.
    monkeypatch.setattr(claim, "max_sessions_per_ne", lambda: 1)
    for tid in ("ro", "rn"):
        db.add(BizStateTask(id=tid, ne_id="old" if tid == "ro" else "new",
            status="paused", collect_group_id="routes", collect_manual_only=True))
        db.add(BizStateTaskItem(id="i-" + tid, task_id=tid, source_profile_id="zte.ip_route", enabled=True))
    db.commit()
    claim.enqueue_collect("ro", manual=True)
    claim.enqueue_collect("old")
    for batch in db.query(BizStateBatch).filter(BizStateBatch.collect_group_id == "routes").all():
        batch.queued_at = utcnow_naive() - timedelta(seconds=301)
    db.commit()
    assert {job["task_id"] for job in claim.claim_queued_batches(4)} == {"ro", "rn"}


@pytest.mark.parametrize("running", [False, True])
def test_stopping_one_side_stops_the_whole_round(db, monkeypatch, running):
    from netx_api.biz_state import collect_stop

    monkeypatch.setattr(collect_stop, "SessionLocal", claim.SessionLocal)
    claim.enqueue_collect("old")
    if running:
        claim.claim_queued_batches(2)
    result = collect_stop.request_stop_collect("new")
    assert result["ok"] and result["stopped"]
    db.expire_all()
    batches = db.query(BizStateBatch).all()
    try:
        if running:
            assert len(result["signaled_batches"]) == 2
            assert all(b.message == collect_stop.STOP_REQUEST_TOKEN for b in batches)
            assert all(t.collect_running for t in db.query(BizStateTask).all())
        else:
            assert len(result["cancelled_batches"]) == 2
            assert all(b.status == "cancelled" for b in batches)
            assert not any(t.collect_running for t in db.query(BizStateTask).all())
            assert claim.claim_queued_batches(2) == []
            assert claim.enqueue_collect("new")["queued"]
    finally:
        for batch in batches:
            collect_stop.clear_stop_requested(batch.id)

