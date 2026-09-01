from __future__ import annotations

import logging
import re
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from .config import settings
from .db import SessionLocal
from .models import ManagedNE, UmeCliOverride, UmeInventoryNE
from .ne_crypto import CredentialCryptoError
from .cli_creds import cli_creds_skip_reason
from .cli_resolve import resolve_cli_target
from .ne_service import get_device_credentials
from .ne_netmiko import send_show_command
from .ne_session_factory import (
    bastion_ssh_cli,
    close_netmiko_connection,
    open_netmiko_connection,
    render_hop_command,
    resolve_bastion_ssh_username,
)

_log = logging.getLogger("netx.ne.connect")
_executor: ThreadPoolExecutor | None = None
_DETAIL_MAX = 8000


def _executor_pool() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        from .cli_budget import clamp_cli_workers

        workers = clamp_cli_workers(int(settings.ne_connect_max_workers or 8))
        _executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ne-connect")
    return _executor


def shutdown_ne_connect_executor(*, wait: bool = False) -> None:
    global _executor
    if _executor is not None:
        try:
            _executor.shutdown(wait=wait, cancel_futures=True)
        except TypeError:
            _executor.shutdown(wait=wait)
        _executor = None


def _truncate_detail(text: str) -> str:
    return str(text or "")[:_DETAIL_MAX]


def _connect_context_lines(creds: dict[str, Any]) -> list[str]:
    lines = [
        f"target={creds.get('ip_address')}:{creds.get('port')}/{creds.get('protocol')}",
        f"device_type={creds.get('device_type')} vendor={creds.get('vendor')}",
        f"username={creds.get('username')}",
    ]
    if creds.get("hop_enabled"):
        lines.append(
            "hop="
            f"enabled vendor={creds.get('hop_vendor')} "
            f"host={creds.get('hop_host')}:{creds.get('hop_port')}/{creds.get('hop_protocol')} "
            f"user={creds.get('hop_username')}"
        )
        tpl = str(creds.get("hop_command_template") or "").strip()
        if tpl:
            lines.append(f"hop_command_template={tpl}")
        auth_mode = str(creds.get("hop_target_auth_mode") or "").strip()
        if auth_mode:
            lines.append(f"hop_target_auth_mode={auth_mode}")
        if str(creds.get("hop_vendor") or "").strip().lower() == "bastion":
            try:
                hop_host = str(creds.get("hop_host") or "").strip()
                rendered = render_hop_command(str(creds.get("hop_command_template") or ""), creds)
                ssh_user = resolve_bastion_ssh_username(rendered, hop_host)
                lines.append(f"bastion_ssh_username={ssh_user}")
                lines.append(
                    f"bastion_ssh_cli={bastion_ssh_cli(ssh_user, hop_host, int(creds.get('hop_port') or 22))}"
                )
            except Exception:
                pass
        vrf = str(creds.get("hop_vrf") or "").strip()
        if vrf:
            lines.append(f"hop_vrf={vrf}")
    else:
        lines.append("hop=disabled (direct)")
    return lines


