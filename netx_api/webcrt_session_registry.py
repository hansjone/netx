"""WebCRT process-local session registry, reaper, and connect lifecycle."""
from __future__ import annotations

import io
import logging
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .config import settings
from .ne_crypto import CredentialCryptoError
from .ne_session_factory import (
    get_cli_hop_guard,
    open_netmiko_connection,
)
from .webcrt_channel import (
    _audit,
    _capture_raw_channel,
    _decode_bytes,
    _drain_channel,
    _encode_text,
    _is_prompt_only_echo,
    _looks_like_cli_prompt,
    _looks_like_login_prompt,
    _looks_like_password_change_prompt,
    _normalize_encoding,
    _prime_interactive_channel,
    _session_log_text,
    prepare_bootstrap_output,
    uses_network_cli_keymap,
)
from .webcrt_session_model import WebcrtSession

_log = logging.getLogger("netx.webcrt")

_sessions_lock = threading.Lock()
_sessions: dict[str, WebcrtSession] = {}
_reaper_started = False

def _ensure_reaper() -> None:
    global _reaper_started
    with _sessions_lock:
        if _reaper_started:
            return
        _reaper_started = True
    t = threading.Thread(target=_reaper_loop, name="webcrt-reaper", daemon=True)
    t.start()


def _reaper_loop() -> None:
    while True:
        try:
            _reap_sessions()
        except Exception:
            _log.exception("webcrt reaper failed")
        time.sleep(2)


def _reap_sessions() -> None:
    idle = max(60, int(settings.webcrt_idle_timeout_sec or 1800))
    attach = max(10, int(settings.webcrt_attach_timeout_sec or 60))
    anti_idle = max(0, int(getattr(settings, "webcrt_anti_idle_sec", 0) or 0))
    anti_payload = str(getattr(settings, "webcrt_anti_idle_payload", " ") or " ")
    now = time.time()
    to_close: list[tuple[WebcrtSession, str]] = []
    to_nudge: list[WebcrtSession] = []
    with _sessions_lock:
        for sess in list(_sessions.values()):
            if sess.closed:
                _sessions.pop(sess.session_id, None)
                continue
            if sess.state == "connecting":
                # Connecting sessions use connect timeout, not attach timeout alone.
                connect_budget = max(30, int(settings.webcrt_connect_timeout_sec or 90)) + 30
                if (now - sess.connect_started_at) > connect_budget:
                    to_close.append((sess, "connect_timeout"))
                continue
            if sess.attached:
                if (now - sess.last_activity) > idle:
                    to_close.append((sess, "idle_timeout"))
                elif (
                    anti_idle > 0
                    and sess.state == "ready"
                    and sess.conn is not None
                    and (now - sess.last_activity) >= anti_idle
                ):
                    to_nudge.append(sess)
                continue
            # Not attached: either never attached, or briefly detached for reconnect.
            if sess.detach_deadline is not None:
                if now >= sess.detach_deadline:
                    to_close.append((sess, "detach_timeout"))
            else:
                # Start attach clock after connect finishes (not HTTP create time),
                # so slow auth + UI mount does not race attach_timeout.
                anchor = float(sess.connect_finished_at or sess.created_at or now)
                if (now - anchor) > attach:
                    to_close.append((sess, "attach_timeout"))
                elif (now - sess.last_activity) > idle:
                    to_close.append((sess, "idle_timeout"))
    for sess in to_nudge:
        try:
            # Touch without changing visible prompt when payload is empty/null-ish.
            payload = anti_payload
            if payload == "\\0":
                payload = "\x00"
            if payload:
                sess.write_stdin(payload)
            else:
                sess.touch()
        except Exception:
            _log.debug("webcrt anti-idle failed session=%s", sess.session_id, exc_info=True)
    for sess, reason in to_close:
        close_session(sess.session_id, reason=reason)


def active_session_count() -> int:
    with _sessions_lock:
        return sum(1 for s in _sessions.values() if not s.closed)


def get_session(session_id: str) -> WebcrtSession | None:
    with _sessions_lock:
        sess = _sessions.get(session_id)
        if sess is None or sess.closed:
            return None
        return sess


def find_ssh_session_for_ne(ne_id: str) -> WebcrtSession | None:
    """Prefer a ready/attached interactive SSH session for SFTP channel reuse."""
    nid = str(ne_id or "").strip()
    if not nid:
        return None
    with _sessions_lock:
        candidates = [
            s
            for s in _sessions.values()
            if (not s.closed)
            and str(s.ne_id) == nid
            and str(s.protocol or "ssh").lower() == "ssh"
            and s.conn is not None
            and s.state in ("ready", "connecting")
            and not s.cli_hop_guard
        ]
    if not candidates:
        return None
    # Prefer attached + ready sessions.
    candidates.sort(key=lambda s: (0 if s.attached and s.state == "ready" else 1, -s.last_activity))
    return candidates[0]


