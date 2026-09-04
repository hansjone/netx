"""Shared Netmiko helpers for managed NE connect/collect."""

from __future__ import annotations

import time
from typing import Any


def normalize_netmiko_device_type(device_type: str, protocol: str) -> str:
    dt = str(device_type or "").strip()
    proto = str(protocol or "ssh").strip().lower()
    low = dt.lower()
    # Raw / SecureCRT-style sessions (WebCRT quick-connect stores device_type=generic).
    if low in ("generic", "generic_ssh", "generic_telnet", "terminal_server", "generic_termserver"):
        return "generic_telnet" if proto == "telnet" or "telnet" in low else "generic_termserver_ssh"
    # Netmiko ships linux / linux_ssh but not linux_telnet — use generic_telnet.
    if low in ("linux", "linux_ssh", "linux_telnet") or low.startswith("linux_"):
        if proto == "telnet" or "telnet" in low:
            return "generic_telnet"
        return "linux_ssh"
    if "zte" in low:
        if dt == "zte":
            return f"zte_zxros_{proto}"
        if "telnet" not in low and "ssh" not in low:
            return f"{dt}_{proto}"
        return dt
    if "telnet" not in low and "ssh" not in low:
        return f"{dt}_{proto}"
    return dt


def is_cisco_ios_device_type(device_type: str) -> bool:
    dt = str(device_type or "").strip().lower()
    return "cisco_ios" in dt or dt in {"cisco_ios", "cisco_ios_ssh", "cisco_ios_telnet"}


def is_zte_device_type(device_type: str) -> bool:
    return "zte" in str(device_type or "").strip().lower()


def is_huawei_device_type(device_type: str) -> bool:
    return "huawei" in str(device_type or "").strip().lower()


def paging_disable_commands(*, vendor: str = "", device_type: str = "") -> list[str]:
    """Vendor CLI commands to disable --More-- paging (collection / LLDP / sync)."""
    dt = str(device_type or "").strip().lower()
    v = str(vendor or "").strip().lower()
    blob = f"{dt} {v}"
    if "huawei" in blob or "华为" in v:
        return ["screen-length 0 temporary"]
    if "hp_comware" in blob or "h3c" in blob or "comware" in blob:
        return ["screen-length disable"]
    if "juniper" in blob or "junos" in blob:
        return ["set cli screen-length 0"]
    if "nokia" in blob or "alcatel_sros" in blob or "sros" in blob:
        return ["environment no more"]
    if "alcatel_aos" in blob:
        return ["terminal length 0"]
    # Cisco / ZTE / generic SSH CLIs
    return ["terminal length 0"]


def disable_target_paging(
    conn: Any,
    *,
    vendor: str = "",
    device_type: str = "",
) -> str:
    """Send paging-off on the *current* CLI (critical after CLI hop / bastion jump).

    Nested stelnet/telnet lands on the target without Netmiko ``session_preparation``,
    so --More-- stays enabled and long ``display/show lldp`` / config dumps time out.
    Uses timing reads so a wrong hop ``base_prompt`` does not block.
    """
    cmds = paging_disable_commands(vendor=vendor, device_type=device_type)
    chunks: list[str] = []
    for cmd in cmds:
        try:
            out = conn.send_command_timing(cmd, read_timeout=15)
            chunks.append(str(out or ""))
            continue
        except Exception:
            pass
        try:
            ret = getattr(conn, "RETURN", None) or "\n"
            conn.write_channel(str(cmd) + ret)
            time.sleep(0.35)
            if hasattr(conn, "read_channel"):
                part = conn.read_channel()
                if part:
                    chunks.append(str(part))
        except Exception:
            pass
    return "".join(chunks)


def drain_read_channel(
    conn: Any,
    *,
    idle_reads: int = 4,
    pause_sec: float = 0.05,
) -> str:
    """Read until the SSH/Telnet channel is quiet.

    Cisco IOSv (and similar) often leave an extra ``R2#`` in the buffer after
    ``find_prompt`` / ``terminal length 0``. Netmiko ``send_command`` then matches
    that leftover prompt immediately and returns empty after strip — while a
    human terminal still echoes show output normally.
    """
    chunks: list[str] = []
    idle = 0
    read = getattr(conn, "read_channel", None)
    if not callable(read):
        clear = getattr(conn, "clear_buffer", None)
        if callable(clear):
            try:
                clear()
            except Exception:
                pass
        return ""
    while idle < max(1, int(idle_reads)):
        try:
            part = read()
        except Exception:
            break
        if part:
            chunks.append(str(part))
            idle = 0
            continue
        idle += 1
        time.sleep(max(0.0, float(pause_sec)))
    return "".join(chunks)


def _send_command_expect_prompt(conn: Any, cmd: str, *, read_timeout: int) -> str:
    try:
        return str(
            conn.send_command(
                command_string=cmd,
                read_timeout=read_timeout,
                cmd_verify=False,
            )
            or ""
        )
    except TypeError:
        # Older Netmiko without cmd_verify kwarg.
        return str(conn.send_command(command_string=cmd, read_timeout=read_timeout) or "")


def send_show_command(conn: Any, command: str, *, read_timeout: int = 120) -> str:
    """Send a show/display command via ``send_command`` (wait for device prompt).

    Do not use ``send_command_timing`` as the primary path for Cisco config
    collection: long idle during ``Building configuration...`` is treated as
    end-of-output and truncates the config. Timing is only a fallback when
    expect-prompt returns empty after a channel drain (IOSv leftover-prompt bug).

    ``cmd_verify=False``: Netmiko's default echo check often raises
    ``Pattern not detected: 'show\\ lldp\\ ...'`` on IOSv / hop / slow echo paths.
    """
    cmd = str(command or "").strip()
    if not cmd:
        return ""

    drain_read_channel(conn)
    out = _send_command_expect_prompt(conn, cmd, read_timeout=read_timeout)
    if out.strip():
        return out

    # Leftover prompt matched before command echo — drain again and retry once.
    drain_read_channel(conn)
    out = _send_command_expect_prompt(conn, cmd, read_timeout=read_timeout)
    if out.strip():
        return out

    drain_read_channel(conn)
    try:
        return str(conn.send_command_timing(cmd, read_timeout=read_timeout) or "")
    except Exception:
        return ""
