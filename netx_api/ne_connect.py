from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from .config import settings
from .db import SessionLocal
from .models import ManagedNE
from .ne_crypto import CredentialCryptoError
from .ne_service import get_device_credentials
from .ne_session_factory import open_netmiko_connection

_log = logging.getLogger("netx.ne.connect")
_executor: ThreadPoolExecutor | None = None


def _executor_pool() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        workers = max(1, int(settings.ne_connect_max_workers or 5))
        _executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ne-connect")
    return _executor


def hostname_probe_command(device_type: str, vendor: str) -> str | None:
    """
    Per-vendor CLI to read system name (ported from legacy connect.extract_dev_command).
    ZTE: rely on login prompt / empty command path.
    """
    dt = str(device_type or "").lower()
    v = str(vendor or "").lower()
    if "huawei" in dt or v == "huawei":
        return "display current-configuration | include sysname"
    if "juniper" in dt or v == "juniper":
        return "show system host-name"
    if "cisco" in dt or v == "cisco":
        return "show hostname"
    return None


def parse_hostname_from_output(
    device_type: str,
    vendor: str,
    output: str,
    prompt: str = "",
) -> str | None:
    """
    Parse device name from command output or prompt (legacy connect.extract_hostname).
    """
    dt = str(device_type or "").lower()
    v = str(vendor or "").lower()
    text = str(output or "")

    if "huawei" in dt or v == "huawei":
        m = re.search(r"sysname\s+(\S+)", text, re.IGNORECASE)
        if m:
            return m.group(1).strip()

    if "juniper" in dt or v == "juniper":
        m = re.search(r"host-name\s+(\S+)", text, re.IGNORECASE)
        if m:
            return m.group(1).strip().rstrip(";")
        m = re.search(r"^\s*name\s+(\S+)", text, re.IGNORECASE | re.MULTILINE)
        if m:
            return m.group(1).strip().rstrip(";")

    if "cisco" in dt or v == "cisco":
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        for ln in reversed(lines):
            if ln.startswith("%") or "invalid" in ln.lower():
                continue
            token = ln.split()[0].strip("<>[]")
            if token:
                return token

    if "zte" in dt or v == "zte":
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if lines:
            last = lines[-1].strip()
            if last and len(last) <= 128 and not last.startswith("%"):
                return last

    cleaned = _clean_prompt_hostname(prompt)
    if cleaned:
        return cleaned
    return None


def _clean_prompt_hostname(prompt: str) -> str | None:
    p = str(prompt or "").strip()
    if not p:
        return None
    p = re.sub(r"[\s#>$]+\s*$", "", p).strip()
    p = re.sub(r"^[<\[]|[>\]]$", "", p).strip()
    if not p or p.lower() in (">", "#"):
        return None
    return p[:256]


def _classify_connect_error(creds: dict[str, Any], exc: BaseException) -> str:
    raw = str(exc).lower()
    detail = str(exc).split("\n")[0][:480]
    if creds.get("hop_enabled"):
        if "hop_credentials_incomplete" in raw or "hop_command_template_invalid" in raw:
            return detail
        if "target_auth_timeout" in raw:
            return "target_auth_failed: " + detail
        if "authentication" in raw or "auth" in raw:
            if "hop_host" in raw or str(creds.get("hop_host") or "") in raw:
                return "hop_auth_failed: " + detail
            return "target_auth_failed: " + detail
        if "timed out" in raw or "timeout" in raw:
            return "hop_connect_failed: " + detail
        return "hop_command_failed: " + detail
    return detail


def _probe_device(creds: dict[str, Any]) -> tuple[str, str, str | None]:
    """Login via Netmiko, probe hostname, return (status, message, discovered_name)."""
    vendor = str(creds.get("vendor") or "")
    session_timeout = 180 if creds.get("hop_enabled") else None
    conn = None
    try:
        conn = open_netmiko_connection(creds, session_timeout=session_timeout)
        prompt = str(conn.find_prompt() or "")
        command = hostname_probe_command(creds["device_type"], vendor)
        output = ""
        if command:
            output = conn.send_command(command_string=command, read_timeout=30)
        hostname = parse_hostname_from_output(creds["device_type"], vendor, output, prompt)
        if hostname:
            return "pass", f"connected: {hostname}", hostname
        if command:
            return "pass", "connected (hostname not parsed)", None
        fallback = _clean_prompt_hostname(prompt)
        if fallback:
            return "pass", f"connected: {fallback}", fallback
        return "pass", "connected", None
    except Exception as exc:
        return "fail", _classify_connect_error(creds, exc), None
    finally:
        if conn is not None:
            try:
                conn.disconnect()
            except Exception:
                pass


def _update_row(ne_id: str, status: str, message: str, discovered_name: str | None = None) -> None:
    db = SessionLocal()
    try:
        row = db.get(ManagedNE, ne_id)
        if not row:
            return
        row.connect_status = status
        row.connect_message = str(message or "")[:500]
        row.connect_tested_at = datetime.utcnow()
        if discovered_name:
            row.name = discovered_name[:256]
        row.updated_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()


def _run_single(ne_id: str) -> None:
    db = SessionLocal()
    try:
        row = db.get(ManagedNE, ne_id)
        if not row:
            return
        row.connect_status = "testing"
        row.connect_message = ""
        row.updated_at = datetime.utcnow()
        db.commit()
        try:
            creds = get_device_credentials(row)
        except CredentialCryptoError as exc:
            _update_row(ne_id, "fail", str(exc))
            return
        status, message, discovered = _probe_device(creds)
        _update_row(ne_id, status, message, discovered)
    except Exception as exc:
        _log.exception("connect test failed for %s", ne_id)
        _update_row(ne_id, "fail", str(exc)[:480])
    finally:
        db.close()


def schedule_connect_tests(ne_ids: list[str]) -> int:
    pool = _executor_pool()
    submitted = 0
    for ne_id in ne_ids:
        ne_id = str(ne_id or "").strip()
        if not ne_id:
            continue
        pool.submit(_run_single, ne_id)
        submitted += 1
    return submitted
