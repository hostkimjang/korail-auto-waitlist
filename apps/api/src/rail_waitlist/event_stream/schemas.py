from __future__ import annotations

from datetime import datetime
from typing import Any

from ..schema_base import ApiModel


class EventRead(ApiModel):
    id: str
    event_type: str
    aggregate_id: str
    payload: dict[str, Any]
    created_at: datetime
