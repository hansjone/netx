"""Background layout jobs + slim payload helpers."""

from __future__ import annotations

import time
from unittest.mock import patch

from netx_topology_mcp.http_tools import _layout_topology_view, _slim_layout_payload
from netx_topology_mcp.layout_jobs import (
    cancel_job,
    get_job,
    is_cancelled,
    job_public,
    report_progress,
    start_job,
)


def test_start_job_and_poll() -> None:
    def runner() -> dict:
        report_progress("mid", pct=50.0, message="halfway", step=1, total_steps=2)
        time.sleep(0.05)
        return {"ok": True, "action": "compose_views", "applied": True, "node_count": 3}

    jid = start_job(action="compose_views", view_id="v1", runner=runner, meta={"n": 1})
    assert jid
    saw_progress = False
    for _ in range(40):
        pub = job_public(jid)
        assert pub is not None
        assert "elapsed_ms" in pub
        assert "progress" in pub
        assert "heartbeat_age_ms" in pub
        if (pub.get("progress") or {}).get("phase") == "mid":
            saw_progress = True
        if pub["status"] != "running":
            break
        time.sleep(0.05)
    assert saw_progress
    job = get_job(jid)
    assert job is not None
    assert job["status"] == "done"
    assert job["result"]["applied"] is True

    st = _layout_topology_view({"action": "job_status", "params": {"job_id": jid}})
    assert st.get("ok") is True
    assert st.get("status") == "done"
    assert st.get("result", {}).get("applied") is True
    assert isinstance(st.get("progress"), dict)


def test_cancel_job_cooperative() -> None:
    started = time.time()

    def runner() -> dict:
        report_progress("work", pct=10.0, message="before sleep")
        for _ in range(40):
            if is_cancelled():
                from netx_topology_mcp.layout_jobs import raise_if_cancelled

                raise_if_cancelled()
            time.sleep(0.05)
        return {"ok": True, "applied": True}

    jid = start_job(action="polish_crossings", view_id="v1", runner=runner)
    time.sleep(0.08)
    out = _layout_topology_view({"action": "job_cancel", "params": {"job_id": jid}})
    assert out.get("ok") is True
    assert out.get("cancel_requested") is True
    for _ in range(40):
        pub = job_public(jid)
        assert pub is not None
        if pub["status"] == "cancelled":
            break
        time.sleep(0.05)
    pub = job_public(jid)
    assert pub is not None
    assert pub["status"] == "cancelled"
    assert time.time() - started < 3.0


def test_slim_layout_payload_drops_guide() -> None:
    fat = {
        "ok": True,
        "action": "compose_views",
        "node_count": 1200,
        "guide": {"how_to_read": "x" * 200},
        "tried": [1, 2, 3],
        "params_used": {"lane": 1},
        "crossing": {
            "status": "fail",
            "score": 0,
            "edge_crossings": 9,
            "crossings_per_link": 0.1,
            "top_nodes": [{"id": "a"}] * 20,
            "top_edges": [{"id": "e"}] * 20,
            "tip": "long",
        },
        "summary": {"total": 40},
    }
    slim = _slim_layout_payload(fat)
    assert slim.get("slim") is True
    assert "guide" not in slim
    assert "tried" not in slim
    assert len(slim["crossing"]["top_nodes"]) <= 5


def test_compose_views_is_not_public() -> None:
    # compose_* was retired from the public surface (dual-unit drain is the main path).
    out = _layout_topology_view(
        {
            "view_id": "full",
            "action": "compose_views",
            "mode": "apply",
            "params": {"source_view_ids": [f"s{i:02d}" for i in range(12)]},
        }
    )
    assert out.get("ok") is False
    assert out.get("error") == "unknown_action:compose_views"


def test_orbit_background_returns_job_id() -> None:
    graph = {
        "ok": True,
        "nodes": [
            {"fabric_node_id": f"n{i}", "x": float(i * 100), "y": 0.0, "label": f"N{i}"}
            for i in range(6)
        ],
        "edges": [
            {"id": f"e{i}", "a_node_id": f"n{i}", "b_node_id": f"n{i + 1}"} for i in range(5)
        ],
    }

    def fake_start(**kwargs):
        assert kwargs["action"] == "orbit_sweep"
        assert kwargs["view_id"] == "full"
        tool_args = kwargs.get("tool_args")
        assert isinstance(tool_args, dict)
        # The worker re-runs the same call synchronously.
        assert tool_args["params"].get("_force_sync") is True
        assert "background" not in tool_args["params"]
        return "abc123job"

    with patch("netx_topology_mcp.http_tools.http_json", return_value={"ok": True, "data": graph}), patch(
        "netx_topology_mcp.http_tools.start_job", side_effect=fake_start
    ):
        out = _layout_topology_view(
            {
                "view_id": "full",
                "action": "orbit_sweep",
                "mode": "apply",
                "params": {"background": True},
            }
        )
    assert out.get("ok") is True, out
    assert out.get("status") == "running"
    assert out.get("job_id") == "abc123job"
    assert out.get("applied") is False


def test_job_status_missing() -> None:
    out = _layout_topology_view({"action": "job_status", "params": {}})
    assert out.get("ok") is False
    assert out.get("error") == "job_id_required"


def test_job_status_unknown() -> None:
    out = _layout_topology_view(
        {"action": "job_status", "params": {"job_id": "does-not-exist"}}
    )
    assert out.get("ok") is False
    assert out.get("error") == "job_not_found"


def test_cancel_missing() -> None:
    out = cancel_job("missing-job")
    assert out.get("ok") is False
    assert out.get("error") == "job_not_found"


def test_soft_stale_keeps_running_and_cancelable() -> None:
    """stale is a poll warning only — must not freeze status or block cancel."""
    from netx_topology_mcp import layout_jobs as lj

    def runner() -> dict:
        time.sleep(0.3)
        return {"ok": True, "applied": False}

    jid = start_job(action="polish_crossings", view_id="v1", runner=runner)
    with lj._LOCK:  # noqa: SLF001 — test injects silent heartbeat on disk
        job = lj._read_job_disk(jid) or lj._JOBS[jid]
        job["heartbeat_at"] = time.time() - (lj._STALE_AFTER_S + 5)
        lj._JOBS[jid] = job
        lj._write_job_disk(job)
    pub = job_public(jid)
    assert pub is not None
    assert pub["stale"] is True
    assert pub["status"] == "running"
    cancelled = cancel_job(jid)
    assert cancelled.get("ok") is True
    assert cancelled.get("status") == "cancelling"
    for _ in range(40):
        pub2 = job_public(jid)
        assert pub2 is not None
        if pub2["status"] in {"cancelled", "done", "error"}:
            break
        time.sleep(0.05)
    assert job_public(jid)["status"] == "cancelled"
