from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from .config import settings


class CredentialCryptoError(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = str(settings.credential_secret_key or "").strip()
    if not key:
        raise CredentialCryptoError("credential_secret_key_not_configured")
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
    return bool(str(settings.credential_secret_key or "").strip())
