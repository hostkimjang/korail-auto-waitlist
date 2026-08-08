from __future__ import annotations

from datetime import datetime

from ..config import Settings, get_settings
from ..domain import Provider
from ..provider_registry.contracts import ProviderCapabilities
from ..timetable_management.schemas import StationCatalog, TimetableItem
from .base import RailProviderAdapter


class ExperimentalRailAdapter(RailProviderAdapter):
    def __init__(self, provider: Provider, settings: Settings | None = None) -> None:
        self.provider = provider
        self.settings = settings or get_settings()

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider,
            timetable=False,
            official_booking_link=True,
            official_waitlist_link=False,
            seat_monitoring=False,
            reservation_once=False,
            experimental=True,
            enabled=self.settings.experimental_rail_enabled,
            note="실험 어댑터는 미구현이며 비공식 endpoint를 호출하지 않습니다.",
        )

    async def timetable(
        self,
        origin: str,
        destination: str,
        departure_from: datetime,
        origin_node_id: str | None = None,
        destination_node_id: str | None = None,
        departure_to: datetime | None = None,
    ) -> list[TimetableItem]:
        raise NotImplementedError("experimental provider has no external implementation")

    async def stations(self) -> StationCatalog:
        raise NotImplementedError("experimental provider has no external implementation")
