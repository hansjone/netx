"""Vendor → port traffic CLI command matrix (TextFSM-backed vendors)."""

from __future__ import annotations

from dataclasses import dataclass

from .config_sync_commands import normalize_vendor_key


@dataclass(frozen=True)
class PortTrafficCommands:
    brief: str
    detail_template: str  # format with ifname=
    vendor_key: str = "other"
    notes: str = ""


def commands_for_vendor(vendor: str, device_type: str = "") -> PortTrafficCommands | None:
    """Return brief/detail commands when a TextFSM path exists (custom or community)."""
    key = normalize_vendor_key(vendor, device_type)
    if key == "zte":
        return PortTrafficCommands(
            brief="show interface brief",
            detail_template="show interface {ifname}",
            vendor_key=key,
            notes="NetX custom TextFSM (+ community zte_zxros).",
        )
    if key == "huawei":
        return PortTrafficCommands(
            brief="display interface brief",
            detail_template="display interface {ifname}",
            vendor_key=key,
            notes="NetX custom TextFSM (community prompt-sensitive).",
        )
    if key == "cisco":
        return PortTrafficCommands(
            brief="show ip interface brief",
            detail_template="show interfaces {ifname}",
            vendor_key=key,
            notes="Community ntc (ios/nxos/xr via device_type platform).",
        )
    if key == "h3c":
        return PortTrafficCommands(
            brief="display interface brief",
            detail_template="display interface {ifname}",
            vendor_key=key,
            notes="Community hp_comware TextFSM.",
        )
    if key == "juniper":
        return PortTrafficCommands(
            brief="show interfaces",
            detail_template="show interfaces {ifname}",
            vendor_key=key,
            notes="Community juniper_junos show interfaces (rates may be sparse).",
        )
    if key == "nokia":
        return PortTrafficCommands(
            brief="show port",
            detail_template="show port {ifname}",
            vendor_key=key,
            notes="Community alcatel_sros show port (status; rates usually absent).",
        )
    if key == "mikrotik":
        return PortTrafficCommands(
            brief="/interface print brief",
            detail_template="/interface print detail where name={ifname}",
            vendor_key=key,
            notes="Community mikrotik_routeros interface print*.",
        )
    return None


def detail_command(cmds: PortTrafficCommands, ifname: str) -> str:
    return cmds.detail_template.format(ifname=ifname)
