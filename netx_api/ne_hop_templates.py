"""Vendor hop / bastion username templates and rendering."""
from __future__ import annotations

import re
from typing import Any

_HOP_PLACEHOLDERS = ("target_ip", "target_port", "target_user", "target_password", "vrf", "hop_user", "hop_host")

# ZTE CLI jump: ssh/telnet <ip> [vrf <name>] — target user/password via secondary auth.
_LEGACY_HOP_TEMPLATES = frozenset({"ssh {target_user}@{target_ip}", "ssh {target_ip}", "telnet {target_ip}"})

_SSH_PREFIX_RE = re.compile(r"^(?:ssh(?:\s+-p\s+\d+)?\s+)", re.IGNORECASE)


def normalize_hop_host(value: str) -> str:
    """Normalize bastion/jump host: IP or FQDN (strip ssh://, port suffix, trailing /)."""
    host = str(value or "").strip()
    if not host:
        return ""
    host = _SSH_PREFIX_RE.sub("", host).strip()
    if "://" in host:
        # ssh://user@host:port/ → keep right-hand host-ish fragment for further parse
        host = host.split("://", 1)[1]
    host = host.strip().rstrip("/")
    # Bracketed IPv6: [2001:db8::1]:22
    if host.startswith("[") and "]" in host:
        inside, _, rest = host[1:].partition("]")
        if rest in ("",) or rest.startswith(":"):
            return inside.strip()
        return host
    # host:port (not IPv6) — keep host only when port is numeric
    if host.count(":") == 1:
        left, right = host.rsplit(":", 1)
        if right.isdigit() and left and "@" not in left:
            return left.strip()
    return host


def parse_bastion_ssh_destination(value: str) -> dict[str, str]:
    """Parse OpenSSH-style bastion destination (IP or domain bastion host).

    Examples::

        ssh-bastion.example.com
        192.0.2.10
        bastion-user@target-user@198.51.100.20@ssh-bastion.example.com
        ssh bastion-user@target-user@198.51.100.20@ssh-bastion.example.com

    OpenSSH splits ``user@host`` at the **last** ``@``, so the bastion address
    (IP or FQDN) is the final segment; preceding segments form the SSH username.
    """
    raw = str(value or "").strip()
    raw = _SSH_PREFIX_RE.sub("", raw).strip().strip('"').strip("'")
    if not raw:
        return {"hop_host": "", "hop_username": "", "target_user": "", "target_ip": "", "ssh_username": ""}

    if "@" not in raw:
        host = normalize_hop_host(raw)
        return {
            "hop_host": host,
            "hop_username": "",
            "target_user": "",
            "target_ip": "",
            "ssh_username": "",
        }

    user_part, host_part = raw.rsplit("@", 1)
    hop_host = normalize_hop_host(host_part)
    ssh_username = str(user_part or "").strip()
    parts = [p for p in ssh_username.split("@") if p != ""]
    hop_username = parts[0] if parts else ""
    target_user = parts[1] if len(parts) >= 2 else ""
    target_ip = parts[2] if len(parts) >= 3 else ""
    return {
        "hop_host": hop_host,
        "hop_username": hop_username,
        "target_user": target_user,
        "target_ip": target_ip,
        "ssh_username": ssh_username,
    }


def expand_bastion_hop_fields(
    *,
    hop_host: str,
    hop_username: str = "",
) -> tuple[str, str]:
    """If hop_host is a pasted ``user@…@bastion`` string, split into (host, username).

    Hostname-only / IP values are returned unchanged. Existing hop_username wins
    unless the paste clearly includes a composite username.
    """
    raw_host = str(hop_host or "").strip()
    cur_user = str(hop_username or "").strip()
    if "@" not in raw_host:
        return normalize_hop_host(raw_host), cur_user
    parsed = parse_bastion_ssh_destination(raw_host)
    host = str(parsed.get("hop_host") or "")
    pasted_user = str(parsed.get("hop_username") or "")
    return host, (pasted_user or cur_user)

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
    and host ``bastion`` (IP or FQDN). Legacy templates that included ``{hop_host}``
    duplicated the bastion address inside the username and break authentication.
    """
    user = str(rendered or "").strip()
    host = normalize_hop_host(hop_host)
    if not user or not host:
        return user
    # Prefer exact suffix strip; also accept case-insensitive FQDN match.
    suffix = f"@{host}"
    if user.endswith(suffix):
        return user[: -len(suffix)]
    lower_user = user.lower()
    lower_suffix = suffix.lower()
    if lower_user.endswith(lower_suffix):
        return user[: -len(suffix)]
    return user


def bastion_ssh_cli(username: str, hop_host: str, hop_port: int = 22) -> str:
    """Human-readable ssh command equivalent (for logs/UI)."""
    host = normalize_hop_host(hop_host)
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
        "hop_host": normalize_hop_host(str(creds.get("hop_host") or "")),
    }
    out = tpl
    for key in _HOP_PLACEHOLDERS:
        out = out.replace("{" + key + "}", values[key])
    if "{" in out or "}" in out:
        raise ValueError("hop_command_template_invalid_placeholder")
    return out

