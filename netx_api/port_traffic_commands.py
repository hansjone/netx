"""Vendor → port traffic CLI command matrix (ZTE first)."""

from __future__ import annotations

from dataclasses import dataclass

from .config_sync_commands import normalize_vendor_key


@dataclass(frozen=True)
class PortTrafficCommands:
    brief: str
    detail_template: str  # format with ifname=
    vendor_key: str = "other"


def commands_for_vendor(vendor: str, device_type: str = "") -> PortTrafficCommands | None:
    key = normalize_vendor_key(vendor, device_type)
    if key == "zte":
        return PortTrafficCommands(
            brief="show interface brief",
            detail_template="show interface {ifname}",
            vendor_key=key,
        )
    return None


def detail_command(cmds: PortTrafficCommands, ifname: str) -> str:
    return cmds.detail_template.format(ifname=ifname)
