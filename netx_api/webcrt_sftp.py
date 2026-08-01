"""Lightweight SFTP helpers for WebCRT (SSH targets only; separate from interactive PTY)."""

from __future__ import annotations

import logging
import posixpath
from typing import Any

import paramiko
from fastapi import HTTPException
from sqlalchemy.orm import Session

from .cli_resolve import resolve_cli_target
from .config import settings
from .ne_crypto import CredentialCryptoError
from .webcrt_service import _webcrt_creds_ready

_log = logging.getLogger("netx.webcrt.sftp")


def _require_ssh_direct(creds: dict[str, Any], device: dict[str, Any]) -> None:
    protocol = str(device.get("protocol") or creds.get("protocol") or "ssh").lower()
    if protocol != "ssh":
        raise HTTPException(status_code=400, detail="sftp_requires_ssh")
    if creds.get("hop_enabled"):
        # Keep v1 simple: SFTP only for direct SSH (no hop/proxy jump).
        raise HTTPException(status_code=400, detail="sftp_hop_not_supported")


def _open_sftp(creds: dict[str, Any]) -> tuple[paramiko.SSHClient, paramiko.SFTPClient]:
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
    except HTTPException:
        try:
            client.close()
        except Exception:
            pass
        raise
    except Exception as exc:
        try:
            client.close()
        except Exception:
            pass
        raise HTTPException(status_code=502, detail=f"sftp_connect_failed:{exc}") from exc


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


def sftp_list(
    db: Session,
    *,
    managed_ne_id: str | None,
    ume_ne_id: str | None,
    path: str = ".",
) -> dict[str, Any]:
    creds, device = _resolve(db, managed_ne_id=managed_ne_id, ume_ne_id=ume_ne_id)
    remote = str(path or ".").strip() or "."
    client, sftp = _open_sftp(creds)
    try:
        entries = []
        for attr in sftp.listdir_attr(remote):
            mode = int(getattr(attr, "st_mode", 0) or 0)
            is_dir = bool(mode & 0o40000)
            entries.append(
                {
                    "name": attr.filename,
                    "size": int(getattr(attr, "st_size", 0) or 0),
                    "mtime": int(getattr(attr, "st_mtime", 0) or 0),
                    "is_dir": is_dir,
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
    finally:
        try:
            sftp.close()
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass


def sftp_download(
    db: Session,
    *,
    managed_ne_id: str | None,
    ume_ne_id: str | None,
    path: str,
) -> tuple[bytes, str]:
    creds, _device = _resolve(db, managed_ne_id=managed_ne_id, ume_ne_id=ume_ne_id)
    remote = str(path or "").strip()
    if not remote or remote.endswith("/"):
        raise HTTPException(status_code=400, detail="sftp_path_required")
    client, sftp = _open_sftp(creds)
    try:
        with sftp.open(remote, "rb") as fh:
            data = fh.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="sftp_file_too_large")
        return data, posixpath.basename(remote) or "download.bin"
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"sftp_download_failed:{exc}") from exc
    finally:
        try:
            sftp.close()
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass


def sftp_upload(
    db: Session,
    *,
    managed_ne_id: str | None,
    ume_ne_id: str | None,
    remote_path: str,
    data: bytes,
) -> dict[str, Any]:
    creds, device = _resolve(db, managed_ne_id=managed_ne_id, ume_ne_id=ume_ne_id)
    remote = str(remote_path or "").strip()
    if not remote:
        raise HTTPException(status_code=400, detail="sftp_path_required")
    client, sftp = _open_sftp(creds)
    try:
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
    finally:
        try:
            sftp.close()
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass
