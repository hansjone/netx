"""WebCRT SFTP helpers — prefer the live SSH session channel; pool only as fallback."""

from __future__ import annotations

import logging
import posixpath
import stat as statmod
import threading
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from typing import Any, BinaryIO, Iterator
from urllib.parse import quote

import paramiko
from fastapi import HTTPException
from sqlalchemy.orm import Session

from .cli_resolve import resolve_cli_target
from .config import settings
from .ne_crypto import CredentialCryptoError
from .webcrt_service import (
    _webcrt_creds_ready,
    find_ssh_session_for_ne,
)

_log = logging.getLogger("netx.webcrt.sftp")

_pool_lock = threading.Lock()
_pool: dict[str, "_PooledSftp"] = {}
_POOL_IDLE_SEC = 180


@dataclass
class _PooledSftp:
    key: str
    client: paramiko.SSHClient
    sftp: paramiko.SFTPClient
    last_used: float = field(default_factory=time.time)
    lock: threading.RLock = field(default_factory=threading.RLock)


def _require_ssh_direct(creds: dict[str, Any], device: dict[str, Any]) -> None:
    protocol = str(device.get("protocol") or creds.get("protocol") or "ssh").lower()
    if protocol != "ssh":
        raise HTTPException(status_code=400, detail="sftp_requires_ssh")
    if creds.get("hop_enabled"):
        raise HTTPException(status_code=400, detail="sftp_hop_not_supported")


def _pool_key(*, managed_ne_id: str | None, ume_ne_id: str | None) -> str:
    mid = str(managed_ne_id or "").strip()
    uid = str(ume_ne_id or "").strip()
    if mid:
        return f"m:{mid}"
    return f"u:{uid}"


def _close_pooled(entry: _PooledSftp) -> None:
    try:
        entry.sftp.close()
    except Exception:
        pass
    try:
        entry.client.close()
    except Exception:
        pass


def _reap_pool_unlocked(now: float | None = None) -> None:
    ts = float(now or time.time())
    dead = [k for k, e in _pool.items() if (ts - e.last_used) > _POOL_IDLE_SEC]
    for k in dead:
        entry = _pool.pop(k, None)
        if entry is not None:
            _close_pooled(entry)


def _open_pooled_sftp(creds: dict[str, Any]) -> tuple[paramiko.SSHClient, paramiko.SFTPClient]:
    timeout = int(settings.ne_connect_timeout_sec or 30)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            str(creds["ip_address"]),
            port=int(creds.get("port") or 22),
            username=str(creds["username"]),
            password=str(creds["password"]),
            timeout=timeout,
            banner_timeout=timeout,
            auth_timeout=timeout,
            look_for_keys=False,
            allow_agent=False,
        )
        sftp = client.open_sftp()
        return client, sftp
    except Exception as exc:
        try:
            client.close()
        except Exception:
            pass
        raise HTTPException(status_code=502, detail=f"sftp_connect_failed:{exc}") from exc


def _get_pooled(key: str, creds: dict[str, Any]) -> _PooledSftp:
    with _pool_lock:
        _reap_pool_unlocked()
        entry = _pool.get(key)
        if entry is not None:
            sock = getattr(entry.sftp, "sock", None)
            if sock is not None and not bool(getattr(sock, "closed", False)):
                entry.last_used = time.time()
                return entry
            _pool.pop(key, None)
            _close_pooled(entry)
        client, sftp = _open_pooled_sftp(creds)
        entry = _PooledSftp(key=key, client=client, sftp=sftp)
        _pool[key] = entry
        return entry


def _resolve(db: Session, *, managed_ne_id: str | None, ume_ne_id: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        creds, device = resolve_cli_target(db, managed_ne_id=managed_ne_id, ume_ne_id=ume_ne_id)
    except HTTPException:
        raise
    except CredentialCryptoError as exc:
        raise HTTPException(status_code=400, detail=str(exc) or "credential_crypto_error") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"credential_error:{exc}") from exc
    if not _webcrt_creds_ready(creds):
        raise HTTPException(status_code=400, detail="credentials_incomplete")
    _require_ssh_direct(creds, device)
    return creds, device


def _normalize_remote(path: str, *, allow_dot: bool = True) -> str:
    remote = str(path or "").strip() or ("." if allow_dot else "")
    if not remote:
        return remote
    if remote not in (".", "/"):
        remote = posixpath.normpath(remote.replace("\\", "/")) or ("." if allow_dot else "")
    return remote


