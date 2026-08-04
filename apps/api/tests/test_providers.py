from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest

from rail_waitlist.config import Settings
from rail_waitlist.domain import Provider, ReservationOutcome, SeatClass
from rail_waitlist.korail_execution import default_korail_execution_source
from rail_waitlist.provider_accounts import ProviderCredentials
from rail_waitlist.providers import (
    ExperimentalRailAdapter,
    KorailBrowserExecutionAdapter,
    MockProviderAdapter,
    OfficialTimetableAdapter,
    ProviderUnavailable,
    RouteValidationError,
    SrtLiveExecutionAdapter,
    TagoClient,
    get_execution_provider,
    get_provider,
    get_timetable_provider,
    list_capabilities,
    response_page,
)
from rail_waitlist.schemas import (
    ReservationRequest,
    ReservationResult,
    SeatObservationRequest,
    SeatObservationResult,
)
from rail_waitlist.srt_execution import default_srt_execution_source


async def test_registry_separates_korail_timetable_from_fail_closed_execution():
    provider = Provider.KORAIL
    settings = Settings(
        _env_file=None,
        tago_service_key=None,
        tago_service_key_file="missing-test-key",
    )

    timetable = get_timetable_provider(provider, settings)
    execution = get_execution_provider(provider, settings)

    assert isinstance(timetable, OfficialTimetableAdapter)
    assert timetable.capabilities().timetable is True
    assert timetable.official_booking_url() == "https://www.korail.com/ticket/search/general"
    assert isinstance(execution, KorailBrowserExecutionAdapter)
    assert execution.official_booking_url() == timetable.official_booking_url()
    assert execution.capabilities().timetable is False
    assert execution.capabilities().official_booking_link is False
    assert execution.capabilities().seat_monitoring is False
    assert execution.capabilities().reservation_once is False
    with pytest.raises(ProviderUnavailable, match="does not expose timetables"):
        await execution.timetable("서울", "부산", datetime(2026, 8, 1, 8))
    with pytest.raises(ProviderUnavailable, match="does not expose stations"):
        await execution.stations()


@pytest.mark.parametrize(
    ("experimental", "browser_adapter", "monitoring", "expected"),
    [
        (False, False, False, False),
        (False, True, True, False),
        (True, False, True, False),
        (True, True, False, False),
        (True, True, True, True),
    ],
)
def test_korail_execution_registry_requires_all_three_explicit_flags(
    experimental,
    browser_adapter,
    monitoring,
    expected,
):
    settings = Settings(
        _env_file=None,
        EXPERIMENTAL_RAIL_ENABLED=experimental,
        korail_browser_adapter_enabled=browser_adapter,
        korail_seat_monitoring_enabled=monitoring,
        korail_browser_adapter_token="b" * 32 if browser_adapter and experimental else None,
    )

    execution = get_execution_provider(Provider.KORAIL, settings)

    assert isinstance(execution, KorailBrowserExecutionAdapter)
    assert execution.capabilities().enabled is expected
    assert execution.capabilities().seat_monitoring is expected
    assert execution.capabilities().reservation_once is False
    assert execution.capabilities().timetable is False


@pytest.mark.parametrize(
    ("experimental", "seat_status", "monitoring", "expected"),
    [
        (False, False, False, False),
        (False, True, True, False),
        (True, False, True, False),
        (True, True, False, False),
        (True, True, True, True),
    ],
)
def test_srt_execution_registry_requires_all_three_explicit_flags(
    experimental,
    seat_status,
    monitoring,
    expected,
):
    settings = Settings(
        _env_file=None,
        EXPERIMENTAL_RAIL_ENABLED=experimental,
        srt_seat_status_enabled=seat_status,
        srt_seat_monitoring_enabled=monitoring,
    )

    execution = get_execution_provider(Provider.SRT, settings)

    assert isinstance(execution, SrtLiveExecutionAdapter)
    assert execution.capabilities().enabled is expected
    assert execution.capabilities().seat_monitoring is expected
    assert execution.capabilities().reservation_once is False
    assert execution.capabilities().timetable is False


def test_capability_list_combines_timetable_and_enabled_srt_execution_contract():
    settings = Settings(
        _env_file=None,
        EXPERIMENTAL_RAIL_ENABLED=True,
        srt_seat_status_enabled=True,
        srt_seat_monitoring_enabled=True,
    )

    capabilities = list_capabilities(settings)
    official_srt = next(
        item
        for item in capabilities
        if item.provider == Provider.SRT and item.experimental is False
    )

    assert official_srt.timetable is True
    assert official_srt.official_booking_link is True
    assert official_srt.seat_monitoring is True
    assert official_srt.reservation_once is False


def test_mock_registry_is_executable_and_legacy_alias_remains_timetable_scoped():
    timetable = get_timetable_provider(Provider.MOCK)
    execution = get_execution_provider(Provider.MOCK)
    legacy = get_provider(Provider.MOCK)

    assert isinstance(timetable, MockProviderAdapter)
    assert isinstance(execution, MockProviderAdapter)
    assert execution.capabilities().seat_monitoring is True
    assert execution.capabilities().reservation_once is True
    assert isinstance(legacy, MockProviderAdapter)


