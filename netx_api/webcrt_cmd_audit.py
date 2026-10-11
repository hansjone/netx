"""Server-side command audit for WebCRT: track the device's own input row.

Why: inferring commands from browser keystrokes / xterm snapshots races with the
device echo (fast typing, Tab, history recall, slow VMs) and records fragments such
as ``R2#s`` or ``cddontainer/r/``.  The device itself always knows what it executes:
when it echoes the line feed for our Enter, the *current input row* is exactly the
prompt + command it is about to run.

``LineScreen`` is a tiny single-row VT100 model (CR/BS/CSI cursor + erase).
``CommandTracker`` owns one ``LineScreen`` per session and resolves each Enter we
send against the first line feed the device echoes back.  Everything here is pure
(no I/O) and cheap: output before the last line feed is skipped unless an Enter
is waiting for its echo.
"""

from __future__ import annotations

import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# Single-row terminal model
# --------------------------------------------------------------------------- #

_TOKEN_RE = re.compile(
    r"(\x1b\[[0-?]*[ -/]*[@-~]"  # CSI
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC
    r"|\x1b[()][0-9A-Za-z]"  # charset
    r"|\x1b[@-Z\\-_0-9=>]"  # 2-byte ESC
    r"|[\x00-\x1f\x7f])"  # C0 + DEL
)
# Incomplete escape at the end of a chunk — carry into the next feed.
_PARTIAL_ESC_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*|\][^\x07\x1b]*\x1b?|[()])?\Z")
_MAX_CARRY = 96


class LineScreen:
    """Current input row of a terminal (no scrolling / wrapping)."""

    __slots__ = ("cells", "cur", "_carry")

    def __init__(self) -> None:
        self.cells: list[str] = []
        self.cur = 0
        self._carry = ""

    def reset_row(self) -> None:
        self.cells = []
        self.cur = 0

    def text(self) -> str:
        return "".join(self.cells).rstrip()

    def _put(self, run: str) -> None:
        if not run:
            return
        n = len(run)
        cells = self.cells
        cur = self.cur
        if cur == len(cells):
            cells.extend(run)
        else:
            if cur > len(cells):
                cells.extend(" " * (cur - len(cells)))
            cells[cur : cur + n] = run
        self.cur = cur + n

    def apply_token(self, tok: str) -> None:
        """Apply one control token (not LF — the tracker owns line feeds)."""
        c0 = tok[0]
        if c0 == "\r":
            self.cur = 0
        elif c0 == "\b":
            if self.cur > 0:
                self.cur -= 1
        elif c0 == "\t":
            self._put(" " * (8 - (self.cur % 8)))
        elif c0 == "\x1b":
            if len(tok) >= 3 and tok[1] == "[":
                self._csi(tok[2:-1], tok[-1])
            elif tok == "\x1bc":
                self.reset_row()
        # every other control byte (BEL, NUL, DEL, SO/SI …) is ignored

    def _csi(self, params: str, final: str) -> None:
        if final not in "DCKP@XGHfJ":
            return
        if params and params[0] in "?>=":
            return
        try:
            first = params.split(";", 1)[0]
            n = int(first) if first else 0
        except ValueError:
            n = 0
        cells = self.cells
        if final == "D":
            self.cur = max(0, self.cur - (n or 1))
        elif final == "C":
            self.cur += n or 1
        elif final == "K":
            if n == 0:
                del cells[self.cur :]
            elif n == 1:
                for i in range(min(self.cur + 1, len(cells))):
                    cells[i] = " "
            else:
                self.reset_row()
        elif final == "P":
            del cells[self.cur : self.cur + (n or 1)]
        elif final == "@":
            if self.cur > len(cells):
                cells.extend(" " * (self.cur - len(cells)))
            cells[self.cur : self.cur] = [" "] * (n or 1)
        elif final == "X":
            for i in range(self.cur, min(self.cur + (n or 1), len(cells))):
                cells[i] = " "
        elif final == "G":
            self.cur = max(0, (n or 1) - 1)
        elif final in "Hf":
            parts = params.split(";")
            try:
                col = int(parts[1]) if len(parts) > 1 and parts[1] else 1
            except ValueError:
                col = 1
            self.cur = max(0, col - 1)
        elif final == "J":
            if n in (2, 3):
                self.reset_row()
            elif n == 0:
                del cells[self.cur :]

    def feed_row(self, text: str) -> None:
        """Apply text that contains no line feed."""
        if not text:
            return
        if not _has_ctl(text):
            self._put(text)
            return
        parts = _TOKEN_RE.split(text)
        for i, part in enumerate(parts):
            if not part:
                continue
            if i & 1:
                self.apply_token(part)
            else:
                self._put(part)

    def take_carry(self, text: str) -> str:
        """Prepend a carried partial escape; stash a new trailing partial."""
        if self._carry:
            text = self._carry + text
            self._carry = ""
        if text and "\x1b" in text[-_MAX_CARRY:]:
            m = _PARTIAL_ESC_RE.search(text, max(0, len(text) - _MAX_CARRY))
            if m and m.group(0):
                self._carry = m.group(0)
                text = text[: m.start()]
        return text


