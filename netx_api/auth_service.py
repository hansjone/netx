"""Auth domain service: bootstrap admin, users, API tokens, audit writes."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .auth_passwords import hash_password, verify_password
from .auth_scopes import (
    MCP_DEFAULT_SCOPES,
    effective_user_scopes,
    normalize_scopes,
)
from .auth_tokens import hash_api_token, issue_access_token, new_api_token_plaintext
from .config import settings
from .models import ApiToken, AppUser, AuditLog
from .timeutil import utcnow_naive

_log = logging.getLogger("netx.auth")

_USERNAME_RE = re.compile(r"^[A-Za-z0-9._@-]{2,64}$")
_SECRET_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "hop_password",
        "enable_secret",
        "access_token",
        "token",
        "authorization",
        "secret",
        "credential_secret_key",
    }
)


def user_public(user: AppUser) -> dict[str, Any]:
    scopes = sorted(
        effective_user_scopes(role=str(user.role or "user"), override=getattr(user, "scopes", None) or [])
    )
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "scopes": scopes,
        "scopes_override": normalize_scopes(getattr(user, "scopes", None) or []),
        "is_active": bool(user.is_active),
        "must_change_password": bool(getattr(user, "must_change_password", False)),
        "created_by": user.created_by or "",
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
    }


def sanitize_detail(detail: Any) -> Any:
    """Recursively drop secret-looking keys from audit detail payloads."""
    if isinstance(detail, dict):
        out: dict[str, Any] = {}
        for k, v in detail.items():
            key = str(k).lower()
            if key in _SECRET_KEYS or key.endswith("_password") or key.endswith("_secret"):
                out[k] = "***"
            else:
                out[k] = sanitize_detail(v)
        return out
    if isinstance(detail, list):
        return [sanitize_detail(x) for x in detail[:50]]
    if isinstance(detail, str) and len(detail) > 2000:
        return detail[:2000] + "…"
    return detail


def write_audit(
    db: Session,
    *,
    action: str,
    actor_user_id: str = "",
    actor_username: str = "",
    method: str = "",
    path: str = "",
    status_code: int = 0,
    client_ip: str = "",
    user_agent: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    row = AuditLog(
        actor_user_id=str(actor_user_id or ""),
        actor_username=str(actor_username or ""),
        action=str(action or "")[:128],
        method=str(method or "")[:16],
        path=str(path or "")[:512],
        status_code=int(status_code or 0),
        client_ip=str(client_ip or "")[:128],
        user_agent=str(user_agent or "")[:512],
        detail=sanitize_detail(detail or {}),
    )
    db.add(row)
    try:
        db.commit()
    except Exception:
        db.rollback()
        _log.exception("audit_log write failed action=%s", action)


def flag_default_password_users(db: Session) -> None:
    """Mark accounts still on the bootstrap default password as must_change_password."""
    default_pwd = str(settings.bootstrap_admin_password or "admin123").strip() or "admin123"
    changed = 0
    for user in db.query(AppUser).filter(AppUser.is_active.is_(True)).all():
        if bool(getattr(user, "must_change_password", False)):
            continue
        if verify_password(default_pwd, user.password_hash):
            user.must_change_password = True
            user.updated_at = utcnow_naive()
            changed += 1
    if changed:
        db.commit()
        _log.warning("flagged %s user(s) still using default password to must_change_password", changed)


def bootstrap_admin_if_needed(db: Session) -> None:
    """Create the first admin when app_user is empty."""
    count = int(db.query(func.count(AppUser.id)).scalar() or 0)
    if count > 0:
        flag_default_password_users(db)
        ensure_default_mcp_token(db)
        return
    username = str(settings.bootstrap_admin_username or "admin").strip() or "admin"
    password = str(settings.bootstrap_admin_password or "admin123").strip() or "admin123"
    if password == "admin123":
        _log.warning(
            "bootstrapping admin %r with default password admin123; change after first login",
            username,
        )
    if not _USERNAME_RE.match(username):
        raise RuntimeError(f"invalid_bootstrap_admin_username:{username}")
    user = AppUser(
        username=username,
        password_hash=hash_password(password),
        role="admin",
        is_active=True,
        must_change_password=True,
        created_by="bootstrap",
    )
    db.add(user)
    db.commit()
    write_audit(
        db,
        action="auth.bootstrap_admin",
        actor_user_id=user.id,
        actor_username=user.username,
        detail={"username": username, "must_change_password": True},
    )
    _log.info("bootstrapped admin user %r id=%s", username, user.id)
    ensure_default_mcp_token(db, user=user)


def mcp_token_file_path() -> Path:
    raw = str(settings.auth_mcp_token_file or "data/auth/mcp_token").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def ensure_default_mcp_token(db: Session, user: AppUser | None = None) -> str | None:
    """Ensure a default API token file exists for MCP (lab convenience).

    Returns plaintext token when created or when file already present; None on failure.
    """
    path = mcp_token_file_path()
    try:
        if path.is_file():
            existing = path.read_text(encoding="utf-8").strip()
            if existing.startswith("nxt_"):
                # Keep DB in sync if token was wiped from DB but file remains.
                th = hash_api_token(existing)
                row = (
                    db.query(ApiToken)
                    .filter(ApiToken.token_hash == th, ApiToken.revoked_at.is_(None))
                    .one_or_none()
                )
                if row is not None:
                    # Ensure MCP bootstrap token stays within MCP_DEFAULT_SCOPES.
                    desired = normalize_scopes(MCP_DEFAULT_SCOPES)
                    current = normalize_scopes(getattr(row, "scopes", None) or [])
                    if current != desired:
                        row.scopes = desired
                        try:
                            db.commit()
                        except Exception:
                            db.rollback()
                    return existing
    except Exception:
        _log.exception("read mcp token file failed path=%s", path)

    admin = user
    if admin is None:
        admin = (
            db.query(AppUser)
            .filter(AppUser.role == "admin", AppUser.is_active.is_(True))
            .order_by(AppUser.created_at.asc())
            .first()
        )
    if admin is None:
        return None
    try:
        row, plaintext = create_api_token(
            db,
            user=admin,
            name="mcp-default",
            expires_in_days=0,
            scopes=list(MCP_DEFAULT_SCOPES),
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(plaintext + "\n", encoding="utf-8")
        try:
            path.chmod(0o600)
        except Exception:
            pass
        write_audit(
            db,
            action="api_tokens.bootstrap_mcp",
            actor_user_id=admin.id,
            actor_username=admin.username,
            detail={"token_id": row.id, "name": row.name, "file": str(path)},
        )
        _log.info("wrote default MCP API token to %s", path)
        return plaintext
    except Exception:
        _log.exception("ensure_default_mcp_token failed")
        return None


def get_user_by_id(db: Session, user_id: str) -> AppUser | None:
    return db.query(AppUser).filter(AppUser.id == str(user_id or "")).one_or_none()


def get_user_by_username(db: Session, username: str) -> AppUser | None:
    return db.query(AppUser).filter(AppUser.username == str(username or "").strip()).one_or_none()


def authenticate_user(db: Session, username: str, password: str) -> AppUser | None:
    user = get_user_by_username(db, username)
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def login_issue_token(user: AppUser) -> dict[str, Any]:
    token = issue_access_token(user_id=user.id, username=user.username, role=user.role)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": user_public(user),
    }


def list_users(db: Session) -> list[dict[str, Any]]:
    rows = db.query(AppUser).order_by(AppUser.created_at.asc()).all()
    return [user_public(u) for u in rows]


def create_user(
    db: Session,
    *,
    username: str,
    password: str,
    role: str,
    actor: AppUser,
    scopes: list[str] | None = None,
) -> AppUser:
    name = str(username or "").strip()
    if not _USERNAME_RE.match(name):
        raise HTTPException(status_code=400, detail="invalid_username")
    pwd = str(password or "")
    if len(pwd) < 6:
        raise HTTPException(status_code=400, detail="password_too_short")
    role_n = str(role or "user").strip().lower()
    if role_n not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="invalid_role")
    if get_user_by_username(db, name) is not None:
        raise HTTPException(status_code=409, detail="username_exists")
    scope_list = normalize_scopes(scopes) if scopes is not None else []
    user = AppUser(
        username=name,
        password_hash=hash_password(pwd),
        role=role_n,
        scopes=scope_list,
        is_active=True,
        created_by=actor.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def update_user(
    db: Session,
    *,
    user_id: str,
    actor: AppUser,
    is_active: bool | None = None,
    role: str | None = None,
    password: str | None = None,
    scopes: list[str] | None = None,
) -> AppUser:
    user = get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    if user.id == actor.id and is_active is False:
        raise HTTPException(status_code=400, detail="cannot_deactivate_self")
    if role is not None:
        role_n = str(role).strip().lower()
        if role_n not in ("admin", "user"):
            raise HTTPException(status_code=400, detail="invalid_role")
        if user.id == actor.id and role_n != "admin":
            raise HTTPException(status_code=400, detail="cannot_demote_self")
        user.role = role_n
    if is_active is not None:
        user.is_active = bool(is_active)
    if password is not None:
        pwd = str(password)
        if len(pwd) < 6:
            raise HTTPException(status_code=400, detail="password_too_short")
        user.password_hash = hash_password(pwd)
        user.must_change_password = True
    if scopes is not None:
        user.scopes = normalize_scopes(scopes)
    user.updated_at = utcnow_naive()
    db.commit()
    db.refresh(user)
    return user


def change_password(db: Session, *, user: AppUser, old_password: str, new_password: str) -> None:
    row = get_user_by_id(db, str(user.id)) or user
    if not verify_password(old_password, row.password_hash):
        raise HTTPException(status_code=400, detail="old_password_incorrect")
    pwd = str(new_password or "")
    if len(pwd) < 6:
        raise HTTPException(status_code=400, detail="password_too_short")
    default_pwd = str(settings.bootstrap_admin_password or "admin123").strip() or "admin123"
    if pwd == default_pwd or pwd == old_password:
        raise HTTPException(status_code=400, detail="password_must_differ_from_default")
    row.password_hash = hash_password(pwd)
    row.must_change_password = False
    row.updated_at = utcnow_naive()
    db.commit()


def create_api_token(
    db: Session,
    *,
    user: AppUser,
    name: str,
    expires_in_days: int | None = None,
    scopes: list[str] | None = None,
) -> tuple[ApiToken, str]:
    label = str(name or "").strip() or "default"
    if len(label) > 128:
        raise HTTPException(status_code=400, detail="token_name_too_long")
    expires_at: datetime | None = None
    if expires_in_days is not None and int(expires_in_days) > 0:
        expires_at = utcnow_naive() + timedelta(days=int(expires_in_days))
    plaintext = new_api_token_plaintext()
    scope_list = normalize_scopes(scopes) if scopes is not None else []
    # Cap token scopes to owner's effective scopes.
    owner_scopes = effective_user_scopes(role=str(user.role or "user"), override=getattr(user, "scopes", None) or [])
    if scope_list:
        scope_list = sorted(frozenset(scope_list) & owner_scopes)
    row = ApiToken(
        name=label,
        token_hash=hash_api_token(plaintext),
        user_id=user.id,
        scopes=scope_list,
        expires_at=expires_at,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, plaintext


def _token_public(db: Session, r: ApiToken) -> dict[str, Any]:
    owner = get_user_by_id(db, r.user_id)
    now = utcnow_naive()
    expired = bool(r.expires_at and r.expires_at <= now)
    return {
        "id": r.id,
        "name": r.name,
        "user_id": r.user_id,
        "username": owner.username if owner else "",
        "scopes": normalize_scopes(getattr(r, "scopes", None) or []),
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "expires_at": r.expires_at.isoformat() if r.expires_at else None,
        "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
        "revoked_at": r.revoked_at.isoformat() if r.revoked_at else None,
        "revoked": bool(r.revoked_at),
        "expired": expired,
        "active": (not bool(r.revoked_at)) and (not expired),
    }


def list_api_tokens(db: Session, *, user_id: str | None = None) -> list[dict[str, Any]]:
    q = db.query(ApiToken)
    if user_id:
        q = q.filter(ApiToken.user_id == user_id)
    rows = q.order_by(ApiToken.created_at.desc()).all()
    return [_token_public(db, r) for r in rows]


def revoke_api_token(db: Session, *, token_id: str, actor: AppUser) -> ApiToken:
    row = db.query(ApiToken).filter(ApiToken.id == str(token_id)).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="api_token_not_found")
    if actor.role != "admin" and row.user_id != actor.id:
        raise HTTPException(status_code=403, detail="forbidden")
    if row.revoked_at is None:
        row.revoked_at = utcnow_naive()
        db.commit()
        db.refresh(row)
    return row


def update_api_token(
    db: Session,
    *,
    token_id: str,
    actor: AppUser,
    name: str | None = None,
    scopes: list[str] | None = None,
) -> ApiToken:
    row = db.query(ApiToken).filter(ApiToken.id == str(token_id)).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="api_token_not_found")
    if actor.role != "admin" and row.user_id != actor.id:
        raise HTTPException(status_code=403, detail="forbidden")
    if row.revoked_at is not None:
        raise HTTPException(status_code=400, detail="api_token_revoked")

    if name is not None:
        label = str(name or "").strip() or row.name
        if len(label) > 128:
            raise HTTPException(status_code=400, detail="token_name_too_long")
        row.name = label

    if scopes is not None:
        owner = get_user_by_id(db, row.user_id)
        if owner is None:
            raise HTTPException(status_code=404, detail="user_not_found")
        owner_scopes = effective_user_scopes(
            role=str(owner.role or "user"), override=getattr(owner, "scopes", None) or []
        )
        scope_list = normalize_scopes(scopes)
        if scope_list:
            scope_list = sorted(frozenset(scope_list) & owner_scopes)
        if not scope_list:
            raise HTTPException(status_code=400, detail="scopes_required")
        row.scopes = scope_list

    db.commit()
    db.refresh(row)
    return row


def resolve_api_token_row(db: Session, plaintext: str) -> ApiToken | None:
    th = hash_api_token(plaintext)
    row = (
        db.query(ApiToken)
        .filter(ApiToken.token_hash == th, ApiToken.revoked_at.is_(None))
        .one_or_none()
    )
    if row is None:
        return None
    if row.expires_at is not None and row.expires_at <= utcnow_naive():
        return None
    user = get_user_by_id(db, row.user_id)
    if user is None or not user.is_active:
        return None
    row.last_used_at = utcnow_naive()
    try:
        db.commit()
    except Exception:
        db.rollback()
    return row


def resolve_api_token_user(db: Session, plaintext: str) -> AppUser | None:
    row = resolve_api_token_row(db, plaintext)
    if row is None:
        return None
    return get_user_by_id(db, row.user_id)


def list_audit_logs(
    db: Session,
    *,
    actor: AppUser,
    page: int = 1,
    page_size: int = 50,
    username: str = "",
    action: str = "",
) -> dict[str, Any]:
    page = max(1, int(page or 1))
    page_size = max(1, min(200, int(page_size or 50)))
    q = db.query(AuditLog)
    if actor.role != "admin":
        q = q.filter(AuditLog.actor_user_id == actor.id)
    elif username.strip():
        q = q.filter(AuditLog.actor_username == username.strip())
    if action.strip():
        q = q.filter(AuditLog.action.ilike(f"%{action.strip()}%"))
    total = int(q.count())
    rows = (
        q.order_by(AuditLog.ts.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = [
        {
            "id": r.id,
            "ts": r.ts.isoformat() if r.ts else None,
            "actor_user_id": r.actor_user_id,
            "actor_username": r.actor_username,
            "action": r.action,
            "method": r.method,
            "path": r.path,
            "status_code": r.status_code,
            "client_ip": r.client_ip,
            "user_agent": r.user_agent,
            "detail": r.detail or {},
        }
        for r in rows
    ]
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
    }
