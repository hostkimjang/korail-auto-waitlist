from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rail_waitlist.korail_pydoll_browser import _PydollSession
from rail_waitlist.korail_sidecar.browser_contracts import BrowserSourceUnavailable
from rail_waitlist.korail_sidecar.pydoll.page_contracts import PydollPageSnapshot, PydollTrainRow
from rail_waitlist.korail_sidecar.pydoll.search_notice import (
    CLOSE_SELECTOR,
    dismiss_booking_window_notice,
)


def script_result(state: object) -> dict[str, object]:
    return {"result": {"result": {"value": state}}}


async def test_reservation_notice_preparation_observes_live_modal_and_refreshes_rows(monkeypatch):
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    initial = PydollPageSnapshot("조회 결과", ())
    clean = PydollPageSnapshot("안내가 사라진 조회 결과", ())
    close = SimpleNamespace(click=AsyncMock())
    session._tab = SimpleNamespace(
        execute_script=AsyncMock(
            side_effect=[script_result("booking_window_expansion"), script_result("absent")]
        )
    )
    monkeypatch.setattr(session, "_visible_elements", AsyncMock(return_value=[close]))
    fresh_snapshot = AsyncMock(return_value=clean)
    monkeypatch.setattr(session, "_snapshot", fresh_snapshot)

    result = await session.dismiss_search_notice(initial)

    assert result is clean
    close.click.assert_awaited_once()
    fresh_snapshot.assert_awaited_once()


async def test_verified_notice_closes_once_before_expanding_and_refreshes_snapshot(
    monkeypatch, caplog
):
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    first = PydollTrainRow("KTX 30", "30", "대전 → 서울(12:00 ~ 13:04)", ())
    second = PydollTrainRow("KTX 260", "260", "대전 → 서울(12:09 ~ 13:11)", ())
    obstructed = PydollPageSnapshot("안내 창닫기", (first,))
    clean = PydollPageSnapshot("조회 결과", (first,))
    grown = PydollPageSnapshot("조회 결과", (first, second))
    clicks = []

    async def close_notice():
        clicks.append("notice_closed")

    async def expand():
        clicks.append("more_clicked")

    close = SimpleNamespace(click=AsyncMock(side_effect=close_notice))
    more = SimpleNamespace(click=AsyncMock(side_effect=expand))
    script = AsyncMock(
        side_effect=[
            script_result("booking_window_expansion"),
            script_result("absent"),
            script_result("absent"),
        ]
    )
    session._tab = SimpleNamespace(execute_script=script)
    controls = AsyncMock(return_value=[close])
    monkeypatch.setattr(session, "_visible_elements", controls)
    snapshot_reader = AsyncMock(return_value=clean)
    monkeypatch.setattr(session, "_snapshot", snapshot_reader)
    monkeypatch.setattr(
        session, "_find_exact_visible", AsyncMock(side_effect=[more, LookupError("더보기")])
    )
    monkeypatch.setattr(session, "_wait_for_result_growth", AsyncMock(return_value=(grown, True)))

    with caplog.at_level(logging.INFO):
        result = await session.expand_results(obstructed, 19)

    assert result.rows == (first, second)
    assert clicks == ["notice_closed", "more_clicked"]
    controls.assert_awaited_once_with(CLOSE_SELECTOR)
    snapshot_reader.assert_awaited_once()
    assert "event=search_notice_dismissed kind=booking_window_expansion" in caplog.text
    assert "안내 창닫기" not in caplog.text


async def test_notice_arriving_after_initial_results_closes_before_more_click(monkeypatch):
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    first = PydollTrainRow("KTX 30", "30", "대전 → 서울(12:00 ~ 13:04)", ())
    second = PydollTrainRow("KTX 260", "260", "대전 → 서울(12:09 ~ 13:11)", ())
    initial = PydollPageSnapshot("조회 결과", (first,))
    grown = PydollPageSnapshot("조회 결과", (first, second))
    events = []

    async def close_notice():
        events.append("notice_closed")

    async def expand():
        events.append("more_clicked")

    close = SimpleNamespace(click=AsyncMock(side_effect=close_notice))
    more = SimpleNamespace(click=AsyncMock(side_effect=expand))
    session._tab = SimpleNamespace(
        execute_script=AsyncMock(
            side_effect=[
                script_result("absent"),
                script_result("booking_window_expansion"),
                script_result("absent"),
            ]
        )
    )
    monkeypatch.setattr(session, "_visible_elements", AsyncMock(return_value=[close]))
    monkeypatch.setattr(session, "_snapshot", AsyncMock(return_value=initial))
    monkeypatch.setattr(
        session, "_find_exact_visible", AsyncMock(side_effect=[more, more, LookupError("더보기")])
    )
    monkeypatch.setattr(session, "_wait_for_result_growth", AsyncMock(return_value=(grown, True)))

    result = await session.expand_results(initial, 19)

    assert result.rows == (first, second)
    assert events == ["notice_closed", "more_clicked"]
    more.click.assert_awaited_once()


@pytest.mark.parametrize(
    "response", [script_result("unrecognized"), script_result("unexpected"), {}]
)
async def test_unrecognized_or_invalid_dialog_is_never_clicked(response):
    controls = AsyncMock()
    with pytest.raises(BrowserSourceUnavailable) as raised:
        await dismiss_booking_window_notice(
            execute_script=AsyncMock(return_value=response),
            find_controls=controls,
            monotonic=lambda: 0,
            sleep=AsyncMock(),
            timeout_seconds=5,
        )
    assert raised.value.stage == "search_notice_unrecognized"
    controls.assert_not_awaited()


@pytest.mark.parametrize("count", [0, 2])
async def test_notice_with_no_unique_close_control_is_never_clicked(count):
    close = SimpleNamespace(click=AsyncMock())
    with pytest.raises(BrowserSourceUnavailable) as raised:
        await dismiss_booking_window_notice(
            execute_script=AsyncMock(return_value=script_result("booking_window_expansion")),
            find_controls=AsyncMock(return_value=[close] * count),
            monotonic=lambda: 0,
            sleep=AsyncMock(),
            timeout_seconds=5,
        )
    assert raised.value.stage == "search_notice_unrecognized"
    close.click.assert_not_awaited()


async def test_persisting_notice_stops_without_repeating_the_close_click():
    close = SimpleNamespace(click=AsyncMock())
    times = iter([0.0, 6.0])
    with pytest.raises(BrowserSourceUnavailable) as raised:
        await dismiss_booking_window_notice(
            execute_script=AsyncMock(return_value=script_result("booking_window_expansion")),
            find_controls=AsyncMock(return_value=[close]),
            monotonic=lambda: next(times),
            sleep=AsyncMock(),
            timeout_seconds=5,
        )
    assert raised.value.stage == "search_notice_persisted"
    close.click.assert_awaited_once()


async def test_protected_snapshot_keeps_the_notice_untouched():
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    script = AsyncMock()
    session._tab = SimpleNamespace(execute_script=script)
    protected = PydollPageSnapshot("창닫기", (), network_responses=((403, "document"),))

    assert await session.expand_results(protected, 19) == protected
    script.assert_not_awaited()
