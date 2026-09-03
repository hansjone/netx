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
    # Never glue multiple PTY rows into one audit command.
    s = s.replace("\r", "\n").split("\n", 1)[0]
    return s.rstrip()


def finalize_audit_line(line: str) -> str:
    """Apply echoed backspaces then normalize (PTY stdout fallback only)."""
    s = _strip_ansi(str(line or ""))
    # Keep a single logical line — swallowing \\n used to glue command + device legend.
    s = s.replace("\r", "\n").split("\n", 1)[0]
    out: list[str] = []
    for ch in s:
        if ch in ("\b", "\x7f"):
            if out:
                out.pop()
            continue
        if ord(ch) < 32 and ch != "\t":
            continue
        out.append(ch)
    return normalize_audit_line("".join(out))


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\].*?\x07|\x1b.", "", str(text or ""))


# Huawei/ZTE interface-brief legends and pager crumbs often stick to the prompt line
# after ANSI cursor moves are stripped — never treat them as part of the command.
_AUDIT_CMD_CONTAMINATION = re.compile(
    r"(?:"
    r"\*down:"
    r"|!down:"
    r"|\^down:"
    r"|\([a-z]{1,3}\):"
    r"|PHY:\s*Physical"
    r"|----\s*More\s*----"
    r"|InUti/OutUti"
    r"|Interface\s+PHY\b"
    r"|The number of interface"
    r"|Local Intf\s+Neighbor"
    r")",
    flags=re.I,
)


def sanitize_audit_command(line: str | None) -> str | None:
    """Clip prompt+command and drop device-output contamination."""
    if line is None:
        return None
    s = normalize_audit_line(line)
    if not s.strip():
        return None
    m = _AUDIT_CMD_CONTAMINATION.search(s)
    if m:
        s = s[: m.start()].rstrip()
    if not is_auditable_command_line(s):
        return None
    # Collapse spaces left by mid-line overwrite deletes (``dis  interface``).
    prompt_m = re.match(
        r"^(?:[\w.-]+(?:\([^)]+\))*[#>]|<[^>]+>|\[[^\]]+\])\s*",
        s,
        flags=re.I,
    )
    if prompt_m:
        s = prompt_m.group(0) + " ".join(s[prompt_m.end() :].split())
    else:
        s = " ".join(s.split())
    cmd = _command_tail(s)
    # Guard against absurd glued blobs that still look like a prompt line.
    if len(cmd) > 240 or len(s) > 300:
        return None
    if _AUDIT_CMD_CONTAMINATION.search(s):
        return None
    return s[:512]


def _is_prompt_command_line(line: str) -> bool:
    """True when line looks like ``hostname#command`` (non-empty command tail)."""
    s = str(line or "").strip()
    if not s:
        return False
    return bool(
        re.match(
            r"^(?:"
            r"[\w.-]+(?:\([^)]+\))*[#>]\s*\S"
            r"|<[^>]+>\s*\S"
            r"|\[[^\]]+\]\s*\S"
            r")",
            s,
            flags=re.I,
        )
    )


def _has_cli_prompt_prefix(line: str) -> bool:
    s = normalize_audit_line(line)
    return bool(
        re.match(
            r"^(?:"
            r"[\w.-]+(?:\([^)]+\))*[#>]"
            r"|>[\w.-]+"
            r"|<[^>]+>"
            r"|\[[^\]]+\]"
            r")",
            s,
            flags=re.I,
        )
    )


def _is_device_output_line(line: str) -> bool:
    """Device error/warning echo — never an operator-typed command."""
    s = normalize_audit_line(line).strip()
    if not s:
        return False
    low = s.lower()
    if low.startswith("%error") or low.startswith("%warning"):
        return True
    if "invalid input detected" in low:
        return True
    if re.match(r"^\^+\s*$", s):
        return True
    if re.match(r"^enter configuration commands", low):
        return True
    if _AUDIT_CMD_CONTAMINATION.search(s):
        # Legend / pager text alone, or glued onto a prompt line.
        if not _has_cli_prompt_prefix(s):
            return True
        # Prompt + legend glued (ANSI stripped): treat as contaminated output.
        cmd = _command_tail(s)
        if _AUDIT_CMD_CONTAMINATION.search(cmd):
            return True
    return False


def is_auditable_command_line(line: str) -> bool:
    """False for empty Enter, device output, or lines without a CLI prompt prefix."""
    s = normalize_audit_line(line)
    if not s.strip():
        return False
    if _is_prompt_only_line(s):
        return False
    if _is_device_output_line(s):
        return False
    return _has_cli_prompt_prefix(s)


