"""WebCRT channel helpers: keymap, prompt heuristics, encoding, queues."""
from __future__ import annotations

import io
import json
import logging
import queue
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from netmiko import ConnectHandler

from .config import settings

_log = logging.getLogger("netx.webcrt")

_NETWORK_CLI_KEY_SEQS: tuple[tuple[str, str], ...] = (
    ("\x1b[1~", "\x01"),  # Home -> Ctrl-A
    ("\x1b[3~", "\x04"),  # Delete key -> Ctrl-D
    ("\x1b[4~", "\x05"),  # End -> Ctrl-E
    ("\x1b[H", "\x01"),
    ("\x1b[F", "\x05"),
    ("\x1bOH", "\x01"),
    ("\x1bOF", "\x05"),
    ("\x1bOA", "\x1b[A"),  # App Up -> CSI Up
    ("\x1bOB", "\x1b[B"),
    ("\x1bOC", "\x1b[C"),
    ("\x1bOD", "\x1b[D"),  # App Left -> CSI Left
    ("\x7f", "\x08"),  # DEL -> BS
)


def uses_network_cli_keymap(device_type: str = "", vendor: str = "") -> bool:
    blob = f"{device_type} {vendor}".strip().lower()
    if not blob:
        return True
    for token in ("linux", "ubuntu", "centos", "debian", "redhat", "unix", "generic_telnet", "generic"):
        if token in blob:
            return False
    return True


def map_network_cli_keys(
    data: str,
    *,
    device_type: str = "",
    vendor: str = "",
    protocol: str = "",
) -> str:
    """Rewrite xterm key sequences for network-device CLIs."""
    del device_type, vendor, protocol  # protocol kept for call-site compatibility
    text = str(data or "")
    if not text:
        return text
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        matched = False
        for seq, repl in _NETWORK_CLI_KEY_SEQS:
            if text.startswith(seq, i):
                out.append(repl)
                i += len(seq)
                matched = True
                break
        if not matched:
            out.append(text[i])
            i += 1
    return "".join(out)


def channel_return(conn: ConnectHandler | None) -> str:
    """Netmiko line ending for this session (SSH usually \\n, Telnet often \\r\\n)."""
    if conn is None:
        return "\n"
    ret = getattr(conn, "RETURN", None)
    if isinstance(ret, str) and ret:
        return ret
    return "\n"


def map_network_cli_enter(data: str, conn: ConnectHandler | None) -> str:
    """Map xterm Enter (\\r) to the device's Netmiko RETURN."""
    text = str(data or "")
    if not text:
        return text
    ret = channel_return(conn)
    if ret == "\r":
        return text
    # Prefer replacing CRLF first so Telnet RETURN \\r\\n does not double-expand.
    return text.replace("\r\n", ret).replace("\r", ret)


def _drain_channel(conn: ConnectHandler, *, rounds: int = 6, wait: float = 0.06) -> str:
    """Read whatever is already sitting on the channel after login."""
    chunks: list[str] = []
    empty_streak = 0
    for _ in range(max(1, rounds)):
        time.sleep(wait)
        try:
            part = conn.read_channel()
        except Exception:
            break
        if part:
            chunks.append(str(part))
            empty_streak = 0
        else:
            empty_streak += 1
            if empty_streak >= 2 and chunks:
                break
    return "".join(chunks)


def _session_log_text(buf: io.BytesIO | None) -> str:
    """Decode Netmiko session_log buffer into display text."""
    if buf is None:
        return ""
    try:
        raw = buf.getvalue()
    except Exception:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw or "")


def _cli_prompt_candidate_lines(text: str) -> list[str]:
    """Non-empty transcript lines, stripping ANSI and ignoring trailing ``[netx]`` markers."""
    s = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", s)
    lines = [ln.strip() for ln in s.split("\n") if ln.strip()]
    while lines and lines[-1].startswith("[netx]"):
        lines.pop()
    return lines


