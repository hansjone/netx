"""Monitoring query correctness, bounded reads and active-collection protection."""
import csv
import io
import zipfile
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from netx_api.db import Base
from netx_api.models import (
    BizCompareRun, BizStateBatch, BizStateBatchCommand, BizStateLldpNeighbor,
    BizStateMetricRow, BizStateTask, BizStateTaskItem, BizStateTaskItemBinding,
)
from netx_api.biz_state import service
from netx_api.biz_state.retention import protected_batch_map, purge_task_batches


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.add(BizStateTask(id="t", ne_id="ne", retention_days=1))
    session.add(BizStateBatch(id="b", task_id="t", status="success"))
    session.commit()
    yield session
    session.close()
    engine.dispose()


def test_task_detail_uses_one_binding_query_and_progress_omits_items(db):
    for n in range(30):
        db.add(BizStateTaskItem(id=f"i{n}", task_id="t", sort_order=n))
        for ph, val in (("vrf", f"v{n}"), ("neighbor", f"p{n}")):
            db.add(BizStateTaskItemBinding(id=f"{n}-{ph}", item_id=f"i{n}", placeholder=ph, value=val))
    db.commit()
    db.expunge_all()
    statements = []
    event.listen(db.get_bind(), "before_cursor_execute", lambda c, cur, sql, p, ctx, many: statements.append(sql))
    result = service.get_task(db, "t")
    assert len(statements) == 3
    assert len(result["items"]) == 30
    assert result["items"][9]["sort_order"] == 9
    assert {b["value"] for b in result["items"][9]["bindings"]} == {"v9", "p9"}
    db.expunge_all()
    statements.clear()
    progress = service.get_task_progress(db, "t")
    assert len(statements) == 1
    assert "items" not in progress
    assert progress["id"] == "t"
    with pytest.raises(HTTPException) as exc:
        service.get_task_progress(db, "missing")
    assert exc.value.status_code == 404


@pytest.mark.parametrize("metric,column", [("custom", "name"), ("lldp_neighbor", "remote_sys")])
@pytest.mark.parametrize("keyword", ["%", "_", "\\"])
def test_metric_search_treats_wildcards_literally(db, metric, column, keyword):
    for n, value in enumerate((f"exact{keyword}value", "different-value")):
        if metric == "lldp_neighbor":
            db.add(BizStateLldpNeighbor(id=f"r{n}", batch_id="b", local_if=f"ge{n}", remote_sys=value))
        else:
            db.add(BizStateMetricRow(id=f"r{n}", batch_id="b", metric_id=metric, seq=n, data_json={column: value}))
    db.commit()
    result = service.list_batch_metric_rows(db, "b", metric, kw=keyword, column=column)
    assert result["total"] == 1
    assert result["items"][0][column] == f"exact{keyword}value"


def test_summary_does_not_select_raw_text_and_keeps_early_aux_failure(db):
    now = datetime(2026, 10, 10)
    for cid, status, raw, stored in (
        ("a", "aux_failed", "first\nsecond\n", 0),
        ("b", "ok", "X" * 1_000_000, 999),
        ("c", "aux_cached", " \t\n", 0),
    ):
        db.add(BizStateBatchCommand(id=cid, batch_id="b", raw_command="show arp" if cid != "c" else "show config",
            metric_id="arp", parse_status=status, raw_text=raw, raw_line_count=stored, created_at=now))
    db.commit()
    db.expunge_all()
    column_names = []
    event.listen(db.get_bind(), "after_cursor_execute", lambda c, cur, sql, p, ctx, many:
        column_names.extend([d[0] for d in (cur.description or [])]) if "biz_state_batch_command" in sql else None)
    summary = service.get_batch(db, "b")
    cmds = {c["id"]: c for c in summary["commands"]}
    assert "biz_state_batch_command_raw_text" not in column_names
    assert cmds["a"]["raw_line_count"] == 2
    assert cmds["b"]["raw_line_count"] == 999
    assert cmds["b"]["has_raw"] is True
    assert cmds["c"]["has_raw"] is False
    assert summary["command_stats"]["aux_failed"] == 1
    assert service.get_batch_command(db, "b", "b")["raw_text"] == "X" * 1_000_000


def test_active_batches_cannot_be_deleted_or_purged(db):
    for status in ("queued", "running"):
        db.add(BizStateBatch(id=status, task_id="t", status=status, started_at=datetime.utcnow() - timedelta(days=3)))
    db.commit()
    for bid in ("queued", "running"):
        with pytest.raises(HTTPException) as exc:
            service.delete_batch(db, bid)
        assert exc.value.status_code == 409
        assert "active_collection" in exc.value.detail["reasons"]
    result = service.delete_batches_bulk(db, ["queued", "running"])
    assert result["deleted"] == []
    purge_task_batches(db, db.get(BizStateTask, "t"))
    assert db.get(BizStateBatch, "queued") is not None
    assert db.get(BizStateBatch, "running") is not None
    db.get(BizStateTask, "t").collect_running = True
    db.commit()
    with pytest.raises(HTTPException) as exc:
        service.delete_task(db, "t")
    assert exc.value.detail == "task_collecting"


def test_protection_reads_only_scoped_reference_columns(db):
    db.add(BizStateBatch(id="other", task_id="other-task", status="success"))
    db.add(BizCompareRun(id="r", before_batch_id="b", after_batch_id="other", summary_json={"large": "x" * 10000}))
    db.commit()
    db.expunge_all()
    statements = []
    event.listen(db.get_bind(), "before_cursor_execute", lambda c, cur, sql, p, ctx, many: statements.append(sql))
    assert protected_batch_map(db, batch_ids=["b"]) == {"b": ["compare_run_before"]}
    assert all("summary_json" not in sql for sql in statements)
    assert protected_batch_map(db, task_id="t") == {"b": ["compare_run_before"]}
    assert protected_batch_map(db, batch_ids=[]) == {}


def test_export_preserves_false_zero_csv_and_duplicate_sequences(db):
    for n in range(1005):
        rec = {"n": n, "enabled": False, "note": 'a,b"c\rd\ne' if n == 0 else None}
        if n == 1004:
            rec["late"] = "last-column"
        db.add(BizStateMetricRow(id=f"r{n:04}", batch_id="b", metric_id="custom", seq=0, data_json=rec))
    db.commit()
    with zipfile.ZipFile(io.BytesIO(service.export_batch_zip(db, "b"))) as archive:
        rows = list(csv.DictReader(io.StringIO(archive.read("tables/custom.csv").decode("utf-8"), newline="")))
    assert len(rows) == 1005
    assert rows[0] == {"n": "0", "enabled": "False", "note": 'a,b"c\rd\ne', "late": ""}
    assert rows[-1]["late"] == "last-column"
    assert [row["n"] for row in rows] == [str(n) for n in range(1005)]
