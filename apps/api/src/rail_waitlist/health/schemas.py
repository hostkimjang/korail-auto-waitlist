from __future__ import annotations

from ..schema_base import ApiModel


class HealthResponse(ApiModel):
    status: str
    experimental_rail_enabled: bool
