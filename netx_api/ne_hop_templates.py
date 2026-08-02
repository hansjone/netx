"""Vendor hop / bastion username templates and rendering."""
from __future__ import annotations

from typing import Any

_HOP_PLACEHOLDERS = ("target_ip", "target_port", "target_user", "target_password", "vrf", "hop_user", "hop_host")

# ZTE CLI jump: ssh/telnet <ip> [vrf <name>] — target user/password via secondary auth.
_LEGACY_HOP_TEMPLATES = frozenset({"ssh {target_user}@{target_ip}", "ssh {target_ip}", "telnet {target_ip}"})

def default_zte_hop_template(protocol: str, vrf: str = "") -> str:
    cmd = "telnet" if str(protocol or "ssh").strip().lower() == "telnet" else "ssh"
    v = str(vrf or "").strip()
    if v:
        return f"{cmd} {{target_ip}} vrf {{vrf}}"
    return f"{cmd} {{target_ip}}"


def default_cisco_hop_template(protocol: str, vrf: str = "") -> str:
    """Cisco CLI jump: ssh -vrf VRF IP; telnet IP [/vrf VRF]."""
    v = str(vrf or "").strip()
    if str(protocol or "ssh").strip().lower() == "telnet":
        if v:
            return "telnet {target_ip} /vrf {vrf}"
        return "telnet {target_ip}"
    if v:
        return "ssh -vrf {vrf} {target_ip}"
    return "ssh {target_ip}"


def default_huawei_hop_template(protocol: str, vrf: str = "") -> str:
    """Huawei CLI jump: telnet [vpn-instance VRF] IP; stelnet = SSH."""
    v = str(vrf or "").strip()
    if str(protocol or "ssh").strip().lower() == "telnet":
        if v:
            return "telnet vpn-instance {vrf} {target_ip}"
        return "telnet {target_ip}"
    if v:
        return "stelnet {target_ip} -vpn-instance {vrf}"
    return "stelnet {target_ip}"


def default_bastion_username_template() -> str:
    """SSH username sent to bastion (OpenSSH splits user@host at the last @)."""
    return "{hop_user}@{target_user}@{target_ip}"


_LEGACY_BASTION_USERNAME_TEMPLATE = "{hop_user}@{target_user}@{target_ip}@{hop_host}"


def resolve_bastion_ssh_username(rendered: str, hop_host: str) -> str:
    """Map template output to the SSH username Paramiko must send.

    CLI ``ssh hop@target@ip@bastion`` is parsed by OpenSSH as user ``hop@target@ip``
    and host ``bastion``. Legacy templates that included ``{hop_host}`` duplicated the
    bastion address inside the username and break authentication.
    """
    user = str(rendered or "").strip()
    host = str(hop_host or "").strip()
    if not user or not host:
        return user
    suffix = f"@{host}"
    if user.endswith(suffix):
        return user[:-len(suffix)]
    return user


def bastion_ssh_cli(username: str, hop_host: str, hop_port: int = 22) -> str:
    """Human-readable ssh command equivalent (for logs/UI)."""
    host = str(hop_host or "").strip()
    user = str(username or "").strip()
    target = f"{user}@{host}" if user else host
    port = int(hop_port or 22)
    if port != 22:
        return f"ssh -p {port} {target}"
    return f"ssh {target}"


def default_hop_command_template(vendor: str, protocol: str, vrf: str = "") -> str:
    v = str(vendor or "zte").strip().lower()
    if v == "bastion":
        return default_bastion_username_template()
    if v == "huawei":
        return default_huawei_hop_template(protocol, vrf)
    if v == "cisco":
        return default_cisco_hop_template(protocol, vrf)
    return default_zte_hop_template(protocol, vrf)


def _hop_vendor(creds: dict[str, Any]) -> str:
    return str(creds.get("hop_vendor") or "zte").strip().lower()


def render_hop_command(template: str, creds: dict[str, Any]) -> str:
    """Render hop command from template using whitelisted placeholders only."""
    tpl = str(template or "").strip()
    if not tpl or tpl in _LEGACY_HOP_TEMPLATES:
        tpl = default_hop_command_template(
            _hop_vendor(creds),
            str(creds.get("hop_protocol") or "ssh"),
            str(creds.get("hop_vrf") or ""),
        )
    values = {
        "target_ip": str(creds.get("ip_address") or ""),
        "target_port": str(int(creds.get("port") or 22)),
        "target_user": str(creds.get("username") or ""),
        "target_password": str(creds.get("password") or ""),
        "vrf": str(creds.get("hop_vrf") or "").strip(),
        "hop_user": str(creds.get("hop_username") or ""),
        "hop_host": str(creds.get("hop_host") or "").strip(),
    }
    out = tpl
    for key in _HOP_PLACEHOLDERS:
        out = out.replace("{" + key + "}", values[key])
    if "{" in out or "}" in out:
        raise ValueError("hop_command_template_invalid_placeholder")
    return out