def _is_prompt_only_line(line: str) -> bool:
    """True when line is a device prompt with no command typed."""
    s = normalize_audit_line(line)
    if not s:
        return True
    return bool(
        re.match(
            r"^(?:"
            r"[\w.-]+(?:\([^)]+\))*[#>]\s*"
            r"|<[^>]+>\s*"
            r"|\[[^\]]+\]\s*"
            r")$",
            s,
            flags=re.I,
        )
    )


def _stdout_has_inplace_edit(text: str) -> bool:
    """True when the *current* input row was rewritten with cursor CSI.

    Only inspects the last fragment (live input line). Older history-edit CSI still
    sitting in ``stdout_tail`` must not make bare Enter look like an in-place edit.
    Excludes the common ``---- More ----`` wipe (``ESC[16D``).

    Huawei rewrites the input row with CSI cursor moves (``ESC[<n>D/C/P/@``). ZTE
    instead erases-to-EOL (``ESC[K``) and reprints, so the ``[DCP@]`` scan alone
    misses ZTE history recalls — also treat a live prompt+command fragment that
    carries ``ESC[K`` as an in-place redraw.
    """
    s = str(text or "")[-4000:]
    if not s:
        return False
    s = re.sub(r"----\s*More\s*----\x1b\[16D\s*\x1b\[16D", "", s, flags=re.I)
    frags = [f for f in re.split(r"\n+", s) if f.strip()]
    if not frags:
        return False
    frag = frags[-1]
    rendered = render_pty_line(frag)
    if _is_prompt_only_line(rendered) or _is_prompt_only_line(normalize_audit_line(frag)):
        return False
    if not _is_prompt_command_line(rendered):
        return False
    for m in re.finditer(r"\x1b\[([0-9]*)([DCP@])", frag):
        n_s, cmd = m.group(1), m.group(2)
        try:
            n = int(n_s) if n_s else 1
        except ValueError:
            n = 1
        if cmd in "CP@":
            return True
        if cmd == "D" and n != 16:
            return True
    # ZTE redraws the live input row with ESC[K (erase-to-EOL) + reprint / backspace
    # rather than CSI cursor moves. A prompt+command fragment carrying ESC[K is an
    # in-place edit (a trailing prompt cleanup alone renders as prompt-only above).
    if "\x1b[K" in frag and _command_tail(rendered).strip():
        return True
    return False


def _live_input_line(stdout_tail: str) -> str:
    """Visible text on the current input row (after last NL / CR redraw).

    Huawei/ZTE often redraw the next prompt with bare ``\\r`` onto the previous
    output row. Taking the last *non-empty* CR segment avoids leftover glyphs
    (``Ethernet...\\r<r1>`` → ``<r1>``) and trailing CRs (``[~r1]\\r\\r`` → ``[~r1]``).
    """
    s = str(stdout_tail or "")[-2000:]
    frags = [f for f in re.split(r"\n+", s) if f.strip()]
    if not frags:
        return ""
    last = frags[-1]
    if "\r" in last:
        parts = last.split("\r")
        non_empty = [p for p in parts if p.strip()]
        last = non_empty[-1] if non_empty else ""
    return render_pty_line(last).strip()


def _live_input_idle(stdout_tail: str) -> bool:
    """True when the device is sitting on a bare prompt (no current command text).

    Empty stdout is *not* idle — callers may only have an xterm audit_line (unit tests
    / early enter). Idle requires a positive bare-prompt observation.
    """
    if not str(stdout_tail or "").strip():
        return False
    live = _live_input_line(stdout_tail)
    return (not live) or _is_prompt_only_line(live)


