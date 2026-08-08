from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ..schema_base import ApiModel

SeatStatusCooldownCause = Literal[
    "provider_access_restricted",
    "source_unavailable",
]


class SeatStatusSourceStatus(ApiModel):
    """Current in-memory/Redis hold only; it is distinct from worker provider circuits."""

    provider: Literal["korail", "srt"]
    source: Literal["korail_browser", "srt_live"]
    state: Literal["ready", "cooldown"]
    cause: SeatStatusCooldownCause | None = None
    retry_after_seconds: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_cooldown_details(self) -> SeatStatusSourceStatus:
        if self.state == "ready":
            if self.cause is not None or self.retry_after_seconds is not None:
                raise ValueError("ready seat status source cannot expose cooldown details")
            return self
        if self.cause is None or self.retry_after_seconds is None:
            raise ValueError("cooldown seat status source requires cause and retry_after_seconds")
        return self
