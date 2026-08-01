"""WebCRT SFTP helpers — prefer the live SSH session channel; pool only as fallback."""

from __future__ import annotations

import logging
import posixpath
import stat as statmod
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

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


def sftp_download(
    db: Session,
    *,
    managed_ne_id: str | None,
    ume_ne_id: str | None,
    path: str,
) -> tuple[bytes, str]:
    remote = _normalize_remote(path, allow_dot=False)
    if not remote or remote.endswith("/"):
        raise HTTPException(status_code=400, detail="sftp_path_required")
    try:
        with _sftp_client(db, managed_ne_id=managed_ne_id, ume_ne_id=ume_ne_id) as (sftp, _device):
            with sftp.open(remote, "rb") as fh:
                data = fh.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="sftp_file_too_large")
        return data, posixpath.basename(remote) or "download.bin"
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"sftp_download_failed:{exc}") from exc


def sftp_upload(
    db: Session,
    *,
    managed_ne_id: str | None,
    ume_ne_id: str | None,
    remote_path: str,
    data: bytes,
) -> dict[str, Any]:
    remote = _normalize_remote(remote_path, allow_dot=False)
    if not remote:
        raise HTTPException(status_code=400, detail="sftp_path_required")
    try:
        with _sftp_client(db, managed_ne_id=managed_ne_id, ume_ne_id=ume_ne_id) as (sftp, device):
            with sftp.open(remote, "wb") as fh:
                fh.write(data)
            return {
                "ok": True,
                "ne_id": str(device.get("id") or ""),
                "path": remote,
                "size": len(data),
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"sftp_upload_failed:{exc}") from exc