def tago_response(items, **metadata):
    item_count = len(items) if isinstance(items, list) else 1
    page_metadata = {
        "totalCount": item_count,
        "numOfRows": max(item_count, 1),
        "pageNo": 1,
        **metadata,
    }
    return {
        "response": {
            "header": {"resultCode": "00"},
            "body": {"items": {"item": items}, **page_metadata},
        }
    }


async def test_official_tago_adapter_resolves_stations_filters_provider_and_caches():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.url.params["serviceKey"] == "decoded-key"
        if request.url.path.endswith("GetCtyCodeList"):
            return httpx.Response(200, json=tago_response([
                {"citycode": "11", "cityname": "서울특별시"},
                {"citycode": "26", "cityname": "부산광역시"},
            ]))
        if request.url.path.endswith("GetCtyAcctoTrainSttnList"):
            city = request.url.params["cityCode"]
            return httpx.Response(200, json=tago_response(
                [{"nodeid": "N1", "nodename": "서울"}, {"nodeid": "N2", "nodename": "수서"}]
                if city == "11" else [{"nodeid": "N3", "nodename": "부산"}]
            ))
        assert request.url.params["depPlaceId"] == "N1"
        assert request.url.params["arrPlaceId"] == "N3"
        return httpx.Response(200, json=tago_response([
            {
                "trainno": "101",
                "traingradename": "KTX",
                "depplandtime": "20260801090000",
                "arrplandtime": "20260801113000",
                "depplacename": "서울",
                "arrplacename": "부산",
                "adultcharge": "59,800",
            },
            {
                "trainno": "201",
                "traingradename": "SRT",
                "depplandtime": "20260801100000",
                "arrplandtime": "20260801123000",
                "depplacename": "수서",
                "arrplacename": "부산",
            },
            {
                "trainno": "301",
                "traingradename": "ITX-새마을",
                "depplandtime": "20260801110000",
                "arrplandtime": "20260801150000",
                "depplacename": "서울",
                "arrplacename": "부산",
            },
        ]))

    settings = Settings(tago_service_key="decoded-key", tago_cache_ttl_seconds=300)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        tago = TagoClient(settings, http_client)
        adapter = OfficialTimetableAdapter(Provider.KORAIL, settings, tago)
        first = await adapter.timetable(
            "서울역", "부산", datetime(2026, 8, 1, 8), "N1", "N3"
        )
        second = await adapter.timetable(
            "서울", "부산역", datetime(2026, 8, 1, 8), "N1", "N3"
        )
    assert [item.train_number for item in first] == ["101"]
    assert [item.train_number for item in second] == ["101"]
    assert first[0].train_type == "KTX"
    assert first[0].adult_fare == 59800
    assert first[0].timetable_source == "TAGO"
    assert first[0].availability.status == "unavailable"
    assert first[0].availability.observed_at is None
    assert [seat.seat_class for seat in first[0].seat_classes] == [
        SeatClass.STANDARD,
        SeatClass.FIRST,
    ]
    assert {seat.status for seat in first[0].seat_classes} == {"unknown"}
    assert all(seat.provenance.kind == "not_observed" for seat in first[0].seat_classes)
    assert all(
        seat.provenance.reason == "source_not_configured"
        for seat in first[0].seat_classes
    )
    assert all(seat.provenance.source is None for seat in first[0].seat_classes)
    assert all(seat.provenance.observed_at is None for seat in first[0].seat_classes)
    assert all(seat.fare is None for seat in first[0].seat_classes)
    assert all(
        [action.kind for action in seat.actions] == ["official_check", "add_to_watch"]
        for seat in first[0].seat_classes
    )
    assert first[0].timetable_retrieved_at == second[0].timetable_retrieved_at
    assert sum(path.endswith("GetStrtpntAlocFndTrainInfo") for path in calls) == 1


async def test_official_timetable_collects_every_page_and_filters_cached_raw_day_by_window():
    timetable_pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("GetCtyCodeList"):
            return httpx.Response(
                200,
                json=tago_response([{"citycode": "11", "cityname": "서울특별시"}]),
            )
        if request.url.path.endswith("GetCtyAcctoTrainSttnList"):
            return httpx.Response(
                200,
                json=tago_response([
                    {"nodeid": "N1", "nodename": "서울"},
                    {"nodeid": "N3", "nodename": "부산"},
                ]),
            )

        page = int(request.url.params["pageNo"])
        timetable_pages.append(page)
        rows = [
            {
                "trainno": f"ITX-{index:03d}",
                "traingradename": "ITX-새마을",
                "depplandtime": "20260801070000",
                "arrplandtime": "20260801090000",
                "depplacename": "서울",
                "arrplacename": "부산",
            }
            for index in range(100)
        ] if page == 1 else [{
            "trainno": "999",
            "traingradename": "KTX",
            "depplandtime": "20260801200000",
            "arrplandtime": "20260801223000",
            "depplacename": "서울",
            "arrplacename": "부산",
        }]
        return httpx.Response(
            200,
            json=tago_response(
                rows,
                totalCount=101,
                numOfRows=100,
                pageNo=page,
            ),
        )

    settings = Settings(tago_service_key="decoded-key", tago_cache_ttl_seconds=300)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        adapter = OfficialTimetableAdapter(
            Provider.KORAIL,
            settings,
            TagoClient(settings, http_client),
        )
        early = await adapter.timetable(
            "서울",
            "부산",
            datetime(2026, 8, 1, 8),
            "N1",
            "N3",
            departure_to=datetime(2026, 8, 1, 19),
        )
        evening = await adapter.timetable(
            "서울",
            "부산",
            datetime(2026, 8, 1, 19),
            "N1",
            "N3",
            departure_to=datetime(2026, 8, 1, 21),
        )

    assert early == []
    assert [item.train_number for item in evening] == ["999"]
    assert timetable_pages == [1, 2]


async def test_official_timetable_includes_both_exact_departure_window_boundaries():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("GetCtyCodeList"):
            return httpx.Response(
                200,
                json=tago_response([{"citycode": "11", "cityname": "서울특별시"}]),
            )
        if request.url.path.endswith("GetCtyAcctoTrainSttnList"):
            return httpx.Response(
                200,
                json=tago_response([
                    {"nodeid": "N1", "nodename": "서울"},
                    {"nodeid": "N3", "nodename": "부산"},
                ]),
            )
        rows = [
            {
                "trainno": train_number,
                "traingradename": "KTX",
                "depplandtime": departure,
                "arrplandtime": arrival,
                "depplacename": "서울",
                "arrplacename": "부산",
            }
            for train_number, departure, arrival in [
                ("BEFORE", "20260801075959", "20260801100000"),
                ("FROM", "20260801080000", "20260801103000"),
                ("TO", "20260801120000", "20260801143000"),
                ("AFTER", "20260801120001", "20260801143001"),
            ]
        ]
        return httpx.Response(200, json=tago_response(rows))

    settings = Settings(tago_service_key="decoded-key")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        items = await OfficialTimetableAdapter(
            Provider.KORAIL,
            settings,
            TagoClient(settings, http_client),
        ).timetable(
            "서울",
            "부산",
            datetime.fromisoformat("2026-08-01T08:00:00+09:00"),
            "N1",
            "N3",
            departure_to=datetime.fromisoformat("2026-08-01T12:00:00+09:00"),
        )

    assert [item.train_number for item in items] == ["FROM", "TO"]


async def test_official_timetable_does_not_reuse_raw_cache_across_service_dates():
    requested_service_dates: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("GetCtyCodeList"):
            return httpx.Response(
                200,
                json=tago_response([{"citycode": "11", "cityname": "서울특별시"}]),
            )
        if request.url.path.endswith("GetCtyAcctoTrainSttnList"):
            return httpx.Response(
                200,
                json=tago_response([
                    {"nodeid": "N1", "nodename": "서울"},
                    {"nodeid": "N3", "nodename": "부산"},
                ]),
            )
        service_date = request.url.params["depPlandTime"]
        requested_service_dates.append(service_date)
        return httpx.Response(200, json=tago_response([{
            "trainno": service_date,
            "traingradename": "KTX",
            "depplandtime": f"{service_date}090000",
            "arrplandtime": f"{service_date}113000",
            "depplacename": "서울",
            "arrplacename": "부산",
        }]))

    settings = Settings(tago_service_key="decoded-key", tago_cache_ttl_seconds=300)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        adapter = OfficialTimetableAdapter(
            Provider.KORAIL,
            settings,
            TagoClient(settings, http_client),
        )
        first_day = await adapter.timetable(
            "서울",
            "부산",
            datetime(2026, 8, 1, 8),
            "N1",
            "N3",
            departure_to=datetime(2026, 8, 1, 12),
        )
        second_day = await adapter.timetable(
            "서울",
            "부산",
            datetime(2026, 8, 2, 8),
            "N1",
            "N3",
            departure_to=datetime(2026, 8, 2, 12),
        )

    assert requested_service_dates == ["20260801", "20260802"]
    assert [item.train_number for item in first_day] == ["20260801"]
    assert [item.train_number for item in second_day] == ["20260802"]


async def test_departure_window_compares_different_offsets_by_korea_service_date():
    accepted = await MockProviderAdapter().timetable(
        "서울",
        "부산",
        datetime.fromisoformat("2026-08-01T08:00:00+09:00"),
        departure_to=datetime.fromisoformat("2026-08-01T02:00:00+00:00"),
    )

    assert accepted[0].departure_at.isoformat() == "2026-08-01T08:00:00+09:00"
    assert accepted[-1].departure_at.isoformat() == "2026-08-01T10:40:00+09:00"

    with pytest.raises(RouteValidationError, match="same Korea service date"):
        await MockProviderAdapter().timetable(
            "서울",
            "부산",
            datetime.fromisoformat("2026-08-01T23:00:00+09:00"),
            departure_to=datetime.fromisoformat("2026-08-01T15:30:00+00:00"),
        )


