from __future__ import annotations

import json
import subprocess
import sys
from operator import setitem
from types import SimpleNamespace

import pytest

import rail_waitlist.provider_adapters.srt_station_roster as canonical_roster_module
import rail_waitlist.srt_station_roster as legacy_roster_module
from rail_waitlist.provider_adapters.srt_station_roster import (
    ROSTER_SOURCE,
    SrtStationRoster,
    SrtStationRosterUnavailable,
    load_srt_station_roster,
    normalize_srt_station_name,
)
from rail_waitlist.timetable_management.schemas import StationItem


def station(name: str, node_id: str) -> StationItem:
    return StationItem(
        node_id=node_id,
        name=name,
        city_code="test-city",
        city_name="테스트",
    )


def test_legacy_roster_symbols_are_exact_canonical_objects() -> None:
    for symbol in (
        "ROSTER_SOURCE",
        "SrtStationRoster",
        "SrtStationRosterUnavailable",
        "load_srt_station_roster",
        "normalize_srt_station_name",
    ):
        assert getattr(legacy_roster_module, symbol) is getattr(canonical_roster_module, symbol)
    assert SrtStationRoster.__module__ == ("rail_waitlist.provider_adapters.srt_station_roster")


def test_roster_preserves_official_cross_operation_station_beyond_srtrain_static_codes() -> None:
    roster = SrtStationRoster.from_station_codes({"수서": "0551", "대전": "0010", "부산": "0020"})

    stations = [
        station("서울", "N-SEOUL"),
        station("수서역", "N-SUSEO"),
        station("대전", "N-DAEJEON"),
        station("부산", "N-BUSAN"),
        station("광주", "N-GWANGJU"),
    ]
    filtered = roster.filter_stations(stations)

    assert [item.name for item in filtered] == ["서울", "수서역", "대전", "부산"]
    assert all(item is stations[index] for index, item in enumerate(filtered))
    assert roster.supports_route("대전", "부산")
    assert roster.supports_route("대전", "서울")
    assert not roster.supports_route("대전", "대전역")
    assert not roster.supports_route("대전", "광주")
    assert roster.station_code("서울역") == "0001"
    assert roster.station_code("대전") == "0010"
    assert roster.source == ROSTER_SOURCE

    with pytest.raises(TypeError):
        setitem(roster.canonical_names, "광주", "광주")
    with pytest.raises(TypeError):
        setitem(roster.station_codes, "광주", "0000")


def test_roster_normalizes_only_explicit_public_client_aliases() -> None:
    roster = SrtStationRoster.from_station_codes(
        {
            "수서": "0551",
            "대전": "0010",
            "부산": "0020",
            "김천(구미)": "0507",
            "경주": "0508",
            "여수EXPO": "0053",
            "울산(통도사)": "0509",
            "진부(오대산)": "0519",
        }
    )

    assert roster.provider_name("김천구미") == "김천(구미)"
    assert roster.provider_name("신경주") == "경주"
    assert roster.provider_name("여수엑스포역") == "여수EXPO"
    assert roster.provider_name("울산") == "울산(통도사)"
    assert roster.station_code("울산") == "0509"
    assert roster.provider_name("진부역") == "진부(오대산)"
    assert normalize_srt_station_name(" 대전역 ") == "대전"


@pytest.mark.parametrize(
    ("station_codes", "message"),
    [
        (None, "SRT station roster is unavailable"),
        ({}, "SRT station roster is unavailable"),
        ({"수서": 551}, "SRT station roster is invalid"),
        ({"수서": " ", "대전": "0010", "부산": "0020"}, "SRT station roster is invalid"),
        (
            {"수서": "0551", "대전": "0010"},
            "SRT station roster sentinels are missing",
        ),
        (
            {"서울": "9999", "수서": "0551", "대전": "0010", "부산": "0020"},
            "SRT station roster extension conflicts",
        ),
        (
            {"수서": "0551", "수서역": "9999", "대전": "0010", "부산": "0020"},
            "SRT station roster has conflicting normalized station codes",
        ),
    ],
)
def test_invalid_or_incomplete_roster_fails_closed(
    station_codes: object,
    message: str,
) -> None:
    with pytest.raises(SrtStationRosterUnavailable, match=f"^{message}$"):
        SrtStationRoster.from_station_codes(station_codes)


def test_loader_cache_and_legacy_facade_share_one_roster(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    constants = SimpleNamespace(STATION_CODE={"수서": "0551", "대전": "0010", "부산": "0020"})

    def import_constants(name: str) -> object:
        calls.append(name)
        return constants

    monkeypatch.setattr(canonical_roster_module, "import_module", import_constants)
    load_srt_station_roster.cache_clear()
    try:
        canonical = load_srt_station_roster()
        legacy = legacy_roster_module.load_srt_station_roster()
    finally:
        load_srt_station_roster.cache_clear()

    assert canonical is legacy
    assert calls == ["SRT.constants"]


def test_loader_maps_missing_constants_to_roster_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    modules = iter(
        [
            object(),
            SimpleNamespace(STATION_CODE={"수서": "0551", "대전": "0010", "부산": "0020"}),
        ]
    )

    def import_constants(name: str) -> object:
        calls.append(name)
        return next(modules)

    monkeypatch.setattr(canonical_roster_module, "import_module", import_constants)
    load_srt_station_roster.cache_clear()
    try:
        with pytest.raises(
            SrtStationRosterUnavailable,
            match="^SRT station roster cannot be loaded$",
        ):
            load_srt_station_roster()
        recovered = load_srt_station_roster()
        cached = load_srt_station_roster()
    finally:
        load_srt_station_roster.cache_clear()

    assert recovered is cached
    assert calls == ["SRT.constants", "SRT.constants"]


def test_roster_import_orders_preserve_identity_and_shared_cache_function() -> None:
    script = r"""
import json
import sys

order = sys.argv[1]
if order == "canonical-first":
    import rail_waitlist.provider_adapters.srt_station_roster as Canonical
    import rail_waitlist.srt_station_roster as Legacy
else:
    import rail_waitlist.srt_station_roster as Legacy
    import rail_waitlist.provider_adapters.srt_station_roster as Canonical

symbols = (
    "ROSTER_SOURCE",
    "SrtStationRoster",
    "SrtStationRosterUnavailable",
    "load_srt_station_roster",
    "normalize_srt_station_name",
)
print(json.dumps({
    "same": all(getattr(Legacy, name) is getattr(Canonical, name) for name in symbols),
    "module": Canonical.SrtStationRoster.__module__,
    "shared_cache": (
        Legacy.load_srt_station_roster.cache_info()
        == Canonical.load_srt_station_roster.cache_info()
    ),
}))
"""

    for order in ("canonical-first", "legacy-first"):
        completed = subprocess.run(
            [sys.executable, "-W", "error", "-c", script, order],
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads(completed.stdout) == {
            "same": True,
            "module": "rail_waitlist.provider_adapters.srt_station_roster",
            "shared_cache": True,
        }
