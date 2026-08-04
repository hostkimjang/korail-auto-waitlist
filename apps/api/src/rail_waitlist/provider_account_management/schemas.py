from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator

from ..domain import Provider
from ..schema_base import ApiModel

RailProviderAuthStatus = Literal[
    "not_checked",
    "authenticated",
    "auth_required",
    "provider_blocked",
    "failed",
]
RailLoginMethod = Literal["membership_number", "email", "phone"]


class RailProviderAccountUpsert(ApiModel):
    login_method: RailLoginMethod
    login_id: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1, max_length=256)
    enabled: bool = True

    @field_validator("login_id")
    @classmethod
    def normalize_login_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("login_id cannot be blank")
        return normalized

    @model_validator(mode="after")
    def normalize_login_method_identifier(self) -> RailProviderAccountUpsert:
        if self.login_method == "email":
            if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", self.login_id) is None:
                raise ValueError("email login identifier is invalid")
        elif self.login_method == "phone":
            digits = "".join(character for character in self.login_id if character.isdigit())
            if len(digits) not in {10, 11} or not digits.startswith("01"):
                raise ValueError("phone login identifier is invalid")
            self.login_id = digits
        return self


class RailProviderAccountRead(ApiModel):
    provider: Literal[Provider.KORAIL, Provider.SRT]
    configured: bool
    enabled: bool
    login_method: RailLoginMethod | None
    masked_login_id: str | None
    credential_version: int = Field(ge=0)
    last_auth_status: RailProviderAuthStatus
    last_authenticated_at: datetime | None
    updated_at: datetime | None

    @field_validator("last_authenticated_at", "updated_at", mode="before")
    @classmethod
    def normalize_account_timezone(cls, value: Any) -> Any:
        if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset() is None):
            return value.replace(tzinfo=UTC)
        return value


RailProviderRuntimeState = Literal[
    "cold",
    "authenticating",
    "ready",
    "stale",
    "auth_required",
    "blocked",
]


class RailProviderRuntimeStatusRead(ApiModel):
    provider: Literal[Provider.KORAIL, Provider.SRT]
    state: RailProviderRuntimeState
    credential_generation: str | None = None
    created_age_seconds: float | None = Field(default=None, ge=0)
    last_verified_age_seconds: float | None = Field(default=None, ge=0)
    last_used_age_seconds: float | None = Field(default=None, ge=0)
    local_reuse_remaining_seconds: float | None = Field(default=None, ge=0)
    locally_reusable: bool
    prewarm_outcome: RailProviderAuthStatus | None = None
