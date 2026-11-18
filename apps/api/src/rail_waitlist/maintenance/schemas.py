from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Version = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")]
Identifier = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
PlanIdentifier = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
UpdateReason = Literal[
    "managed_install_required",
    "manager_unavailable",
    "checking",
    "up_to_date",
    "update_available",
    "update_blocked",
    "recovery_required",
]


class ManagementModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)
    schema_version: Literal[1] = Field(alias="schema")

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_exact_schema(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("invalid management schema")
        return value


class ManagerStatus(ManagementModel):
    state: Literal["ready", "stopped", "failed"]
    pid: int = Field(gt=0)
    started_at: datetime
    heartbeat_at: datetime


class UpdateCatalog(ManagementModel):
    checked_at: datetime
    current_version: Version | None = None
    latest_version: Version | None = None
    plan_id: PlanIdentifier | None = None
    available: bool
    reason: UpdateReason

    @field_validator("current_version", "latest_version", "plan_id", mode="before")
    @classmethod
    def normalize_optional_strings(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("checked_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone required")
        return value


class UpdateStatus(ManagementModel):
    schema_version: Literal[1] = Field(default=1, alias="schema")
    managed: bool
    manager_available: bool
    checked_at: datetime | None = None
    current_version: Version | None = None
    latest_version: Version | None = None
    plan_id: PlanIdentifier | None = None
    available: bool
    reason: UpdateReason
    message: str


class UpdateSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target_release_id: Version
    plan_id: PlanIdentifier
    idempotency_key: Identifier


class UpdateJob(ManagementModel):
    id: Identifier
    target_release_id: Version
    plan_id: PlanIdentifier
    idempotency_key: Identifier
    state: Literal[
        "queued", "running", "succeeded", "failed", "deferred", "recovery_required", "rejected"
    ]
    stage: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    created_at: datetime
    updated_at: datetime
    current_version: Version | None = None
    target_version: Version | None = None
    error_code: (
        Literal[
            "E_INVALID_REQUEST",
            "E_UNAUTHORIZED",
            "E_IDEMPOTENCY_CONFLICT",
            "E_HANDLER_FAILED",
            "E_MANAGER_RESTARTED",
        ]
        | None
    ) = None
    duplicate_of: Identifier | None = None


class UpdateAccepted(ManagementModel):
    schema_version: Literal[1] = Field(default=1, alias="schema")
    id: Identifier
    state: Literal["queued"] = "queued"