def _line_looks_like_cli_prompt(line: str) -> bool:
    last = str(line or "").strip()
    if not last:
        return False
    # Buffer races: ``<r1>:`` (stray from [Y/N]:) or ``<r1>N`` (password-change answer glued).
    if last.endswith(":") and (">" in last or "]" in last):
        last = last[:-1].rstrip()
    if len(last) >= 2 and last[-1] in "NYny" and (last[-2] in ">]" or last.endswith(">")):
        # ``<r1>N`` / ``[HW]Y`` after Change-now answer — treat as prompted.
        last = last[:-1].rstrip()
    return bool(re.search(r"(?:[>\]]|#)\s*$", last)) or bool(re.search(r"<[^>\r\n]+>\s*$", last))


def _looks_like_cli_prompt(text: str) -> bool:
    lines = _cli_prompt_candidate_lines(text)
    if not lines:
        return False
    return _line_looks_like_cli_prompt(lines[-1])


def _looks_like_login_prompt(text: str) -> bool:
    """True when the transcript ends at Username:/Login:/Password: (interactive auth)."""
    lines = _cli_prompt_candidate_lines(text)
    if not lines:
        return False
    last = lines[-1]
    return bool(re.search(r"(?i)(user\s*name|login|password)\s*:\s*$", last))


_PASSWORD_CHANGE_LINE_RE = re.compile(
    r"(?i)(change\s*now|please\s*choose|password\s+needs\s+to\s+be\s+changed).{0,80}:\s*$"
)


def _looks_like_password_change_prompt(text: str) -> bool:
    """Huawei/VRP post-auth ``Change now? [Y/N]:`` (Netmiko already answers N).

    Do not match bare ``[Y/N]:`` — stelnet host-key trust prompts share that suffix
    and are answered in ``_interactive_target_auth``, not by skipping Enter here.
    """
    lines = _cli_prompt_candidate_lines(text)
    if not lines:
        return False
    return bool(_PASSWORD_CHANGE_LINE_RE.search(lines[-1]))


def _password_change_still_pending(text: str) -> bool:
    """True only when Change-now is still awaiting an answer.

    Netmiko ``HuaweiTelnet.telnet_login`` often already sent ``N`` before WebCRT
    ``_finish_connect`` runs. Re-sending ``N`` lands as a command on ``<r1>``.
    Treat a lone ``N``/``Y`` echo (or a later CLI prompt) as already answered.
    """
    lines = _cli_prompt_candidate_lines(text)
    if not lines:
        return False
    last_change = -1
    for i, ln in enumerate(lines):
        if _PASSWORD_CHANGE_LINE_RE.search(ln):
            last_change = i
    if last_change < 0:
        return False
    after = lines[last_change + 1 :]
    if not after:
        # Still sitting on Change now? [Y/N]:
        return True
    if any(_line_looks_like_cli_prompt(ln) for ln in after):
        return False
    # Device already echoed N/Y from Netmiko (or a prior answer) — do not send again.
    if any(ln.strip().upper() in {"N", "Y"} for ln in after):
        return False
    # Banner / last-login text after Change-now without a prompt yet: Netmiko may still
    # be draining; do not inject a second N (would race onto the prompt).
    return False


# Cisco/Netmiko often yields "R2#R2#" when a sync Enter is appended without a newline.
_GLUED_PROMPT_RE = re.compile(r"(?<=[#>])(?=(?:[A-Za-z0-9][\w.\-:]{0,62})[#>])")


def normalize_cli_transcript(text: str) -> str:
    """Normalize login transcript for xterm (convertEol) and un-glue prompts."""
    s = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    s = _GLUED_PROMPT_RE.sub("\n", s)
    lines = s.split("\n")
    while lines and not str(lines[-1]).strip():
        lines.pop()
    # Drop blank lines immediately before a final prompt (banner\n\nR2# -> banner\nR2#).
    while len(lines) >= 2 and not str(lines[-2]).strip() and _looks_like_cli_prompt(lines[-1]):
        lines.pop(-2)
    # Collapse trailing duplicate prompt lines (slow VMs often echo R2# several times).
    while len(lines) >= 2 and str(lines[-1]).strip() == str(lines[-2]).strip() and _looks_like_cli_prompt(lines[-1]):
        lines.pop()
    return "\n".join(lines)


def prepare_bootstrap_output(text: str) -> str:
    """Full login transcript for UI replay; keep final prompt, no trailing newline after it.

    Trailing newline would leave the cursor on a blank line so the first typed line
    looks wrong; cursor should sit after the prompt like a real CRT.
    """
    s = normalize_cli_transcript(text)
    # Drop a stray ':' glued onto Huawei ``<host>`` after ``[Y/N]:`` buffer races.
    s = re.sub(r"(<[^\r\n>]+>):\s*$", r"\1", s)
    return s


def _capture_raw_channel(conn: ConnectHandler, *, duration: float = 0.5) -> str:
    """Read leftover PTY bytes into text (banner/MOTD after SSH auth).

    Interactive WebCRT skips Netmiko session_preparation, so the post-auth banner
    often never lands in ``session_log`` and must be pulled from the live channel.
    """
    chunks: list[str] = []
    channel = getattr(conn, "remote_conn", None)
    if channel is None:
        try:
            return _drain_channel(conn, rounds=max(2, int(duration / 0.05)), wait=0.05)
        except Exception:
            return ""
    end = time.time() + max(0.1, float(duration))
    while time.time() < end:
        got = False
        try:
            # Paramiko SSH channel
            if hasattr(channel, "recv_ready") and hasattr(channel, "recv"):
                if channel.recv_ready():
                    raw = channel.recv(65535)
                    if raw:
                        got = True
                        if isinstance(raw, bytes):
                            chunks.append(raw.decode("utf-8", errors="replace"))
                        else:
                            chunks.append(str(raw))
            # telnetlib-style
            elif callable(getattr(channel, "read_very_eager", None)):
                data = channel.read_very_eager()
                if data:
                    got = True
                    if isinstance(data, bytes):
                        chunks.append(data.decode("utf-8", errors="replace"))
                    else:
                        chunks.append(str(data))
            else:
                part = conn.read_channel()
                if part:
                    got = True
                    chunks.append(str(part))
        except Exception:
            break
        if not got:
            time.sleep(0.04)
    return "".join(chunks)


def _drain_raw_channel(conn: ConnectHandler, *, duration: float = 0.5) -> None:
    """Discard leftover bytes on the live channel (SSH/Telnet) after login priming."""
    _capture_raw_channel(conn, duration=duration)


def _prime_interactive_channel(conn: ConnectHandler, *, already_prompted: bool = False) -> str:
    """Sync interactive channel after login; return captured banner/prompt text.

    Skip the sync Enter when the login transcript already ends with a CLI prompt —
    otherwise slow Cisco VMs accumulate duplicate ``R2#`` lines in the bootstrap.
    """
    parts: list[str] = []
    try:
        parts.append(_capture_raw_channel(conn, duration=0.25))
    except Exception:
        pass
    if not already_prompted:
        try:
            conn.write_channel(channel_return(conn))
        except Exception:
            try:
                conn.write_channel("\n")
            except Exception:
                return "".join(parts)
        try:
            parts.append(_drain_channel(conn, rounds=6, wait=0.08))
        except Exception:
            pass
    try:
        parts.append(_capture_raw_channel(conn, duration=0.35))
    except Exception:
        pass
    return "".join(parts)


def _is_prompt_only_echo(text: str, prompt_hint: str = "") -> bool:
    """True when chunk is only whitespace / CR / a repeated prompt (safe to drop after bootstrap)."""
    s = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not s:
        return True
    hint = str(prompt_hint or "").strip()
    if hint and s == hint:
        return True
    # Single-line prompt echo only.
    if "\n" not in s and _looks_like_cli_prompt(s):
        return True
    if hint and all(line.strip() in ("", hint) for line in s.split("\n")):
        return True
    return False


def _normalize_encoding(name: str) -> str:
    enc = str(name or "utf-8").strip().lower().replace("_", "-")
    if enc in ("gbk", "gb2312", "gb18030", "cp936"):
        return "gbk"
    return "utf-8"


