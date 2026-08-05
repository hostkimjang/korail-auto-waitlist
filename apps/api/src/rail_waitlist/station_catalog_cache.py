from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .domain import Provider
from .models import StationCatalogCache
from .provider_adapters.tago import TagoClient
from .provider_contracts import ProviderUnavailable
from .schemas import StationCatalog, StationItem
from .station_visibility import (
    KORAIL_STATION_DATA_URL,
    KorailStationVisibility,
    StationVisibilityRoster,
    filter_station_items,
)

CANONICAL_CACHE_KEY = "tago_station_catalog_all"
STATION_CATALOG_SCHEMA_VERSION = 2
STATION_CATALOG_TTL = timedelta(hours=24)
REFRESH_LEASE = timedelta(seconds=75)
COLLECTION_TIMEOUT_SECONDS = 60.0
INITIAL_WAIT_SECONDS = 65.0
OTHER_OWNER_POLL_SECONDS = 0.05

_station_list_adapter = TypeAdapter(list[StationItem])


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True)
class StationCatalogSnapshot:
    identity_stations: list[StationItem]
    display_stations: list[StationItem]
    retrieved_at: datetime
    refresh_after: datetime

    @property
    def is_fresh(self) -> bool:
        return self.refresh_after > datetime.now(UTC)


class StationCatalogRepository:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        visibility_url: str = KORAIL_STATION_DATA_URL,
    ) -> None:
        self._session_factory = session_factory
        self._visibility_url = visibility_url

    async def ensure_canonical_row(self) -> None:
        async with self._session_factory() as session:
            if await session.get(StationCatalogCache, CANONICAL_CACHE_KEY) is not None:
                return
            session.add(
                StationCatalogCache(
                    cache_key=CANONICAL_CACHE_KEY,
                    schema_version=STATION_CATALOG_SCHEMA_VERSION,
                    payload=None,
                    station_count=0,
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()

    async def load_snapshot(self) -> StationCatalogSnapshot | None:
        await self.ensure_canonical_row()
        async with self._session_factory() as session:
            row = await session.get(StationCatalogCache, CANONICAL_CACHE_KEY)
            if (
                row is None
                or row.schema_version != STATION_CATALOG_SCHEMA_VERSION
                or row.payload is None
                or row.retrieved_at is None
                or row.refresh_after is None
            ):
                return None
            try:
                raw_identity_stations = row.payload.get("stations")
                raw_display_stations = row.payload.get("display_stations")
                visibility = row.payload.get("visibility")
                identity_stations = _station_list_adapter.validate_python(raw_identity_stations)
                display_stations = _station_list_adapter.validate_python(raw_display_stations)
                visibility_retrieved_at = datetime.fromisoformat(visibility["retrieved_at"])
            except (AttributeError, KeyError, TypeError, ValidationError, ValueError):
                return None
            if (
                not isinstance(visibility, dict)
                or visibility.get("source") != "korail_station_guide"
                or visibility.get("url") != self._visibility_url
                or visibility_retrieved_at.tzinfo is None
                or visibility_retrieved_at.utcoffset() is None
            ):
                return None
            if not identity_stations or len(identity_stations) != row.station_count:
                return None
            if not display_stations:
                return None
            identity_ids = {station.node_id for station in identity_stations}
            display_ids = {station.node_id for station in display_stations}
            if (
                len(identity_ids) != len(identity_stations)
                or len(display_ids) != len(display_stations)
                or not display_ids.issubset(identity_ids)
            ):
                return None
            return StationCatalogSnapshot(
                identity_stations=identity_stations,
                display_stations=display_stations,
                retrieved_at=_aware_utc(row.retrieved_at),
                refresh_after=_aware_utc(row.refresh_after),
            )

    async def try_acquire_lease(self, owner: str, now: datetime, lease_until: datetime) -> bool:
        await self.ensure_canonical_row()
        async with self._session_factory() as session:
            result = await session.execute(
                update(StationCatalogCache)
                .where(
                    StationCatalogCache.cache_key == CANONICAL_CACHE_KEY,
                    or_(
                        StationCatalogCache.lease_until.is_(None),
                        StationCatalogCache.lease_until <= now,
                    ),
                )
                .values(
                    refresh_owner=owner,
                    lease_until=lease_until,
                    last_attempt_at=now,
                    last_error_category=None,
                    updated_at=now,
                )
            )
            await session.commit()
            return result.rowcount == 1

    async def save_success(
        self,
        owner: str,
        identity_stations: list[StationItem],
        display_stations: list[StationItem],
        visibility_roster: StationVisibilityRoster,
        retrieved_at: datetime,
        refresh_after: datetime,
        now: datetime,
    ) -> bool:
        if not identity_stations or not display_stations:
            return False
        identity_ids = {station.node_id for station in identity_stations}
        display_ids = {station.node_id for station in display_stations}
        if (
            len(identity_ids) != len(identity_stations)
            or len(display_ids) != len(display_stations)
            or not display_ids.issubset(identity_ids)
        ):
            return False
        payload = {
            "stations": [station.model_dump(mode="json") for station in identity_stations],
            "display_stations": [station.model_dump(mode="json") for station in display_stations],
            "visibility": {
                "source": "korail_station_guide",
                "url": self._visibility_url,
                "retrieved_at": visibility_roster.retrieved_at.isoformat(),
                "etag": visibility_roster.etag,
                "last_modified": visibility_roster.last_modified,
            },
        }
        async with self._session_factory() as session:
            result = await session.execute(
                update(StationCatalogCache)
                .where(
                    StationCatalogCache.cache_key == CANONICAL_CACHE_KEY,
                    StationCatalogCache.refresh_owner == owner,
                    StationCatalogCache.lease_until >= now,
                )
                .values(
                    schema_version=STATION_CATALOG_SCHEMA_VERSION,
                    payload=payload,
                    station_count=len(identity_stations),
                    retrieved_at=retrieved_at,
                    refresh_after=refresh_after,
                    refresh_owner=None,
                    lease_until=None,
                    last_error_category=None,
                    updated_at=now,
                )
            )
            await session.commit()
            return result.rowcount == 1

    async def record_failure(self, owner: str, category: str, now: datetime) -> bool:
        bounded_category = category[:64]
        async with self._session_factory() as session:
            result = await session.execute(
                update(StationCatalogCache)
                .where(
                    StationCatalogCache.cache_key == CANONICAL_CACHE_KEY,
                    StationCatalogCache.refresh_owner == owner,
                )
                .values(
                    refresh_owner=None,
                    lease_until=None,
                    last_error_category=bounded_category,
                    updated_at=now,
                )
            )
            await session.commit()
            return result.rowcount == 1


def _catalog_from_snapshot(snapshot: StationCatalogSnapshot, provider: Provider) -> StationCatalog:
    stations = snapshot.display_stations
    return StationCatalog(
        provider=provider,
        source="TAGO",
        retrieved_at=snapshot.retrieved_at,
        catalog_scope="intercity_station_guide_intersection",
        provider_membership="not_verified_by_source",
        note=(
            "공개 철도역 식별자 중 일반·고속열차 여정 선택에 적합한 역만 표시합니다. "
            "운영사 소속과 특정 날짜의 정차 여부는 시간표 결과에서 확인합니다."
        ),
        stations=stations,
    )


def _error_category(error: BaseException) -> str:
    if isinstance(error, TimeoutError):
        return "upstream_timeout"
    if isinstance(error, ProviderUnavailable):
        return "provider_unavailable"
    if isinstance(error, (ValidationError, ValueError, TypeError)):
        return "invalid_catalog"
    return "unexpected_failure"


class StationCatalogService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        tago_client: TagoClient | None = None,
        station_visibility: KorailStationVisibility | None = None,
    ) -> None:
        self.tago_client = tago_client or TagoClient()
        self.station_visibility = station_visibility or KorailStationVisibility()
        self.repository = StationCatalogRepository(
            session_factory,
            getattr(self.station_visibility, "url", KORAIL_STATION_DATA_URL),
        )
        self._task_lock = asyncio.Lock()
        self._refresh_task: asyncio.Task[StationCatalogSnapshot | None] | None = None
        self._closed = False

    def _hydrate(self, snapshot: StationCatalogSnapshot) -> None:
        self.tago_client.hydrate_station_catalog(
            snapshot.identity_stations,
            snapshot.retrieved_at,
            snapshot.refresh_after,
        )

    async def get_catalog(self, provider: Provider) -> StationCatalog:
        snapshot = await self.repository.load_snapshot()
        if snapshot is not None:
            self._hydrate(snapshot)
            if not snapshot.is_fresh:
                await self._start_refresh()
            return _catalog_from_snapshot(snapshot, provider)

        task = await self._start_refresh()
        try:
            snapshot = await asyncio.wait_for(asyncio.shield(task), INITIAL_WAIT_SECONDS)
        except TimeoutError:
            snapshot = None
        if snapshot is None:
            raise ProviderUnavailable("station catalog is unavailable")
        self._hydrate(snapshot)
        return _catalog_from_snapshot(snapshot, provider)

    async def preload(self) -> None:
        snapshot = await self.repository.load_snapshot()
        if snapshot is not None:
            self._hydrate(snapshot)
        if snapshot is None or not snapshot.is_fresh:
            await self._start_refresh()

    async def _start_refresh(self) -> asyncio.Task[StationCatalogSnapshot | None]:
        async with self._task_lock:
            if self._closed:
                raise ProviderUnavailable("station catalog service is shutting down")
            if self._refresh_task is None or self._refresh_task.done():
                self._refresh_task = asyncio.create_task(self._refresh_or_wait())
            return self._refresh_task

    async def _refresh_or_wait(self) -> StationCatalogSnapshot | None:
        owner = uuid.uuid4().hex
        now = datetime.now(UTC)
        acquired = await self.repository.try_acquire_lease(owner, now, now + REFRESH_LEASE)
        if not acquired:
            return await self._wait_for_other_owner()
        try:
            async with asyncio.timeout(COLLECTION_TIMEOUT_SECONDS):
                catalog = await self.tago_client.fetch_station_catalog(Provider.KORAIL)
                visibility_roster = await self.station_visibility.load_roster()
            if not catalog.stations:
                raise ValueError("empty station catalog")
            display_stations = filter_station_items(catalog.stations, visibility_roster)
            completed_at = datetime.now(UTC)
            retrieved_at = min(
                catalog.retrieved_at.astimezone(UTC),
                visibility_roster.retrieved_at.astimezone(UTC),
            )
            saved = await self.repository.save_success(
                owner,
                catalog.stations,
                display_stations,
                visibility_roster,
                retrieved_at,
                retrieved_at + STATION_CATALOG_TTL,
                completed_at,
            )
            snapshot = await self.repository.load_snapshot()
            if saved and snapshot is not None:
                self._hydrate(snapshot)
            return snapshot
        except asyncio.CancelledError:
            await asyncio.shield(
                self.repository.record_failure(owner, "cancelled", datetime.now(UTC))
            )
            raise
        # This background boundary must reduce every unexpected adapter failure to a
        # bounded category so stale data remains usable and task exceptions never leak.
        except Exception as error:  # noqa: BLE001
            await self.repository.record_failure(owner, _error_category(error), datetime.now(UTC))
            return await self.repository.load_snapshot()

    async def _wait_for_other_owner(self) -> StationCatalogSnapshot | None:
        deadline = asyncio.get_running_loop().time() + INITIAL_WAIT_SECONDS
        while asyncio.get_running_loop().time() < deadline:
            snapshot = await self.repository.load_snapshot()
            if snapshot is not None:
                return snapshot
            await asyncio.sleep(OTHER_OWNER_POLL_SECONDS)
        return None

    async def close(self) -> None:
        self._closed = True
        task = self._refresh_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
