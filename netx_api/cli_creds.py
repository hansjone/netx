"""Shared CLI credential readiness checks (non-interactive exec vs WebCRT)."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

# Reasons surfaced in job rows / execManagedNe responses (grep-friendly).
REASON_IP_REQUIRED = "ip_address_required"
REASON_USERNAME_REQUIRED = "cli_username_required"
REASON_NO_PASSWORD = "no_password"
REASON_HOP_INCOMPLETE = "hop_credentials_incomplete"
REASON_INCOMPLETE = "credentials_incomplete"


def cli_creds_ready(creds: dict[str, Any], *, interactive: bool = False) -> tuple[bool, str]:
    """Return (ready, reason). ``reason`` is empty when ready.

    Non-interactive paths (LLDP, config sync, collection, MCP exec) require
    enough credentials to authenticate without a human at the terminal.

    Interactive WebCRT allows telnet without saved username/password so the user
    can type credentials in the terminal (SecureCRT-style).
    """
    ip = str(creds.get("ip_address") or "").strip()
    if not ip:
        return False, REASON_IP_REQUIRED

    hop_enabled = bool(creds.get("hop_enabled"))
    hop_vendor = str(creds.get("hop_vendor") or "").strip().lower()
    auth_mode = str(creds.get("hop_target_auth_mode") or "bastion_managed").strip().lower()
    protocol = str(creds.get("protocol") or "ssh").strip().lower()
    username = str(creds.get("username") or "").strip()
    password = str(creds.get("password") or "")

    if hop_enabled:
        hop_host = str(creds.get("hop_host") or "").strip()
        hop_user = str(creds.get("hop_username") or "").strip()
        hop_pass = str(creds.get("hop_password") or "")
        if not hop_host or not hop_user or not hop_pass:
            return False, REASON_HOP_INCOMPLETE
        # Bastion-managed: target password may live on the bastion side.
        if hop_vendor == "bastion" and auth_mode == "bastion_managed":
            return True, ""
        if not username:
            return False, REASON_USERNAME_REQUIRED
        if not password:
            return False, REASON_NO_PASSWORD
        return True, ""

    if interactive and protocol == "telnet":
        return True, ""

    if not username:
        return False, REASON_USERNAME_REQUIRED
    if not password:
        return False, REASON_NO_PASSWORD
    return True, ""


def cli_creds_skip_reason(creds: dict[str, Any], *, interactive: bool = False) -> str | None:
    """Return a skip/fail reason, or None when CLI may proceed."""
    ready, reason = cli_creds_ready(creds, interactive=interactive)
    return None if ready else reason


def require_cli_creds_ready(creds: dict[str, Any], *, interactive: bool = False) -> None:
    """Raise HTTP 400 when credentials cannot support the requested CLI mode."""
    ready, reason = cli_creds_ready(creds, interactive=interactive)
    if not ready:
        raise HTTPException(status_code=400, detail=reason or REASON_INCOMPLETE)