@contextmanager
def _sftp_client(
    db: Session,
    *,
    managed_ne_id: str | None,
    ume_ne_id: str | None,
) -> Iterator[tuple[Any, dict[str, Any]]]:
    """Yield ``(sftp, device)`` — prefers live WebCRT SSH session channel."""
    creds, device = _resolve(db, managed_ne_id=managed_ne_id, ume_ne_id=ume_ne_id)
    ne_key = str(device.get("id") or managed_ne_id or ume_ne_id or "").strip()
    sess = find_ssh_session_for_ne(ne_key) if ne_key else None
    if sess is not None:
        opened = False
        try:
            with sess._sftp_lock:
                sftp = sess._ensure_sftp_unlocked()
                opened = True
                yield sftp, device
            return
        except HTTPException:
            raise
        except Exception as exc:
            if opened:
                # Operation failed on an already-open session channel — don't double-yield.
                raise HTTPException(status_code=502, detail=f"sftp_failed:{exc}") from exc
            _log.debug("session sftp open failed ne=%s: %s — pool fallback", ne_key, exc)

    key = _pool_key(managed_ne_id=managed_ne_id, ume_ne_id=ume_ne_id)
    entry = _get_pooled(key, creds)
    with entry.lock:
        entry.last_used = time.time()
        try:
            yield entry.sftp, device
        except Exception:
            # Drop broken pooled socket so the next call reconnects once.
            with _pool_lock:
                cur = _pool.pop(key, None)
            if cur is not None:
                _close_pooled(cur)
            raise


def _filemode(mode: int) -> str:
    try:
        return str(statmod.filemode(int(mode)))
    except Exception:
        return ""


def _owner_group(attr: Any) -> tuple[str, str]:
    longname = str(getattr(attr, "longname", "") or "").strip()
    parts = longname.split()
    if len(parts) >= 4 and parts[0][:1] in ("-", "d", "l", "c", "b", "p", "s"):
        return str(parts[2]), str(parts[3])
    uid = getattr(attr, "st_uid", None)
    gid = getattr(attr, "st_gid", None)
    return ("" if uid is None else str(uid), "" if gid is None else str(gid))


def _mkdir_p(sftp: Any, remote: str) -> None:
    """Create remote directory and parents (like mkdir -p)."""
    path = _normalize_remote(remote, allow_dot=False)
    if not path or path in (".", "/"):
        return
    to_create: list[str] = []
    cur = path
    while cur and cur not in (".", "/"):
        to_create.append(cur)
        parent = posixpath.dirname(cur)
        if parent == cur:
            break
        cur = parent
    for candidate in reversed(to_create):
        try:
            st = sftp.stat(candidate)
            mode = int(getattr(st, "st_mode", 0) or 0)
            if not statmod.S_ISDIR(mode):
                raise HTTPException(status_code=400, detail=f"sftp_not_a_directory:{candidate}")
            continue
        except HTTPException:
            raise
        except Exception:
            pass
        try:
            sftp.mkdir(candidate)
        except Exception as exc:
            # Race: another client created it; accept if it is now a directory.
            try:
                st = sftp.stat(candidate)
                if statmod.S_ISDIR(int(getattr(st, "st_mode", 0) or 0)):
                    continue
            except Exception:
                pass
            raise HTTPException(status_code=502, detail=f"sftp_mkdir_failed:{candidate}:{exc}") from exc


def sftp_list(
    db: Session,
    *,
    managed_ne_id: str | None,
    ume_ne_id: str | None,
    path: str = ".",
) -> dict[str, Any]:
    remote = _normalize_remote(path, allow_dot=True) or "."
    try:
        with _sftp_client(db, managed_ne_id=managed_ne_id, ume_ne_id=ume_ne_id) as (sftp, device):
            entries = []
            for attr in sftp.listdir_attr(remote):
                mode = int(getattr(attr, "st_mode", 0) or 0)
                name = str(attr.filename or "")
                if not name or name in (".", ".."):
                    continue
                owner, group = _owner_group(attr)
                entries.append(
                    {
                        "name": name,
                        "size": int(getattr(attr, "st_size", 0) or 0),
                        "mtime": int(getattr(attr, "st_mtime", 0) or 0),
                        "is_dir": bool(statmod.S_ISDIR(mode)),
                        "mode": _filemode(mode),
                        "owner": owner,
                        "group": group,
                        "uid": int(getattr(attr, "st_uid", 0) or 0),
                        "gid": int(getattr(attr, "st_gid", 0) or 0),
                    }
                )
            entries.sort(key=lambda x: (not x["is_dir"], str(x["name"]).lower()))
            return {
                "ne_id": str(device.get("id") or ""),
                "ne_name": str(device.get("name") or ""),
                "path": remote,
                "items": entries,
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"sftp_list_failed:{exc}") from exc


def _sftp_max_file_bytes() -> int:
    return max(1, int(settings.webcrt_sftp_max_file_bytes or (512 * 1024 * 1024)))