def wait_session_ready(session_id: str, *, timeout: float = 120.0) -> WebcrtSession:
    """Block until async connect finishes (ready or error). Used by tests and WS."""
    # Honor short slice timeouts from the WS wait loop (do not clamp to 1s).
    deadline = time.time() + max(0.05, float(timeout))
    while time.time() < deadline:
        sess = get_session(session_id)
        if sess is None:
            raise HTTPException(status_code=404, detail="webcrt_session_not_found")
        if sess.state == "ready":
            return sess
        if sess.state == "error":
            raise HTTPException(status_code=502, detail=sess.connect_error or "connect_failed")
        if sess.closed or sess.state == "closed":
            raise HTTPException(status_code=404, detail="webcrt_session_not_found")
        sess._ready_event.wait(timeout=min(0.25, max(0.01, deadline - time.time())))
    raise HTTPException(status_code=504, detail="connect_timeout")


def _webcrt_creds_ready(creds: dict[str, Any]) -> bool:
    """True when WebCRT can open a session with the resolved credentials.

    Bastion-managed hops store the target password on the bastion side, so an empty
    NE password is valid (same as connectivity test). Direct / manual / Linux hops
    still require a target password for SSH.

    Telnet (no hop) allows empty username/password so the user can authenticate
    interactively in the terminal (SecureCRT-style).
    """
    hop_enabled = bool(creds.get("hop_enabled"))
    hop_vendor = str(creds.get("hop_vendor") or "").strip().lower()
    auth_mode = str(creds.get("hop_target_auth_mode") or "bastion_managed").strip().lower()
    protocol = str(creds.get("protocol") or "ssh").strip().lower()
    if hop_enabled and hop_vendor == "bastion" and auth_mode == "bastion_managed":
        return bool(
            str(creds.get("hop_host") or "").strip()
            and str(creds.get("hop_username") or "").strip()
            and str(creds.get("hop_password") or "")
        )
    if protocol == "telnet" and not hop_enabled:
        return True
    if not str(creds.get("username") or "").strip():
        return False
    return bool(str(creds.get("password") or ""))