async def test_official_adapter_without_service_key_fails_instead_of_synthesizing():
    settings = Settings(tago_service_key=None, tago_service_key_file="missing-test-key")
    adapter = OfficialTimetableAdapter(Provider.KORAIL, settings, TagoClient(settings))
    with pytest.raises(ProviderUnavailable, match="service key"):
        await adapter.timetable(
            "서울", "부산", datetime(2026, 8, 1, 8), "N1", "N3"
        )


async def test_official_station_catalog_is_cached_and_does_not_infer_provider_membership():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("GetCtyCodeList"):
            return httpx.Response(200, json=tago_response([
                {"citycode": "11", "cityname": "서울특별시"},
                {"citycode": "26", "cityname": "부산광역시"},
            ]))
        city = request.url.params["cityCode"]
        return httpx.Response(200, json=tago_response(
            [
                {"nodeid": "N2", "nodename": "수서"},
                {"nodeid": "N1", "nodename": "서울"},
                {"nodeid": "", "nodename": "잘못된 역"},
            ]
            if city == "11"
            else [{"nodeid": "N3", "nodename": "부산"}]
        ))

    settings = Settings(tago_service_key="decoded-key")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        tago = TagoClient(settings, http_client)
        korail, srt = await asyncio.gather(
            OfficialTimetableAdapter(Provider.KORAIL, settings, tago).stations(),
            OfficialTimetableAdapter(Provider.SRT, settings, tago).stations(),
        )

    assert korail.provider == Provider.KORAIL
    assert srt.provider == Provider.SRT
    assert korail.source == srt.source == "TAGO"
    assert korail.catalog_scope == srt.catalog_scope == "all_tago_train_stations"
    assert korail.provider_membership == srt.provider_membership == "not_verified_by_source"
    assert "공용 철도역" in korail.note
    assert "소속" in korail.note
    assert korail.retrieved_at == srt.retrieved_at
    assert [station.node_id for station in korail.stations] == ["N3", "N1", "N2"]
    assert [station.node_id for station in srt.stations] == ["N3", "N1", "N2"]
    assert len(calls) == 3


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [],
        {"response": {}},
        {"response": {"header": {}, "body": {}}},
        {
            "response": {
                "header": {"resultCode": "00"},
                "body": {"items": {"item": []}},
            }
        },
    ],
)
def test_tago_page_rejects_malformed_or_metadata_free_envelopes(payload):
    with pytest.raises(ProviderUnavailable):
        response_page(payload)  # type: ignore[arg-type]


def test_tago_page_accepts_explicitly_unpaginated_city_catalog():
    payload = {
        "response": {
            "header": {"resultCode": "00"},
            "body": {
                "items": {
                    "item": [
                        {"citycode": "11", "cityname": "서울특별시"},
                        {"citycode": "26", "cityname": "부산광역시"},
                    ]
                }
            },
        }
    }

    page = response_page(payload, requested_page=1, requested_num_rows=100, allow_unpaginated=True)

    assert page.total_count == 2
    assert page.page_no == 1
    assert page.num_rows == 100


async def test_empty_tago_city_catalog_is_not_cached():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=tago_response([]))

    settings = Settings(tago_service_key="decoded-key")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        tago = TagoClient(settings, http_client)
        with pytest.raises(ProviderUnavailable, match="city catalog is empty"):
            await tago.station_catalog(Provider.KORAIL)
        assert tago._cached("cities") is None


async def test_station_catalog_retrieved_at_preserves_oldest_source_fetch_time():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("GetCtyCodeList"):
            return httpx.Response(
                200,
                json=tago_response([{"citycode": "11", "cityname": "서울특별시"}]),
            )
        return httpx.Response(
            200,
            json=tago_response([{"nodeid": "N1", "nodename": "서울"}]),
        )

    settings = Settings(tago_service_key="decoded-key")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        tago = TagoClient(settings, http_client)
        await tago.city_codes()
        city_retrieved_at = tago._cache_retrieved_at("cities")
        catalog = await tago.station_catalog(Provider.KORAIL)

    assert city_retrieved_at is not None
    assert catalog.retrieved_at == city_retrieved_at


