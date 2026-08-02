"""API process startup orchestration (schema, recovery, schedulers, UME sidebands)."""

from __future__ import annotations

import logging

from .auth_service import bootstrap_admin_if_needed
from .config import settings
from .db import Base, SessionLocal, engine
from .schema_patches import (
    apply_all_legacy_startup_ddl,
    apply_auth_schema_patches,
    run_alembic_upgrade_to_head,
)
from .security_bootstrap import assert_secure_defaults_or_exit
from .ume_runtime import start_api_sideband_threads, start_device_schedulers
import netx_api.ume_support as ume_support
from .ume_alarm_ws import (
    begin_startup_alarm_sync_gate,
    complete_startup_alarm_sync_gate,
)

_log = logging.getLogger("netx.ume.schedule")


def _configure_ume_diag_logging() -> None:
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


def run_api_startup() -> None:
    """Full API boot sequence previously inlined in ``main.on_startup``."""
    assert_secure_defaults_or_exit()
    _configure_ume_diag_logging()
    Base.metadata.create_all(bind=engine)

    alembic_ok = True
    if bool(getattr(settings, "alembic_upgrade_on_start", True)):
        try:
            run_alembic_upgrade_to_head()
        except Exception:
            alembic_ok = False
            _log.exception("startup: alembic upgrade head failed")

    skip_ddl = bool(getattr(settings, "skip_legacy_startup_ddl", True))
    try:
        with engine.begin() as conn:
            apply_auth_schema_patches(conn)
    except Exception:
        _log.exception("startup: auth schema patches failed")
    if skip_ddl and alembic_ok:
        _log.info("startup: schema via Alembic (legacy inline DDL skipped)")
    else:
        if skip_ddl and not alembic_ok:
            _log.warning("startup: Alembic failed — falling back to legacy schema patches")
        try:
            apply_all_legacy_startup_ddl(engine)
        except Exception:
            _log.exception("startup: legacy schema patches failed")

    ume_support._reset_runtime_pause_flags()
    ume_support._fail_stale_running_sync_jobs_on_startup()
    try:
        from .topology_service import bootstrap_topology_tree, reclaim_stale_discover_jobs

        db_topo = SessionLocal()
        try:
            bootstrap_topology_tree(db_topo)
            closed = reclaim_stale_discover_jobs(db_topo, force_all_open=True)
            if closed:
                _log.warning("startup: closed %s orphaned topology discover jobs", closed)
        finally:
            db_topo.close()
    except Exception:
        _log.exception("startup: topology discover job cleanup failed")

    if ume_support._needs_startup_alarm_sync_before_ws():
        begin_startup_alarm_sync_gate()
        _log.info(
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
            _log.exception("startup: auth bootstrap admin failed")
        from .collection_recovery import recover_collection_jobs_on_startup

        resumed = recover_collection_jobs_on_startup(db)
        if resumed:
            _log.info("startup: resumed %s pending ne collection runs", resumed)
        from .config_sync_recovery import recover_config_sync_on_startup
        from .config_sync_service import ensure_policy
        from .port_traffic_recovery import recover_port_traffic_on_startup

        ensure_policy(db)
        cfg_resumed = recover_config_sync_on_startup(db)
        if cfg_resumed:
            _log.info("startup: resumed %s config_sync task(s) from interrupted cycle", cfg_resumed)
        try:
            from .lldp_collect_service import ensure_policy as ensure_lldp_collect_policy

            ensure_lldp_collect_policy(db)
        except Exception:
            _log.exception("startup: lldp_collect policy ensure failed")
        pt_cleared = recover_port_traffic_on_startup(db)
        if pt_cleared:
            _log.info("startup: cleared %s port_traffic stuck collect_running flag(s)", pt_cleared)
        try:
            from .port_traffic_migrate import backfill_port_traffic_series

            backfill_port_traffic_series(db)
        except Exception:
            _log.exception("startup: port_traffic series backfill failed")
    except Exception:
        _log.exception("startup: ne collection / config_sync recovery failed")
    finally:
        db.close()

    if bool(getattr(settings, "run_inline_schedulers", False)):
        try:
            start_device_schedulers()
        except Exception:
            _log.exception("startup: device schedulers init failed")
    else:
        _log.info(
            "startup: inline schedulers disabled — run `python -m netx_api.worker` for "
            "config_sync / lldp_collect / port_traffic"
        )

    start_api_sideband_threads()
