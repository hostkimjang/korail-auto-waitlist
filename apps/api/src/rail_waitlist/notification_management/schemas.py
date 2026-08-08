from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import StringConstraints, field_validator

from ..domain import NotificationKind
from ..schema_base import ApiModel

NotificationChannelName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=80),
]


class NotificationChannelCreate(ApiModel):
    kind: NotificationKind
    name: NotificationChannelName
    config: dict[str, Any]
    enabled: bool = True


class NotificationChannelUpdate(ApiModel):
    name: NotificationChannelName | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None


class NotificationChannelRead(ApiModel):
    id: str
    kind: NotificationKind
    name: str
    enabled: bool
    configured: bool = True
    device_key: str | None = None
    active_device_count: int | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def normalize_database_timestamp(cls, value: object) -> object:
        if not isinstance(value, datetime):
            return value
        # PostgreSQL stores UTC instants and SQLite drops timezone metadata in tests.
        # The HTTP contract is always timezone-aware so strict clients never have to guess.
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class QueuedResponse(ApiModel):
    queued: bool
    event_id: str
