"""UME / long-task runtime helpers shared by API and optional worker process.

The API process still owns UME token keepalive, alarm WSS, and oclaw forwarder.
Config-sync / LLDP / port-traffic tick loops can run inline (default) or via
``python -m netx_api.worker`` when ``NETX_RUN_INLINE_SCHEDULERS=false``.
"""

from __future__ import annotations

import logging

_log = logging.getLogger("netx.ume.runtime")


def start_device_schedulers() -> None:
    """Start device-facing periodic collectors (safe to call once per process)."""
    from .config_sync_scheduler import start_config_sync_scheduler
    from .lldp_collect_scheduler import start_lldp_collect_scheduler
    from .port_traffic_scheduler import start_port_traffic_scheduler

    start_config_sync_scheduler()
    start_lldp_collect_scheduler()
    start_port_traffic_scheduler()
    _log.info("device schedulers started")
