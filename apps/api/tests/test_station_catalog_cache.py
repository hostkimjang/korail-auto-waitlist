from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from rail_waitlist.config import Settings
from rail_waitlist.domain import Provider
from rail_waitlist.models import StationCatalogCache
from rail_waitlist.providers import ProviderUnavailable, TagoClient
from rail_waitlist.schemas import StationCatalog, StationItem
from rail_waitlist.station_catalog_cache import (
    CANONICAL_CACHE_KEY,
    StationCatalogRepository,
    StationCatalogService,
)
from rail_waitlist.station_visibility import (
    KORAIL_STATION_DATA_URL,
    StationVisibilityRoster,
    normalize_visibility_station_name,
)


def station(node_id: str = "N1", name: str = "서울") -> StationItem:
    return StationItem(node_id=node_id, name=name, city_code="11", city_name="서울특별시")


def upstream_catalog(stations: list[StationItem], retrieved_at: datetime) -> StationCatalog:
    return StationCatalog(
        provider=Provider.KORAIL,
        source="TAGO",
        retrieved_at=retrieved_at,
        catalog_scope="all_tago_train_stations",
        provider_membership="not_verified_by_source",
        note="TAGO 공용 철도역 카탈로그 테스트입니다.",
        stations=stations,
    )


def visibility_roster(stations: list[StationItem]) -> StationVisibilityRoster:
    return StationVisibilityRoster(
        names=frozenset(normalize_visibility_station_name(item.name) for item in stations),
        retrieved_at=datetime.now(UTC),
        etag='"test-roster"',
        last_modified="Wed, 29 Jul 2026 00:00:00 GMT",
    )


class FakeStationVisibility:
    def __init__(self, stations: list[StationItem] | None = None) -> None:
        self.roster = visibility_roster(stations or [station()])
        self.load_count = 0

    async def load_roster(self) -> StationVisibilityRoster:
        self.load_count += 1
        return self.roster


class FakeTagoClient(TagoClient):
    def __init__(
        self,
        catalog: StationCatalog | None = None,
        error: BaseException | None = None,
        gate: asyncio.Event | None = None,
    ) -> None:
        super().__init__(Settings(tago_service_key="test-key"))
        self.catalog = catalog
        self.error = error
        self.gate = gate
        self.fetch_count = 0
        self.fetch_started = asyncio.Event()

    async def fetch_station_catalog(self, provider: Provider) -> StationCatalog:
        self.fetch_count += 1
        self.fetch_started.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        assert self.catalog is not None
        return self.catalog.model_copy(update={"provider": provider})


async def seed_snapshot(
    repository: StationCatalogRepository,
    *,
    retrieved_at: datetime,
    refresh_after: datetime,
    stations: list[StationItem] | None = None,
) -> None:
    now = retrieved_at
    assert await repository.try_acquire_lease("seed", now, now + timedelta(minutes=5))
    assert await repository.save_success(
        "seed",
        stations or [station()],
        stations or [station()],
        visibility_roster(stations or [station()]),
        retrieved_at,
        refresh_after,
        now,
    )


async def test_repository_lease_is_portable_and_fences_late_old_owner(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    repository = StationCatalogRepository(factory)
    started = datetime(2026, 7, 29, 1, tzinfo=UTC)

    assert await repository.try_acquire_lease("old-owner", started, started + timedelta(seconds=10))
    assert not await repository.try_acquire_lease(
        "new-owner", started + timedelta(seconds=5), started + timedelta(seconds=20)
    )
    assert await repository.try_acquire_lease(
        "new-owner", started + timedelta(seconds=11), started + timedelta(seconds=30)
    )
    assert not await repository.save_success(
        "old-owner",
        [station("OLD", "오래된역")],
        [station("OLD", "오래된역")],
        visibility_roster([station("OLD", "오래된역")]),
        started,
        started + timedelta(hours=24),
        started + timedelta(seconds=12),
    )
    assert await repository.save_success(
        "new-owner",
        [station("NEW", "새역")],
        [station("NEW", "새역")],
        visibility_roster([station("NEW", "새역")]),
        started + timedelta(seconds=11),
        started + timedelta(hours=24),
        started + timedelta(seconds=12),
    )

    snapshot = await repository.load_snapshot()
    assert snapshot is not None
    assert [item.node_id for item in snapshot.identity_stations] == ["NEW"]
    assert [item.node_id for item in snapshot.display_stations] == ["NEW"]


async def test_fresh_snapshot_survives_restart_and_hydrates_l1_without_http(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    repository = StationCatalogRepository(factory)
    now = datetime.now(UTC)
    await seed_snapshot(
        repository,
        retrieved_at=now - timedelta(minutes=1),
        refresh_after=now + timedelta(hours=23),
        stations=[station("N-SUSEO", "수서")],
    )
    http_calls = 0

    def reject_http(request: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        http_calls += 1
        raise AssertionError(f"unexpected HTTP request: {request.url.path}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(reject_http)) as http_client:
        tago = TagoClient(Settings(tago_service_key="test-key"), http_client)
        visibility = FakeStationVisibility()
        service = StationCatalogService(factory, tago, visibility)
        catalog = await service.get_catalog(Provider.SRT)
        hydrated = await tago.station_catalog(Provider.KORAIL)
        await service.close()

    assert catalog.provider is Provider.SRT
    assert catalog.catalog_scope == "intercity_station_guide_intersection"
    assert hydrated.provider is Provider.KORAIL
    assert catalog.retrieved_at == hydrated.retrieved_at
    assert http_calls == 0
    assert visibility.load_count == 0


async def test_stale_snapshot_returns_immediately_then_refreshes(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    repository = StationCatalogRepository(factory)
    now = datetime.now(UTC)
    await seed_snapshot(
        repository,
        retrieved_at=now - timedelta(days=2),
        refresh_after=now - timedelta(days=1),
        stations=[station("OLD", "기존역")],
    )
    gate = asyncio.Event()
    tago = FakeTagoClient(
        upstream_catalog([station("NEW", "새역")], now),
        gate=gate,
    )
    service = StationCatalogService(factory, tago, FakeStationVisibility(tago.catalog.stations))

    catalog = await asyncio.wait_for(service.get_catalog(Provider.KORAIL), timeout=0.2)
    assert [item.node_id for item in catalog.stations] == ["OLD"]
    assert tago.fetch_count == 0 or tago.fetch_count == 1

    gate.set()
    assert service._refresh_task is not None
    await service._refresh_task
    refreshed = await repository.load_snapshot()
    await service.close()

    assert refreshed is not None
    assert [item.node_id for item in refreshed.identity_stations] == ["NEW"]
    assert [item.node_id for item in refreshed.display_stations] == ["NEW"]
    assert tago.fetch_count == 1


async def test_concurrent_provider_requests_share_one_initial_collection(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime.now(UTC)
    gate = asyncio.Event()
    shared_stations = [station("N-SEOUL", "서울"), station("N-SUSEO", "수서")]
    tago = FakeTagoClient(upstream_catalog(shared_stations, now), gate=gate)
    service = StationCatalogService(factory, tago, FakeStationVisibility(shared_stations))

    korail_task = asyncio.create_task(service.get_catalog(Provider.KORAIL))
    srt_task = asyncio.create_task(service.get_catalog(Provider.SRT))
    try:
        await asyncio.wait_for(tago.fetch_started.wait(), timeout=2)
        assert tago.fetch_count == 1
    except BaseException:
        gate.set()
        await asyncio.gather(korail_task, srt_task, return_exceptions=True)
        await service.close()
        raise
    gate.set()
    korail, srt = await asyncio.gather(korail_task, srt_task)
    await service.close()

    assert korail.provider is Provider.KORAIL
    assert srt.provider is Provider.SRT
    assert [item.name for item in korail.stations] == ["서울", "수서"]
    assert [item.name for item in srt.stations] == ["서울", "수서"]
    assert tago.fetch_count == 1


async def test_srt_catalog_shares_the_intercity_station_union_with_korail(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime.now(UTC)
    stations = [
        station("N-SEOUL", "서울"),
        station("N-SUSEO", "수서"),
        station("N-DAEJEON", "대전"),
        station("N-BUSAN", "부산"),
    ]
    await seed_snapshot(
        StationCatalogRepository(factory),
        retrieved_at=now,
        refresh_after=now + timedelta(hours=23),
        stations=stations,
    )
    service = StationCatalogService(factory)

    korail = await service.get_catalog(Provider.KORAIL)
    srt = await service.get_catalog(Provider.SRT)
    await service.close()

    assert [item.name for item in korail.stations] == ["서울", "수서", "대전", "부산"]
    assert [item.name for item in srt.stations] == ["서울", "수서", "대전", "부산"]
    assert srt.provider_membership == "not_verified_by_source"
    assert "운영사 소속" in srt.note


@pytest.mark.parametrize(
    ("catalog", "error"),
    [
        (None, ProviderUnavailable("response body intentionally omitted")),
        (upstream_catalog([], datetime.now(UTC)), None),
    ],
)
async def test_failed_or_empty_refresh_preserves_last_known_good(db_engine, catalog, error):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    repository = StationCatalogRepository(factory)
    now = datetime.now(UTC)
    await seed_snapshot(
        repository,
        retrieved_at=now - timedelta(days=2),
        refresh_after=now - timedelta(days=1),
        stations=[station("GOOD", "정상역")],
    )
    visibility_stations = (
        catalog.stations if catalog is not None and catalog.stations else [station()]
    )
    service = StationCatalogService(
        factory,
        FakeTagoClient(catalog, error=error),
        FakeStationVisibility(visibility_stations),
    )

    immediate = await service.get_catalog(Provider.KORAIL)
    assert service._refresh_task is not None
    await service._refresh_task
    preserved = await repository.load_snapshot()
    async with factory() as session:
        row = await session.get(StationCatalogCache, CANONICAL_CACHE_KEY)
    await service.close()

    assert [item.node_id for item in immediate.stations] == ["GOOD"]
    assert preserved is not None
    assert [item.node_id for item in preserved.identity_stations] == ["GOOD"]
    assert [item.node_id for item in preserved.display_stations] == ["GOOD"]
    assert row is not None
    assert row.last_error_category in {"provider_unavailable", "invalid_catalog"}
    assert "response body" not in (row.last_error_category or "")


async def test_no_snapshot_failure_is_bounded_and_fails_closed(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    service = StationCatalogService(
        factory,
        FakeTagoClient(error=ProviderUnavailable("secret upstream response omitted")),
        FakeStationVisibility(),
    )

    with pytest.raises(ProviderUnavailable, match="catalog is unavailable"):
        await asyncio.wait_for(service.get_catalog(Provider.KORAIL), timeout=1)
    await service.close()


async def test_preload_starts_collection_without_blocking_health_startup(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    gate = asyncio.Event()
    service = StationCatalogService(
        factory,
        FakeTagoClient(upstream_catalog([station()], datetime.now(UTC)), gate=gate),
        FakeStationVisibility(),
    )

    await asyncio.wait_for(service.preload(), timeout=0.2)
    assert service._refresh_task is not None
    assert not service._refresh_task.done()
    await service.close()


async def test_snapshot_keeps_raw_identity_catalog_but_returns_only_visible_stations(
    db_engine,
):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    raw = [station("N1", "서울"), station("N2", "광운대")]
    tago = FakeTagoClient(upstream_catalog(raw, datetime.now(UTC)))
    visibility = FakeStationVisibility([raw[0]])
    service = StationCatalogService(factory, tago, visibility)

    catalog = await service.get_catalog(Provider.KORAIL)
    hydrated = await tago.station_catalog(Provider.KORAIL)
    snapshot = await service.repository.load_snapshot()
    await service.close()

    assert [item.node_id for item in catalog.stations] == ["N1"]
    assert [item.node_id for item in hydrated.stations] == ["N1", "N2"]
    assert snapshot is not None
    assert [item.node_id for item in snapshot.identity_stations] == ["N1", "N2"]
    assert [item.node_id for item in snapshot.display_stations] == ["N1"]
    assert KORAIL_STATION_DATA_URL.startswith("https://www.korail.com/")