def _finish_connect(
    sess: WebcrtSession,
    *,
    creds: dict[str, Any],
    device: dict[str, Any],
    connect_timeout: int,
    client: str,
) -> None:
    log_buf = io.BytesIO()
    try:
        conn = open_netmiko_connection(
            creds,
            session_timeout=connect_timeout,
            session_log=log_buf,
            cols=sess.cols,
            rows=sess.rows,
            interactive=True,
            keepalive=int(sess.keepalive_sec or 0),
        )
    except Exception as exc:
        partial = _session_log_text(log_buf).strip()
        from .ne_cli_errors import format_cli_failure

        classified = format_cli_failure(exc, partial)
        detail = f"connect_failed:{classified}"
        if partial:
            detail = f"{detail}\n--- device transcript ---\n{partial[-4000:]}"
        sess.state = "error"
        sess.connect_error = detail
        sess.connect_finished_at = time.time()
        sess._ready_event.set()
        _audit(
            "session_open_failed",
            session_id=sess.session_id,
            ne_id=sess.ne_id,
            ne_ip=sess.ne_ip,
            source=str(device.get("source") or ""),
            client=client or "",
            error=str(exc)[:500],
            transcript_len=len(partial),
        )
        return

    channel = getattr(conn, "remote_conn", None)
    if channel is not None and hasattr(channel, "resize_pty"):
        try:
            channel.resize_pty(width=sess.cols, height=sess.rows)
        except Exception:
            pass

    pre_log = _session_log_text(log_buf)
    # Pull post-auth banner/MOTD from the PTY. With interactive no-op session_preparation
    # (generic_termserver), Netmiko session_log is often empty — do not discard these bytes.
    try:
        early = _capture_raw_channel(conn, duration=0.35)
    except Exception:
        early = ""
    seed = f"{pre_log}{early}"
    already_prompted = _looks_like_cli_prompt(seed)
    primed = ""
    # Do not send Enter at Username:/Password: or Huawei password-change [Y/N]:
    # (Netmiko telnet_login already answers password-change with "N").
    if _looks_like_login_prompt(seed) or _looks_like_password_change_prompt(seed):
        try:
            primed = _capture_raw_channel(conn, duration=0.9)
        except Exception:
            primed = ""
    else:
        try:
            primed = _prime_interactive_channel(conn, already_prompted=already_prompted)
        except Exception:
            primed = ""
    combined = f"{seed}{primed}"
    # Final settle: keep stragglers in bootstrap (normalize collapses duplicate prompts).
    try:
        combined += _capture_raw_channel(conn, duration=0.35)
    except Exception:
        pass
    if not str(combined).strip():
        try:
            combined = _drain_channel(conn, rounds=6, wait=0.08)
        except Exception:
            combined = ""
    bootstrap = prepare_bootstrap_output(combined)
    # Discard lone punctuation left on the wire (would glue onto ``<r1>`` in xterm).
    try:
        leftover = _capture_raw_channel(conn, duration=0.12)
    except Exception:
        leftover = ""
    if leftover and leftover.strip() not in {":", ">", "#", "]", "$"}:
        bootstrap = prepare_bootstrap_output(f"{bootstrap}{leftover}")

    hop_guard = get_cli_hop_guard(conn)
    sess.conn = conn
    sess.cli_hop_guard = bool(hop_guard)
    sess.cli_hop_prompt = str((hop_guard or {}).get("hop_prompt") or "")
    sess.bootstrap_output = _encode_text(str(bootstrap or ""), sess.encoding)
    # Nudge Enter on WS attach only when we still need a shell prompt.
    # Never when already at CLI prompt or Username:/Password: (would empty-submit login).
    sess.needs_live_prompt = (
        not _looks_like_cli_prompt(bootstrap) and not _looks_like_login_prompt(bootstrap)
    )
    sess.open_session_log()
    if bootstrap:
        sess.append_session_log(bootstrap if bootstrap.endswith("\n") else bootstrap + "\n")
    sess.start_reader()
    # Drop late prompt echoes that race into the queue right after reader start.
    prompt_hint = ""
    if bootstrap:
        prompt_hint = str(bootstrap).replace("\r\n", "\n").replace("\r", "\n").strip().split("\n")[-1].strip()
    settle_deadline = time.time() + 0.45
    while time.time() < settle_deadline:
        try:
            chunk = sess.out_queue.get_nowait()
        except queue.Empty:
            time.sleep(0.02)
            continue
        if chunk is None:
            sess.out_queue.put(None)
            break
        try:
            text = _decode_bytes(chunk, sess.encoding)
        except Exception:
            text = ""
        if _is_prompt_only_echo(text, prompt_hint):
            continue
        # Non-prompt data: put back and stop settling.
        sess.out_queue.put(chunk)
        break
    try:
        sess.run_post_login_commands()
    except Exception:
        _log.debug("post_login failed session=%s", sess.session_id, exc_info=True)
    # Same SSH transport: open SFTP channel when the device supports it.
    sftp_ok = sess.try_attach_sftp()
    sess.state = "ready"
    sess.connect_finished_at = time.time()
    sess._ready_event.set()
    elapsed_ms = int((sess.connect_finished_at - sess.connect_started_at) * 1000)
    _audit(
        "session_created",
        session_id=sess.session_id,
        ne_id=sess.ne_id,
        ne_name=sess.ne_name,
        ne_ip=sess.ne_ip,
        protocol=sess.protocol,
        encoding=sess.encoding,
        source=str(device.get("source") or ""),
        hop_enabled=bool(creds.get("hop_enabled")),
        hop_vendor=str(creds.get("hop_vendor") or "") if creds.get("hop_enabled") else "",
        cli_hop_guard=bool(hop_guard),
        cli_hop_prompt=str((hop_guard or {}).get("hop_prompt") or ""),
        sftp_ready=bool(sftp_ok),
        client=client or "",
        connect_ms=elapsed_ms,
        active=active_session_count(),
    )


