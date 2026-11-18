from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from importlib import resources

import httpx2
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from rail_waitlist.config import Settings
from rail_waitlist.domain import Provider
from rail_waitlist.providers import ProviderUnavailable, TagoClient
from rail_waitlist.timetable_management import catalog_application
from rail_waitlist.timetable_management.bundled_station_catalog import (
    BUNDLED_STATION_CATALOG_FORMAT,
    BUNDLED_STATION_CATALOG_FORMAT_VERSION,
    BUNDLED_STATION_CATALOG_PACKAGE,
    BUNDLED_STATION_CATALOG_RESOURCE,
    BundledStationCatalogError,
    load_bundled_station_catalog,
    parse_bundled_station_catalog,
)
from rail_waitlist.timetable_management.catalog_application import (
    CANONICAL_CACHE_KEY,
    STATION_CATALOG_SCHEMA_VERSION,
    StationCatalogRepository,
    StationCatalogService,
)
from rail_waitlist.timetable_management.models import StationCatalogCache
from rail_waitlist.timetable_management.schemas import StationCatalog, StationItem
from rail_waitlist.timetable_management.station_visibility import (
    KORAIL_STATION_DATA_URL,
    StationVisibilityRoster,
    normalize_visibility_station_name,
)


def station(node_id: str, name: str) -> StationItem:
    return StationItem(node_id=node_id, name=name, city_code="11", city_name="서울특별시")


def roster(stations: list[StationItem]) -> StationVisibilityRoster:
    return StationVisibilityRoster(
        names=frozenset(normalize_visibility_station_name(item.name) for item in stations),
        retrieved_at=datetime.now(UTC),
        etag=None,
        last_modified=None,
        canonical_names={
            normalize_visibility_station_name(item.name): item.name for item in stations
        },
        station_codes={
            normalize_visibility_station_name(item.name): item.node_id for item in stations
        },
    )


class CountingStationVisibility:
    url = KORAIL_STATION_DATA_URL

    def __init__(self, stations: list[StationItem] | None = None) -> None:
        self.roster = roster(stations or [station("N-SEOUL", "서울")])
        self.load_count = 0

    async def load_roster(self) -> StationVisibilityRoster:
        self.load_count += 1
        return self.roster


class ScriptedTagoClient(TagoClient):
    def __init__(
        self,
        *,
        service_key: str | None,
        stations: list[StationItem] | None = None,
        error: BaseException | None = None,
    ) -> None:
        super().__init__(Settings(tago_service_key=service_key))
        self.stations = stations
        self.error = error
        self.fetch_count = 0

    async def fetch_station_catalog(self, provider: Provider) -> StationCatalog:
        self.fetch_count += 1
        if self.error is not None:
            raise self.error
        assert self.stations is not None
        return StationCatalog(
            provider=provider,
            source="TAGO",
            retrieved_at=datetime.now(UTC),
            catalog_scope="all_tago_train_stations",
            provider_membership="not_verified_by_source",
            note="TAGO 공용 철도역 카탈로그 테스트입니다.",
            stations=self.stations,
        )


def bundled():
    return load_bundled_station_catalog(STATION_CATALOG_SCHEMA_VERSION)


async def cache_row(factory) -> StationCatalogCache | None:
    async with factory() as session:
        return await session.get(StationCatalogCache, CANONICAL_CACHE_KEY)


async def seed_cache(factory, *, fresh: bool, stations: list[StationItem]) -> None:
    repository = StationCatalogRepository(factory)
    now = datetime.now(UTC)
    retrieved_at = now - (timedelta(minutes=1) if fresh else timedelta(days=2))
    assert await repository.try_acquire_lease("seed", now, now + timedelta(minutes=5))
    assert await repository.save_success(
        "seed",
        stations,
        stations,
        roster(stations),
        retrieved_at,
        retrieved_at + timedelta(hours=24),
        now,
    )


async def test_missing_key_serves_bundled_snapshot_without_tago_or_cache_writes(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    http_calls = 0

    def reject_http(request: httpx2.Request) -> httpx2.Response:
        nonlocal http_calls
        http_calls += 1
        raise AssertionError(f"unexpected HTTP request: {request.url.path}")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(reject_http)) as http_client:
        tago = TagoClient(Settings(tago_service_key=None), http_client)
        visibility = CountingStationVisibility()
        service = StationCatalogService(factory, tago, visibility)
        await service.preload()
        korail = await service.get_catalog(Provider.KORAIL)
        srt = await service.get_catalog(Provider.SRT)
        row = await cache_row(factory)
        await service.close()

    expected = bundled()
    assert korail.provider is Provider.KORAIL
    assert srt.provider is Provider.SRT
    for catalog in (korail, srt):
        assert catalog.catalog_origin == "bundled_snapshot"
        assert catalog.source == "TAGO"
        assert catalog.catalog_scope == "intercity_station_guide_intersection"
        assert catalog.provider_membership == "not_verified_by_source"
        assert catalog.retrieved_at == expected.retrieved_at
        assert catalog.stations == list(expected.display_stations)
    assert http_calls == 0
    assert visibility.load_count == 0
    assert service._refresh_task is None
    assert row is not None
    assert row.payload is None
    assert row.refresh_owner is None
    assert row.last_attempt_at is None
    assert row.last_error_category is None


