"""Port traffic monitoring service facade (device CRUD, discover, samples, dashboard)."""

from __future__ import annotations

from .port_traffic_common import _target_out, _utcnow
from .port_traffic_devices import (
    create_device,
    delete_device,
    discover_ports,
    get_device,
    list_devices,
    list_series,
    list_targets,
    put_interfaces,
    rebind_device,
    replace_series_port,
    set_device_status,
    update_device,
)
from .port_traffic_samples import (
    append_device_event,
    baseline_offset_hours,
    compare_series,
    compare_targets,
    dashboard,
    get_samples,
    list_device_events,
    purge_expired_samples,
)

__all__ = [
    "_target_out",
    "_utcnow",
    "append_device_event",
    "baseline_offset_hours",
    "compare_series",
    "compare_targets",
    "create_device",
    "dashboard",
    "delete_device",
    "discover_ports",
    "get_device",
    "get_samples",
    "list_device_events",
    "list_devices",
    "list_series",
    "list_targets",
    "purge_expired_samples",
    "put_interfaces",
    "rebind_device",
    "replace_series_port",
    "set_device_status",
    "update_device",
]
