"""Pydantic schemas for auth / users / audit / API tokens."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=6, max_length=256)


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=6, max_length=256)
    role: str = Field(default="user")
    scopes: list[str] | None = None


class UserUpdateRequest(BaseModel):
    is_active: bool | None = None
    role: str | None = None
    password: str | None = Field(default=None, min_length=6, max_length=256)
    scopes: list[str] | None = None


class ApiTokenCreateRequest(BaseModel):
    name: str = Field(default="default", max_length=128)
    # Days until expiry; 0 / null = never expires.
    expires_in_days: int | None = Field(default=90, ge=0, le=3650)
    # Admin may create a token for another user; others ignored / forced to self.
    user_id: str | None = None
    # Explicit capability list; empty inherits owner scopes (legacy). Prefer non-empty.
    scopes: list[str] | None = None


class ApiTokenUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    # Replace token scopes (capped to owner). Empty list clears to inherit owner scopes.
    scopes: list[str] | None = None