def _has_ctl(text: str) -> bool:
    # str.isprintable is a fast C scan; only fall back to token split when needed.
    return not text.isprintable()


# --------------------------------------------------------------------------- #
# Prompt splitting
# --------------------------------------------------------------------------- #

_PROMPT_RES: tuple[re.Pattern[str], ...] = (
    # Huawei / H3C / ZTE user & system view: <r1>  [r1]  [~r1]  [*r1-GigabitEthernet0/0/1]
    re.compile(r"^<[^<>\s]{1,63}>"),
    re.compile(r"^\[[~*]?[A-Za-z0-9][\w.\-/:+]{0,79}\]"),
    # Cisco / Arista / ZTE / Ruijie: R2#  R2>  R2(config-if)#  SW-1(config)#
    re.compile(r"^[A-Za-z0-9][\w.\-/:+]{0,62}(?:\([^()\s]{1,60}\))*[#>]"),
    # MikroTik: [admin@MikroTik] >   [admin@MikroTik] /ip>
    re.compile(r"^\[[\w.\-]+@[\w.\-]+\][^\n]{0,40}?>\s?"),
)
_LINUX_PROMPT_RES: tuple[re.Pattern[str], ...] = (
    # [user@host dir]$   user@host:~/dir$   (venv) user@host:~#
    re.compile(r"^(?:\([^()]{1,40}\)\s*)?\[[^\]]{1,80}\]\s?[$#%>]\s?"),
    re.compile(r"^(?:\([^()]{1,40}\)\s*)?[\w.\-]+@[\w.\-]+(?::|\s)[^\s$#%>]{0,120}\s?[$#%>]\s?"),
    re.compile(r"^[^\s$#%>]{1,80}[$#%]\s"),
)
_NOT_PROMPT_RE = re.compile(r"^\[[YyNn]/[YyNn]\]")
_PASSWORD_ROW_RE = re.compile(r"(?:password|passwd|passphrase|密码)\s*[:：>]\s*\**\s*$", re.IGNORECASE)
_PAGER_ROW_RE = re.compile(r"-{2,}\s*more\s*-{2,}|--\s*more\s*--|\(END\)|lines?\s+\d+-\d+", re.IGNORECASE)


def split_prompt(row: str, hint: str = "", *, linux_like: bool = False) -> tuple[str, str] | None:
    """Split ``row`` into ``(prompt, command)``; None when no CLI prompt is recognised.

    ``prompt`` keeps the whitespace before the command, so ``prompt + command`` is the
    row exactly as the device displayed it.
    """
    s = row.rstrip()
    if not s or _NOT_PROMPT_RE.match(s) or _PAGER_ROW_RE.search(s[:80]):
        return None
    h = hint.rstrip()
    if h and s.startswith(h):
        return _cut(s, len(h))
    pats = _LINUX_PROMPT_RES + _PROMPT_RES if linux_like else _PROMPT_RES + _LINUX_PROMPT_RES
    for pat in pats:
        m = pat.match(s)
        if m:
            return _cut(s, m.end())
    return None


