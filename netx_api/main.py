from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timezone
from io import StringIO
import time
import re
import threading

_schedule_log = logging.getLogger("netx.ume.schedule")
_BOOT_MONO = time.monotonic()
from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session
from typing import Any
import uvicorn

from .ap_client import analyze_with_oclaw, health_with_oclaw
from .auth_middleware import AuthAuditMiddleware
from .auth_router import router as auth_router
from .auth_service import bootstrap_admin_if_needed
from .config import settings
from .db import Base, SessionLocal, engine, get_db
from .collection_router import router as collection_router
from .cli_router import router as cli_router
from .config_sync_router import router as config_sync_router
from .port_traffic_router import router as port_traffic_router
from .managed_ne_router import router as managed_ne_router
from .webcrt_router import router as webcrt_router
from .topology_router import router as topology_router
from .lldp_collect_router import router as lldp_collect_router
from .ops_router import router as ops_router
from .sql_router import router as sql_router
from .sql_router import sql_query, sql_ume_query  # noqa: F401 — tests import from main
from .security_bootstrap import assert_secure_defaults_or_exit
from .integrations_router import router as integrations_router
from .ume_router import router as ume_router
from .alarms_router import router as alarms_router
from .ume_router import (  # noqa: F401 — tests import from main
    _extract_ume_raw_group_field,
    _serialize_ume_alarm_raw_row,
    ume_alarms_fields,
)
from .ume_support import (  # noqa: F401 — tests import from main
    _classify_protocol_bucket,
    _protocol_bucket_label,
)
import netx_api.ume_support as ume_support
from .ume_runtime import start_device_schedulers
from .importer import aggregate_alarms, import_alarm_excel, query_alarms
from .models import (
    AiAnalyzeHistory,
    AlarmBatch,
    AlarmNorm,
    ApiToken,
    AppUser,
    AuditLog,
    ImportErrorRow,
    ManagedNE,
    NeCollectionJob,
    NeCollectionRun,
    UmeAlarmCurrent,
    UmeAlarmHistory,
    UmeInventoryNE,
    UmeKeyAlertRule,
    UmeKeyAlertForwardLog,
    UmeSyncJob,
)
from .models import ImportJob
from .parser_config import load_parser_config
from .ume_client import UMEClient
from .ume_alarm_ws import (
    begin_startup_alarm_sync_gate,
    cancel_alarm_subscription_manual,
    clear_local_alarm_subscription_manual,
    complete_startup_alarm_sync_gate,
    establish_alarm_subscription_manual,
    get_alarms_coordination_status,
    get_subscription_status,
    get_ws_connection_status,
    get_ws_logs,
    is_startup_alarm_sync_pending,
    is_wss_active_for_current_alarms,
    load_persisted_subscription,
    request_ws_reconnect,
    shutdown_ws_consumer,
    start_ume_alarm_ws_consumer,
)
from .ume_sync_service import sync_alarms_current, sync_alarms_history_full, sync_inventory_full
from .runtime_task_messages import (
    RT_ALARMS_SYNC_IN_PROGRESS_SKIP,
    RT_OCLAW_FWD_DISABLED,
    RT_PULLING_ALARMS_CURRENT,
    RT_PULLING_INVENTORY,
    RT_RESUMED,
    RT_RESUMED_OCLAW_WSS_RECONNECT,
    RT_RESUMED_SYNC_SOON,
    RT_RESUMED_WSS_RECONNECT,
    RT_STARTUP_ALARM_SYNC_BEFORE_WS,
    RT_STARTUP_GATE_WAITING,
    RT_KEEPALIVE_FAILED,
    RT_UME_WS_DISABLED_NO_BASE_URL,
    RT_WSS_ACTIVE_SKIP_REST,
)
from .oclaw_alarm_forwarder import (
    forwarder_status,
    is_forwarder_enabled,
    request_forwarder_reconnect,
    configure_oclaw_alarm_forwarder,
    shutdown_oclaw_alarm_forwarder,
    start_oclaw_alarm_forwarder,
)
from .ume_token_store import (
    clear_shared_token,
    load_shared_token,
    release_refresh_lock,
    save_shared_token,
    try_acquire_refresh_lock,
    wait_for_token_update,
)
from .schemas import (
    AlarmAggregateBucket,
    AlarmAggregateResponse,
    AiAnalyzeHistoryItem,
    AiAnalyzeHistoryResponse,
    AlarmItem,
    AlarmQueryResponse,
    BatchSummary,
    ImportJobItem,
    ImportJobListResponse,
)

