"""UME / long-task runtime helpers shared by API and optional worker process.

Device collectors (config_sync / LLDP / port_traffic) run via ``start_device_schedulers``
(API inline by default; set ``NETX_RUN_INLINE_SCHEDULERS=false`` and run
``python -m netx_api.worker`` for a split process).
API process also owns UME keepalive, alarm WSS, current-alarm/inventory sync loops,
and oclaw forwarder via ``start_api_sideband_threads``.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from .config import settings
from .db import SessionLocal
import netx_api.ume_support as ume_support
from .ume_alarm_ws import (
    complete_startup_alarm_sync_gate,
    is_startup_alarm_sync_pending,
    is_wss_active_for_current_alarms,
    load_persisted_subscription,
    start_ume_alarm_ws_consumer,
)
from .ume_sync_service import sync_alarms_current, sync_inventory_full, sync_topology_full
from .ume_sync_topology import fail_stale_topology_running_jobs
from .runtime_task_messages import (
    RT_ALARMS_SYNC_IN_PROGRESS_SKIP,
    RT_KEEPALIVE_FAILED,
    RT_OCLAW_FWD_DISABLED,
    RT_PULLING_ALARMS_CURRENT,
    RT_PULLING_INVENTORY,
    RT_PULLING_TOPOLOGY,
    RT_STARTUP_GATE_WAITING,
    RT_UME_WS_DISABLED_NO_BASE_URL,
    RT_WSS_ACTIVE_SKIP_REST,
)
from .oclaw_alarm_forwarder import (
    configure_oclaw_alarm_forwarder,
    forwarder_status,
    is_forwarder_enabled,
    start_oclaw_alarm_forwarder,
)

_log = logging.getLogger("netx.ume.runtime")
_schedule_log = logging.getLogger("netx.ume.schedule")


def start_device_schedulers() -> None:
    """Start device-facing periodic collectors (safe to call once per process)."""
    from .config_sync_scheduler import start_config_sync_scheduler
    from .fabric_reconcile_scheduler import start_fabric_reconcile_scheduler
    from .lldp_collect_scheduler import start_lldp_collect_scheduler
    from .port_traffic_scheduler import start_port_traffic_scheduler
    from .scheduler_heartbeat import start_scheduler_heartbeat_publisher

    start_config_sync_scheduler()
    start_lldp_collect_scheduler()
    start_port_traffic_scheduler()
    start_fabric_reconcile_scheduler()
    # Publish status so API /metrics can see collectors when run in a split worker.
    role = "api_inline" if bool(getattr(settings, "run_inline_schedulers", True)) else "worker"
    start_scheduler_heartbeat_publisher(role=role)
    _log.info("device schedulers started")


def start_api_sideband_threads() -> None:
    """UME keepalive / alarm sync / inventory / WSS / oclaw forwarder (API process)."""
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
                            ume_support._debounce_wake_event("alarms_current_auto_sync").wait(timeout=1.0)
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
                            ume_support._debounce_wake_event("inventory_auto_sync").wait(timeout=1.0)
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
        if bool(getattr(settings, "ume_sync_topology_auto_enabled", True)):
            hours = int(getattr(settings, "ume_sync_topology_every_hours", 24) or 24)
            hours = max(1, min(hours, 168))
            topology_interval_s = int(hours * 3600)
            ume_support._refresh_runtime_task_idle("topology_auto_sync", "topology")

            def _topology_auto_sync_loop() -> None:
                ume_support._refresh_runtime_task_idle("topology_auto_sync", "topology")
                while True:
                    try:
                        _schedule_log.info(
                            "topology_auto_sync: loop tick paused=%s",
                            ume_support._runtime_is_paused("topology_auto_sync"),
                        )
                        if ume_support._runtime_is_paused("topology_auto_sync"):
                            ume_support._debounce_wake_event("topology_auto_sync").wait(timeout=1.0)
                            continue
                        ume_support._maybe_wait_for_sync_interval(
                            task_id="topology_auto_sync",
                            domain="topology",
                            interval_s=topology_interval_s,
                            label="topology_auto_sync",
                        )
                        _schedule_log.info(
                            "topology_auto_sync: iteration start (interval=%ss)",
                            topology_interval_s,
                        )
                        ume_support._set_runtime_task(
                            "topology_auto_sync",
                            status="running",
                            last_run_at=datetime.now(timezone.utc),
                            last_error=RT_PULLING_TOPOLOGY,
                        )
                        db = SessionLocal()
                        try:
                            fail_stale_topology_running_jobs(db)
                            client = ume_support._ume_client()
                            # Empty inventory makes Fabric naming/IP weak — pull inventory first.
                            try:
                                from sqlalchemy import func

                                from .models import UmeInventoryNE

                                inv_n = int(db.query(func.count(UmeInventoryNE.ne_id)).scalar() or 0)
                                if inv_n <= 0:
                                    _schedule_log.info(
                                        "topology_auto_sync: inventory empty — syncing inventory first"
                                    )
                                    sync_inventory_full(db, client, trigger_mode="schedule")
                            except Exception:
                                _schedule_log.exception(
                                    "topology_auto_sync: pre-inventory sync failed (continuing topology)"
                                )
                            sync_topology_full(db, client, trigger_mode="schedule")
                            _schedule_log.info("topology_auto_sync: sync finished ok")
                            ume_support._set_runtime_task(
                                "topology_auto_sync",
                                status="running",
                                last_run_at=datetime.now(timezone.utc),
                                last_error="",
                            )
                        finally:
                            db.close()
                    except RuntimeError as exc:
                        if str(exc).startswith("topology_sync_busy"):
                            _schedule_log.info("topology_auto_sync: skipped busy (%s)", exc)
                            ume_support._refresh_runtime_task_idle(
                                "topology_auto_sync",
                                "topology",
                                last_error="rt:topology_sync_busy",
                            )
                            time.sleep(30)
                        else:
                            raise
                    except Exception as exc:
                        _schedule_log.exception("topology_auto_sync: sync failed: %s", exc)
                        ume_support._set_runtime_task(
                            "topology_auto_sync",
                            status="error",
                            last_run_at=datetime.now(timezone.utc),
                            last_error=str(exc)[:240],
                        )

            t_topo = threading.Thread(
                target=_topology_auto_sync_loop, name="ume-topology-auto-sync", daemon=True
            )
            t_topo.start()
            _schedule_log.info("started thread %s alive=%s", t_topo.name, t_topo.is_alive())
            if not t_topo.is_alive():
                _schedule_log.error("ume-topology-auto-sync thread exited immediately (check uncaught errors above)")
    except Exception as exc:
        _schedule_log.exception("startup: topology_auto_sync thread init failed: %s", exc)
        ume_support._set_runtime_task(
            "topology_auto_sync",
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

    # Dock already has topology but Fabric/World empty (or last apply partial): heal without waiting 24h.
    try:

        def _startup_ume_fabric_apply() -> None:
            time.sleep(3)
            db = SessionLocal()
            try:
                from .ume_topology_apply import apply_ume_dock_to_fabric_if_needed

                out = apply_ume_dock_to_fabric_if_needed(db, reason="startup")
                if out:
                    _schedule_log.info(
                        "startup: ume dock→fabric apply ok nodes_ensured=%s",
                        (out.get("fabric_apply") or {}).get("nodes_ensured"),
                    )
                else:
                    _schedule_log.info("startup: ume dock→fabric apply skipped (no gap)")
            except Exception as exc:
                _schedule_log.exception("startup: ume dock→fabric apply failed: %s", exc)
            finally:
                db.close()

        t_apply = threading.Thread(
            target=_startup_ume_fabric_apply,
            name="ume-fabric-apply-startup",
            daemon=True,
        )
        t_apply.start()
        _schedule_log.info("started thread %s alive=%s", t_apply.name, t_apply.is_alive())
    except Exception as exc:
        _schedule_log.exception("startup: ume fabric apply thread init failed: %s", exc)