def _cut(s: str, end: int) -> tuple[str, str]:
    rest = s[end:]
    cmd = rest.lstrip()
    return s[: len(s) - len(cmd)], cmd.rstrip()


def looks_like_password_row(row: str) -> bool:
    return bool(_PASSWORD_ROW_RE.search(row.rstrip()))


# --------------------------------------------------------------------------- #
# Command tracker
# --------------------------------------------------------------------------- #


@dataclass
class AuditCmd:
    command: str  # prompt + command as the device displayed it
    prompt: str = ""
    input: str = ""  # command only
    redacted: bool = False
    source: str = "stdin"
    via: str = "device_echo"


@dataclass
class _Pending:
    t: float
    typed: str
    snapshot: str  # input row at the time we sent Enter
    prompt_hint: str
    client_hints: list[str]
    source: str
    # "bare": Enter with nothing typed in this payload (history recall / empty Enter) —
    # consumes the first line feed whatever it shows.
    bare: bool = False
    # Tab can make the device redraw the line after a CRLF before the Enter echo; keep the
    # latest prompt+command row seen and settle once the real output starts.
    has_tab: bool = False
    cand: tuple[str, str] | None = None
    cand_t: float = 0.0


_TAB_SETTLE_SEC = 1.2


@dataclass
class CommandTracker:
    """Per-session: device input row + FIFO of Enters waiting for their echo."""

    linux_like: bool = False
    expire_sec: float = 3.0
    max_command: int = 512
    screen: LineScreen = field(default_factory=LineScreen)
    lock: threading.Lock = field(default_factory=threading.Lock)
    pending: deque[_Pending] = field(default_factory=deque)
    prompt_hint: str = ""
    line_started: bool = False
    password_mode: bool = False
    # Rows abandoned with Ctrl-C: some devices (IOS, bash) echo that row + CRLF, and that
    # line feed must not be mistaken for the echo of the next Enter.
    abandoned: list[tuple[str, float]] = field(default_factory=list)
    _typed: str = ""

    # ----- device output -------------------------------------------------- #
    def feed(self, text: str) -> list[AuditCmd]:
        """Feed decoded device output; returns commands resolved by line-feed echoes."""
        out: list[AuditCmd] = []
        with self.lock:
            scr = self.screen
            text = scr.take_carry(text)
            if not text:
                return out
            while text:
                nl = text.find("\n")
                if nl < 0:
                    scr.feed_row(text)
                    break
                self.line_started = False
                if self.pending:
                    scr.feed_row(text[:nl])
                    self._resolve_one(out)
                    scr.reset_row()
                    text = text[nl + 1 :]
                    continue
                # Nothing waiting: only the last row matters — skip the bulk.
                last = text.rfind("\n")
                scr.reset_row()
                text = text[last + 1 :]
                scr.feed_row(text)
                break
            self.password_mode = looks_like_password_row(scr.text())
            if self.line_started and not scr.text():
                # Row wiped by the device (screen clear / new prompt): start over.
                self.line_started = False
        return out

    def _resolve_one(self, out: list[AuditCmd]) -> None:
        row = self.screen.text()
        p = self.pending[0]
        if self.abandoned and not p.bare:
            now = time.monotonic()
            self.abandoned = [(r, t) for r, t in self.abandoned if now - t < 5.0]
            typed = p.typed.replace("\t", "").strip()
            for i, (ab, _t) in enumerate(self.abandoned):
                # The device reprints the aborted row; ``startswith`` covers a slow echo
                # (row snapshot was taken before every typed char came back). A VRP that
                # keeps the text yields ``garbage`` + our command, which contains ``typed``.
                if row == ab or (row.startswith(ab) and typed and typed not in row):
                    del self.abandoned[i]
                    return  # echo of the aborted line, not our Enter
        parsed = split_prompt(row, p.prompt_hint, linux_like=self.linux_like)
        has_cmd = bool(parsed and parsed[1])
        if p.bare:
            # A bare Enter (empty line, or executing a recalled history line) is echoed by
            # the first *prompt* row's line feed; output rows of a command that is still
            # printing must not consume it (Enter typed ahead during output).
            if parsed is None:
                return
            self.pending.popleft()
            if has_cmd:
                out.append(self._make(parsed[0], parsed[1], p.source, "device_echo"))  # type: ignore[index]
                # Enter pressed at a pager left an orphan bare Enter in front of the next
                # typed line: that line feed belongs to both — do not record it twice.
                nxt = self.pending[0] if self.pending else None
                if nxt is not None and not nxt.bare:
                    typed = nxt.typed.replace("\t", "").replace(" ", "").lower()
                    if typed and parsed[1].replace(" ", "").lower().startswith(typed[:3]):
                        self.pending.popleft()
            return
        if p.has_tab:
            if has_cmd:
                p.cand = (parsed[0], parsed[1])  # type: ignore[index]
                p.cand_t = time.monotonic()
            elif p.cand is not None:
                # Prompt / output row after the echo: the last candidate is the executed line.
                self._finish_candidate(p, out)
            return
        if not has_cmd:
            # Prompt-only / non-CLI row: earlier output, a prompt reprint — not our echo.
            return
        self.pending.popleft()
        out.append(self._make(parsed[0], parsed[1], p.source, "device_echo"))  # type: ignore[index]

    def _finish_candidate(self, p: _Pending, out: list[AuditCmd]) -> None:
        if self.pending and self.pending[0] is p:
            self.pending.popleft()
        if p.cand is not None:
            out.append(self._make(p.cand[0], p.cand[1], p.source, "device_echo"))
            p.cand = None

    def _make(self, prompt: str, cmd: str, source: str, via: str) -> AuditCmd:
        cmd = cmd[: self.max_command]
        base = prompt.rstrip()
        # Keep one space after user@host style prompts; network CLIs print none.
        sep = " " if (len(prompt) > len(base) and (self.linux_like or "@" in base)) else ""
        return AuditCmd(
            command=(base + sep + cmd)[: self.max_command],
            prompt=base,
            input=cmd,
            source=source,
            via=via,
        )

    def expire(self, now: float | None = None) -> list[AuditCmd]:
        """Settle Tab candidates and resolve Enters whose echo never arrived."""
        out: list[AuditCmd] = []
        if not self.pending:
            return out
        t = time.monotonic() if now is None else now
        with self.lock:
            while self.pending:
                p = self.pending[0]
                if p.cand is not None and (t - p.cand_t) >= _TAB_SETTLE_SEC:
                    self._finish_candidate(p, out)
                    continue
                if (t - p.t) >= self.expire_sec:
                    self.pending.popleft()
                    cmd = self._fallback(p)
                    if cmd is not None:
                        out.append(cmd)
                    continue
                break
        return out

    def _fallback(self, p: _Pending) -> AuditCmd | None:
        parsed = split_prompt(p.snapshot, p.prompt_hint, linux_like=self.linux_like)
        if parsed and parsed[1]:
            return self._make(parsed[0], parsed[1], p.source, "snapshot")
        for hint in reversed(p.client_hints):
            parsed = split_prompt(hint, p.prompt_hint, linux_like=self.linux_like)
            if parsed and parsed[1]:
                return self._make(parsed[0], parsed[1], p.source, "client_hint")
        # Last resort: what the operator typed, under the prompt that was on screen.
        typed = p.typed.replace("\t", "").strip()
        if typed and p.prompt_hint:
            return self._make(p.prompt_hint, typed, p.source, "typed")
        return None

    def flush_all(self) -> list[AuditCmd]:
        """Session closing: resolve every still-pending Enter with the best fallback."""
        out: list[AuditCmd] = []
        with self.lock:
            while self.pending:
                p = self.pending.popleft()
                if p.cand is not None:
                    out.append(self._make(p.cand[0], p.cand[1], p.source, "device_echo"))
                    continue
                cmd = self._fallback(p)
                if cmd is not None:
                    out.append(cmd)
        return out

    # ----- operator input -------------------------------------------------- #
    def on_stdin(
        self,
        lines: list[str],
        *,
        saw_input: bool,
        had_enter: bool,
        client_hints: list[str] | None = None,
        source: str = "stdin",
        now: float | None = None,
        abort: bool = False,
        abort_text: str = "",
    ) -> list[AuditCmd]:
        """Register keystrokes already split into completed ``lines`` by the caller.

        ``saw_input``: payload had anything besides bare Enter (captures the prompt).
        ``had_enter``: payload contained at least one line terminator.
        Returns immediately-final records (password redaction); echo-resolved ones
        come from ``feed``.
        """
        out: list[AuditCmd] = []
        t = time.monotonic() if now is None else now
        with self.lock:
            row = self.screen.text()
            if abort:
                parsed_row = split_prompt(row, self.prompt_hint, linux_like=self.linux_like)
                if parsed_row and parsed_row[1]:
                    self.abandoned.append((row, t))
                # What we sent before the Ctrl-C may not have been echoed yet.
                if abort_text.strip() and self.prompt_hint:
                    self.abandoned.append((self.prompt_hint + abort_text.strip(), t))
                del self.abandoned[:-6]
            if saw_input and not self.line_started:
                # First keystroke of a line: the row is still just the prompt. Prefer the
                # regex-detected prompt so typed echo never leaks into the hint.
                if row and not looks_like_password_row(row):
                    known = split_prompt(row, "", linux_like=self.linux_like)
                    if known:
                        self.prompt_hint = known[0]
                    elif row[-1] in "#>$%]":
                        self.prompt_hint = row  # exotic CLI prompt
                    else:
                        # "Username:" / "Continue? [y/n]:" … a dialog, not a CLI prompt.
                        self.prompt_hint = ""
                self.line_started = True
            if not had_enter:
                return out
            hints = [h for h in (client_hints or []) if h]
            if self.password_mode or looks_like_password_row(row):
                # Typed secret: never keep it, record a redacted marker.
                n = max(1, len(lines) or 1)
                out.extend(AuditCmd(command="***", redacted=True, source=source, via="password") for _ in range(n))
                self.password_mode = False
                self.line_started = False
                return out
            # No completed text: a bare Enter, possibly executing a recalled history line
            # whose redraw has not reached us yet.
            queue_lines = list(lines) or [""]
            for i, typed in enumerate(queue_lines):
                self.pending.append(
                    _Pending(
                        t=t,
                        typed=typed,
                        snapshot=row if i == 0 else "",
                        prompt_hint=self.prompt_hint,
                        client_hints=hints[-1:] if i == len(queue_lines) - 1 else [],
                        source=source,
                        bare=not typed,
                        has_tab="\t" in typed,
                    )
                )
            self.line_started = False
            # Bound runaway growth (device never echoing).
            while len(self.pending) > 64:
                self.pending.popleft()
        return out

    def immediate(self, lines: list[str], source: str) -> list[AuditCmd]:
        """Trusted non-interactive input (post-login commands): record as typed."""
        return [
            AuditCmd(command=ln[: self.max_command], input=ln[: self.max_command], source=source, via="trusted")
            for ln in lines
            if ln.strip()
        ]

    def current_row(self) -> str:
        with self.lock:
            return self.screen.text()

    def seed(self, text: str) -> None:
        """Prime the row from the login transcript (prompt printed before the reader)."""
        self.feed(text)

    def set_row(self, text: str) -> None:
        """Force the current input row (e.g. after a duplicate prompt was dropped)."""
        with self.lock:
            self.screen.reset_row()
            self.screen._carry = ""
            self.screen.feed_row(text)
            self.line_started = False