app = FastAPI(
    title="netx ops tool",
    version="0.1.0",
    docs_url="/docs" if bool(settings.docs_enabled) else None,
    redoc_url="/redoc" if bool(settings.docs_enabled) else None,
    openapi_url="/openapi.json" if bool(settings.docs_enabled) else None,
)
app.add_middleware(AuthAuditMiddleware)
app.include_router(auth_router)
app.include_router(managed_ne_router)
app.include_router(cli_router)
app.include_router(collection_router)
app.include_router(config_sync_router)
app.include_router(port_traffic_router)
app.include_router(webcrt_router)
app.include_router(topology_router)
app.include_router(lldp_collect_router)
app.include_router(ops_router)
app.include_router(sql_router)
app.include_router(integrations_router)
app.include_router(ume_router)
app.include_router(alarms_router)
parser_cfg = load_parser_config()
def _configure_ume_diag_logging() -> None:
    """Emit netx.ume.* INFO to stderr so background scripts/.run/*.log and consoles show scheduler lines."""
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    for name in ("netx.ume.schedule", "netx.ume.sync"):
        lg = logging.getLogger(name)
        if lg.handlers:
            continue
        h = logging.StreamHandler()
        h.setFormatter(fmt)
        lg.addHandler(h)
        lg.setLevel(logging.INFO)
        lg.propagate = False


