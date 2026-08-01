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
    """Yield ``(sftp, device)`` — prefers a short-lived channel on the live SSH session."""
    creds, device = _resolve(db, managed_ne_id=managed_ne_id, ume_ne_id=ume_ne_id)
    ne_key = str(device.get("id") or managed_ne_id or ume_ne_id or "").strip()
    sess = find_ssh_session_for_ne(ne_key) if ne_key else None
    if sess is not None:
        sftp = None
        try:
            sftp = sess.open_ephemeral_sftp()
            yield sftp, device
            return
        except HTTPException:
            raise
        except Exception as exc:
            if sftp is not None:
                raise HTTPException(status_code=502, detail=f"sftp_failed:{exc}") from exc
            _log.debug("session sftp open failed ne=%s: %s — pool fallback", ne_key, exc)
        finally:
            if sftp is not None:
                try:
                    sftp.close()
                except Exception:
                    pass

    key = _pool_key(managed_ne_id=managed_ne_id, ume_ne_id=ume_ne_id)
    entry = _get_pooled(key, creds)
    # Borrow transport under a short lock, then use a dedicated SFTP channel for I/O.
    with entry.lock:
        entry.last_used = time.time()
        transport = None
        try:
            transport = entry.client.get_transport()
        except Exception:
            transport = None
        if transport is None or not bool(getattr(transport, "is_active", lambda: False)()):
            with _pool_lock:
                cur = _pool.pop(key, None)
            if cur is not None:
                _close_pooled(cur)
            raise HTTPException(status_code=502, detail="sftp_pool_transport_unavailable")
    sftp = None
    try:
        sftp = paramiko.SFTPClient.from_transport(transport)
        if sftp is None:
            raise HTTPException(status_code=502, detail="sftp_open_failed")
        yield sftp, device
    except HTTPException:
        raise
    except Exception:
        with _pool_lock:
            cur = _pool.pop(key, None)
        if cur is not None:
            _close_pooled(cur)
        raise
    finally:
        if sftp is not None:
            try:
                sftp.close()
            except Exception:
                pass


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


def _rmtree(sftp: Any, remote: str) -> None:
    path = _normalize_remote(remote, allow_dot=False)
    if not path or path in (".", "/"):
        raise HTTPException(status_code=400, detail="sftp_path_required")
    try:
        for attr in sftp.listdir_attr(path):
            name = str(getattr(attr, "filename", "") or "")
            if not name or name in (".", ".."):
                continue
            child = posixpath.join(path, name)
            mode = int(getattr(attr, "st_mode", 0) or 0)
            if statmod.S_ISDIR(mode):
                _rmtree(sftp, child)
            else:
                sftp.remove(child)
        sftp.rmdir(path)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"sftp_remove_failed:{exc}") from exc


def sftp_mkdir(
    db: Session,
    *,
    managed_ne_id: str | None,
    ume_ne_id: str | None,
    path: str,
) -> dict[str, Any]:
    remote = _normalize_remote(path, allow_dot=False)
    if not remote or remote in (".", "/"):
        raise HTTPException(status_code=400, detail="sftp_path_required")
    try:
        with _sftp_client(db, managed_ne_id=managed_ne_id, ume_ne_id=ume_ne_id) as (sftp, device):
            _mkdir_p(sftp, remote)
            return {
                "ok": True,
                "ne_id": str(device.get("id") or ""),
                "path": remote,
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"sftp_mkdir_failed:{exc}") from exc


def sftp_remove(
    db: Session,
    *,
    managed_ne_id: str | None,
    ume_ne_id: str | None,
    path: str,
    recursive: bool = False,
) -> dict[str, Any]:
    remote = _normalize_remote(path, allow_dot=False)
    if not remote or remote in (".", "/"):
        raise HTTPException(status_code=400, detail="sftp_path_required")
    try:
        with _sftp_client(db, managed_ne_id=managed_ne_id, ume_ne_id=ume_ne_id) as (sftp, device):
            try:
                st = sftp.stat(remote)
            except Exception as exc:
                raise HTTPException(status_code=404, detail=f"sftp_not_found:{exc}") from exc
            mode = int(getattr(st, "st_mode", 0) or 0)
            if statmod.S_ISDIR(mode):
                if recursive:
                    _rmtree(sftp, remote)
                else:
                    try:
                        sftp.rmdir(remote)
                    except Exception as exc:
                        raise HTTPException(status_code=409, detail="sftp_dir_not_empty") from exc
            else:
                sftp.remove(remote)
            return {
                "ok": True,
                "ne_id": str(device.get("id") or ""),
                "path": remote,
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"sftp_remove_failed:{exc}") from exc


