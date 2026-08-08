from __future__ import annotations

from datetime import datetime

from ..config import Settings, get_settings
from ..domain import Provider
from ..provider_contracts import ProviderUnavailable, RouteValidationError
from ..provider_registry.contracts import ProviderCapabilities
from ..timetable_management.schemas import StationCatalog, TimetableItem
from . import tago
from .base import RailProviderAdapter
from .srt_station_roster import (
    SrtStationRoster,
    SrtStationRosterUnavailable,
    load_srt_station_roster,
)
from .timetable_support import normalize_station_name


class OfficialTimetableAdapter(RailProviderAdapter):
    def __init__(
        self,
        provider: Provider,
        settings: Settings | None = None,
        tago_client: tago.TagoClient | None = None,
        srt_station_roster: SrtStationRoster | None = None,
    ) -> None:
        self.provider = provider
        self.settings = settings or get_settings()
        self.tago_client = tago_client or tago.default_tago_client()
        self._srt_station_roster = srt_station_roster

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider,
            timetable=True,
            official_booking_link=True,
            official_waitlist_link=False,
            seat_monitoring=False,
            reservation_once=False,
            note="TAGO 공식 시간표와 철도사 예매 링크만 제공합니다. 예약대기 자동 API는 아닙니다.",
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
        if self.provider == Provider.SRT:
            if normalize_station_name(origin) == normalize_station_name(destination):
                raise RouteValidationError("origin and destination must differ")
            if origin_node_id is None or destination_node_id is None:
                raise RouteValidationError(
                    "official timetable requests require both origin and destination node ids"
                )
            if origin_node_id == destination_node_id:
                raise RouteValidationError("origin and destination nodes must differ")
            try:
                roster = self._srt_station_roster or load_srt_station_roster()
            except SrtStationRosterUnavailable as error:
                raise ProviderUnavailable("SRT station roster is unavailable") from error
            if not roster.supports_route(origin, destination):
                return []
        return await self.tago_client.timetable(
            self.provider,
            origin,
            destination,
            departure_from,
            self.official_booking_url(),
            origin_node_id,
            destination_node_id,
            departure_to,
        )

    async def stations(self) -> StationCatalog:
        return await self.tago_client.station_catalog(self.provider)
