from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

Version = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")]
Identifier = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
PlanIdentifier = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Revision = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
EnvName = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")]
UpdateReason = Literal[
    "managed_install_required",
    "manager_unavailable",
    "checking",
    "up_to_date",
    "update_available",
    "update_blocked",
    "recovery_required",
]
SettingsReason = Literal[
    "ready",
    "managed_install_required",
    "manager_unavailable",
    "manager_upgrade_required",
]
SettingKey = Literal[
    "KORAIL_RESERVATION_ONCE_ENABLED",
    "SRT_RESERVATION_ONCE_ENABLED",
    "TAGO_SERVICE_KEY",
    "WEBPUSH_VAPID_SUBJECT",
    "VITE_KORAIL_BOOKING_DEEPLINK_ENABLED",
    "VITE_KORAIL_BOOKING_VALIDATED_VERSION",
    "VITE_KORAIL_TICKET_DEEPLINK_ENABLED",
    "VITE_KORAIL_TICKET_VALIDATED_VERSION",
    "VITE_SRT_MAIN_DEEPLINK_ENABLED",
    "VITE_SRT_MAIN_VALIDATED_VERSION",
    "VITE_SRT_TICKET_DEEPLINK_ENABLED",
    "VITE_SRT_TICKET_VALIDATED_VERSION",
]
SettingKind = Literal["boolean", "version", "secret", "contact"]
SettingScope = Literal["web", "app"]
BooleanSetting = Literal["true", "false"]
VersionSetting = Annotated[str, Field(pattern=r"^[0-9A-Za-z.+_-]{0,64}$")]

SETTINGS_SNAPSHOT_RAW_LIMIT = 196608
SETTINGS_SUBMISSION_RAW_BYTES = 65536
_SECRET_SETTING = re.compile(r"[!#-&(-~]{1,512}")
_HOST_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_HOSTNAME = rf"{_HOST_LABEL}(?:\.{_HOST_LABEL})*"
# The manager stores the contact single-quoted in .env, so a quote is refused here too.
_CONTACT_SETTING = re.compile(
    rf"mailto:[A-Za-z0-9.!#$%&*+/=^_`{{|}}~-]+@{_HOSTNAME}|https://{_HOSTNAME}"
)


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
            "E_SETTINGS_CHANGED",
            "E_SETTINGS_INVALID",
            "E_SETTINGS_FORBIDDEN",
            "E_SETTINGS_UNCHANGED",
        ]
        | None
    ) = None
    duplicate_of: Identifier | None = None


class UpdateAccepted(ManagementModel):
    schema_version: Literal[1] = Field(default=1, alias="schema")
    id: Identifier
    state: Literal["queued"] = "queued"


class SettingsSnapshotValue(BaseModel):
    """One non-secret entry of the manager's settings.json snapshot."""

    model_config = ConfigDict(extra="forbid", strict=True)
    value: str
    configured: bool


class SettingsSnapshotSecret(BaseModel):
    """A secret entry; a snapshot that carries its value is rejected as unsafe."""

    model_config = ConfigDict(extra="forbid", strict=True)
    configured: bool


class SettingsSnapshot(ManagementModel):
    """Secret-free view of the install .env written by the host manager."""

    manager_started_at: datetime
    generated_at: datetime
    revision: Revision
    # Only allowlisted keys are read, so unknown keys from newer managers are ignored.
    fields: dict[str, object]
    raw: str = Field(max_length=SETTINGS_SNAPSHOT_RAW_LIMIT)
    secret_keys: list[EnvName] = Field(max_length=256)
    locked_keys: list[EnvName] = Field(max_length=256)

    @field_validator("manager_started_at", "generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone required")
        return value


class SettingsField(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    key: SettingKey
    kind: SettingKind
    scope: SettingScope
    value: str | None
    configured: bool


class SettingsStatus(ManagementModel):
    schema_version: Literal[1] = Field(default=1, alias="schema")
    managed: bool
    manager_available: bool
    available: bool
    reason: SettingsReason
    revision: Revision | None = None
    fields: list[SettingsField] = Field(default_factory=list)
    raw: str | None = None
    secret_keys: list[EnvName] = Field(default_factory=list)
    locked_keys: list[EnvName] = Field(default_factory=list)


class SettingsFieldValues(BaseModel):
    """Allowlisted form values; unknown keys are rejected and the secret stays masked."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    KORAIL_RESERVATION_ONCE_ENABLED: BooleanSetting | None = None
    SRT_RESERVATION_ONCE_ENABLED: BooleanSetting | None = None
    TAGO_SERVICE_KEY: SecretStr | None = None
    WEBPUSH_VAPID_SUBJECT: str | None = Field(default=None, max_length=254)
    VITE_KORAIL_BOOKING_DEEPLINK_ENABLED: BooleanSetting | None = None
    VITE_KORAIL_BOOKING_VALIDATED_VERSION: VersionSetting | None = None
    VITE_KORAIL_TICKET_DEEPLINK_ENABLED: BooleanSetting | None = None
    VITE_KORAIL_TICKET_VALIDATED_VERSION: VersionSetting | None = None
    VITE_SRT_MAIN_DEEPLINK_ENABLED: BooleanSetting | None = None
    VITE_SRT_MAIN_VALIDATED_VERSION: VersionSetting | None = None
    VITE_SRT_TICKET_DEEPLINK_ENABLED: BooleanSetting | None = None
    VITE_SRT_TICKET_VALIDATED_VERSION: VersionSetting | None = None

    @field_validator("TAGO_SERVICE_KEY")
    @classmethod
    def validate_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and value.get_secret_value() != "":
            if not _SECRET_SETTING.fullmatch(value.get_secret_value()):
                raise ValueError("invalid secret setting")
        return value

    @field_validator("WEBPUSH_VAPID_SUBJECT")
    @classmethod
    def validate_contact(cls, value: str | None) -> str | None:
        if value is not None and value != "" and not _CONTACT_SETTING.fullmatch(value):
            raise ValueError("invalid contact setting")
        return value

    @model_validator(mode="after")
    def require_submitted_values(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("at least one setting required")
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("setting value required")
        return self

    def submitted(self) -> dict[str, str]:
        """Return only the submitted keys, in allowlist order, with the secret revealed."""
        values: dict[str, str] = {}
        for name in type(self).model_fields:
            if name not in self.model_fields_set:
                continue
            value = getattr(self, name)
            values[name] = value.get_secret_value() if isinstance(value, SecretStr) else value
        return values


class SettingsSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    base_revision: Revision
    idempotency_key: Identifier
    fields: SettingsFieldValues | None = None
    raw: SecretStr | None = None

    @field_validator("raw")
    @classmethod
    def validate_raw(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return value
        text = value.get_secret_value()
        try:
            size = len(text.encode("utf-8"))
        except UnicodeEncodeError:
            raise ValueError("raw settings must be valid UTF-8") from None
        if "\x00" in text or size > SETTINGS_SUBMISSION_RAW_BYTES:
            raise ValueError("invalid raw settings")
        return value

    @model_validator(mode="after")
    def require_one_form(self) -> Self:
        forms = [name for name in ("fields", "raw") if name in self.model_fields_set]
        if len(forms) != 1 or getattr(self, forms[0]) is None:
            raise ValueError("exactly one of fields or raw required")
        return self


class SettingsAccepted(UpdateAccepted):
    pass