async def test_station_catalog_collects_all_tago_city_and_station_pages():
    calls: list[tuple[str, int, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["pageNo"])
        city = request.url.params.get("cityCode")
        calls.append((request.url.path, page, city))
        if request.url.path.endswith("GetCtyCodeList"):
            rows = (
                [{"citycode": "11", "cityname": "서울특별시"}]
                if page == 1
                else [{"citycode": "26", "cityname": "부산광역시"}]
            )
            return httpx.Response(
                200,
                json=tago_response(rows, totalCount=2, numOfRows=1, pageNo=page),
            )
        node_number = 1 if page == 1 else 2
        return httpx.Response(
            200,
            json=tago_response(
                [{"nodeid": f"{city}-N{node_number}", "nodename": f"역-{city}-{node_number}"}],
                totalCount=2,
                numOfRows=1,
                pageNo=page,
            ),
        )

    settings = Settings(tago_service_key="decoded-key")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        catalog = await TagoClient(settings, http_client).station_catalog(Provider.KORAIL)

    assert len(catalog.stations) == 4
    assert {station.node_id for station in catalog.stations} == {
        "11-N1",
        "11-N2",
        "26-N1",
        "26-N2",
    }
    assert sum(path.endswith("GetCtyCodeList") for path, _, _ in calls) == 2
    assert sum(path.endswith("GetCtyAcctoTrainSttnList") for path, _, _ in calls) == 4


async def test_station_node_pairs_are_validated_and_skip_name_resolution():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("GetCtyCodeList"):
            return httpx.Response(
                200,
                json=tago_response([{"citycode": "11", "cityname": "서울특별시"}]),
            )
        if request.url.path.endswith("GetCtyAcctoTrainSttnList"):
            return httpx.Response(
                200,
                json=tago_response([
                    {"nodeid": "N1", "nodename": "서울"},
                    {"nodeid": "N2", "nodename": "수서"},
                    {"nodeid": "N3", "nodename": "부산"},
                ]),
            )
        departure = request.url.params["depPlaceId"]
        grade = "SRT" if departure == "N1" else "KTX"
        return httpx.Response(200, json=tago_response([{
            "trainno": "101",
            "traingradename": grade,
            "depplandtime": "20260801090000",
            "arrplandtime": "20260801113000",
            "depplacename": "서울" if departure == "N1" else "수서",
            "arrplacename": "부산",
        }]))

    settings = Settings(tago_service_key="decoded-key")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        tago = TagoClient(settings, http_client)

        async def fail_if_name_resolution_runs(station_name: str):
            raise AssertionError(f"resolve_station must not run for {station_name}")

        tago.resolve_station = fail_if_name_resolution_runs  # type: ignore[method-assign]
        srt_from_seoul = await OfficialTimetableAdapter(Provider.SRT, settings, tago).timetable(
            "서울", "부산", datetime(2026, 8, 1, 8), "N1", "N3"
        )
        ktx_from_suseo = await OfficialTimetableAdapter(
            Provider.KORAIL, settings, tago
        ).timetable("수서", "부산", datetime(2026, 8, 1, 8), "N2", "N3")

    assert [item.train_type for item in srt_from_seoul] == ["SRT"]
    assert [item.train_type for item in ktx_from_suseo] == ["KTX"]
    assert sum(path.endswith("GetCtyCodeList") for path in calls) == 1
    assert sum(path.endswith("GetCtyAcctoTrainSttnList") for path in calls) == 1


async def test_station_node_pair_rejects_mismatch_partial_and_same_node():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("GetCtyCodeList"):
            return httpx.Response(
                200,
                json=tago_response([{"citycode": "11", "cityname": "서울특별시"}]),
            )
        return httpx.Response(
            200,
            json=tago_response([
                {"nodeid": "N1", "nodename": "서울"},
                {"nodeid": "N3", "nodename": "부산"},
            ]),
        )

    from rail_waitlist.providers import RouteValidationError

    settings = Settings(tago_service_key="decoded-key")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        adapter = OfficialTimetableAdapter(
            Provider.KORAIL, settings, TagoClient(settings, http_client)
        )
        with pytest.raises(RouteValidationError, match="require both"):
            await adapter.timetable(
                "서울", "부산", datetime(2026, 8, 1, 8), origin_node_id="N1"
            )
        with pytest.raises(RouteValidationError, match="nodes must differ"):
            await adapter.timetable(
                "서울", "부산", datetime(2026, 8, 1, 8), "N1", "N1"
            )
        with pytest.raises(RouteValidationError, match="must match"):
            await adapter.timetable(
                "수서", "부산", datetime(2026, 8, 1, 8), "N1", "N3"
            )


async def test_official_timetable_requires_node_ids_even_when_names_are_resolvable():
    settings = Settings(tago_service_key="decoded-key")
    adapter = OfficialTimetableAdapter(Provider.KORAIL, settings, TagoClient(settings))

    with pytest.raises(RouteValidationError, match="require both"):
        await adapter.timetable("서울", "부산", datetime(2026, 8, 1, 8))


async def test_srt_unsupported_route_keeps_basic_station_identity_validation():
    settings = Settings(tago_service_key="decoded-key")
    adapter = OfficialTimetableAdapter(Provider.SRT, settings, TagoClient(settings))

    with pytest.raises(RouteValidationError, match="require both"):
        await adapter.timetable("대전", "서울", datetime(2026, 8, 1, 8))
    with pytest.raises(RouteValidationError, match="nodes must differ"):
        await adapter.timetable(
            "대전", "서울", datetime(2026, 8, 1, 8), "N-DAEJEON", "N-DAEJEON"
        )


async def test_korail_and_srt_share_raw_timetable_single_flight():
    timetable_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal timetable_calls
        if request.url.path.endswith("GetCtyCodeList"):
            return httpx.Response(
                200,
                json=tago_response([{"citycode": "11", "cityname": "서울특별시"}]),
            )
        if request.url.path.endswith("GetCtyAcctoTrainSttnList"):
            return httpx.Response(
                200,
                json=tago_response([
                    {"nodeid": "N1", "nodename": "대전"},
                    {"nodeid": "N3", "nodename": "부산"},
                ]),
            )
        timetable_calls += 1
        await asyncio.sleep(0.02)
        return httpx.Response(200, json=tago_response([
            {
                "trainno": "101",
                "traingradename": "KTX",
                "depplandtime": "20260801090000",
                "arrplandtime": "20260801113000",
                "depplacename": "대전",
                "arrplacename": "부산",
            },
            {
                "trainno": "201",
                "traingradename": "SRT",
                "depplandtime": "20260801100000",
                "arrplandtime": "20260801123000",
                "depplacename": "대전",
                "arrplacename": "부산",
            },
        ]))

    settings = Settings(tago_service_key="decoded-key")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        tago = TagoClient(settings, http_client)
        korail, srt = await asyncio.gather(
            OfficialTimetableAdapter(Provider.KORAIL, settings, tago).timetable(
                "대전", "부산", datetime(2026, 8, 1, 8), "N1", "N3"
            ),
            OfficialTimetableAdapter(Provider.SRT, settings, tago).timetable(
                "대전", "부산", datetime(2026, 8, 1, 8), "N1", "N3"
            ),
        )

    assert [item.train_type for item in korail] == ["KTX"]
    assert [item.train_type for item in srt] == ["SRT"]
    assert timetable_calls == 1
    assert korail[0].timetable_retrieved_at == srt[0].timetable_retrieved_at


async def test_official_station_catalog_without_service_key_fails_closed():
    settings = Settings(tago_service_key=None, tago_service_key_file="missing-test-key")
    adapter = OfficialTimetableAdapter(Provider.SRT, settings, TagoClient(settings))
    with pytest.raises(ProviderUnavailable, match="service key"):
        await adapter.stations()


async def test_mock_station_catalog_is_explicitly_marked_as_mock():
    catalog = await MockProviderAdapter().stations()
    assert catalog.provider == Provider.MOCK
    assert catalog.source == "mock"
    assert catalog.catalog_scope == "mock"
    assert catalog.provider_membership == "mock"
    assert [station.name for station in catalog.stations] == ["서울", "수서", "대전", "부산"]


async def test_mock_adapter_returns_observed_per_class_statuses_and_legacy_availability():
    items = await MockProviderAdapter().timetable("서울", "부산", datetime(2026, 8, 1, 8))

    assert [
        [seat.status for seat in item.seat_classes]
        for item in items
    ] == [
        ["available", "sold_out"],
        ["sold_out", "waitlist_available"],
        ["stale", "error"],
    ]
    assert all(item.availability.status == "available" for item in items)
    assert [seat.fare for seat in items[0].seat_classes] == [59_800, 83_700]
    assert all(
        seat.fare_currency == "KRW"
        for item in items
        for seat in item.seat_classes
    )
    assert all(
        seat.provenance.kind == "mock"
        and seat.provenance.source == "mock"
        and seat.provenance.observed_at is not None
        for item in items
        for seat in item.seat_classes
    )
    assert [action.kind for action in items[0].seat_classes[0].actions] == [
        "official_check",
        "add_to_watch",
    ]
    assert [action.kind for action in items[1].seat_classes[1].actions] == [
        "official_waitlist",
        "add_to_watch",
    ]
    assert [action.kind for action in items[2].seat_classes[0].actions] == [
        "retry_provider"
    ]


async def test_mock_adapter_generates_every_fixture_in_the_departure_window():
    items = await MockProviderAdapter().timetable(
        "서울",
        "부산",
        datetime(2026, 8, 1, 8),
        departure_to=datetime(2026, 8, 1, 12),
    )

    assert len(items) == 7
    assert items[0].departure_at == datetime(2026, 8, 1, 8, tzinfo=items[0].departure_at.tzinfo)
    assert items[-1].departure_at == datetime(
        2026, 8, 1, 12, tzinfo=items[-1].departure_at.tzinfo
    )
    assert all(
        items[index].departure_at < items[index + 1].departure_at
        for index in range(len(items) - 1)
    )


@pytest.mark.parametrize(
    "departure_to",
    [datetime(2026, 8, 1, 8), datetime(2026, 8, 2, 8)],
)
async def test_mock_adapter_rejects_invalid_departure_window(departure_to):
    with pytest.raises(RouteValidationError, match="departure_to"):
        await MockProviderAdapter().timetable(
            "서울",
            "부산",
            datetime(2026, 8, 1, 8),
            departure_to=departure_to,
        )


def mock_observation_request(**overrides) -> SeatObservationRequest:
    payload = {
        "provider": Provider.MOCK,
        "origin_node_id": "MOCK-SEOUL",
        "destination_node_id": "MOCK-BUSAN",
        "origin": "서울",
        "destination": "부산",
        "train_number": "MOCK-001",
        "departure_at": "2026-08-01T09:00:00+09:00",
        "seat_class": SeatClass.STANDARD,
        "passenger_count": 1,
    }
    payload.update(overrides)
    return SeatObservationRequest(**payload)


async def test_mock_provider_contract_uses_current_observation_time_without_external_io():
    adapter = MockProviderAdapter()
    request = mock_observation_request()

    before = datetime.now(timezone.utc)
    first = await adapter.observe_seats(request)
    second = await adapter.observe_seats(request)
    after = datetime.now(timezone.utc)

    assert len(first) == 1
    assert len(second) == 1
    assert first[0].seat_class == SeatClass.STANDARD
    assert first[0].status == "available"
    assert first[0].source == "mock"
    assert first[0].observed_at.tzinfo is not None
    assert before <= first[0].observed_at <= after
    assert before <= second[0].observed_at <= after
    assert first[0].fresh_until > first[0].observed_at


async def test_mock_reserve_once_returns_current_payment_handoff_contract():
    adapter = MockProviderAdapter()
    request = ReservationRequest(
        **mock_observation_request().model_dump(),
        candidate_id="candidate-1",
        idempotency_key="reservation-attempt-1",
    )

    before = datetime.now(timezone.utc)
    first = await adapter.reserve_once(request)
    second = await adapter.reserve_once(request)
    after = datetime.now(timezone.utc)

    assert first.outcome == "payment_required"
    assert first.source == "mock"
    assert first.payment_deadline is not None
    assert before <= first.observed_at <= after
    assert before <= second.observed_at <= after
    assert first.payment_deadline > after
    assert first.official_handoff_url is not None
    assert first.official_handoff_url.host == "example.invalid"


@pytest.mark.parametrize("provider", [Provider.KORAIL, Provider.SRT])
async def test_official_provider_contract_methods_fail_closed(provider):
    adapter = OfficialTimetableAdapter(provider, Settings(tago_service_key="unused"))
    observation = mock_observation_request(provider=provider)
    reservation = ReservationRequest(
        **observation.model_dump(),
        candidate_id="candidate-1",
        idempotency_key="reservation-attempt-1",
    )

    with pytest.raises(ProviderUnavailable, match="does not support seat monitoring"):
        await adapter.observe_seats(observation)
    with pytest.raises(ProviderUnavailable, match="does not support one-time reservation"):
        await adapter.reserve_once(reservation)


async def test_experimental_provider_contract_methods_fail_closed_even_when_enabled():
    adapter = ExperimentalRailAdapter(
        Provider.KORAIL, Settings(experimental_rail_enabled=True)
    )
    observation = mock_observation_request(provider=Provider.KORAIL)
    reservation = ReservationRequest(
        **observation.model_dump(),
        candidate_id="candidate-1",
        idempotency_key="reservation-attempt-1",
    )

    with pytest.raises(ProviderUnavailable, match="does not support seat monitoring"):
        await adapter.observe_seats(observation)
    with pytest.raises(ProviderUnavailable, match="does not support one-time reservation"):
        await adapter.reserve_once(reservation)


async def test_mock_provider_rejects_contract_for_a_different_provider():
    adapter = MockProviderAdapter()
    with pytest.raises(ProviderUnavailable, match="does not match adapter"):
        await adapter.observe_seats(mock_observation_request(provider=Provider.KORAIL))


class FakeSrtSeatObserver:
    def __init__(self) -> None:
        self.calls: list[tuple[SeatObservationRequest, str, str]] = []
        self.deferred_until: datetime | None = None
        self.drain_calls = 0

    async def observation_deferred_until(self) -> datetime | None:
        return self.deferred_until

    async def observe(
        self,
        request: SeatObservationRequest,
        *,
        origin: str,
        destination: str,
    ) -> list[SeatObservationResult]:
        self.calls.append((request, origin, destination))
        observed_at = datetime.now(UTC)
        return [
            SeatObservationResult(
                seat_class=request.seat_class,
                status="sold_out",
                source="srt-test-observer",
                observed_at=observed_at,
                fresh_until=observed_at + timedelta(seconds=30),
            )
        ]

    async def drain_pending_calls(self) -> None:
        self.drain_calls += 1

    async def reserve_once(self, _request, credentials) -> ReservationResult:
        return ReservationResult(
            outcome=ReservationOutcome.NOT_AVAILABLE,
            source="test-korail-reservation",
            observed_at=datetime.now(UTC),
            credential_version=credentials.credential_version,
        )


async def test_srt_execution_adapter_delegates_exact_route_identity_without_reservation():
    source = FakeSrtSeatObserver()
    settings = Settings(
        _env_file=None,
        EXPERIMENTAL_RAIL_ENABLED=True,
        srt_seat_status_enabled=True,
        srt_seat_monitoring_enabled=True,
    )
    adapter = SrtLiveExecutionAdapter(settings, source)
    deferred_until = datetime.now(UTC) + timedelta(minutes=3)
    source.deferred_until = deferred_until
    request = mock_observation_request(
        provider=Provider.SRT,
        origin_node_id="0017",
        destination_node_id="0020",
        origin=" 수서 ",
        destination="부산",
        train_number="28",
    )

    assert await adapter.observation_deferred_until() == deferred_until
    result = await adapter.observe_seats(request)
    await adapter.drain_pending_calls()

    assert result[0].status == "sold_out"
    assert source.calls == [(request, "수서", "부산")]
    assert source.drain_calls == 1
    reservation = ReservationRequest(
        **request.model_dump(),
        candidate_id="candidate-srt-1",
        idempotency_key="reservation-attempt-srt-1",
    )
    with pytest.raises(ProviderUnavailable, match="does not support one-time reservation"):
        await adapter.reserve_once(reservation)


async def test_disabled_srt_execution_adapter_never_calls_its_source():
    source = FakeSrtSeatObserver()
    adapter = SrtLiveExecutionAdapter(Settings(_env_file=None), source)
    request = mock_observation_request(
        provider=Provider.SRT,
        origin_node_id="0017",
        destination_node_id="0020",
        origin="수서",
        destination="부산",
    )

    with pytest.raises(ProviderUnavailable, match="does not support seat monitoring"):
        await adapter.observe_seats(request)

    assert await adapter.observation_deferred_until() is None
    assert source.calls == []


async def test_korail_execution_adapter_delegates_exact_route_without_reservation():
    source = FakeSrtSeatObserver()
    settings = Settings(
        _env_file=None,
        EXPERIMENTAL_RAIL_ENABLED=True,
        korail_browser_adapter_enabled=True,
        korail_seat_monitoring_enabled=True,
        korail_browser_adapter_token="b" * 32,
    )
    adapter = KorailBrowserExecutionAdapter(settings, source)
    deferred_until = datetime.now(UTC) + timedelta(minutes=3)
    source.deferred_until = deferred_until
    request = mock_observation_request(
        provider=Provider.KORAIL,
        origin_node_id="NAT010000",
        destination_node_id="NAT014445",
        origin=" 서울 ",
        destination="부산",
        train_number="43",
    )

    assert await adapter.observation_deferred_until() == deferred_until
    result = await adapter.observe_seats(request)
    await adapter.drain_pending_calls()

    assert result[0].status == "sold_out"
    assert source.calls == [(request, "서울", "부산")]
    assert source.drain_calls == 1
    reservation = ReservationRequest(
        **request.model_dump(),
        candidate_id="candidate-korail-1",
        idempotency_key="reservation-attempt-korail-1",
    )
    with pytest.raises(ProviderUnavailable, match="does not support one-time reservation"):
        await adapter.reserve_once(reservation)


async def test_korail_reservation_result_reports_the_actual_credential_generation():
    source = FakeSrtSeatObserver()
    settings = Settings(
        _env_file=None,
        EXPERIMENTAL_RAIL_ENABLED=True,
        korail_browser_adapter_enabled=True,
        korail_seat_monitoring_enabled=True,
        korail_reservation_once_enabled=True,
        korail_browser_adapter_token="b" * 32,
    )

    async def credentials(provider):
        assert provider is Provider.KORAIL
        return ProviderCredentials("fixture-account", "fixture-password", 7)

    adapter = KorailBrowserExecutionAdapter(
        settings,
        source,
        credential_loader=credentials,
    )
    observation = mock_observation_request(provider=Provider.KORAIL)
    request = ReservationRequest(
        **observation.model_dump(),
        candidate_id="candidate-korail-generation",
        idempotency_key="reservation-attempt-korail-generation",
    )

    result = await adapter.reserve_once(request)

    assert result.outcome is ReservationOutcome.NOT_AVAILABLE
    assert result.credential_version == 7


async def test_disabled_korail_execution_adapter_never_calls_its_source():
    source = FakeSrtSeatObserver()
    adapter = KorailBrowserExecutionAdapter(Settings(_env_file=None), source)
    request = mock_observation_request(
        provider=Provider.KORAIL,
        origin_node_id="NAT010000",
        destination_node_id="NAT014445",
        origin="서울",
        destination="부산",
    )

    with pytest.raises(ProviderUnavailable, match="does not support seat monitoring"):
        await adapter.observe_seats(request)

    assert await adapter.observation_deferred_until() is None
    assert source.calls == []


async def test_default_srt_execution_source_is_scoped_to_one_worker_task_loop():
    settings = Settings(
        _env_file=None,
        EXPERIMENTAL_RAIL_ENABLED=True,
        redis_url="redis://localhost:6379/15",
        srt_seat_status_enabled=True,
        srt_seat_monitoring_enabled=True,
        srt_seat_status_cache_ttl_seconds=30,
        srt_seat_status_timeout_seconds=8,
    )

    first = default_srt_execution_source(settings)
    second = default_srt_execution_source(settings)

    assert first is not second
    assert first.redis is not second.redis
    await first.aclose()
    await second.aclose()


async def test_default_korail_execution_source_is_scoped_to_one_worker_task_loop():
    settings = Settings(
        _env_file=None,
        EXPERIMENTAL_RAIL_ENABLED=True,
        redis_url="redis://localhost:6379/15",
        korail_browser_adapter_enabled=True,
        korail_seat_monitoring_enabled=True,
        korail_browser_adapter_token="b" * 32,
    )

    first = default_korail_execution_source(settings)
    second = default_korail_execution_source(settings)

    assert first is not second
    assert first.source is not second.source
    assert first.redis is not second.redis
    await first.aclose()
    await second.aclose()