def create_session(
    db: Session,
    *,
    ne_id: str | None = None,
    ume_ne_id: str | None = None,
    cols: int = 80,
    rows: int = 24,
    client: str = "",
    encoding: str = "utf-8",
    keepalive_sec: int | None = None,
    post_login_commands: list[str] | None = None,
    async_connect: bool = True,
    username_override: str | None = None,
    password_override: str | None = None,
) -> dict[str, Any]:
    from .cli_resolve import resolve_cli_target

    _ensure_reaper()
    max_sessions = max(1, int(settings.webcrt_max_sessions or 20))
    if active_session_count() >= max_sessions:
        raise HTTPException(status_code=429, detail="webcrt_session_limit")

    mid = str(ne_id or "").strip()
    uid = str(ume_ne_id or "").strip()
    try:
        creds, device = resolve_cli_target(db, managed_ne_id=mid or None, ume_ne_id=uid or None)
    except HTTPException:
        raise
    except CredentialCryptoError as exc:
        raise HTTPException(status_code=400, detail=str(exc) or "credential_crypto_error") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"credential_error:{exc}") from exc

    # One-shot credentials for SecureCRT-style "do not save password" / retry.
    if username_override is not None and str(username_override).strip():
        creds["username"] = str(username_override).strip()
    if password_override is not None:
        creds["password"] = str(password_override)

    protocol = str(device.get("protocol") or creds.get("protocol") or "ssh").strip().lower()
    creds["protocol"] = protocol
    # Netmiko telnet drivers dislike a completely missing username; use a placeholder
    # for the wire only (interactive login still happens in the terminal).
    if protocol == "telnet" and not bool(creds.get("hop_enabled")) and not str(creds.get("username") or "").strip():
        creds["username"] = "telnet"

    if not _webcrt_creds_ready(creds):
        raise HTTPException(status_code=400, detail="credentials_incomplete")

    session_id = str(uuid.uuid4())
    c = max(20, min(500, int(cols or 80)))
    r = max(5, min(200, int(rows or 24)))
    connect_timeout = max(30, int(settings.webcrt_connect_timeout_sec or 90))
    target_id = str(device.get("id") or mid or uid)
    target_ip = str(device.get("ip_address") or "")
    target_name = str(device.get("name") or target_ip)
    device_type = str(device.get("device_type") or creds.get("device_type") or "")
    vendor = str(device.get("vendor") or creds.get("vendor") or "")
    cli_keymap = uses_network_cli_keymap(device_type, vendor)
    enc = _normalize_encoding(encoding)
    if keepalive_sec is None:
        ka = max(0, int(getattr(settings, "webcrt_keepalive_sec", 0) or 0))
    else:
        ka = max(0, min(600, int(keepalive_sec)))

    sess = WebcrtSession(
        session_id=session_id,
        ne_id=target_id,
        ne_name=target_name,
        ne_ip=target_ip,
        protocol=protocol,
        cols=c,
        rows=r,
        device_type=device_type,
        vendor=vendor,
        cli_keymap=cli_keymap,
        encoding=enc,
        keepalive_sec=ka,
        state="connecting",
        post_login_commands=list(post_login_commands or [])[:20],
    )
    with _sessions_lock:
        _sessions[session_id] = sess

    _audit(
        "session_connecting",
        session_id=session_id,
        ne_id=sess.ne_id,
        ne_ip=sess.ne_ip,
        protocol=sess.protocol,
        encoding=enc,
        client=client or "",
        async_connect=bool(async_connect),
    )

    if async_connect:
        t = threading.Thread(
            target=_finish_connect,
            kwargs={
                "sess": sess,
                "creds": creds,
                "device": device,
                "connect_timeout": connect_timeout,
                "client": client or "",
            },
            name=f"webcrt-connect-{session_id[:8]}",
            daemon=True,
        )
        t.start()
    else:
        _finish_connect(
            sess,
            creds=creds,
            device=device,
            connect_timeout=connect_timeout,
            client=client or "",
        )
        if sess.state == "error":
            with _sessions_lock:
                _sessions.pop(session_id, None)
            raise HTTPException(status_code=502, detail=sess.connect_error or "connect_failed")

    return {
        "session_id": session_id,
        "ne_id": sess.ne_id,
        "ne_name": sess.ne_name,
        "ne_ip": sess.ne_ip,
        "source": str(device.get("source") or ""),
        "protocol": sess.protocol,
        "cols": sess.cols,
        "rows": sess.rows,
        "encoding": enc,
        "keepalive_sec": ka,
        "state": sess.state,
        "ws_path": f"/v1/webcrt/sessions/{session_id}/ws",
        "cli_hop": bool(sess.cli_hop_guard),
        "sftp_ready": bool(sess.sftp_ready),
    }


def mark_attached(session_id: str) -> tuple[WebcrtSession, int]:
    sess = get_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="webcrt_session_not_found")
    # Allow re-attach after brief WS drop (React StrictMode remount / network blip).
    # Bump generation so the previous WS pump stops and does not steal echo bytes.
    sess.attach_gen += 1
    attach_gen = sess.attach_gen
    sess.attached = True
    sess.detach_deadline = None
    sess.touch()
    _audit(
        "session_attached",
        session_id=session_id,
        ne_id=sess.ne_id,
        ne_ip=sess.ne_ip,
        attach_gen=attach_gen,
        state=sess.state,
    )
    return sess, attach_gen


