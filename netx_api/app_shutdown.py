"""Centralized runtime shutdown for API / worker processes."""
from __future__ import annotations

import logging

_log = logging.getLogger("netx.shutdown")


def shutdown_runtime(*, reason: str = "lifespan") -> None:
    """Best-effort stop of schedulers, sidebands, pools, and sessions."""
    _log.info("shutdown_runtime begin reason=%s", reason)

    try:
        from .scheduler_heartbeat import stop_scheduler_heartbeat_publisher

        stop_scheduler_heartbeat_publisher()
    except Exception:  # noqa: BLE001
        _log.exception("stop_scheduler_heartbeat_publisher failed")

    try:
        from .config_sync_scheduler import stop_config_sync_scheduler

        stop_config_sync_scheduler()
    except Exception:  # noqa: BLE001
        _log.exception("stop_config_sync_scheduler failed")

    try:
        from .lldp_collect_scheduler import stop_lldp_collect_scheduler

        stop_lldp_collect_scheduler()
    except Exception:  # noqa: BLE001
        _log.exception("stop_lldp_collect_scheduler failed")

    try:
        from .ne_collect_scheduler import stop_ne_collect_scheduler

        stop_ne_collect_scheduler()
    except Exception:  # noqa: BLE001
        _log.exception("stop_ne_collect_scheduler failed")

    try:
        from .port_traffic_scheduler import stop_port_traffic_scheduler

        stop_port_traffic_scheduler()
    except Exception:  # noqa: BLE001
        _log.exception("stop_port_traffic_scheduler failed")

    try:
        from .fabric_reconcile_scheduler import stop_fabric_reconcile_scheduler

        stop_fabric_reconcile_scheduler()
    except Exception:  # noqa: BLE001
        _log.exception("stop_fabric_reconcile_scheduler failed")

    try:
        import netx_api.ume_support as ume_support

        if ume_support._UME_WS_STOP_EVENT is not None:
            ume_support._UME_WS_STOP_EVENT.set()
    except Exception:  # noqa: BLE001
        _log.exception("set UME_WS_STOP_EVENT failed")

    try:
        from .ume_alarm_ws import shutdown_ws_consumer

        shutdown_ws_consumer()
    except Exception:  # noqa: BLE001
        _log.exception("shutdown_ws_consumer failed")

    try:
        from .oclaw_alarm_forwarder import shutdown_oclaw_alarm_forwarder

        shutdown_oclaw_alarm_forwarder()
    except Exception:  # noqa: BLE001
        _log.exception("shutdown_oclaw_alarm_forwarder failed")

    try:
        from .webcrt_session_registry import close_all_sessions

        close_all_sessions()
    except Exception:  # noqa: BLE001
        _log.exception("close_all_webcrt_sessions failed")

    try:
        from .cli_timeout import shutdown_cli_timeout_pool

        shutdown_cli_timeout_pool(wait=False)
    except Exception:  # noqa: BLE001
        _log.exception("shutdown_cli_timeout_pool failed")

    try:
        from .webcrt_io import shutdown_webcrt_io_executor

        shutdown_webcrt_io_executor(wait=False)
    except Exception:  # noqa: BLE001
        _log.exception("shutdown_webcrt_io_executor failed")

    try:
        from .audit_async import shutdown_audit_worker

        shutdown_audit_worker(timeout_sec=2.0)
    except Exception:  # noqa: BLE001
        _log.exception("shutdown_audit_worker failed")

    try:
        from .ne_collect_runner import shutdown_ne_collect_executor
        from .ne_connect import shutdown_ne_connect_executor
        from .config_sync_runner import shutdown_config_sync_pools

        shutdown_ne_collect_executor()
        shutdown_ne_connect_executor()
        shutdown_config_sync_pools()
    except Exception:  # noqa: BLE001
        _log.exception("shutdown executors failed")

    _log.info("shutdown_runtime done reason=%s", reason)
