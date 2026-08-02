"""Startup capacity checks and operator-facing budget logging."""
from __future__ import annotations

import logging

from .cli_budget import cli_budget_status, feature_hard_cap
from .config import settings
from .db import db_pool_status

_log = logging.getLogger("netx.runtime.budget")


def log_runtime_budget(*, role: str = "api") -> None:
    """Log effective pools/CLI caps and warn when capacity looks undersized."""
    pool_size = max(1, int(getattr(settings, "db_pool_size", 25) or 25))
    overflow = max(0, int(getattr(settings, "db_max_overflow", 15) or 15))
    pool_cap = pool_size + overflow
    cli = max(1, int(getattr(settings, "cli_max_concurrent", 12) or 12))
    hard = feature_hard_cap()
    http_reserve = 8  # rough floor for request handlers + UME WS bursts
    recommended_pool = cli + http_reserve

    _log.info(
        "runtime budget role=%s db_pool=%s+%s=%s cli_max=%s feature_hard_cap=%s "
        "timeout_pool=%s pt_dispatch=%s webcrt_sessions=%s audit_sample_n=%s inline_schedulers=%s",
        role,
        pool_size,
        overflow,
        pool_cap,
        cli,
        hard,
        int(getattr(settings, "cli_timeout_pool_workers", 12) or 12),
        int(getattr(settings, "port_traffic_dispatch_workers", 3) or 3),
        int(getattr(settings, "webcrt_max_sessions", 12) or 12),
        int(getattr(settings, "audit_sample_n", 10) or 10),
        bool(getattr(settings, "run_inline_schedulers", True)),
    )
    _log.info("runtime budget snapshot pool=%s cli=%s", db_pool_status(), cli_budget_status())

    if pool_cap < recommended_pool:
        _log.warning(
            "DB pool capacity %s < recommended %s (cli_max=%s + http_reserve=%s). "
            "Raise NETX_DB_POOL_SIZE / NETX_DB_MAX_OVERFLOW or lower NETX_CLI_MAX_CONCURRENT.",
            pool_cap,
            recommended_pool,
            cli,
            http_reserve,
        )
    host = str(getattr(settings, "host", "") or "").strip().lower()
    if host not in {"127.0.0.1", "localhost", "::1"} and bool(
        getattr(settings, "run_inline_schedulers", True)
    ):
        _log.warning(
            "Non-loopback bind (%s) with inline schedulers — for production prefer "
            "NETX_RUN_INLINE_SCHEDULERS=false and `python -m netx_api.worker`.",
            host,
        )
