from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from ..domain import NotificationKind
from ..schema_base import ApiModel


class NotificationChannelCreate(ApiModel):
    kind: NotificationKind
    name: str = Field(min_length=1, max_length=80)
    config: dict[str, Any]
    enabled: bool = True


class NotificationChannelUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    config: dict[str, Any] | None = None
    enabled: bool | None = None


class NotificationChannelRead(ApiModel):
    id: str
    kind: NotificationKind
    name: str
    enabled: bool
    configured: bool = True
    created_at: datetime
    updated_at: datetime


class QueuedResponse(ApiModel):
    queued: bool
    event_id: str
