"""Vendor → config-collection CLI command matrix."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConfigCommands:
    """primary → config_zlib; alt → config_alt_zlib (Juniper only)."""

    primary: str
    alt: str | None = None
    vendor_key: str = "other"


def normalize_vendor_key(vendor: str, device_type: str = "") -> str:
    blob = f"{vendor} {device_type}".strip().lower()
    if "juniper" in blob or "junos" in blob:
        return "juniper"
    if "nokia" in blob or "alcatel" in blob or "sros" in blob or "tiomos" in blob:
        return "nokia"
    if "ericsson" in blob:
        return "ericsson"
    if "huawei" in blob or "vrp" in blob:
        return "huawei"
    if "h3c" in blob or "comware" in blob:
        return "h3c"
    if "zte" in blob or "zxros" in blob:
        return "zte"
    if "cisco" in blob or "ios" in blob or "nx-os" in blob or "xr" in blob:
        return "cisco"
    return "other"


def commands_for_vendor(vendor: str, device_type: str = "") -> ConfigCommands | None:
    key = normalize_vendor_key(vendor, device_type)
    if key in ("cisco", "zte"):
        return ConfigCommands(primary="show running-config", vendor_key=key)
    if key in ("huawei", "h3c"):
        return ConfigCommands(primary="display current-configuration", vendor_key=key)
    if key == "juniper":
        return ConfigCommands(
            primary="show configuration | display set",
            alt="show configuration | no-more",
            vendor_key=key,
        )
    if key == "nokia":
        return ConfigCommands(primary="admin display-config", vendor_key=key)
    if key == "ericsson":
        return ConfigCommands(primary="show configuration", vendor_key=key)
    return None


def command_list(cmds: ConfigCommands) -> list[str]:
    out = [cmds.primary]
    if cmds.alt:
        out.append(cmds.alt)
    return out
