from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from rail_waitlist.config import Settings
from rail_waitlist.domain import Provider
from rail_waitlist.provider_adapters.tago import TagoClient
from rail_waitlist.timetable_management.schemas import StationItem
from rail_waitlist.timetable_management.station_catalog_supplements import (
    REVIEWED_TAGO_STATION_SUPPLEMENTS,
    StationCatalogSupplementConflict,
    apply_reviewed_tago_station_supplements,
)


def station(node_id: str, name: str) -> StationItem:
    return StationItem(
        node_id=node_id,
        name=name,
        city_code="00",
        city_name="테스트",
    )


def test_reviewed_supplements_pin_only_live_verified_cross_source_identities() -> None:
    assert [
        (item.node_id, item.name, item.city_code, item.city_name, item.korail_station_code)
        for item in REVIEWED_TAGO_STATION_SUPPLEMENTS
    ] == [
        ("NATH30536", "평택지제", "31", "경기도", "0553"),
        ("NAT023073", "군위", "22", "대구광역시", "0548"),
    ]


def test_supplements_add_missing_identities_only_while_korail_lists_them() -> None:
    raw = [station("NAT011668", "대전")]

    result = apply_reviewed_tago_station_supplements(
        raw,
        {"대전": "0010", "평택지제": "0553", "군위": "0548"},
    )

    assert [(item.node_id, item.name) for item in result] == [
        ("NAT023073", "군위"),
        ("NAT011668", "대전"),
        ("NATH30536", "평택지제"),
    ]
    assert result[1] is raw[0]
    assert apply_reviewed_tago_station_supplements(raw, {"대전": "0010"}) == raw


def test_supplement_fails_closed_when_korail_reassigns_the_reviewed_code() -> None:
    with pytest.raises(StationCatalogSupplementConflict, match="KORAIL.*평택지제"):
        apply_reviewed_tago_station_supplements([], {"평택지제": "9999"})


def test_matching_upstream_identity_replaces_the_need_for_a_supplement() -> None:
    upstream = station("NATH30536", "평택지제")

    result = apply_reviewed_tago_station_supplements([upstream], {"평택지제": "0553"})

    assert result == [upstream]
    assert result[0] is upstream


@pytest.mark.parametrize(
    "upstream",
    [
        station("OTHER-NODE", "평택지제"),
        station("NATH30536", "다른역"),
    ],
)
def test_supplement_conflict_fails_closed_for_review(upstream: StationItem) -> None:
    with pytest.raises(StationCatalogSupplementConflict, match="평택지제"):
        apply_reviewed_tago_station_supplements([upstream], {"평택지제": "0553"})


@pytest.mark.parametrize(
    (
        "origin",
        "origin_node_id",
        "destination",
        "destination_node_id",
        "train_type",
    ),
    [
        ("대전", "NAT011668", "평택지제", "NATH30536", "KTX"),
        ("군위", "NAT023073", "동대구", "NAT013271", "ITX-마음"),
    ],
)
async def test_reviewed_identity_is_sent_as_the_exact_tago_timetable_node(
    origin: str,
    origin_node_id: str,
    destination: str,
    destination_node_id: str,
    train_type: str,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "response": {
                    "header": {"resultCode": "00"},
                    "body": {
                        "items": {
                            "item": {
                                "trainno": "101",
                                "traingradename": train_type,
                                "depplandtime": "20260903090000",
                                "arrplandtime": "20260903100000",
                                "depplacename": origin,
                                "arrplacename": destination,
                            }
                        },
                        "totalCount": 1,
                        "pageNo": 1,
                        "numOfRows": 100,
                    },
                }
            },
        )

    base_stations = [
        station("NAT011668", "대전"),
        station("NAT013271", "동대구"),
    ]
    stations = apply_reviewed_tago_station_supplements(
        base_stations,
        {"평택지제": "0553", "군위": "0548"},
    )
    retrieved_at = datetime.now(UTC)
    settings = Settings(tago_service_key="test-key")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = TagoClient(settings, http_client)
        client.hydrate_station_catalog(
            stations,
            retrieved_at,
            retrieved_at + timedelta(hours=1),
        )
        result = await client.timetable(
            Provider.KORAIL,
            origin,
            destination,
            datetime(2026, 9, 3, 8),
            "https://www.korail.com/ticket/main",
            origin_node_id,
            destination_node_id,
        )

    assert len(result) == (1 if train_type == "KTX" else 0)
    assert len(requests) == 1
    assert requests[0].url.path.endswith("GetStrtpntAlocFndTrainInfo")
    assert requests[0].url.params["depPlaceId"] == origin_node_id
    assert requests[0].url.params["arrPlaceId"] == destination_node_id
