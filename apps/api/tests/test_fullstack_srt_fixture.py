from __future__ import annotations

import json
from typing import Self
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

from rail_waitlist import fullstack_srt_fixture
from rail_waitlist.fullstack_srt_fixture import FullstackSrtFixtureClient


class _FixtureResponse:
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(
            {
                "trains": [
                    {
                        "train_number": "9002",
                        "dep_date": "20260807",
                        "dep_time": "131000",
                        "arr_date": "20260807",
                        "arr_time": "154000",
                        "general_seat_state": "매진",
                        "special_seat_state": "매진",
                        "reserve_wait_possible_code": "",
                    }
                ]
            }
        ).encode()


def test_fullstack_fixture_maps_complete_strict_timetable_identity(monkeypatch) -> None:
    requested_urls: list[str] = []

    def fake_urlopen(request: Request, timeout: int) -> _FixtureResponse:
        requested_urls.append(request.full_url)
        assert timeout == 3
        return _FixtureResponse()

    monkeypatch.setattr(fullstack_srt_fixture, "urlopen", fake_urlopen)

    trains = FullstackSrtFixtureClient("http://e2e-fake-upstream:8001/srt/search").search_train(
        "수서",
        "부산",
        "20260807",
        "120000",
        time_limit="180000",
        available_only=False,
    )

    assert len(trains) == 1
    train = trains[0]
    assert (
        train.train_name,
        train.dep_station_name,
        train.arr_station_name,
        train.dep_date,
        train.dep_time,
        train.arr_date,
        train.arr_time,
    ) == ("SRT", "수서", "부산", "20260807", "131000", "20260807", "154000")
    query = parse_qs(urlsplit(requested_urls[0]).query)
    assert query == {
        "dep": ["수서"],
        "arr": ["부산"],
        "date": ["20260807"],
        "time": ["120000"],
        "time_limit": ["180000"],
        "available_only": ["0"],
    }