def extract_last_prompt_command(text: str) -> str | None:
    """Last prompt+command line in PTY transcript (tab-complete / history-recall aware).

    Network devices often refresh the current input with ``\\r`` after tab, or rewrite
    the line in-place with CSI cursor moves after Up-arrow history recall. Plain ANSI
    stripping would glue ``commit`` + ``ip address...``; we render CSI first.
    """
    s = str(text or "")
    if not s.strip():
        return None

    candidates: list[tuple[int, str]] = []
    for frag in re.split(r"\n+", s):
        if not frag.strip("\r"):
            continue
        edited = 1 if re.search(r"\x1b\[[0-9]*[DCP@]", frag) else 0
        rendered = render_pty_line(frag)
        if rendered.strip():
            candidates.append((edited, rendered))
        if "\r" in frag:
            sub = frag.rsplit("\r", 1)[-1]
            edited_sub = 1 if re.search(r"\x1b\[[0-9]*[DCP@]", sub) else 0
            candidates.append((edited_sub, render_pty_line(sub)))

    # Prefer chronologically later rows; among the last few, prefer CSI-edited rows
    # so a stale pre-edit recall does not win over the post-edit line.
    for edited, line in reversed(candidates):
        if edited and line and _is_prompt_command_line(line):
            clipped = sanitize_audit_command(normalize_audit_line(line))
            if clipped:
                return clipped
    for _edited, line in reversed(candidates):
        if line and _is_prompt_command_line(line):
            clipped = sanitize_audit_command(normalize_audit_line(line))
            if clipped:
                return clipped
    return None


def _command_tail(line: str) -> str:
    s = normalize_audit_line(line)
    for pat in (
        r"^[\w.-]+(?:\([^)]+\))*[#>]\s*(.*)$",
        r"^<[^>]+>\s*(.*)$",
        r"^\[[^\]]+\]\s*(.*)$",
    ):
        m = re.match(pat, s, flags=re.I)
        if m:
            return m.group(1).strip()
    return s.strip()


def _token_appears(token: str, cmd: str) -> bool:
    """Whole-token match so ``ip`` does not hit the letters inside ``display``."""
    t = str(token or "").replace("\t", "").strip()
    if not t:
        return False
    parts = str(cmd or "").split()
    if t in parts:
        return True
    if parts and (parts[-1].startswith(t) or t.startswith(parts[-1])):
        return True
    return False


def _looks_like_edit_fragment(typed: str, full_line: str) -> bool:
    """True when stdin bytes look like a mid-line history edit, not a full command.

    Up-arrow recall + cursor edit only sends newly typed chars (``33``, ``ip``), while
    the device / xterm holds the full ``ip address ... 33`` line.
    """
    t = str(typed or "").replace("\t", "").strip()
    if not t or not full_line:
        return False
    ph_cmd = _command_tail(full_line)
    if not ph_cmd or ph_cmd == t:
        return False
    # Multi-word recalled command vs short typed fragment.
    if len(ph_cmd.split()) >= 2 and (" " not in t) and len(t) <= 64:
        return True
    if len(t) * 2 < len(ph_cmd) and (_token_appears(t, ph_cmd) or ph_cmd.endswith(t)):
        return True
    return False


def render_pty_line(text: str) -> str:
    """Best-effort single-row CSI renderer for Huawei/ZTE history-recall redraws."""
    raw = str(text or "").split("\n")[-1]
    cells: list[str] = []
    cursor = 0
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == "\x1b" and i + 1 < len(raw):
            nxt = raw[i + 1]
            if nxt == "[":
                j = i + 2
                while j < len(raw) and raw[j] not in "ABCDEFGHJKSTfhlmnpsu":
                    j += 1
                if j >= len(raw):
                    break
                final = raw[j]
                params_s = raw[i + 2 : j]
                try:
                    n = int(params_s) if params_s else 1
                except ValueError:
                    n = 1
                if final == "D":
                    cursor = max(0, cursor - n)
                elif final == "C":
                    cursor = min(len(cells), cursor + n)
                elif final == "G":
                    cursor = max(0, n - 1) if n > 0 else 0
                    if cursor > len(cells):
                        cells.extend([" "] * (cursor - len(cells)))
                elif final == "K":
                    mode = int(params_s) if params_s else 0
                    if mode == 0:
                        cells = cells[:cursor]
                    elif mode == 1:
                        for k in range(min(cursor, len(cells))):
                            cells[k] = " "
                    elif mode == 2:
                        cells = []
                        cursor = 0
                elif final == "P":
                    del cells[cursor : cursor + n]
                elif final == "@":
                    cells[cursor:cursor] = [" "] * n
                i = j + 1
                continue
            if nxt == "O" and i + 2 < len(raw):
                i += 3
                continue
            i += 2
            continue
        if ch == "\r":
            cursor = 0
            i += 1
            continue
        if ch == "\b":
            # Backspace (0x08) in PTY output is cursor-left WITHOUT erasing —
            # ZTE line redraws move the cursor via long backspace runs then
            # overwrite/ESC[K to repaint.  A destructive delete here mangles
            # the recalled command text, so render_pty_line ends up with a
            # fragment ("p") instead of the full "show ip interface brief".
            if cursor > 0:
                cursor -= 1
            i += 1
            continue
        if ch == "\x7f":
            # DEL (0x7f) deletes the cell to the left of the cursor.
            if cursor > 0:
                cursor -= 1
                if cursor < len(cells):
                    del cells[cursor]
            i += 1
            continue
        if ord(ch) < 32:
            i += 1
            continue
        if cursor < len(cells):
            cells[cursor] = ch
        else:
            if cursor > len(cells):
                cells.extend([" "] * (cursor - len(cells)))
            cells.append(ch)
        cursor += 1
        i += 1
    return "".join(cells).rstrip()


