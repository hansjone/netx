"""H1: bounded auto-run history and run-aware batch protection."""
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from netx_api.biz_state.retention import protected_batch_map, purge_auto_runs
from netx_api.db import Base
from netx_api.models import (
    BizCompareDiff, BizCompareJob, BizCompareRun, BizMigrationBatch, BizMigrationDiff, BizMigrationRedTicket,
    BizMigrationRun, BizStateBatch, BizStateTask,
)
from netx_api.timeutil import utcnow_naive


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()
    engine.dispose()


def _runs(db, n, *, purpose="auto", batch="wave", prefix="r"):
    base = utcnow_naive() - timedelta(hours=10)
    for i in range(n):
        db.add(BizMigrationRun(id=f"{prefix}{i}", project_id="p", batch_id=batch, purpose=purpose,
                               old_batch_id=f"o-{prefix}{i}", new_batch_id=f"n-{prefix}{i}",
                               created_at=base + timedelta(minutes=i), summary_json={}))
        db.add(BizMigrationDiff(id=f"d-{prefix}{i}", run_id=f"{prefix}{i}", metric_id="m"))
    db.commit()


def test_only_the_newest_auto_runs_survive_and_acceptance_runs_stay(db):
    _runs(db, 400)
    _runs(db, 1, purpose="acceptance", prefix="acc")
    out = purge_auto_runs(db, migration_keep=300)
    assert out["migration_runs"] == 100
    assert db.query(BizMigrationRun).count() == 301
    assert db.get(BizMigrationRun, "acc0") is not None
    assert db.get(BizMigrationRun, "r399") is not None and db.get(BizMigrationRun, "r99") is None
    assert db.query(BizMigrationDiff).count() == 301  # diffs follow their run


def test_runs_pinned_by_red_tickets_or_acceptance_are_never_dropped(db):
    _runs(db, 12)
    db.add(BizMigrationRedTicket(id="t", project_id="p", batch_id="wave", run_id="r0"))
    db.add(BizMigrationBatch(id="wave", project_id="p", accept_run_id="r1"))
    db.commit()
    purge_auto_runs(db, migration_keep=5)
    assert db.get(BizMigrationRun, "r0") is not None and db.get(BizMigrationRun, "r1") is not None
    assert db.get(BizMigrationRun, "r2") is None and db.query(BizMigrationRun).count() == 7


def test_keep_is_per_wave(db):
    _runs(db, 6, batch="w1", prefix="a")
    _runs(db, 6, batch="w2", prefix="b")
    assert purge_auto_runs(db, migration_keep=4)["migration_runs"] == 4
    assert db.query(BizMigrationRun).filter_by(batch_id="w1").count() == 4


def test_compare_keeps_manual_runs_and_trims_auto_runs_of_auto_jobs(db):
    db.add(BizCompareJob(id="job", name="j", mode="auto"))
    base = utcnow_naive() - timedelta(hours=5)
    for i in range(8):
        db.add(BizCompareRun(id=f"c{i}", job_id="job", status="success", created_at=base + timedelta(minutes=i),
                             summary_json={"purpose": "auto" if i != 0 else "manual"}))
        db.add(BizCompareDiff(id=f"cd{i}", run_id=f"c{i}", metric_id="m"))
    db.commit()
    out = purge_auto_runs(db, compare_keep=3)
    assert out["compare_runs"] == 4  # c1..c4; c0 is manual and survives, c5..c7 are the newest
    assert {r.id for r in db.query(BizCompareRun)} == {"c0", "c5", "c6", "c7"}
    assert db.query(BizCompareDiff).count() == 4


def _batch(db, bid, task="t"):
    db.add(BizStateBatch(id=bid, task_id=task, status="success"))


def test_protection_follows_surviving_runs_and_all_metric_batches(db):
    db.add(BizStateTask(id="t", source="managed", ne_id="n"))
    for bid in ("o-r0", "n-r0", "hf-a", "hf-b", "o-r1", "n-r1", "o-acc0", "n-acc0"):
        _batch(db, bid)
    db.commit()
    _runs(db, 2)
    _runs(db, 1, purpose="acceptance", prefix="acc")
    acc = db.get(BizMigrationRun, "acc0")
    acc.summary_json = {"metric_batches": {"arp": {"old_batch_id": "hf-a", "new_batch_id": "hf-b"}}}
    db.commit()
    reasons = protected_batch_map(db)
    assert "hf-a" in reasons and "hf-b" in reasons  # beyond the first binding
    purge_auto_runs(db, migration_keep=1)           # r0 goes away
    assert "o-r0" not in protected_batch_map(db) and "o-r1" in protected_batch_map(db)