def _sftp_chunk_bytes() -> int:
    return max(4 * 1024, int(settings.webcrt_sftp_chunk_bytes or (64 * 1024)))


def _content_disposition(filename: str) -> str:
    name = str(filename or "download.bin").replace('"', "").replace("\r", "").replace("\n", "")
    if not name:
        name = "download.bin"
    ascii_name = name.encode("ascii", "replace").decode("ascii") or "download.bin"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


class SftpDownloadStream:
    """Hold the SFTP channel open while StreamingResponse consumes chunks."""

    def __init__(
        self,
        db: Session,
        *,
        managed_ne_id: str | None,
        ume_ne_id: str | None,
        path: str,
    ) -> None:
        self._db = db
        self._managed_ne_id = managed_ne_id
        self._ume_ne_id = ume_ne_id
        self.remote = _normalize_remote(path, allow_dot=False)
        self.filename = "download.bin"
        self.size = 0
        self._stack: ExitStack | None = None
        self._fh: Any = None
        self._chunk = _sftp_chunk_bytes()

    def open(self) -> "SftpDownloadStream":
        if not self.remote or self.remote.endswith("/"):
            raise HTTPException(status_code=400, detail="sftp_path_required")
        stack = ExitStack()
        try:
            sftp, _device = stack.enter_context(
                _sftp_client(self._db, managed_ne_id=self._managed_ne_id, ume_ne_id=self._ume_ne_id)
            )
            try:
                st = sftp.stat(self.remote)
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"sftp_download_failed:{exc}") from exc
            mode = int(getattr(st, "st_mode", 0) or 0)
            if statmod.S_ISDIR(mode):
                raise HTTPException(status_code=400, detail="sftp_path_is_directory")
            size = int(getattr(st, "st_size", 0) or 0)
            if size > _sftp_max_file_bytes():
                raise HTTPException(status_code=413, detail="sftp_file_too_large")
            self.size = max(0, size)
            self.filename = posixpath.basename(self.remote) or "download.bin"
            try:
                self._fh = stack.enter_context(sftp.open(self.remote, "rb"))
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"sftp_download_failed:{exc}") from exc
        except Exception:
            stack.close()
            raise
        self._stack = stack
        return self

    def __iter__(self) -> Iterator[bytes]:
        fh = self._fh
        if fh is None:
            return
        try:
            while True:
                chunk = fh.read(self._chunk)
                if not chunk:
                    break
                yield chunk
        finally:
            self.close()

    def close(self) -> None:
        stack = self._stack
        self._stack = None
        self._fh = None
        if stack is not None:
            try:
                stack.close()
            except Exception:
                pass

    def content_disposition(self) -> str:
        return _content_disposition(self.filename)


def sftp_upload_stream(
    db: Session,
    *,
    managed_ne_id: str | None,
    ume_ne_id: str | None,
    remote_path: str,
    reader: BinaryIO,
    expected_size: int | None = None,
) -> dict[str, Any]:
    remote = _normalize_remote(remote_path, allow_dot=False)
    if not remote:
        raise HTTPException(status_code=400, detail="sftp_path_required")
    max_bytes = _sftp_max_file_bytes()
    if expected_size is not None and int(expected_size) > max_bytes:
        raise HTTPException(status_code=413, detail="sftp_file_too_large")
    chunk_size = _sftp_chunk_bytes()
    try:
        with _sftp_client(db, managed_ne_id=managed_ne_id, ume_ne_id=ume_ne_id) as (sftp, device):
            parent = posixpath.dirname(remote)
            if parent and parent not in (".", "/"):
                _mkdir_p(sftp, parent)
            written = 0
            try:
                with sftp.open(remote, "wb") as fh:
                    while True:
                        buf = reader.read(chunk_size)
                        if not buf:
                            break
                        written += len(buf)
                        if written > max_bytes:
                            raise HTTPException(status_code=413, detail="sftp_file_too_large")
                        fh.write(buf)
            except HTTPException:
                try:
                    sftp.remove(remote)
                except Exception:
                    pass
                raise
            return {
                "ok": True,
                "ne_id": str(device.get("id") or ""),
                "path": remote,
                "size": written,
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"sftp_upload_failed:{exc}") from exc


def sftp_upload(
    db: Session,
    *,
    managed_ne_id: str | None,
    ume_ne_id: str | None,
    remote_path: str,
    data: bytes,
) -> dict[str, Any]:
    """Compatibility helper for tests — wraps streamed upload over an in-memory buffer."""
    import io

    return sftp_upload_stream(
        db,
        managed_ne_id=managed_ne_id,
        ume_ne_id=ume_ne_id,
        remote_path=remote_path,
        reader=io.BytesIO(data or b""),
        expected_size=len(data or b""),
    )