def sftp_rename(
    db: Session,
    *,
    managed_ne_id: str | None,
    ume_ne_id: str | None,
    old_path: str,
    new_path: str,
) -> dict[str, Any]:
    src = _normalize_remote(old_path, allow_dot=False)
    dst = _normalize_remote(new_path, allow_dot=False)
    if not src or not dst or src in (".", "/") or dst in (".", "/"):
        raise HTTPException(status_code=400, detail="sftp_path_required")
    if src == dst:
        return {"ok": True, "ne_id": "", "old_path": src, "new_path": dst}
    try:
        with _sftp_client(db, managed_ne_id=managed_ne_id, ume_ne_id=ume_ne_id) as (sftp, device):
            parent = posixpath.dirname(dst)
            if parent and parent not in (".", "/"):
                _mkdir_p(sftp, parent)
            try:
                sftp.rename(src, dst)
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"sftp_rename_failed:{exc}") from exc
            return {
                "ok": True,
                "ne_id": str(device.get("id") or ""),
                "old_path": src,
                "new_path": dst,
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"sftp_rename_failed:{exc}") from exc


def _parse_chmod_mode(mode: str | int) -> int:
    if isinstance(mode, int):
        return int(mode) & 0o7777
    raw = str(mode or "").strip().lower()
    if not raw:
        raise HTTPException(status_code=400, detail="sftp_chmod_invalid_mode")
    if raw.startswith("0o"):
        raw = raw[2:]
    if raw.isdigit():
        try:
            return int(raw, 8) & 0o7777
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="sftp_chmod_invalid_mode") from exc
    sym = raw.lstrip("d").lstrip("-")
    if len(sym) != 9 or any(c not in "rwx-" for c in sym):
        raise HTTPException(status_code=400, detail="sftp_chmod_invalid_mode")
    bits = {"r": 4, "w": 2, "x": 1, "-": 0}
    value = 0
    for i in range(3):
        trip = sym[i * 3 : (i + 1) * 3]
        value = (value << 3) | (bits[trip[0]] + bits[trip[1]] + bits[trip[2]])
    return value & 0o7777


def sftp_chmod(
    db: Session,
    *,
    managed_ne_id: str | None,
    ume_ne_id: str | None,
    path: str,
    mode: str | int,
) -> dict[str, Any]:
    remote = _normalize_remote(path, allow_dot=False)
    if not remote or remote in (".", "/"):
        raise HTTPException(status_code=400, detail="sftp_path_required")
    mode_int = _parse_chmod_mode(mode)
    try:
        with _sftp_client(db, managed_ne_id=managed_ne_id, ume_ne_id=ume_ne_id) as (sftp, device):
            try:
                sftp.chmod(remote, mode_int)
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"sftp_chmod_failed:{exc}") from exc
            return {
                "ok": True,
                "ne_id": str(device.get("id") or ""),
                "path": remote,
                "mode": f"{mode_int:04o}",
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"sftp_chmod_failed:{exc}") from exc


def _list_max_entries() -> int:
    return max(100, int(settings.webcrt_sftp_list_max_entries or 5000))


def _list_timeout_sec() -> float:
    return max(1.0, float(settings.webcrt_sftp_list_timeout_sec or 30.0))


def sftp_list(
    db: Session,
    *,
    managed_ne_id: str | None,
    ume_ne_id: str | None,
    path: str = ".",
) -> dict[str, Any]:
    remote = _normalize_remote(path, allow_dot=True) or "."
    max_entries = _list_max_entries()
    timeout_sec = _list_timeout_sec()
    try:
        with _sftp_client(db, managed_ne_id=managed_ne_id, ume_ne_id=ume_ne_id) as (sftp, device):
            entries = []
            truncated = False
            deadline = time.monotonic() + timeout_sec
            for attr in sftp.listdir_attr(remote):
                if time.monotonic() > deadline:
                    truncated = True
                    break
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
                if len(entries) >= max_entries:
                    truncated = True
                    break
            entries.sort(key=lambda x: (not x["is_dir"], str(x["name"]).lower()))
            return {
                "ne_id": str(device.get("id") or ""),
                "ne_name": str(device.get("name") or ""),
                "path": remote,
                "items": entries,
                "truncated": truncated,
                "max_entries": max_entries,
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