async def test_configured_key_without_cache_falls_back_and_keeps_retrying(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tago = ScriptedTagoClient(
        service_key="test-key", error=ProviderUnavailable("upstream response omitted")
    )
    service = StationCatalogService(factory, tago, CountingStationVisibility())

    first = await service.get_catalog(Provider.KORAIL)
    second = await service.get_catalog(Provider.KORAIL)
    persisted = await service.repository.load_snapshot()
    row = await cache_row(factory)
    await service.close()

    assert first.catalog_origin == "bundled_snapshot"
    assert second.catalog_origin == "bundled_snapshot"
    assert tago.fetch_count == 2
    assert persisted is None
    assert row is not None
    assert row.payload is None
    assert row.retrieved_at is None
    assert row.last_error_category == "provider_unavailable"


@pytest.mark.parametrize("fresh", [True, False])
async def test_valid_cache_is_served_instead_of_bundled_snapshot(db_engine, fresh):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    cached = [station("N-CACHED", "캐시역")]
    await seed_cache(factory, fresh=fresh, stations=cached)
    tago = ScriptedTagoClient(service_key="test-key", stations=[station("N-NEW", "새역")])
    service = StationCatalogService(factory, tago, CountingStationVisibility(tago.stations))

    catalog = await service.get_catalog(Provider.KORAIL)
    if service._refresh_task is not None:
        await service._refresh_task
    await service.close()

    assert catalog.catalog_origin == "collected"
    assert [item.node_id for item in catalog.stations] == ["N-CACHED"]
    assert tago.fetch_count == (0 if fresh else 1)


async def test_stale_cache_without_key_is_served_without_refresh(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    await seed_cache(factory, fresh=False, stations=[station("N-CACHED", "캐시역")])
    tago = ScriptedTagoClient(service_key=None, stations=[station("N-NEW", "새역")])
    service = StationCatalogService(factory, tago, CountingStationVisibility())

    await service.preload()
    catalog = await service.get_catalog(Provider.KORAIL)
    row = await cache_row(factory)
    await service.close()

    assert catalog.catalog_origin == "collected"
    assert [item.node_id for item in catalog.stations] == ["N-CACHED"]
    assert tago.fetch_count == 0
    assert service._refresh_task is None
    assert row is not None
    assert row.last_error_category is None


async def test_key_configured_after_bundled_fallback_collects_fresh_catalog(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    keyless = StationCatalogService(
        factory, ScriptedTagoClient(service_key=None), CountingStationVisibility()
    )
    fallback = await keyless.get_catalog(Provider.KORAIL)
    await keyless.close()

    collected_stations = [station("N-SEOUL", "서울"), station("N-SUSEO", "수서")]
    tago = ScriptedTagoClient(service_key="test-key", stations=collected_stations)
    restarted = StationCatalogService(factory, tago, CountingStationVisibility(collected_stations))
    collected = await restarted.get_catalog(Provider.KORAIL)
    persisted = await restarted.repository.load_snapshot()
    await restarted.close()

    assert fallback.catalog_origin == "bundled_snapshot"
    assert collected.catalog_origin == "collected"
    assert [item.node_id for item in collected.stations] == ["N-SEOUL", "N-SUSEO"]
    assert collected.retrieved_at > fallback.retrieved_at
    assert tago.fetch_count == 1
    assert persisted is not None
    assert [item.node_id for item in persisted.display_stations] == ["N-SEOUL", "N-SUSEO"]


async def test_unusable_bundled_snapshot_keeps_the_previous_fail_closed_error(
    db_engine, monkeypatch
):
    def broken_snapshot(schema_version: int):
        raise BundledStationCatalogError("bundled station catalog is unreadable: OSError")

    monkeypatch.setattr(catalog_application, "load_bundled_station_catalog", broken_snapshot)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    service = StationCatalogService(
        factory, ScriptedTagoClient(service_key=None), CountingStationVisibility()
    )

    with pytest.raises(ProviderUnavailable, match="station catalog is unavailable"):
        await service.get_catalog(Provider.KORAIL)
    await service.close()


async def test_stations_endpoint_marks_bundled_snapshot_without_tago_key(app, client):
    previous = app.state.station_catalog_service
    service = StationCatalogService(
        app.state.test_session_factory,
        ScriptedTagoClient(service_key=None),
        CountingStationVisibility(),
    )
    app.state.station_catalog_service = service
    try:
        response = await client.get("/api/v1/stations", params={"provider": "korail"})
    finally:
        app.state.station_catalog_service = previous
        await service.close()

    expected = bundled()
    assert response.status_code == 200
    payload = response.json()
    assert payload["catalog_origin"] == "bundled_snapshot"
    assert payload["source"] == "TAGO"
    assert payload["catalog_scope"] == "intercity_station_guide_intersection"
    assert payload["provider_membership"] == "not_verified_by_source"
    assert datetime.fromisoformat(payload["retrieved_at"]) == expected.retrieved_at
    assert len(payload["stations"]) == len(expected.display_stations)


def test_shipped_snapshot_satisfies_the_reviewed_contract() -> None:
    resource = resources.files(BUNDLED_STATION_CATALOG_PACKAGE).joinpath(
        *BUNDLED_STATION_CATALOG_RESOURCE
    )
    raw = json.loads(resource.read_text(encoding="utf-8"))
    snapshot = parse_bundled_station_catalog(raw, schema_version=STATION_CATALOG_SCHEMA_VERSION)

    assert set(raw) == {
        "format",
        "format_version",
        "station_catalog_schema_version",
        "source",
        "retrieved_at",
        "visibility",
        "identity_stations",
        "display_stations",
    }
    assert set(raw["visibility"]) == {"source", "url", "retrieved_at", "etag", "last_modified"}
    station_fields = {"node_id", "name", "city_code", "city_name"}
    for key in ("identity_stations", "display_stations"):
        assert all(set(item) == station_fields for item in raw[key])
    assert raw["source"] == "TAGO"
    assert raw["visibility"]["source"] == "korail_station_guide"
    assert snapshot.visibility_url == KORAIL_STATION_DATA_URL
    assert snapshot.retrieved_at <= datetime.now(UTC)
    assert snapshot.visibility_retrieved_at <= datetime.now(UTC)
    identity_ids = {item.node_id for item in snapshot.identity_stations}
    display_ids = {item.node_id for item in snapshot.display_stations}
    assert len(identity_ids) == len(snapshot.identity_stations) > 0
    assert len(display_ids) == len(snapshot.display_stations) > 0
    assert display_ids <= identity_ids
    display_names = {item.name for item in snapshot.display_stations}
    assert {"서울", "수서", "대전", "부산"} <= display_names
    assert load_bundled_station_catalog(STATION_CATALOG_SCHEMA_VERSION) == snapshot


def valid_document() -> dict[str, object]:
    seoul = station("N-SEOUL", "서울").model_dump(mode="json")
    busan = station("N-BUSAN", "부산").model_dump(mode="json")
    return {
        "format": BUNDLED_STATION_CATALOG_FORMAT,
        "format_version": BUNDLED_STATION_CATALOG_FORMAT_VERSION,
        "station_catalog_schema_version": STATION_CATALOG_SCHEMA_VERSION,
        "source": "TAGO",
        "retrieved_at": "2026-11-18T12:35:53.649310+00:00",
        "visibility": {
            "source": "korail_station_guide",
            "url": KORAIL_STATION_DATA_URL,
            "retrieved_at": "2026-11-18T12:35:55.072141+00:00",
            "etag": None,
            "last_modified": None,
        },
        "identity_stations": [seoul, busan],
        "display_stations": [seoul],
    }


def test_valid_document_parses_to_utc_provenance() -> None:
    snapshot = parse_bundled_station_catalog(
        valid_document(), schema_version=STATION_CATALOG_SCHEMA_VERSION
    )

    assert snapshot.retrieved_at == datetime(2026, 11, 18, 12, 35, 53, 649310, tzinfo=UTC)
    assert [item.node_id for item in snapshot.display_stations] == ["N-SEOUL"]


def mutated(path: tuple[str, ...], value: object) -> dict[str, object]:
    document = valid_document()
    target: dict[str, object] = document
    for key in path[:-1]:
        nested = target[key]
        assert isinstance(nested, dict)
        target = nested
    target[path[-1]] = value
    return document


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ([], "JSON object"),
        (mutated(("format",), "other"), "format"),
        (mutated(("station_catalog_schema_version",), 3), "catalog schema"),
        (mutated(("source",), "mock"), "source must be TAGO"),
        (mutated(("retrieved_at",), None), "retrieved_at"),
        (mutated(("retrieved_at",), "2026-11-18T12:35:53"), "timezone"),
        (mutated(("visibility",), None), "visibility provenance"),
        (mutated(("visibility", "url"), "https://example.com/stations.json"), "KORAIL"),
        (mutated(("visibility", "retrieved_at"), ""), "visibility.retrieved_at"),
        (mutated(("visibility", "etag"), 1), "visibility.etag"),
        (mutated(("identity_stations",), []), "must not be empty"),
        (mutated(("display_stations",), []), "must not be empty"),
        (
            mutated(
                ("identity_stations",),
                [
                    station("N-SEOUL", "서울").model_dump(mode="json"),
                    station("N-SEOUL", "서울역").model_dump(mode="json"),
                ],
            ),
            "duplicate node ids",
        ),
        (
            mutated(
                ("display_stations",),
                [station("N-OUTSIDE", "외부역").model_dump(mode="json")],
            ),
            "identity stations",
        ),
        (mutated(("display_stations",), [{"node_id": "N-SEOUL"}]), "invalid station"),
    ],
)
def test_invalid_documents_fail_closed(document: object, message: str) -> None:
    with pytest.raises(BundledStationCatalogError, match=message):
        parse_bundled_station_catalog(document, schema_version=STATION_CATALOG_SCHEMA_VERSION)
