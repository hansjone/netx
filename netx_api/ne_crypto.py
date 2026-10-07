from __future__ import annotations

import logging
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .config import settings

_log = logging.getLogger("netx.crypto")
_cached_credential_key: str | None = None


class CredentialCryptoError(RuntimeError):
    pass


def credential_secret_file_path() -> Path:
    raw = str(getattr(settings, "credential_secret_file", None) or "data/auth/credential_secret").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def ensure_credential_secret_key() -> str:
    """Resolve Fernet key: env ``NETX_CREDENTIAL_SECRET_KEY`` > file > generate once.

    Packaged installs should also write the key into ``.env`` via setup_first_run;
    this startup/path fallback covers upgrades and missed first-run keys.
    """
    global _cached_credential_key
    configured = str(settings.credential_secret_key or "").strip()
    if configured:
        return configured
    if _cached_credential_key:
        return _cached_credential_key

    path = credential_secret_file_path()
    try:
        if path.is_file():
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                _cached_credential_key = existing
                settings.credential_secret_key = existing
                return existing
    except Exception:
        _log.exception("read credential secret file failed path=%s", path)

    generated = Fernet.generate_key().decode("ascii")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(generated + "\n", encoding="utf-8")
        try:
            path.chmod(0o600)
        except Exception:
            pass
        _log.info("wrote per-install credential Fernet key to %s", path)
    except Exception:
        _log.exception(
            "write credential secret file failed path=%s; using in-memory key only",
            path,
        )
    _cached_credential_key = generated
    settings.credential_secret_key = generated
    return generated


def _fernet() -> Fernet:
    key = ensure_credential_secret_key()
    try:
        return Fernet(key.encode("ascii"))
    except Exception as exc:
        raise CredentialCryptoError("credential_secret_key_invalid") from exc


def encrypt_secret(value: str) -> str:
    plain = str(value or "")
    if not plain:
        return ""
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    enc = str(value or "").strip()
    if not enc:
        return ""
    try:
        return _fernet().decrypt(enc.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise CredentialCryptoError("credential_decrypt_failed") from exc


def credentials_configured() -> bool:
    try:
        return bool(ensure_credential_secret_key())
    except Exception:
        return False
