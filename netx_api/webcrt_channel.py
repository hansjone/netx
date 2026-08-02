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


def _looks_like_cli_prompt(text: str) -> bool:
    s = str(text or "").rstrip()
    if not s:
        return False
    # Buffer races can leave a stray ':' after Huawei ``<r1>`` (from prior ``[Y/N]:``).
    if s.endswith(":") and ">" in s:
        s = s[:-1].rstrip()
    # Common network CLI prompts: <r1>  [HUAWEI]  Router#  Router>
    return bool(re.search(r"(?:[>\]]|#)\s*$", s)) or bool(re.search(r"<[^>\r\n]+>\s*$", s))


def _looks_like_login_prompt(text: str) -> bool:
    """True when the transcript ends at Username:/Login:/Password: (interactive auth)."""
    s = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.strip() for ln in s.split("\n") if ln.strip()]
    if not lines:
        return False
    last = lines[-1]
    return bool(re.search(r"(?i)(user\s*name|login|password)\s*:\s*$", last))


def _looks_like_password_change_prompt(text: str) -> bool:
    """Huawei/VRP post-auth ``Change now? [Y/N]:`` (Netmiko already answers N)."""
    s = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.strip() for ln in s.split("\n") if ln.strip()]
    if not lines:
        return False
    last = lines[-1]
    return bool(re.search(r"(?i)(change\s*now|please\s*choose|password\s+needs\s+to\s+be\s+changed).{0,80}:\s*$", last)) or bool(
        re.search(r"\[Y/N\]\s*:\s*$", last, flags=re.I)
    )


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


def _audit(event: str, **fields: Any) -> None:
    record = {"ts": _utc_iso(), "event": event, **fields}
    try:
        path = webcrt_data_root() / "audit.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        _log.exception("webcrt audit write failed")
    _log.info("webcrt.%s %s", event, {k: v for k, v in fields.items() if k != "detail"})