def detach_session(
    session_id: str,
    *,
    grace_sec: float = 8.0,
    client: str = "",
    attach_gen: int | None = None,
) -> dict[str, Any]:
    """Mark session unattached but keep device channel open briefly for reconnect."""
    sess = get_session(session_id)
    if sess is None:
        return {"ok": True, "session_id": session_id, "detached": False}
    if sess.closed:
        return {"ok": True, "session_id": session_id, "detached": False}
    # Ignore detach from an older StrictMode WS once a newer attach owns the session.
    if attach_gen is not None and attach_gen != sess.attach_gen:
        return {
            "ok": True,
            "session_id": session_id,
            "detached": False,
            "ignored_stale_attach": True,
        }
    sess.attached = False
    sess.detach_deadline = time.time() + max(1.0, float(grace_sec))
    sess.touch()
    _audit(
        "session_detached",
        session_id=session_id,
        ne_id=sess.ne_id,
        ne_ip=sess.ne_ip,
        grace_sec=grace_sec,
        client=client or "",
        attach_gen=attach_gen,
    )
    return {"ok": True, "session_id": session_id, "detached": True}


def close_session(session_id: str, *, reason: str = "closed", client: str = "") -> dict[str, Any]:
    with _sessions_lock:
        sess = _sessions.pop(session_id, None)
    if sess is None:
        return {"ok": True, "session_id": session_id, "closed": False}
    if not sess.closed:
        sess.close(reason)
        _audit(
            "session_closed",
            session_id=session_id,
            ne_id=sess.ne_id,
            ne_ip=sess.ne_ip,
            reason=reason,
            client=client or "",
            bytes_in=sess.bytes_in,
            bytes_out=sess.bytes_out,
            queue_dropped=getattr(sess.out_queue, "dropped", 0),
            active=active_session_count(),
        )
    return {"ok": True, "session_id": session_id, "closed": True, "reason": reason}


def close_all_sessions(*, reason: str = "shutdown") -> int:
    """Close every active WebCRT session (API shutdown). Returns count closed."""
    with _sessions_lock:
        ids = [sid for sid, s in _sessions.items() if not s.closed]
    closed = 0
    for sid in ids:
        try:
            out = close_session(sid, reason=reason, client="shutdown")
            if out.get("closed"):
                closed += 1
        except Exception:  # noqa: BLE001
            _log.exception("webcrt close_all failed session=%s", sid)
    return closed


def list_sessions() -> dict[str, Any]:
    with _sessions_lock:
        items = []
        for s in _sessions.values():
            if s.closed:
                continue
            state = str(s.state or "unknown")
            attached = bool(s.attached)
            # Lifecycle for ops UI: distinguish login vs live vs grace-period detach.
            if state == "connecting":
                lifecycle = "connecting"
            elif state == "error":
                lifecycle = "error"
            elif state == "ready" and attached:
                lifecycle = "ready"
            elif state == "ready" and not attached:
                lifecycle = "detached"
            else:
                lifecycle = state
            elapsed_ms = None
            if state == "connecting":
                elapsed_ms = int(max(0.0, time.time() - float(s.connect_started_at or time.time())) * 1000)
            items.append(
                {
                    "session_id": s.session_id,
                    "ne_id": s.ne_id,
                    "ne_name": s.ne_name,
                    "ne_ip": s.ne_ip,
                    "protocol": s.protocol,
                    "encoding": s.encoding,
                    "keepalive_sec": int(s.keepalive_sec or 0),
                    "state": state,
                    "lifecycle": lifecycle,
                    "attached": attached,
                    "detach_deadline": s.detach_deadline,
                    "connect_error": str(s.connect_error or "")[:500],
                    "elapsed_ms": elapsed_ms,
                    "created_at": datetime.fromtimestamp(s.created_at, tz=timezone.utc).isoformat(),
                    "last_activity": datetime.fromtimestamp(s.last_activity, tz=timezone.utc).isoformat(),
                    "bytes_in": s.bytes_in,
                    "bytes_out": s.bytes_out,
                    "queue_depth": s.out_queue.qsize(),
                    "queue_dropped": getattr(s.out_queue, "dropped", 0),
                    "connect_ms": (
                        int((s.connect_finished_at - s.connect_started_at) * 1000)
                        if s.connect_finished_at
                        else None
                    ),
                }
            )
    return {
        "total": len(items),
        "max_sessions": max(1, int(settings.webcrt_max_sessions or 20)),
        "idle_timeout_sec": max(60, int(settings.webcrt_idle_timeout_sec or 1800)),
        "keepalive_sec": int(getattr(settings, "webcrt_keepalive_sec", 0) or 0),
        "anti_idle_sec": int(getattr(settings, "webcrt_anti_idle_sec", 0) or 0),
        "items": items,
    }