@app.on_event("startup")
def on_startup() -> None:
    assert_secure_defaults_or_exit()
    _configure_ume_diag_logging()
    Base.metadata.create_all(bind=engine)
    from .schema_patches import (
        apply_all_legacy_startup_ddl,
        apply_auth_schema_patches,
        run_alembic_upgrade_to_head,
    )

    alembic_ok = True
    if bool(getattr(settings, "alembic_upgrade_on_start", True)):
        try:
            run_alembic_upgrade_to_head()
        except Exception:
            alembic_ok = False
            _schedule_log.exception("startup: alembic upgrade head failed")

    skip_ddl = bool(getattr(settings, "skip_legacy_startup_ddl", True))
    # Auth columns must exist before bootstrap even when legacy DDL is skipped.
    try:
        with engine.begin() as conn:
            apply_auth_schema_patches(conn)
    except Exception:
        _schedule_log.exception("startup: auth schema patches failed")
    if skip_ddl and alembic_ok:
        _schedule_log.info("startup: schema via Alembic (legacy inline DDL skipped)")
    else:
        if skip_ddl and not alembic_ok:
            _schedule_log.warning(
                "startup: Alembic failed — falling back to legacy schema patches"
            )
        try:
            apply_all_legacy_startup_ddl(engine)
        except Exception:
            _schedule_log.exception("startup: legacy schema patches failed")
    ume_support._reset_runtime_pause_flags()
    ume_support._fail_stale_running_sync_jobs_on_startup()
    try:
        from .topology_service import bootstrap_topology_tree, reclaim_stale_discover_jobs

        db_topo = SessionLocal()
        try:
            bootstrap_topology_tree(db_topo)
            closed = reclaim_stale_discover_jobs(db_topo, force_all_open=True)
            if closed:
                _schedule_log.warning(
                    "startup: closed %s orphaned topology discover jobs", closed
                )
        finally:
            db_topo.close()
    except Exception:
        _schedule_log.exception("startup: topology discover job cleanup failed")
    if ume_support._needs_startup_alarm_sync_before_ws():
        begin_startup_alarm_sync_gate()
        _schedule_log.info(
            "startup: WSS blocked until initial REST current-alarm sync completes (delay=%ss)",
            ume_support._startup_alarm_pull_delay_s(),
        )
    else:
        complete_startup_alarm_sync_gate()
    db = SessionLocal()
    try:
        try:
            bootstrap_admin_if_needed(db)
        except Exception:
            _schedule_log.exception("startup: auth bootstrap admin failed")
        from .collection_recovery import recover_collection_jobs_on_startup

        resumed = recover_collection_jobs_on_startup(db)
        if resumed:
            _schedule_log.info("startup: resumed %s pending ne collection runs", resumed)
        from .config_sync_recovery import recover_config_sync_on_startup
        from .config_sync_service import ensure_policy
        from .port_traffic_recovery import recover_port_traffic_on_startup

        ensure_policy(db)
        cfg_resumed = recover_config_sync_on_startup(db)
        if cfg_resumed:
            _schedule_log.info("startup: resumed %s config_sync task(s) from interrupted cycle", cfg_resumed)
        try:
            from .lldp_collect_service import ensure_policy as ensure_lldp_collect_policy

            ensure_lldp_collect_policy(db)
        except Exception:
            _schedule_log.exception("startup: lldp_collect policy ensure failed")
        pt_cleared = recover_port_traffic_on_startup(db)
        if pt_cleared:
            _schedule_log.info("startup: cleared %s port_traffic stuck collect_running flag(s)", pt_cleared)
        try:
            from .port_traffic_migrate import backfill_port_traffic_series

            backfill_port_traffic_series(db)
        except Exception:
            _schedule_log.exception("startup: port_traffic series backfill failed")
    except Exception:
        _schedule_log.exception("startup: ne collection / config_sync recovery failed")
    finally:
        db.close()
    if bool(getattr(settings, "run_inline_schedulers", True)):
        try:
            start_device_schedulers()
        except Exception:
            _schedule_log.exception("startup: device schedulers init failed")
    else:
        _schedule_log.info(
            "startup: inline schedulers disabled — run `python -m netx_api.worker` for "
            "config_sync / lldp_collect / port_traffic"
        )
    try:
        if bool(getattr(settings, "ume_keepalive_enabled", True)):
            interval_keepalive_s = int(getattr(settings, "ume_keepalive_interval_s", 600) or 600)
            interval_keepalive_s = max(30, min(interval_keepalive_s, 3600))
            renew_before_s = int(getattr(settings, "ume_keepalive_renew_before_s", 900) or 900)
            renew_before_s = max(30, min(renew_before_s, 86400))

            def _keepalive_loop() -> None:
                # Best-effort keepalive: if token exists, periodically handshake to extend TTL.
                while True:
                    try:
                        if ume_support._runtime_is_paused("token_keepalive"):
                            time.sleep(1)
                            continue
                        client = ume_support._ume_client()
                        st = client.token_status()
                        expires_in = int(st.get("expires_in_s") or 0)
                        # Renew when missing/invalid TTL (0) or nearing expiry — previously 0 skipped renew forever.
                        if bool(st.get("has_token")) and (expires_in <= 0 or expires_in < renew_before_s):
                            client.renew_token()
                        ume_support._set_runtime_task("token_keepalive", status="running", last_run_at=datetime.now(timezone.utc), last_error="")
                    except Exception:
                        ume_support._set_runtime_task("token_keepalive", status="error", last_run_at=datetime.now(timezone.utc), last_error=RT_KEEPALIVE_FAILED)
                    time.sleep(interval_keepalive_s)

            t = threading.Thread(target=_keepalive_loop, name="ume-token-keepalive", daemon=True)
            t.start()
    except Exception as exc:
        _schedule_log.exception("startup: token_keepalive thread init failed: %s", exc)
        ume_support._set_runtime_task(
            "token_keepalive",
            status="error",
            last_run_at=datetime.now(timezone.utc),
            last_error=f"startup_thread_init_failed: {str(exc)[:180]}",
        )
    try:

        def _startup_alarm_sync_worker() -> None:
            try:
                ume_support._run_startup_alarm_sync_before_ws()
            except Exception as exc:
                _schedule_log.exception("startup: alarm sync before WSS failed: %s", exc)
                complete_startup_alarm_sync_gate()

        # Do not block HTTP /health on slow UME REST pull; WSS waits on startup_alarm_sync_gate.
        t_startup_sync = threading.Thread(
            target=_startup_alarm_sync_worker,
            name="ume-startup-alarm-sync",
            daemon=True,
        )
        t_startup_sync.start()
    except Exception as exc:
        _schedule_log.exception("startup: alarm sync thread init failed: %s", exc)
        complete_startup_alarm_sync_gate()
    try:
        if bool(getattr(settings, "ume_sync_alarms_current_enabled", True)):
            alarms_interval_s = int(getattr(settings, "ume_sync_alarms_current_interval_s", 18000) or 18000)
            alarms_interval_s = max(30, min(alarms_interval_s, 86400))

            def _alarms_current_sync_loop() -> None:
                ume_support._refresh_runtime_task_idle("alarms_current_auto_sync", "alarms_current")
                ume_support._wait_until_startup_alarm_pull_allowed("alarms_current_auto_sync")
                while True:
                    try:
                        _schedule_log.info(
                            "alarms_current_auto_sync: loop tick paused=%s",
                            ume_support._runtime_is_paused("alarms_current_auto_sync"),
                        )
                        if ume_support._runtime_is_paused("alarms_current_auto_sync"):
                            time.sleep(1)
                            continue
                        if is_startup_alarm_sync_pending():
                            ume_support._refresh_runtime_task_idle(
                                "alarms_current_auto_sync",
                                "alarms_current",
                                last_error=RT_STARTUP_GATE_WAITING,
                            )
                            time.sleep(10)
                            continue
                        if (
                            bool(getattr(settings, "ume_sync_alarms_current_skip_when_ws", True))
                            and is_wss_active_for_current_alarms()
                        ):
                            ume_support._refresh_runtime_task_idle(
                                "alarms_current_auto_sync",
                                "alarms_current",
                                last_error=RT_WSS_ACTIVE_SKIP_REST,
                            )
                            time.sleep(max(30, min(alarms_interval_s, 300)))
                            continue
                        ume_support._maybe_wait_for_sync_interval(
                            task_id="alarms_current_auto_sync",
                            domain="alarms_current",
                            interval_s=alarms_interval_s,
                            label="alarms_current_auto_sync",
                        )
                        _schedule_log.info(
                            "alarms_current_auto_sync: iteration start (interval=%ss)",
                            alarms_interval_s,
                        )
                        ume_support._set_runtime_task(
                            "alarms_current_auto_sync",
                            status="running",
                            last_run_at=datetime.now(timezone.utc),
                            last_error=RT_PULLING_ALARMS_CURRENT,
                        )
                        db = SessionLocal()
                        try:
                            client = ume_support._ume_client()
                            sync_alarms_current(db, client, trigger_mode="schedule")
                            _schedule_log.info("alarms_current_auto_sync: sync finished ok")
                            ume_support._set_runtime_task(
                                "alarms_current_auto_sync",
                                status="running",
                                last_run_at=datetime.now(timezone.utc),
                                last_error="",
                            )
                        finally:
                            db.close()
                    except RuntimeError as exc:
                        if str(exc) == "alarms_current_sync_busy":
                            ume_support._refresh_runtime_task_idle(
                                "alarms_current_auto_sync",
                                "alarms_current",
                                last_error=RT_ALARMS_SYNC_IN_PROGRESS_SKIP,
                            )
                            time.sleep(30)
                        else:
                            raise
                    except Exception as exc:
                        _schedule_log.exception("alarms_current_auto_sync: sync failed: %s", exc)
                        ume_support._set_runtime_task(
                            "alarms_current_auto_sync",
                            status="error",
                            last_run_at=datetime.now(timezone.utc),
                            last_error=str(exc)[:240],
                        )

            t2 = threading.Thread(target=_alarms_current_sync_loop, name="ume-alarms-current-sync", daemon=True)
            t2.start()
            _schedule_log.info("started thread %s alive=%s", t2.name, t2.is_alive())
            if not t2.is_alive():
                _schedule_log.error("ume-alarms-current-sync thread exited immediately (check uncaught errors above)")
    except Exception as exc:
        _schedule_log.exception("startup: alarms_current_auto_sync thread init failed: %s", exc)
        ume_support._set_runtime_task(
            "alarms_current_auto_sync",
            status="error",
            last_run_at=datetime.now(timezone.utc),
            last_error=f"startup_thread_init_failed: {str(exc)[:180]}",
        )
    try:
        if bool(getattr(settings, "ume_sync_inventory_auto_enabled", True)):
            hours = int(getattr(settings, "ume_sync_inventory_every_hours", 48) or 48)
            hours = max(1, min(hours, 168))
            inventory_interval_s = int(hours * 3600)
            ume_support._refresh_runtime_task_idle("inventory_auto_sync", "inventory")

            def _inventory_auto_sync_loop() -> None:
                ume_support._refresh_runtime_task_idle("inventory_auto_sync", "inventory")
                while True:
                    try:
                        _schedule_log.info(
                            "inventory_auto_sync: loop tick paused=%s",
                            ume_support._runtime_is_paused("inventory_auto_sync"),
                        )
                        if ume_support._runtime_is_paused("inventory_auto_sync"):
                            time.sleep(1)
                            continue
                        ume_support._maybe_wait_for_sync_interval(
                            task_id="inventory_auto_sync",
                            domain="inventory",
                            interval_s=inventory_interval_s,
                            label="inventory_auto_sync",
                        )
                        _schedule_log.info(
                            "inventory_auto_sync: iteration start (interval=%ss)",
                            inventory_interval_s,
                        )
                        ume_support._set_runtime_task(
                            "inventory_auto_sync",
                            status="running",
                            last_run_at=datetime.now(timezone.utc),
                            last_error=RT_PULLING_INVENTORY,
                        )
                        db = SessionLocal()
                        try:
                            client = ume_support._ume_client()
                            sync_inventory_full(db, client, trigger_mode="schedule")
                            _schedule_log.info("inventory_auto_sync: sync finished ok")
                            ume_support._set_runtime_task(
                                "inventory_auto_sync",
                                status="running",
                                last_run_at=datetime.now(timezone.utc),
                                last_error="",
                            )
                        finally:
                            db.close()
                    except Exception as exc:
                        _schedule_log.exception("inventory_auto_sync: sync failed: %s", exc)
                        ume_support._set_runtime_task(
                            "inventory_auto_sync",
                            status="error",
                            last_run_at=datetime.now(timezone.utc),
                            last_error=str(exc)[:240],
                        )

            t3 = threading.Thread(target=_inventory_auto_sync_loop, name="ume-inventory-auto-sync", daemon=True)
            t3.start()
            _schedule_log.info("started thread %s alive=%s", t3.name, t3.is_alive())
            if not t3.is_alive():
                _schedule_log.error("ume-inventory-auto-sync thread exited immediately (check uncaught errors above)")
    except Exception as exc:
        _schedule_log.exception("startup: inventory_auto_sync thread init failed: %s", exc)
        ume_support._set_runtime_task(
            "inventory_auto_sync",
            status="error",
            last_run_at=datetime.now(timezone.utc),
            last_error=f"startup_thread_init_failed: {str(exc)[:180]}",
        )
    try:
        if bool(getattr(settings, "ume_alarm_ws_enabled", True)) and str(getattr(settings, "ume_base_url", "") or "").strip():
            if load_persisted_subscription():
                _schedule_log.info("startup: loaded persisted UME alarm subscription")
            ume_support._UME_WS_STOP_EVENT = threading.Event()

            def _ws_on_status(msg: str) -> None:
                ume_support._set_runtime_task(
                    "alarms_current_ws_consumer",
                    status="running",
                    last_run_at=datetime.now(timezone.utc),
                    last_error=str(msg or "")[:240],
                )

            t_ws = start_ume_alarm_ws_consumer(
                ume_support._ume_client(),
                on_status=_ws_on_status,
                stop_event=ume_support._UME_WS_STOP_EVENT,
                is_paused=lambda: ume_support._runtime_is_paused("alarms_current_ws_consumer"),
            )
            _schedule_log.info("started thread %s alive=%s", t_ws.name, t_ws.is_alive())
        else:
            ume_support._set_runtime_task("alarms_current_ws_consumer", status="paused", last_error=RT_UME_WS_DISABLED_NO_BASE_URL)
    except Exception as exc:
        _schedule_log.exception("startup: alarms_current_ws_consumer thread init failed: %s", exc)
        ume_support._set_runtime_task(
            "alarms_current_ws_consumer",
            status="error",
            last_run_at=datetime.now(timezone.utc),
            last_error=f"startup_thread_init_failed: {str(exc)[:180]}",
        )
    try:
        def _fwd_on_status(msg: str) -> None:
            paused = ume_support._runtime_is_paused("oclaw_alarm_forwarder")
            fwd = forwarder_status()
            if paused:
                status = "paused"
            elif not bool(fwd.get("enabled")):
                status = "paused"
            elif bool(fwd.get("connected")):
                status = "running"
            else:
                status = "running"
            ume_support._set_runtime_task(
                "oclaw_alarm_forwarder",
                status=status,
                last_run_at=datetime.now(timezone.utc),
                last_error=str(msg or "")[:240],
            )

        configure_oclaw_alarm_forwarder(
            is_paused=lambda: ume_support._runtime_is_paused("oclaw_alarm_forwarder"),
            on_status=_fwd_on_status,
        )
        if is_forwarder_enabled():
            ume_support._set_runtime_task("oclaw_alarm_forwarder", status="running", last_error="")
        else:
            ume_support._set_runtime_task(
                "oclaw_alarm_forwarder",
                status="paused",
                last_error=RT_OCLAW_FWD_DISABLED,
            )
        t_fwd = start_oclaw_alarm_forwarder()
        if t_fwd is not None:
            _schedule_log.info("started thread %s alive=%s", t_fwd.name, t_fwd.is_alive())
    except Exception as exc:
        _schedule_log.exception("startup: oclaw_alarm_forwarder thread init failed: %s", exc)
        ume_support._set_runtime_task(
            "oclaw_alarm_forwarder",
            status="error",
            last_run_at=datetime.now(timezone.utc),
            last_error=f"startup_thread_init_failed: {str(exc)[:180]}",
        )


