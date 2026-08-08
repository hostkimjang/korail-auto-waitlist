from __future__ import annotations

from ..domain import Provider
from ..schema_base import ApiModel


class ProviderCapabilities(ApiModel):
    provider: Provider
    timetable: bool
    official_booking_link: bool
    official_waitlist_link: bool
    seat_monitoring: bool
    reservation_once: bool
    experimental: bool = False
    enabled: bool = True
    note: str | None = None
