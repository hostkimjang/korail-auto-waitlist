from __future__ import annotations

import logging
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import Provider
from ..korail_browser_bridge import overlay_korail_browser_snapshots
from ..korail_browser_seat_source import KorailBrowserTimetableUnavailable
from ..official_page_confirmations import overlay_official_page_confirmations
from ..providers import OfficialTimetableAdapter, get_execution_provider, get_timetable_provider
from ..schemas import TimetableItem
from ..srt_live_timetable import map_srt_live_timetable
from ..srt_provider_adapter import SrtProviderAdapterUnavailable
from ..srt_seat_source import SrtLiveTimetableUnavailable
from ..timetable_evidence import persist_timetable_seat_evidence
from ..watch_registration_policy import apply_watch_registration_capability

LOGGER = logging.getLogger(__name__)


class TimetableApplicationState(Protocol):
    station_catalog_service: object
    korail_browser_seat_source: object
    srt_seat_source: object


class TimetableApplication(Protocol):
    state: TimetableApplicationState


class UnsupportedTimetableProvider(ValueError):
    """Raised when a provider has no timetable query contract."""


async def load_timetable_items(
    *,
    app: TimetableApplication,
    session: AsyncSession,
    provider: Provider,
    origin: str,
    destination: str,
    departure_from: datetime,
    departure_to: datetime,
    passenger_count: int,
    origin_node_id: str | None,
    destination_node_id: str | None,
) -> list[TimetableItem]:
    if provider == Provider.MOCK:
        return await _load_adapter_timetable(
            app=app,
            provider=provider,
            origin=origin,
            destination=destination,
            departure_from=departure_from,
            departure_to=departure_to,
            origin_node_id=origin_node_id,
            destination_node_id=destination_node_id,
        )
    if provider not in {Provider.KORAIL, Provider.SRT}:
        raise UnsupportedTimetableProvider("unsupported provider")

    try:
        items = await _load_live_timetable(
            app=app,
            provider=provider,
            origin=origin,
            destination=destination,
            departure_from=departure_from,
            departure_to=departure_to,
            passenger_count=passenger_count,
        )
    except (
        KorailBrowserTimetableUnavailable,
        SrtLiveTimetableUnavailable,
        SrtProviderAdapterUnavailable,
        ValueError,
    ):
        LOGGER.warning(
            "Official live timetable unavailable provider=%s; trying TAGO fallback",
            provider.value,
        )
        items = await _load_adapter_timetable(
            app=app,
            provider=provider,
            origin=origin,
            destination=destination,
            departure_from=departure_from,
            departure_to=departure_to,
            origin_node_id=origin_node_id,
            destination_node_id=destination_node_id,
        )

    has_station_nodes = origin_node_id is not None and destination_node_id is not None
    if has_station_nodes:
        items = await overlay_official_page_confirmations(
            session,
            items,
            provider=provider,
            origin_node_id=origin_node_id,
            destination_node_id=destination_node_id,
            passenger_count=passenger_count,
        )
    if provider == Provider.KORAIL:
        items = await overlay_korail_browser_snapshots(
            session,
            items,
            origin=origin,
            destination=destination,
            passenger_count=passenger_count,
        )
    execution_capabilities = get_execution_provider(provider).capabilities()
    items = apply_watch_registration_capability(
        items,
        seat_monitoring_enabled=execution_capabilities.seat_monitoring,
    )
    if has_station_nodes:
        items = await persist_timetable_seat_evidence(
            session,
            items,
            provider=provider,
            origin_node_id=origin_node_id,
            destination_node_id=destination_node_id,
            passenger_count=passenger_count,
        )
    await session.commit()
    return items


async def _load_live_timetable(
    *,
    app: TimetableApplication,
    provider: Provider,
    origin: str,
    destination: str,
    departure_from: datetime,
    departure_to: datetime,
    passenger_count: int,
) -> list[TimetableItem]:
    if provider == Provider.KORAIL:
        return await app.state.korail_browser_seat_source.search_timetable(
            origin=origin,
            destination=destination,
            departure_from=departure_from,
            departure_to=departure_to,
            passenger_count=passenger_count,
        )
    if provider == Provider.SRT:
        trains = await app.state.srt_seat_source.search_timetable(
            origin=origin,
            destination=destination,
            departure_from=departure_from,
            departure_to=departure_to,
            passenger_count=passenger_count,
        )
        return map_srt_live_timetable(trains)
    raise UnsupportedTimetableProvider("provider does not expose a live timetable")


async def _load_adapter_timetable(
    *,
    app: TimetableApplication,
    provider: Provider,
    origin: str,
    destination: str,
    departure_from: datetime,
    departure_to: datetime,
    origin_node_id: str | None,
    destination_node_id: str | None,
) -> list[TimetableItem]:
    adapter = get_timetable_provider(provider)
    if isinstance(adapter, OfficialTimetableAdapter):
        service = app.state.station_catalog_service
        await service.get_catalog(provider)
        adapter.tago_client = service.tago_client
    return await adapter.timetable(
        origin=origin,
        destination=destination,
        departure_from=departure_from,
        origin_node_id=origin_node_id,
        destination_node_id=destination_node_id,
        departure_to=departure_to,
    )
