from __future__ import annotations

from datetime import date, time
from types import SimpleNamespace

import pytest
from pydoll.exceptions import PageLoadTimeout

from rail_waitlist.korail_pydoll_browser import _PydollSession
from rail_waitlist.korail_search_bootstrap import KorailStationIdentity
from rail_waitlist.korail_sidecar.pydoll.page_contracts import PydollPageSnapshot
from rail_waitlist.provider_registry.korail_search_url_policy import (
    build_korail_general_search_url,
)


class TimedOutNavigationTab:
    async def go_to(self, _url: str, *, timeout: int) -> None:
        assert timeout == 25
        raise PageLoadTimeout()


class ResultSnapshotDriver:
    def __init__(self, snapshot: PydollPageSnapshot) -> None:
        self.snapshot_value = snapshot
        self.calls = 0

    async def snapshot(self) -> PydollPageSnapshot:
        self.calls += 1
        return self.snapshot_value


@pytest.mark.asyncio
async def test_direct_navigation_load_signal_timeout_keeps_the_verifiable_result_dom() -> None:
    session = object.__new__(_PydollSession)
    expected = PydollPageSnapshot(
        body_text="열차 조회 결과",
        rows=(),
        url="https://www.korail.com/ticket/search/general",
    )
    driver = ResultSnapshotDriver(expected)
    session._chromium_lifecycle = SimpleNamespace(tab=TimedOutNavigationTab())
    session._search_driver = driver
    session.timeout_ms = 25_000
    session._submitted = False
    session._network_responses = []

    direct_url = build_korail_general_search_url(
        origin=KorailStationIdentity("0010", "대전"),
        destination=KorailStationIdentity("0001", "서울"),
        travel_date=date(2026, 8, 12),
        departure_time=time(12, 15),
    )

    result = await session.navigate(direct_url)

    assert result is expected
    assert session._submitted is True
    assert driver.calls == 1