def _is_cli_expansion(short_line: str, long_line: str) -> bool:
    """True when ``long_line`` looks like Tab / abbreviation expansion of ``short_line``."""
    short = sanitize_audit_command(short_line) or normalize_audit_line(short_line)
    long = sanitize_audit_command(long_line)
    if not long:
        return False
    a = " ".join(_command_tail(short).split())
    b = " ".join(_command_tail(long).split())
    if not a or not b or a == b:
        return False
    # Reject glued device legends that merely startswith the short command.
    if _AUDIT_CMD_CONTAMINATION.search(_command_tail(long_line) or ""):
        return False
    if len(b) > len(a) + 80:
        return False
    if b.startswith(a) and len(b) > len(a):
        # Expansion should stay within CLI token charset (no *!^ legend glue).
        extra = b[len(a) :]
        if re.search(r"[*!^]", extra):
            return False
        return True
    ta, tb = a.split(), b.split()
    if not ta or len(ta) > len(tb):
        return False
    # Only allow long tokens to extend short tokens (Tab), never the reverse
    # (``interface`` vs ``ip`` used to false-match via startswith both ways).
    # Case-insensitive: Huawei expands ``lo`` → ``LoopBack``.
    for i, tok in enumerate(ta):
        other = tb[i]
        if other.lower() == tok.lower() or other.lower().startswith(tok.lower()):
            continue
        return False
    # Remaining long tokens are Tab-inserted middle/trailing words.
    return len(tb) >= len(ta) and len(b) <= len(a) + 80


def _attach_prompt_prefix(typed: str, hint: str) -> str | None:
    cmd = str(typed or "").strip()
    if not cmd:
        return None
    h = normalize_audit_line(hint)
    if not _has_cli_prompt_prefix(h):
        return None
    m = re.match(r"^([\w.-]+(?:\([^)]+\))*[#>])\s*", h, flags=re.I)
    if m:
        return f"{m.group(1)}{cmd}"
    m = re.match(r"^(<[^>]+>)\s*", h)
    if m:
        return f"{m.group(1)}{cmd}"
    m = re.match(r"^(\[[^\]]+\])\s*", h)
    if m:
        return f"{m.group(1)}{cmd}"
    return None