def hostname_probe_command(device_type: str, vendor: str) -> str | None:
    """
    Per-vendor CLI to read system name (ported from legacy connect.extract_dev_command).
    ZTE: rely on login prompt when no dedicated command.
    Cisco: show configuration filter; Huawei: current-configuration sysname.
    """
    dt = str(device_type or "").lower()
    v = str(vendor or "").lower()
    if "huawei" in dt or v == "huawei":
        return "display current-configuration | include sysname"
    if "juniper" in dt or v == "juniper":
        return "show system host-name"
    if "cisco" in dt or v == "cisco":
        return "show configuration | include hostname"
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
        m = re.search(r"hostname\s+(\S+)", text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        for ln in reversed(lines):
            if ln.startswith("%") or "invalid" in ln.lower():
                continue
            token = ln.split()[0].strip("<>[]")
            if token and token.lower() != "hostname":
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
    full = str(exc).strip()
    detail = full.split("\n")[0][:480] if full else type(exc).__name__
    if creds.get("hop_enabled"):
        hop_v = str(creds.get("hop_vendor") or "zte").lower()
        if "hop_credentials_incomplete" in raw or "hop_command_template_invalid" in raw:
            return detail
        if "target_auth_timeout" in raw:
            return "target_auth_failed: " + detail
        if hop_v == "bastion" and (
            "bastion_vault_auth_failed" in raw
            or "bad authentication type" in raw
            or "keyboard-interactive" in raw
            or "vault" in raw
        ):
            return "bastion_auth_failed: " + detail
        if "authentication" in raw or "auth" in raw:
            if hop_v == "bastion":
                return "bastion_auth_failed: " + detail
            if "hop_host" in raw or str(creds.get("hop_host") or "") in raw:
                return "hop_auth_failed: " + detail
            return "target_auth_failed: " + detail
        if "timed out" in raw or "timeout" in raw or "hop_connect_failed" in raw:
            return "hop_connect_failed: " + detail
        if "no existing session" in raw:
            return (
                "hop_connect_failed: SSH handshake dropped (often hop VTY/session limit "
                "or concurrent WebCRT). Close spare terminals and retry. " + detail
            )
        if hop_v in ("linux", "bastion"):
            if "vault" in raw or "bastion" in raw:
                return "bastion_auth_failed: " + detail
            return "hop_connect_failed: " + detail
        return "hop_command_failed: " + detail
    if "readtimeout" in raw.replace(" ", "") or "pattern not detected" in raw:
        return "probe_command_timeout: " + detail
    return detail


def _exc_headline(exc: BaseException) -> str:
    """First line of the exception only (Netmiko often appends multi-line advice)."""
    text = str(exc).strip() or type(exc).__name__
    first = text.splitlines()[0].strip()
    return f"{type(exc).__name__}: {first}"[:480]


def _root_cause_headline(exc: BaseException) -> str | None:
    """Underlying OS/socket error when Netmiko wraps TimeoutError / etc."""
    cause = exc.__cause__ or exc.__context__
    if cause is None or cause is exc:
        return None
    # Prefer the deepest non-trivial cause one level down (TimeoutError under NetmikoTimeout).
    headline = _exc_headline(cause)
    outer = _exc_headline(exc)
    if headline == outer:
        return None
    return headline


def _is_network_reachability_fail(exc: BaseException) -> bool:
    """TCP timeout / refused / unreachable — stack traces add noise, not diagnosis."""
    chunks = [str(exc)]
    if exc.__cause__ is not None:
        chunks.append(str(exc.__cause__))
    if exc.__context__ is not None:
        chunks.append(str(exc.__context__))
    raw = " ".join(chunks).lower()
    name = type(exc).__name__.lower()
    if "timeout" in name or "timeout" in raw:
        return True
    if "connection refused" in raw or "10060" in raw or "10061" in raw:
        return True
    if "no route" in raw or "network is unreachable" in raw or "name or service not known" in raw:
        return True
    return False


def _compact_traceback(*, max_frames: int = 8) -> str:
    """File/line frames only; drop exception body (already shown on error=)."""
    tb = traceback.format_exc().strip()
    if not tb:
        return ""
    frames: list[str] = []
    for line in tb.splitlines():
        s = line.rstrip()
        if s.startswith("Traceback ") or s.startswith("During handling"):
            continue
        # Exception summary / advice paragraphs (no leading indent, not a frame header).
        if frames and s and not s.startswith(" ") and not s.startswith("File "):
            break
        if s.startswith("  File ") or (frames and s.startswith("    ")):
            frames.append(s)
            continue
        if s.startswith("File "):
            frames.append(s)
    if not frames:
        return ""
    # Keep the deepest frames (closest to failure).
    if len(frames) > max_frames * 2:
        # each frame is typically 2 lines (File + code)
        frames = frames[-(max_frames * 2) :]
    return "\n".join(frames)


def _format_failure_detail(creds: dict[str, Any], exc: BaseException) -> str:
    lines = _connect_context_lines(creds)
    lines.append("result=fail")
    lines.append(f"error={_exc_headline(exc)}")
    root = _root_cause_headline(exc)
    if root:
        lines.append(f"cause={root}")
    # Reachability failures: context + one-liners are enough.
    # Auth/CLI/hop surprises still get a short stack for support.
    if not _is_network_reachability_fail(exc):
        stack = _compact_traceback()
        if stack:
            lines.append("")
            lines.append("stack:")
            lines.append(stack)
    return _truncate_detail("\n".join(lines))


_PROBE_READ_TIMEOUT = 60


def _format_success_detail(
    creds: dict[str, Any],
    *,
    prompt: str,
    command: str | None,
    output: str,
    hostname: str | None,
    summary: str,
) -> str:
    lines = _connect_context_lines(creds)
    lines.append(f"result=pass summary={summary}")
    if prompt:
        lines.append(f"prompt={prompt}")
    if command:
        lines.append(f"probe_command={command}")
    if output:
        lines.append("probe_output:")
        lines.append(output[:3000])
    if hostname:
        lines.append(f"parsed_hostname={hostname}")
    return _truncate_detail("\n".join(lines))


def _probe_device(creds: dict[str, Any]) -> tuple[str, str, str | None, str]:
    """Login via Netmiko, probe hostname; return (status, message, discovered_name, detail)."""
    vendor = str(creds.get("vendor") or "")
    session_timeout = 180 if creds.get("hop_enabled") else None
    conn = None
    try:
        # CLI hop (Huawei/ZTE/Cisco): use interactive driver so Change-now / prompt
        # waits match WebCRT (stock Netmiko ``[\]>]`` matches ``[Y/N]`` and breaks hop login).
        hop_v = str(creds.get("hop_vendor") or "").strip().lower()
        use_interactive = bool(creds.get("hop_enabled")) and hop_v not in ("linux", "bastion", "")
        conn = open_netmiko_connection(
            creds,
            session_timeout=session_timeout,
            interactive=use_interactive,
        )
        prompt = str(conn.find_prompt() or "")
        command = hostname_probe_command(creds["device_type"], vendor)
        output = ""
        if command:
            output = send_show_command(conn, command, read_timeout=_PROBE_READ_TIMEOUT)
        hostname = parse_hostname_from_output(creds["device_type"], vendor, output, prompt)
        if hostname:
            msg = f"connected: {hostname}"
            return (
                "pass",
                msg,
                hostname,
                _format_success_detail(
                    creds, prompt=prompt, command=command, output=output, hostname=hostname, summary=msg
                ),
            )
        if command:
            msg = "connected (hostname not parsed)"
            return (
                "pass",
                msg,
                None,
                _format_success_detail(creds, prompt=prompt, command=command, output=output, hostname=None, summary=msg),
            )
        fallback = _clean_prompt_hostname(prompt)
        if fallback:
            msg = f"connected: {fallback}"
            return (
                "pass",
                msg,
                fallback,
                _format_success_detail(
                    creds, prompt=prompt, command=command, output=output, hostname=fallback, summary=msg
                ),
            )
        msg = "connected"
        return (
            "pass",
            msg,
            None,
            _format_success_detail(creds, prompt=prompt, command=command, output=output, hostname=None, summary=msg),
        )
    except Exception as exc:
        _log.exception(
            "connect probe failed target=%s hop=%s",
            creds.get("ip_address"),
            creds.get("hop_enabled"),
        )
        msg = _classify_connect_error(creds, exc)
        return "fail", msg, None, _format_failure_detail(creds, exc)
    finally:
        close_netmiko_connection(conn)


def _update_row(
    ne_id: str,
    status: str,
    message: str,
    discovered_name: str | None = None,
    *,
    detail: str = "",
) -> None:
    db = SessionLocal()
    try:
        row = db.get(ManagedNE, ne_id)
        if not row:
            return
        row.connect_status = status
        row.connect_message = str(message or "")[:500]
        row.connect_detail = _truncate_detail(detail)
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
        row.connect_detail = ""
        row.updated_at = datetime.utcnow()
        db.commit()
        try:
            creds = get_device_credentials(row)
        except CredentialCryptoError as exc:
            ctx = {
                "ip_address": row.ip_address,
                "port": row.port,
                "protocol": row.protocol,
                "device_type": row.device_type,
                "vendor": row.vendor,
                "username": row.username,
                "hop_enabled": bool(row.hop_enabled),
                "hop_vendor": row.hop_vendor,
                "hop_host": row.hop_host,
                "hop_port": row.hop_port,
                "hop_protocol": row.hop_protocol,
                "hop_username": row.hop_username,
                "hop_command_template": row.hop_command_template,
                "hop_vrf": row.hop_vrf,
            }
            detail = _truncate_detail(
                "\n".join(_connect_context_lines(ctx)) + f"\nresult=fail\nerror=CredentialCryptoError: {exc}"
            )
            _update_row(ne_id, "fail", str(exc), detail=detail)
            return
        skip = cli_creds_skip_reason(creds, interactive=False)
        if skip:
            detail = _truncate_detail(
                "\n".join(_connect_context_lines(creds)) + f"\nresult=fail\nerror={skip}"
            )
            _update_row(ne_id, "fail", skip, detail=detail)
            return
        status, message, discovered, detail = _probe_device(creds)
        _update_row(ne_id, status, message, discovered, detail=detail)
    except Exception as exc:
        _log.exception("connect test failed for %s", ne_id)
        detail = _format_failure_detail(
            {
                "ip_address": "?",
                "port": "?",
                "protocol": "?",
                "device_type": "?",
                "vendor": "?",
                "username": "?",
                "hop_enabled": False,
            },
            exc,
        )
        # Prefer short message; full multi-line Netmiko advice is not useful in the pill.
        _update_row(ne_id, "fail", _exc_headline(exc)[:480], detail=detail)
    finally:
        db.close()


def _update_ume_override_row(
    ume_ne_id: str,
    status: str,
    message: str,
    discovered_name: str | None = None,
    *,
    detail: str = "",
) -> None:
    db = SessionLocal()
    try:
        uid = str(ume_ne_id or "").strip()
        row = db.get(UmeCliOverride, uid)
        if row is None:
            if not db.get(UmeInventoryNE, uid):
                return
            row = UmeCliOverride(ume_ne_id=uid)
            db.add(row)
        row.connect_status = status
        row.connect_message = str(message or "")[:500]
        row.connect_detail = _truncate_detail(detail)
        row.connect_tested_at = datetime.utcnow()
        row.updated_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()


def _run_single_ume(ume_ne_id: str) -> None:
    db = SessionLocal()
    try:
        uid = str(ume_ne_id or "").strip()
        if not db.get(UmeInventoryNE, uid):
            return
        row = db.get(UmeCliOverride, uid)
        if row is None:
            row = UmeCliOverride(ume_ne_id=uid)
            db.add(row)
        row.connect_status = "testing"
        row.connect_message = ""
        row.connect_detail = ""
        row.updated_at = datetime.utcnow()
        db.commit()
        try:
            creds, _device = resolve_cli_target(db, ume_ne_id=uid)
        except Exception as exc:
            detail = _format_failure_detail(
                {
                    "ip_address": "?",
                    "port": "?",
                    "protocol": "?",
                    "device_type": "?",
                    "vendor": "?",
                    "username": "?",
                    "hop_enabled": False,
                },
                exc,
            )
            _update_ume_override_row(uid, "fail", _exc_headline(exc)[:480], detail=detail)
            return
        skip = cli_creds_skip_reason(creds, interactive=False)
        if skip:
            detail = _truncate_detail(
                "\n".join(_connect_context_lines(creds)) + f"\nresult=fail\nerror={skip}"
            )
            _update_ume_override_row(uid, "fail", skip, detail=detail)
            return
        status, message, discovered, detail = _probe_device(creds)
        _update_ume_override_row(uid, status, message, discovered, detail=detail)
    except Exception as exc:
        _log.exception("ume connect test failed for %s", ume_ne_id)
        detail = _format_failure_detail(
            {
                "ip_address": "?",
                "port": "?",
                "protocol": "?",
                "device_type": "?",
                "vendor": "?",
                "username": "?",
                "hop_enabled": False,
            },
            exc,
        )
        _update_ume_override_row(ume_ne_id, "fail", _exc_headline(exc)[:480], detail=detail)
    finally:
        db.close()


def schedule_ume_connect_tests(ume_ne_ids: list[str]) -> int:
    pool = _executor_pool()
    submitted = 0
    for ume_ne_id in ume_ne_ids:
        uid = str(ume_ne_id or "").strip()
        if not uid:
            continue
        pool.submit(_run_single_ume, uid)
        submitted += 1
    return submitted


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
