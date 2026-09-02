from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from rail_waitlist.timetable_management.schemas import StationItem
from rail_waitlist.timetable_management.station_visibility import (
    KORAIL_STATION_DATA_URL,
    MAX_KORAIL_ROSTER_COUNT,
    MIN_KORAIL_ROSTER_COUNT,
    REQUEST_TIMEOUT,
    KorailStationVisibility,
    StationVisibilityRoster,
    StationVisibilityUnavailable,
    filter_station_items,
    normalize_visibility_station_name,
)


def station(name: str, node_id: str) -> StationItem:
    return StationItem(node_id=node_id, name=name, city_code="11", city_name="테스트")


def roster_rows(
    *,
    include_sentinels: bool = True,
    count: int = MIN_KORAIL_ROSTER_COUNT,
) -> list[dict[str, str]]:
    names = ["서울", "수서", "대전", "부산"] if include_sentinels else []
    names.extend(
        [
            "김천구미",
            "여수EXPO",
            "경주",
            "울산(통도사)",
            "진부(오대산)",
            "광운대",
            "노량진",
            "신도림",
            "서빙고",
            "왕십리",
            "옥수",
        ]
    )
    names.extend(f"테스트{i}" for i in range(count - len(names)))
    return [{"stn_cd": f"{index:04d}", "stn_nm": name} for index, name in enumerate(names, start=1)]


def test_filter_preserves_station_items_node_ids_and_order_with_explicit_aliases():
    items = [
        station("서울역", "TAGO-SEOUL"),
        station("김천(구미)", "TAGO-GIMCHEON-GUMI"),
        station("여수엑스포역", "TAGO-YEOSU"),
        station("신경주", "TAGO-GYEONGJU"),
        station("울산", "TAGO-ULSAN"),
        station("진부", "TAGO-JINBU"),
        station("광운대", "TAGO-GWANGUNDAE"),
        station("노량진", "TAGO-NORYANGJIN"),
        station("신도림", "TAGO-SINDORIM"),
        station("서빙고", "TAGO-SEOBINGGO"),
        station("왕십리", "TAGO-WANGSIMNI"),
        station("옥수", "TAGO-OKSU"),
        station("임의의역", "TAGO-UNKNOWN"),
    ]
    roster = StationVisibilityRoster(
        names=frozenset(normalize_visibility_station_name(row["stn_nm"]) for row in roster_rows()),
        retrieved_at=datetime.now(UTC),
        etag=None,
        last_modified=None,
    )

    result = filter_station_items(items, roster)

    assert result == items[:6]
    assert [item.node_id for item in result] == [
        "TAGO-SEOUL",
        "TAGO-GIMCHEON-GUMI",
        "TAGO-YEOSU",
        "TAGO-GYEONGJU",
        "TAGO-ULSAN",
        "TAGO-JINBU",
    ]
    assert all(actual is expected for actual, expected in zip(result, items[:6], strict=True))


@pytest.mark.parametrize(
    ("tago_name", "korail_name"),
    [
        ("김천(구미)", "김천구미"),
        ("여수엑스포역", "여수EXPO"),
        ("신경주", "경주"),
        ("울산", "울산(통도사)"),
        (" 진부역 ", "진부(오대산)"),
    ],
)
def test_only_reviewed_station_name_equivalences_share_one_normalized_key(
    tago_name: str,
    korail_name: str,
) -> None:
    assert normalize_visibility_station_name(tago_name) == normalize_visibility_station_name(
        korail_name
    )


async def test_live_roster_names_replace_tago_aliases_without_changing_node_identity():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"stns": {"stn": roster_rows()}})

    raw = [station("울산", "NATH13717"), station("진부", "NATN10787")]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        displayed = await KorailStationVisibility(http_client).filter_stations(raw)

    assert [(item.node_id, item.name) for item in displayed] == [
        ("NATH13717", "울산(통도사)"),
        ("NATN10787", "진부(오대산)"),
    ]
    assert all(displayed_item is not raw_item for displayed_item, raw_item in zip(displayed, raw))


def test_filter_rejects_alias_and_canonical_name_collision_fail_closed():
    roster = StationVisibilityRoster(
        names=frozenset({normalize_visibility_station_name("울산(통도사)")}),
        retrieved_at=datetime.now(UTC),
        etag=None,
        last_modified=None,
        canonical_names={normalize_visibility_station_name("울산(통도사)"): "울산(통도사)"},
    )

    with pytest.raises(
        StationVisibilityUnavailable,
        match="conflicting normalized station names",
    ):
        filter_station_items(
            [station("울산", "NATH13717"), station("울산(통도사)", "OTHER-NODE")],
            roster,
        )


