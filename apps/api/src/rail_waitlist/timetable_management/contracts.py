from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol, runtime_checkable

from ..domain import Provider
from ..provider_adapters.tago import TagoClient
from ..srt_sidecar.contracts import SrtTimetableTrain
from ..timetable_snapshot_cache import TimetableSnapshotKey
from .schemas import StationCatalog, TimetableItem


@runtime_checkable
class StationCatalogReader(Protocol):
    async def get_catalog(self, provider: Provider) -> StationCatalog: ...


class StationCatalogTimetablePort(StationCatalogReader, Protocol):
    @property
    def tago_client(self) -> TagoClient: ...


class KorailTimetableSource(Protocol):
    async def search_timetable(
        self,
        *,
        origin: str,
        destination: str,
        departure_from: datetime,
        departure_to: datetime,
        passenger_count: int,
    ) -> list[TimetableItem]: ...


class SrtTimetableSource(Protocol):
    async def search_timetable(
        self,
        *,
        origin: str,
        destination: str,
        departure_from: datetime,
        departure_to: datetime,
        passenger_count: int,
    ) -> list[SrtTimetableTrain]: ...


@runtime_checkable
class TimetableSnapshotCachePort(Protocol):
    async def get(self, key: TimetableSnapshotKey) -> list[TimetableItem] | None: ...

    async def store(self, key: TimetableSnapshotKey, items: list[TimetableItem]) -> None: ...

    async def refresh_if_due(
        self,
        key: TimetableSnapshotKey,
        loader: Callable[[], Awaitable[list[TimetableItem]]],
    ) -> bool: ...


class TimetableApplicationState(Protocol):
    station_catalog_service: StationCatalogTimetablePort
    korail_browser_seat_source: KorailTimetableSource
    srt_seat_source: SrtTimetableSource


class TimetableApplication(Protocol):
    state: TimetableApplicationState