def pick_audit_command(
    stdin_line: str,
    audit_hint: str | None,
    *,
    prompt_hint: str = "",
    stdout_tail: str = "",
    source: str = "stdin",
) -> str | None:
    """Pick auditable text for one completed stdin line (actual send + optional xterm hint)."""
    typed_raw = str(stdin_line or "")
    typed_has_tab = "\t" in typed_raw
    typed = normalize_audit_line(typed_raw).strip()
    hint = normalize_audit_line(audit_hint) if audit_hint else ""
    src = str(source or "stdin")
    compact_typed = typed.replace("\t", "").strip()

    def _out(cmd: str | None) -> str | None:
        if not cmd:
            return None
        cleaned = sanitize_audit_command(cmd)
        if cleaned:
            return cleaned
        # early/post_login may lack a prompt prefix — still strip legend glue.
        s = normalize_audit_line(cmd)
        m = _AUDIT_CMD_CONTAMINATION.search(s)
        if m:
            s = s[: m.start()].rstrip()
        if not s or _AUDIT_CMD_CONTAMINATION.search(s):
            return None
        if src in ("post_login", "early_stdin") or (
            src == "stdin" and not _has_cli_prompt_prefix(s) and not _is_prompt_only_line(s)
        ):
            return s[:512]
        return None

    if typed and _is_device_output_line(typed):
        return None

    if src == "post_login" and typed:
        return _out(typed.replace("\t", " ").strip() or typed)

    # No keystroke payload and no auditable xterm snapshot → only scrape stdout when
    # the device just did an in-place history edit (CSI). Bare Enter must not re-audit.
    if not compact_typed and not (hint and is_auditable_command_line(hint)):
        if src != "early_stdin":
            if _stdout_has_inplace_edit(stdout_tail):
                echoed = extract_last_prompt_command(stdout_tail)
                if echoed:
                    return _out(echoed)
            return None

    # Empty Enter while the live row is a bare prompt: never trust a stale xterm hint
    # (walk-up into the previous command echo). Applies even when callers skip the
    # resolve_audit_commands idle gate.
    if not compact_typed and src == "stdin" and str(stdout_tail or "").strip():
        live = _live_input_line(stdout_tail)
        if ((not live) or _is_prompt_only_line(live)) and not _stdout_has_inplace_edit(
            stdout_tail
        ):
            return None

    def _from_device_echo() -> str | None:
        def _usable(full: str) -> bool:
            if not full or not is_auditable_command_line(full):
                return False
            ph_cmd = _command_tail(full) or full
            if typed_has_tab or not compact_typed:
                return True
            if ph_cmd.startswith(compact_typed) or compact_typed.startswith(ph_cmd):
                return True
            if _token_appears(compact_typed, ph_cmd):
                return True
            # History fragment: only accept echo that already contains the typed token.
            if _looks_like_edit_fragment(compact_typed, full):
                return _token_appears(compact_typed, ph_cmd)
            return False

        # Prefer live stdout (CSI-rendered history line) over possibly stale prompt_hint.
        if stdout_tail:
            ext = extract_last_prompt_command(stdout_tail)
            if ext and _usable(ext):
                return ext
        if prompt_hint:
            ph = sanitize_audit_command(prompt_hint) or (
                normalize_audit_line(prompt_hint)
                if is_auditable_command_line(prompt_hint)
                else ""
            )
            if ph and _usable(ph):
                return ph
        return None

    def _prefer_expanded(base: str) -> str:
        """Reconcile xterm hint with live device echo (Tab / history mid-line edits)."""
        echoed = _from_device_echo()
        if not echoed:
            return base
        base_n = sanitize_audit_command(base) or normalize_audit_line(base)
        echo_n = sanitize_audit_command(echoed) or echoed
        bc = " ".join(_command_tail(base_n).split())
        ec = " ".join(_command_tail(echo_n).split())
        if not ec or ec == bc:
            return base_n
        # In-place history edit on the device: always trust the CSI-rendered echo.
        if _stdout_has_inplace_edit(stdout_tail):
            return echo_n
        # Tab completion only: accept longer real expansions. Never re-inflate a
        # shorter post-delete snapshot back into a longer stale echo without Tab.
        if typed_has_tab and _is_cli_expansion(base_n, echo_n):
            return echo_n
        # Tab: prefer the longer device expansion when token heads match
        # (``interface lo`` → ``interface LoopBack 1``).
        if typed_has_tab:
            bt, et = bc.split(), ec.split()
            if bt and et and bt[0].lower() == et[0].lower() and len(ec) > len(bc):
                return echo_n
        bt, et = bc.split(), ec.split()
        if bt and et and bt[0].lower() == et[0].lower():
            if len(et) < len(bt):
                return echo_n
            if len(et) == len(bt) and et != bt:
                return echo_n
            # Echo longer with the xterm hint as a token-prefix of the fully
            # redrawn device line: the xterm visible row lagged a partial prefix
            # at Enter (ZTE reprints via ESC[K, so the snapshot can still read
            # ``show ip`` while the device already shows ``show ip interface
            # brief``). Trust the complete device echo.
            if len(et) > len(bt) and all(
                et[i].lower() == bt[i].lower() for i in range(len(bt))
            ):
                return echo_n
            return base_n
        return base_n

    # Tab completion: device echo is authoritative (xterm snapshot may still show
    # the pre-expansion fragment ``interface lo`` when Enter is delayed after Tab).
    if typed_has_tab:
        echoed = _from_device_echo()
        if echoed:
            return _out(echoed)
        if hint and is_auditable_command_line(hint):
            return _out(hint)
        # Never persist literal Tab into audit_log.
        typed = compact_typed

    # xterm visible row at Enter is usually authoritative — but history mid-line
    # deletes may leave a stale longer snapshot in audit_line while device echo
    # is already shorter.
    if hint and is_auditable_command_line(hint):
        return _out(_prefer_expanded(hint))

    if src == "early_stdin" and typed and not _is_prompt_only_line(typed):
        echoed = _from_device_echo()
        if echoed:
            return _out(echoed)
        return _out(typed)

    if typed and is_auditable_command_line(typed):
        return _out(_prefer_expanded(typed))

    # History/arrow edits: stdin may be only the newly typed fragment ("33", "ip").
    # Never glue that onto the prompt as "[*r1]33" — prefer device-rendered full line.
    for candidate in (
        sanitize_audit_command(hint) if hint else None,
        _from_device_echo(),
        sanitize_audit_command(prompt_hint) if prompt_hint else None,
    ):
        if not candidate or not is_auditable_command_line(candidate):
            continue
        if not _looks_like_edit_fragment(typed, candidate):
            continue
        # Require the candidate to already reflect the typed edit (token-level).
        if compact_typed and not _token_appears(compact_typed, _command_tail(candidate)):
            continue
        return _out(candidate)

    for prefix_src in (hint, prompt_hint):
        if prefix_src and _looks_like_edit_fragment(typed, prefix_src):
            continue
        enriched = _attach_prompt_prefix(typed, prefix_src)
        if enriched and is_auditable_command_line(enriched):
            # Reject enrichment that clearly dropped the recalled command body.
            if prompt_hint and _looks_like_edit_fragment(typed, prompt_hint):
                continue
            return _out(_prefer_expanded(enriched))

    echoed = _from_device_echo()
    if echoed:
        return _out(echoed)

    if src == "stdin" and typed and not _is_prompt_only_line(typed):
        # Last resort: still avoid publishing bare fragments when we have a full echo.
        if prompt_hint and _looks_like_edit_fragment(typed, prompt_hint):
            ph = sanitize_audit_command(prompt_hint)
            if ph:
                return _out(ph)
        return _out(typed)

    return None