async def test_load_roster_uses_exact_https_url_does_not_follow_redirects():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://example.invalid/roster"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as http_client:
        with pytest.raises(StationVisibilityUnavailable, match="invalid status"):
            await KorailStationVisibility(http_client).load_roster()

    assert [str(request.url) for request in requests] == [KORAIL_STATION_DATA_URL]


async def test_load_roster_validates_schema_sentinels_and_metadata():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"stns": {"stn": roster_rows()}},
            headers={"etag": '"station-v1"', "last-modified": "Wed, 29 Jul 2026 00:00:00 GMT"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        roster = await KorailStationVisibility(http_client).load_roster()

    assert {"서울", "수서", "대전", "부산"}.issubset(roster.names)
    assert {"광운대", "노량진", "신도림", "서빙고", "왕십리", "옥수"}.isdisjoint(roster.names)
    assert roster.retrieved_at.tzinfo is UTC
    assert roster.etag == '"station-v1"'
    assert roster.last_modified == "Wed, 29 Jul 2026 00:00:00 GMT"
    assert roster.canonical_names["울산(통도사)"] == "울산(통도사)"
    assert roster.canonical_names["진부(오대산)"] == "진부(오대산)"
    assert roster.station_codes["울산(통도사)"] == "0008"
    assert roster.station_codes["진부(오대산)"] == "0009"


@pytest.mark.parametrize("count", [MIN_KORAIL_ROSTER_COUNT, MAX_KORAIL_ROSTER_COUNT])
async def test_load_roster_accepts_inclusive_count_boundaries(count: int):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"stns": {"stn": roster_rows(count=count)}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        roster = await KorailStationVisibility(http_client).load_roster()

    assert len(roster.names) == count - 6


async def test_custom_fixture_url_is_public_and_used_for_the_exact_request():
    custom_url = "http://fixture.internal/station_data.json"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"stns": {"stn": roster_rows()}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        visibility = KorailStationVisibility(http_client, url=custom_url)
        await visibility.load_roster()

    assert visibility.url == custom_url
    assert [str(request.url) for request in requests] == [custom_url]


def test_request_timeout_contract_is_preserved():
    assert REQUEST_TIMEOUT.connect == 5.0
    assert REQUEST_TIMEOUT.read == 10.0
    assert REQUEST_TIMEOUT.write == 10.0
    assert REQUEST_TIMEOUT.pool == 10.0


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"stns": None},
        {"stns": {"stn": []}},
        {"stns": {"stn": roster_rows(include_sentinels=False)}},
        {"stns": {"stn": roster_rows() + [{"stn_cd": "0001", "stn_nm": "중복코드"}]}},
        {"stns": {"stn": roster_rows() + [{"stn_cd": "9999", "stn_nm": "서울"}]}},
        {"stns": {"stn": roster_rows() + [{"stn_cd": " 0001 ", "stn_nm": "새이름"}]}},
        {"stns": {"stn": roster_rows() + [{"stn_cd": "9998", "stn_nm": " 서울역 "}]}},
        {"stns": {"stn": roster_rows() + [{"stn_cd": "9997", "stn_nm": "울산"}]}},
        {
            "stns": {
                "stn": [
                    {"stn_cd": str(index), "stn_nm": f"과다{index}"}
                    for index in range(MAX_KORAIL_ROSTER_COUNT + 1)
                ]
            }
        },
    ],
)
async def test_corrupt_or_out_of_bounds_roster_fails_closed(payload: object):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(StationVisibilityUnavailable):
            await KorailStationVisibility(http_client).load_roster()


@pytest.mark.parametrize("failure", ["status", "json", "timeout"])
async def test_upstream_failures_raise_without_returning_raw_tago_items(failure: str):
    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "status":
            return httpx.Response(503, text="upstream details must not be exposed")
        if failure == "json":
            return httpx.Response(200, content=b"not-json")
        raise httpx.ReadTimeout("secret upstream timeout detail", request=request)

    raw = [station("서울", "TAGO-SEOUL")]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(StationVisibilityUnavailable) as raised:
            await KorailStationVisibility(http_client).filter_stations(raw)

    assert "secret" not in str(raised.value)


def test_empty_input_or_empty_intersection_fails_closed():
    roster = StationVisibilityRoster(
        names=frozenset({"서울", "수서", "대전", "부산"}),
        retrieved_at=datetime.now(UTC),
        etag=None,
        last_modified=None,
    )

    with pytest.raises(StationVisibilityUnavailable, match="TAGO station catalog is empty"):
        filter_station_items([], roster)
    with pytest.raises(StationVisibilityUnavailable, match="intersection is empty"):
        filter_station_items([station("없는역", "TAGO-NONE")], roster)