@app.on_event("shutdown")
def on_shutdown() -> None:
    shutdown_oclaw_alarm_forwarder()
    if ume_support._UME_WS_STOP_EVENT is not None:
        ume_support._UME_WS_STOP_EVENT.set()
    shutdown_ws_consumer()


@app.get("/health", status_code=200)
def health() -> dict[str, str]:
    return {"status": "ok"}



@app.get("/v1/integrations/status")
def integrations_status(db: Session = Depends(get_db)) -> dict:
    # netx api is up if this handler executes; still verify DB + oclaw bridge separately.
    netx_api = {"status": "up"}

    db_status: dict = {"status": "unknown"}
    try:
        t0 = time.monotonic()
        db.execute(sql_text("select 1"))
        db_status = {"status": "up", "latency_ms": int((time.monotonic() - t0) * 1000)}
    except Exception as exc:
        db_status = {"status": "down", "error": str(exc)[:240]}

    oclaw_status: dict = {"status": "unknown"}
    fwd = forwarder_status()
    if not bool(fwd.get("enabled")):
        oclaw_status = {
            "status": "unknown",
            "mode": "ws",
            "enabled": False,
            "connected": False,
            "error_kind": "disabled",
            "error": "NETX_OCLAW_ALARM_WS_ENABLED=false or missing token/url",
            "forwarder": fwd,
        }
    elif bool(fwd.get("paused")):
        oclaw_status = {
            "status": "unknown",
            "mode": "ws",
            "enabled": True,
            "connected": False,
            "error_kind": "paused",
            "error": "oclaw_alarm_forwarder runtime task paused",
            "forwarder": fwd,
        }
    elif bool(fwd.get("connected")):
        oclaw_status = {
            "status": "up",
            "mode": "ws",
            "enabled": True,
            "connected": True,
            "queue_size": int(fwd.get("queue_size") or 0),
            "published_ok": int(fwd.get("published_ok") or 0),
            "published_fail": int(fwd.get("published_fail") or 0),
            "url": str(fwd.get("url") or ""),
            "forwarder": fwd,
        }
    else:
        oclaw_status = {
            "status": "down",
            "mode": "ws",
            "enabled": True,
            "connected": False,
            "error_kind": "ws_disconnected",
            "error": "oclaw netx-bridge WebSocket not connected",
            "queue_size": int(fwd.get("queue_size") or 0),
            "url": str(fwd.get("url") or ""),
            "forwarder": fwd,
        }

    return {"netx_api": netx_api, "db": db_status, "oclaw_bridge": oclaw_status}


@app.get("/")
def root() -> dict:
    return {
        "ok": True,
        "mode": "api_only",
        "message": "netx UI is served by Vite frontend only",
        "frontend_url": settings.frontend_url,
        "api_health": "/health",
        "api_status": "/v1/integrations/status",
    }


if __name__ == "__main__":
    uvicorn.run("netx_api.main:app", host=settings.host, port=settings.port, reload=False)