def resolve_audit_commands(
    buf_lines: list[str],
    *,
    audit_line: str | None = None,
    audit_lines: list[str] | None = None,
    prompt_hint: str = "",
    stdout_tail: str = "",
    source: str = "stdin",
) -> list[str]:
    """Map all completed stdin lines in one flush to auditable command strings."""
    src = str(source or "stdin")
    if src == "prompt_sync":
        return []

    if not buf_lines:
        # History mid-line CSI edits can complete Enter with empty stdin.
        hint = str(audit_line or "").strip()
        has_stdout = bool(str(stdout_tail or "").strip())
        live = _live_input_line(stdout_tail) if has_stdout else ""
        inplace = _stdout_has_inplace_edit(stdout_tail)
        # Bare Enter / empty stdin: never re-audit from a stale xterm audit_line that
        # walked up into the previous command echo. Only proceed when the *live*
        # device row still shows a command (history recall) or an in-place CSI edit.
        if has_stdout and not inplace:
            if (not live) or _is_prompt_only_line(live) or not is_auditable_command_line(live):
                return []
        if hint and is_auditable_command_line(normalize_audit_line(hint)):
            cmd = pick_audit_command(
                "",
                hint,
                prompt_hint=prompt_hint,
                stdout_tail=stdout_tail,
                source=src,
            )
            return [cmd] if cmd else []
        if inplace:
            cmd = pick_audit_command(
                "",
                None,
                prompt_hint=prompt_hint,
                stdout_tail=stdout_tail,
                source=src,
            )
            return [cmd] if cmd else []
        return []

    hints: list[str | None] = [None] * len(buf_lines)
    merged: list[str] = [str(x).strip() for x in (audit_lines or []) if str(x).strip()]
    if audit_line and str(audit_line).strip():
        if not merged:
            merged = [str(audit_line).strip()]
        elif merged[-1] != str(audit_line).strip():
            merged.append(str(audit_line).strip())

    if merged:
        if len(merged) == len(buf_lines):
            hints = list(merged)
        elif len(merged) < len(buf_lines):
            start = len(buf_lines) - len(merged)
            for j, h in enumerate(merged):
                hints[start + j] = h
        else:
            # Duplicate Enter can produce more audit_line snapshots than completed
            # stdin lines — keep the trailing hints (most recent).
            hints = list(merged[-len(buf_lines) :])

    out: list[str] = []
    for i, typed in enumerate(buf_lines):
        cmd = pick_audit_command(
            typed,
            hints[i],
            prompt_hint=prompt_hint,
            stdout_tail=stdout_tail if i == len(buf_lines) - 1 else "",
            source=src,
        )
        if cmd:
            out.append(cmd[:512])
    return out


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
