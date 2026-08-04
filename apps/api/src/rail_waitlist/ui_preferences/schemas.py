from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import Field, field_validator, model_validator

from ..schema_base import ApiModel


class UiPreferencesRead(ApiModel):
    timetable_refresh_interval_seconds: int = Field(ge=5, le=300)
    observation_interval_seconds: int = Field(ge=1, le=600)
    preferences_updated_at: datetime

    @field_validator("preferences_updated_at", mode="before")
    @classmethod
    def normalize_preferences_timezone(cls, value: Any) -> Any:
        if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset() is None):
            return value.replace(tzinfo=UTC)
        return value


class UiPreferencesUpdate(ApiModel):
    timetable_refresh_interval_seconds: int | None = Field(default=None, ge=5, le=300)
    observation_interval_seconds: int | None = Field(default=None, ge=1, le=600)
    # 0025 clients may still submit these fields during a rolling deployment. They are
    # accepted but deliberately ignored: scheduling has one global cadence from 0026.
    balanced_observation_interval_seconds: int | None = Field(default=None, ge=30, le=600)
    focused_observation_interval_seconds: int | None = Field(default=None, ge=20, le=30)

    @model_validator(mode="after")
    def require_at_least_one_preference(self) -> UiPreferencesUpdate:
        if not self.model_fields_set or all(
            getattr(self, field) is None for field in self.model_fields_set
        ):
            raise ValueError("at least one UI or observation preference is required")
        return self