def _decode_bytes(data: bytes, encoding: str) -> str:
    enc = _normalize_encoding(encoding)
    try:
        return data.decode(enc, errors="replace")
    except Exception:
        return data.decode("utf-8", errors="replace")


def _encode_text(text: str, encoding: str) -> bytes:
    enc = _normalize_encoding(encoding)
    try:
        return text.encode(enc, errors="replace")
    except Exception:
        return text.encode("utf-8", errors="replace")


class _BoundedByteQueue:
    """Thread-safe queue that drops oldest chunks when full (backpressure)."""

    def __init__(self, maxsize: int = 2000) -> None:
        self._q: queue.Queue[bytes | None] = queue.Queue()
        self._max = max(8, int(maxsize or 2000))
        self._cond = threading.Condition()
        self.dropped = 0
        self._reported = 0

    def put(self, item: bytes | None) -> None:
        with self._cond:
            while self._q.qsize() >= self._max:
                try:
                    self._q.get_nowait()
                    self.dropped += 1
                except queue.Empty:
                    break
            self._q.put(item)
            self._cond.notify()

    def put_nowait(self, item: bytes | None) -> None:
        self.put(item)

    def get_nowait(self) -> bytes | None:
        with self._cond:
            return self._q.get_nowait()

    def get(self, timeout: float = 0.25) -> bytes | None:
        """Block until a chunk is available or timeout (raises queue.Empty)."""
        deadline = time.time() + max(0.0, float(timeout))
        with self._cond:
            while self._q.empty():
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise queue.Empty
                self._cond.wait(timeout=remaining)
            return self._q.get_nowait()

    def qsize(self) -> int:
        with self._cond:
            return self._q.qsize()

    def take_drop_delta(self) -> int:
        """Return newly dropped chunk count since last call (for client notice)."""
        with self._cond:
            delta = int(self.dropped) - int(self._reported)
            if delta <= 0:
                return 0
            self._reported = int(self.dropped)
            return delta


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso() -> str:
    return _utc_now().isoformat()


def webcrt_data_root() -> Path:
    root = Path(str(settings.webcrt_data_dir or "data/webcrt"))
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _session_log_path(session_id: str) -> Path:
    folder = webcrt_data_root() / "sessions"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{session_id}.log"


def read_session_log_tail(session_id: str, *, max_bytes: int = 49152) -> str:
    """Best-effort UTF-8 tail of the on-disk session transcript (for WS re-attach)."""
    path = _session_log_path(session_id)
    try:
        if not path.is_file():
            return ""
        size = path.stat().st_size
        take = max(1024, min(int(max_bytes or 49152), 256 * 1024))
        with path.open("rb") as fh:
            if size > take:
                fh.seek(size - take)
                raw = fh.read()
                # Drop partial first line after seek.
                nl = raw.find(b"\n")
                if 0 <= nl < len(raw) - 1:
                    raw = raw[nl + 1 :]
            else:
                raw = fh.read()
        text = raw.decode("utf-8", errors="replace")
        # Strip header comment lines from the visible replay.
        lines = [ln for ln in text.splitlines(keepends=True) if not ln.startswith("# session=")]
        return "".join(lines)
    except Exception:
        _log.debug("webcrt session log tail failed session=%s", session_id, exc_info=True)
        return ""


# Lifecycle + command events also land in audit_log (ops UI). Attach/detach/sftp stay file-only.
_DB_AUDIT_EVENTS = frozenset(
    {
        "session_connecting",
        "session_created",
        "session_open_failed",
        "session_closed",
        "command",
    }
)

_PASSWORD_PROMPT_RE = re.compile(
    r"(?:enter\s+)?(?:password|密码|passwd)\s*[:>]\s*$",
    re.IGNORECASE,
)


