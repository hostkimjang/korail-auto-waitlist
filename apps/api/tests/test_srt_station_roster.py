from __future__ import annotations

import pytest

from rail_waitlist.schemas import StationItem
from rail_waitlist.srt_station_roster import (
    SrtStationRoster,
    SrtStationRosterUnavailable,
    normalize_srt_station_name,
)


def station(name: str, node_id: str) -> StationItem:
    return StationItem(
        node_id=node_id,
        name=name,
        city_code="test-city",
        city_name="테스트",
    )


def test_roster_preserves_official_cross_operation_station_beyond_srtrain_static_codes():
    roster = SrtStationRoster.from_station_codes(
        {"수서": "0551", "대전": "0010", "부산": "0020"}
    )

    filtered = roster.filter_stations(
        [
            station("서울", "N-SEOUL"),
            station("수서역", "N-SUSEO"),
            station("대전", "N-DAEJEON"),
            station("부산", "N-BUSAN"),
        ]
    )

    assert [item.name for item in filtered] == ["서울", "수서역", "대전", "부산"]
    assert roster.supports_route("대전", "부산")
    assert roster.supports_route("대전", "서울")
    assert roster.station_code("서울역") == "0001"
    assert roster.station_code("대전") == "0010"


def test_roster_normalizes_only_explicit_public_client_aliases():
    roster = SrtStationRoster.from_station_codes(
        {
            "수서": "0551",
            "대전": "0010",
            "부산": "0020",
            "김천(구미)": "0507",
            "경주": "0508",
            "여수EXPO": "0053",
        }
    )

    assert roster.provider_name("김천구미") == "김천(구미)"
    assert roster.provider_name("신경주") == "경주"
    assert roster.provider_name("여수엑스포역") == "여수EXPO"
    assert normalize_srt_station_name(" 대전역 ") == "대전"


@pytest.mark.parametrize(
    "station_codes",
    [None, {}, {"수서": "0551", "대전": "0010"}, {"수서": 551}],
)
def test_invalid_or_incomplete_roster_fails_closed(station_codes):
    with pytest.raises(SrtStationRosterUnavailable):
        SrtStationRoster.from_station_codes(station_codes)
