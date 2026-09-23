"""Split device CLI transcript logs into show/display command segments."""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from ..config import settings

# Text-like extensions accepted from zip members / bare uploads.
_TEXT_SUFFIXES = {".txt", ".log", ".ini", ".cfg", ".cli", ".out", ".text"}


@dataclass(frozen=True)
class LogSegment:
    """One CLI command and its captured output body."""

    command: str
    body: str
    source_file: str = ""
    line_start: int = 0


def import_max_bytes() -> int:
    return max(
        1,
        int(getattr(settings, "biz_state_import_max_bytes", 256 * 1024 * 1024) or 0)
        or (256 * 1024 * 1024),
    )


def import_max_files() -> int:
    return max(1, int(getattr(settings, "biz_state_import_max_files", 200) or 200))


def normalize_log_text(text: str) -> str:
    """NBSP → space, unify newlines, strip trailing CR."""
    s = str(text or "").replace("\u00a0", " ").replace("\r\n", "\n").replace("\r", "\n")
    return s


def _anchor_re(vendor_key: str) -> re.Pattern[str]:
    key = str(vendor_key or "").strip().lower()
    if key.startswith("huawei") or key in ("vrp", "ce", "ne"):
        # Prefer display; also accept show (mixed dumps).
        return re.compile(r"(?im)^(?:display|show)\s+")
    if key.startswith("cisco") or key in ("ios", "nxos", "iosxe", "iosxr"):
        return re.compile(r"(?im)^(?:show)\s+")
    if key.startswith("zte") or key in ("zxros", "zxr10"):
        return re.compile(r"(?im)^(?:show)\s+")
    # Unknown / generic: both
    return re.compile(r"(?im)^(?:show|display)\s+")


_PROMPT_LINE_RE = re.compile(r"^[A-Za-z0-9._\-\[\]/]+[#>]\s*(.+)$")
_HW_PROMPT_LINE_RE = re.compile(r"^<[^>]+>\s*(.+)$")


def _strip_prompt_noise(line: str) -> str:
    """Drop hostname# / hostname> / <VRP> prefixes from a command line."""
    s = line.strip()
    if not s:
        return ""
    # Whole-line prompt alone
    if re.fullmatch(r"[A-Za-z0-9._\-\[\]/]+[#>]", s):
        return ""
    if re.fullmatch(r"<[^>]+>", s):
        return ""
    # "R1#show arp" → "show arp"
    m = _PROMPT_LINE_RE.match(s)
    if m:
        return m.group(1).strip()
    # "<HUAWEI>display ip routing-table"
    m = _HW_PROMPT_LINE_RE.match(s)
    if m:
        return m.group(1).strip()
    return s


def split_log_text(
    text: str,
    *,
    vendor_key: str = "",
    source_file: str = "",
) -> list[LogSegment]:
    """Split a CLI transcript into show/display segments.

    Handles real device pastes such as::

        MDN-BCP-CN1-ZM8SP#show arp | one-line
        ...
        MDN-BCP-CN1-ZM8SP#show interface brief
        ...

    Prompt prefixes (``host#`` / ``host>`` / ``<VRP>``) are stripped before
    matching; each new show/display line starts a new segment.

    Anything before the first show/display (banners, clocks, lone prompts) is
    discarded. If the whole text has no show/display, returns an empty list.
    """
    raw = normalize_log_text(text)
    if not raw.strip():
        return []
    anchor = _anchor_re(vendor_key)
    lines = raw.split("\n")
    starts: list[tuple[int, str]] = []  # (0-based line idx, command)
    for i, line in enumerate(lines):
        cleaned = _strip_prompt_noise(line)
        if not cleaned:
            continue
        if anchor.match(cleaned):
            # Command is the cleaned line (may include | filters)
            cmd = re.sub(r"\s+", " ", cleaned).strip()
            starts.append((i, cmd))

    if not starts:
        return []

    out: list[LogSegment] = []
    for idx, (line_i, cmd) in enumerate(starts):
        end = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        body_lines = lines[line_i + 1 : end]
        # Drop leading blank lines; keep rest (incl. prompts inside body — parsers skip them)
        while body_lines and not body_lines[0].strip():
            body_lines = body_lines[1:]
        # Trim trailing blank
        while body_lines and not body_lines[-1].strip():
            body_lines = body_lines[:-1]
        body = "\n".join(body_lines)
        out.append(
            LogSegment(
                command=cmd,
                body=body,
                source_file=str(source_file or ""),
                line_start=line_i + 1,
            )
        )
    return out


def _decode_bytes(data: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _is_text_member(name: str) -> bool:
    n = str(name or "").replace("\\", "/").strip()
    if not n or n.endswith("/"):
        return False
    base = Path(n).name
    if base.startswith(".") or base.startswith("__MACOSX"):
        return False
    suf = Path(base).suffix.lower()
    if suf in _TEXT_SUFFIXES:
        return True
    # Extensionless small dumps sometimes appear; allow if no suffix
    return suf == ""


def unpack_upload(
    *,
    filename: str,
    data: bytes,
    vendor_key: str = "",
) -> tuple[list[LogSegment], dict[str, int]]:
    """Unpack a bare text upload or zip into ordered LogSegments.

    Returns (segments, stats) where stats has files / bytes / segments counts.
    Raises ValueError on size / format / zip-bomb limits.
    """
    name = str(filename or "upload.bin").strip() or "upload.bin"
    blob = data or b""
    max_b = import_max_bytes()
    max_f = import_max_files()
    if len(blob) > max_b:
        raise ValueError(f"upload exceeds max size {max_b}B")

    lower = name.lower()
    segments: list[LogSegment] = []
    total_bytes = 0
    file_count = 0

    if lower.endswith(".zip"):
        try:
            zf = zipfile.ZipFile(io.BytesIO(blob))
        except zipfile.BadZipFile as exc:
            raise ValueError(f"invalid zip: {exc}") from exc
        with zf:
            members = [
                info
                for info in zf.infolist()
                if not info.is_dir() and _is_text_member(info.filename)
            ]
            if len(members) > max_f:
                raise ValueError(f"zip has too many text files (>{max_f})")
            for info in sorted(members, key=lambda x: x.filename.lower()):
                if info.file_size > max_b:
                    raise ValueError(
                        f"zip member {info.filename!r} exceeds max size {max_b}B"
                    )
                # Zip bomb: compressed ratio / total uncompressed
                total_bytes += int(info.file_size or 0)
                if total_bytes > max_b:
                    raise ValueError(f"zip uncompressed total exceeds max size {max_b}B")
                raw = zf.read(info)
                text = _decode_bytes(raw)
                file_count += 1
                segs = split_log_text(
                    text, vendor_key=vendor_key, source_file=info.filename
                )
                segments.extend(segs)
    else:
        total_bytes = len(blob)
        text = _decode_bytes(blob)
        file_count = 1
        segments = split_log_text(text, vendor_key=vendor_key, source_file=name)

    return segments, {
        "files": file_count,
        "bytes": total_bytes,
        "segments": len(segments),
    }


def read_upload_stream(fh: BinaryIO, *, max_bytes: int | None = None) -> bytes:
    """Read upload stream with a hard byte cap."""
    cap = int(max_bytes if max_bytes is not None else import_max_bytes())
    buf = fh.read(cap + 1)
    if buf is None:
        return b""
    if len(buf) > cap:
        raise ValueError(f"upload exceeds max size {cap}B")
    return buf