def looks_like_password_prompt(text: str) -> bool:
    """True when device stdout tail asks for a password (interactive auth)."""
    s = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    # Drop ANSI so prompt detection is stable.
    s = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\].*?\x07|\x1b.", "", s)
    parts = [ln.strip() for ln in s.split("\n") if ln.strip()]
    if not parts:
        return False
    return bool(_PASSWORD_PROMPT_RE.search(parts[-1]))


def normalize_audit_line(line: str) -> str:
    """Normalize xterm-visible input line for audit (keep device prompt prefix)."""
    s = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\].*?\x07|\x1b.", "", str(line or ""))
    return s.replace("\r", "").rstrip()


def feed_command_line_buffer(buf: str, data: str, *, max_line: int = 512) -> tuple[str, list[str]]:
    """Accumulate stdin into completed command lines (Enter / CR / LF).

    Handles backspace, ignores most control chars, truncates over-long lines.
    Returns ``(new_buffer, completed_lines)``.
    """
    cur = str(buf or "")
    completed: list[str] = []
    limit = max(64, min(int(max_line or 512), 4096))
    raw = str(data or "")
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == "\x1b" and i + 1 < len(raw):
            # Skip CSI / SS3 cursor-key sequences (Delete, arrows, etc.).
            if raw[i + 1] == "[":
                j = i + 2
                while j < len(raw) and raw[j] not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz~":
                    j += 1
                i = j + 1 if j < len(raw) else len(raw)
                continue
            if raw[i + 1] == "O" and i + 2 < len(raw):
                i += 3
                continue
        if ch in ("\r", "\n"):
            if cur:
                completed.append(cur[:limit])
            cur = ""
            i += 1
            continue
        if ch in ("\b", "\x7f"):
            cur = cur[:-1] if cur else ""
            i += 1
            continue
        if ch == "\x03":  # Ctrl-C — abandon current line
            cur = ""
            i += 1
            continue
        if ord(ch) < 32 and ch != "\t":
            i += 1
            continue
        if len(cur) < limit:
            cur += ch
        i += 1
    return cur, completed


def _audit(event: str, **fields: Any) -> None:
    """Write WebCRT audit to jsonl; dual-write selected events into audit_log."""
    # Enrich actor / device fields from the live session when callers omit them.
    sid = str(fields.get("session_id") or "").strip()
    if sid and (
        not fields.get("owner_user_id")
        or not fields.get("owner_username")
        or not fields.get("ne_name")
        or "protocol" not in fields
    ):
        try:
            from .webcrt_session_registry import get_session

            sess = get_session(sid)
            if sess is not None:
                fields.setdefault("owner_user_id", sess.owner_user_id)
                fields.setdefault("owner_username", sess.owner_username)
                fields.setdefault("ne_id", sess.ne_id)
                fields.setdefault("ne_name", sess.ne_name)
                fields.setdefault("ne_ip", sess.ne_ip)
                fields.setdefault("protocol", sess.protocol)
        except Exception:
            pass

    record = {"ts": _utc_iso(), "event": event, **fields}
    try:
        path = webcrt_data_root() / "audit.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        _log.exception("webcrt audit write failed")
    _log.info("webcrt.%s %s", event, {k: v for k, v in fields.items() if k != "detail"})

    if str(event or "") not in _DB_AUDIT_EVENTS:
        return
    try:
        from .audit_async import enqueue_audit

        actor_uid = str(fields.get("owner_user_id") or fields.get("actor_user_id") or "")
        actor_name = str(fields.get("owner_username") or fields.get("actor_username") or "")
        detail = {
            k: v
            for k, v in fields.items()
            if k
            not in {
                "owner_user_id",
                "owner_username",
                "actor_user_id",
                "actor_username",
            }
        }
        enqueue_audit(
            action=f"webcrt.{event}",
            actor_user_id=actor_uid,
            actor_username=actor_name,
            method="",
            path=f"/v1/webcrt/sessions/{sid}" if sid else "/v1/webcrt",
            status_code=0,
            client_ip=str(fields.get("client_ip") or ""),
            user_agent=str(fields.get("client") or "")[:512],
            detail=detail,
        )
    except Exception:
        _log.exception("webcrt audit_log enqueue failed event=%s", event)
