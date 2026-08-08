from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import Field, field_validator

from ..schema_base import ApiModel


class AuthStatus(ApiModel):
    configured: bool
    authenticated: bool
    registration_allowed: bool
    session_expires_at: datetime | None = None

    @field_validator("session_expires_at", mode="before")
    @classmethod
    def normalize_session_expiry(cls, value: Any) -> Any:
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class UsernamePasswordCredentials(ApiModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    password: str = Field(min_length=12, max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().casefold()
        return value


class LoginResult(ApiModel):
    authenticated: bool
    expires_at: datetime
