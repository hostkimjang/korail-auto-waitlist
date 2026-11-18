from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rail_waitlist.korail_pydoll_browser import _PydollSession
from rail_waitlist.korail_sidecar.browser_contracts import (
    BrowserProtectionDetected,
    BrowserRateLimited,
    BrowserSourceUnavailable,
)
from rail_waitlist.korail_sidecar.browser_service_availability import BrowserProviderUnavailable
from rail_waitlist.korail_sidecar.pydoll.page_contracts import PydollPageSnapshot, PydollTrainRow
from rail_waitlist.korail_sidecar.pydoll.reservation_driver import (
    _RESERVATION_CLICK_OBSERVATION_SCRIPT,
    _RESERVATION_CLICK_PREPARE_SCRIPT,
)
from rail_waitlist.korail_sidecar.pydoll.search_notice import (
    CLOSE_SELECTOR,
    FALLBACK_CLOSE_SELECTOR,
    FALLBACK_OBSERVE_SCRIPT,
    MAX_NOTICE_CLOSE_ACTIONS,
    OBSERVE_NOTICE_SCRIPT,
    RESERVATION_NOTICE_SCRIPT,
    dismiss_public_search_notices,
)

pytestmark = pytest.mark.asyncio(loop_scope="module")


def script_result(
    state: object, *, key: str = "1:abc", close_index: int = 0, control_count: int = 1
) -> dict[str, object]:
    if state == "public_search_notice":
        state = {
            "state": state,
            "key": key,
            "close_index": close_index,
            "control_count": control_count,
            "target": 1,
        }
    return {"result": {"result": {"value": state}}}


async def test_reservation_notice_preparation_observes_live_modal_and_refreshes_rows(monkeypatch):
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    initial = PydollPageSnapshot("조회 결과", ())
    clean = PydollPageSnapshot("안내가 사라진 조회 결과", ())
    close = SimpleNamespace(click=AsyncMock())
    session._tab = SimpleNamespace(
        execute_script=AsyncMock(
            side_effect=[
                script_result("public_search_notice"),
                script_result("public_search_notice"),
                script_result("absent"),
            ]
        )
    )
    monkeypatch.setattr(session, "_visible_elements", AsyncMock(return_value=[close]))
    fresh_snapshot = AsyncMock(return_value=clean)
    monkeypatch.setattr(session, "_snapshot", fresh_snapshot)

    result = await session.dismiss_search_notice(initial)

    assert result is clean
    close.click.assert_awaited_once()
    fresh_snapshot.assert_awaited_once()


@pytest.mark.parametrize(
    ("kind", "station", "trigger_label", "input_name"),
    [
        ("departure", "대전", "출발역 선택", "txtGoStart"),
        ("arrival", "서울", "도착역 선택", "txtGoEnd"),
    ],
)
async def test_late_notice_closes_before_station_control_dispatch(
    monkeypatch, kind, station, trigger_label, input_name
):
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    # A stale body without a close label must not suppress the live modal observation.
    ready = PydollPageSnapshot("열차 조회", ())
    events = []
    close = SimpleNamespace(click=AsyncMock(side_effect=lambda: events.append("notice_close")))
    trigger = SimpleNamespace(click=AsyncMock(side_effect=lambda: events.append("station_open")))
    target = SimpleNamespace(click=AsyncMock(side_effect=lambda: events.append("station_select")))
    dialog = SimpleNamespace(text=asyncio.sleep(0, result="기차역 조회"))
    session._tab = SimpleNamespace(
        execute_script=AsyncMock(
            side_effect=[
                script_result("public_search_notice"),
                script_result("public_search_notice"),
                script_result("absent"),
            ]
        )
    )
    snapshots = AsyncMock(return_value=ready)
    controls = AsyncMock(
        side_effect=lambda selector: [dialog] if selector == "[role='dialog']" else [close]
    )
    finder = AsyncMock(side_effect=[trigger, target])
    readback = AsyncMock()
    monkeypatch.setattr(session, "_snapshot", snapshots)
    monkeypatch.setattr(session, "_visible_elements", controls)
    monkeypatch.setattr(session, "_find_exact_visible", finder)
    monkeypatch.setattr(session, "_wait_for_value", readback)

    await session.choose_station(kind, station)

    assert events == ["notice_close", "station_open", "station_select"]
    close.click.assert_awaited_once()
    trigger.click.assert_awaited_once()
    target.click.assert_awaited_once()
    assert [call.args[0] for call in controls.await_args_list] == [
        CLOSE_SELECTOR,
        "[role='dialog']",
    ]
    assert snapshots.await_count == 3
    assert finder.await_args_list[0].args == ("a", trigger_label)
    readback.assert_awaited_once_with(f"input[name='{input_name}']", station)
    assert session._submitted is False


@pytest.mark.parametrize("uncertain_close", [False, True])
async def test_station_selection_stops_on_unrecognized_or_uncertain_notice(
    monkeypatch, uncertain_close
):
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    close = SimpleNamespace(click=AsyncMock(side_effect=RuntimeError("click outcome unknown")))
    session._tab = SimpleNamespace(
        execute_script=AsyncMock(
            side_effect=[script_result("public_search_notice")] * 2
            if uncertain_close
            else [script_result("unrecognized")]
        )
    )
    finder = AsyncMock()
    monkeypatch.setattr(session, "_snapshot", AsyncMock(return_value=PydollPageSnapshot("", ())))
    monkeypatch.setattr(session, "_visible_elements", AsyncMock(return_value=[close]))
    monkeypatch.setattr(session, "_find_exact_visible", finder)

    with pytest.raises(BrowserSourceUnavailable) as raised:
        await session.choose_station("departure", "대전")

    assert raised.value.stage == (
        "search_notice_close_unknown" if uncertain_close else "search_notice_unrecognized"
    )
    assert close.click.await_count == int(uncertain_close)
    finder.assert_not_awaited()
    assert session._submitted is False


@pytest.mark.parametrize(
    ("network", "exception_type"),
    [
        ((403, "document"), BrowserProtectionDetected),
        ((429, "fetch"), BrowserRateLimited),
        ((500, "business_xhr"), BrowserProviderUnavailable),
    ],
)
async def test_station_selection_preserves_blocking_page_before_notice_actions(
    monkeypatch, network, exception_type
):
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    script = AsyncMock()
    session._tab = SimpleNamespace(execute_script=script)
    finder = AsyncMock()
    monkeypatch.setattr(
        session,
        "_snapshot",
        AsyncMock(return_value=PydollPageSnapshot("", (), network_responses=(network,))),
    )
    monkeypatch.setattr(session, "_find_exact_visible", finder)

    with pytest.raises(exception_type):
        await session.choose_station("departure", "대전")

    script.assert_not_awaited()
    finder.assert_not_awaited()
    assert session._submitted is False


async def test_station_selection_rechecks_page_block_after_notice_closed(monkeypatch):
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    close = SimpleNamespace(click=AsyncMock())
    session._tab = SimpleNamespace(
        execute_script=AsyncMock(
            side_effect=[
                script_result("public_search_notice"),
                script_result("public_search_notice"),
                script_result("absent"),
            ]
        )
    )
    finder = AsyncMock()
    monkeypatch.setattr(
        session,
        "_snapshot",
        AsyncMock(
            side_effect=[
                PydollPageSnapshot("", ()),
                PydollPageSnapshot("", (), network_responses=((500, "business_xhr"),)),
            ]
        ),
    )
    monkeypatch.setattr(session, "_visible_elements", AsyncMock(return_value=[close]))
    monkeypatch.setattr(session, "_find_exact_visible", finder)

    with pytest.raises(BrowserProviderUnavailable):
        await session.choose_station("departure", "대전")

    close.click.assert_awaited_once()
    finder.assert_not_awaited()
    assert session._submitted is False


class _StationDialog:
    @property
    async def text(self):
        return "기차역 조회"


def station_notice_race(monkeypatch, *, outcome="retry", network=None):
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    state = {"surface": "none", "clicks": 0, "clock": 0.0}
    events = []
    dialog = _StationDialog()

    async def click_trigger():
        state["clicks"] += 1
        events.append("station_open")
        if state["clicks"] == 1:
            state["surface"] = "none" if outcome == "no_notice" else "notice"
        else:
            state["surface"] = "notice_again" if outcome == "second_notice" else "station"

    async def close_notice():
        events.append("notice_close")
        if outcome == "uncertain_close":
            raise RuntimeError("fixture dispatch outcome unknown")
        state["surface"] = "station" if outcome == "opened_during_close" else "none"

    async def execute(script, *, return_by_value):
        assert script == OBSERVE_NOTICE_SCRIPT and return_by_value
        surface = state["surface"]
        if surface in {"notice", "notice_again"}:
            if outcome == "unrecognized":
                return script_result("unrecognized")
            return script_result("public_search_notice", key=f"{state['clicks']}:abc")
        return script_result("absent")

    trigger = SimpleNamespace(click=AsyncMock(side_effect=click_trigger))
    close = SimpleNamespace(click=AsyncMock(side_effect=close_notice))
    target = SimpleNamespace(click=AsyncMock(side_effect=lambda: events.append("station_select")))

    async def visible(selector, *, scope=None):
        assert scope is None
        if selector == CLOSE_SELECTOR:
            return [close]
        assert selector == "[role='dialog']"
        return [dialog] if state["surface"] == "station" else []

    async def find(selector, text, *, scope=None):
        assert selector == "a"
        if scope is None:
            assert text == "출발역 선택"
            return trigger
        assert scope is dialog and text == "대전"
        return target

    async def snapshot():
        failures = (network,) if network and state["clicks"] else ()
        return PydollPageSnapshot("열차 조회", (), network_responses=failures)

    async def sleep(seconds):
        state["clock"] += seconds

    session._tab = SimpleNamespace(execute_script=AsyncMock(side_effect=execute))
    monkeypatch.setattr(session, "_visible_elements", AsyncMock(side_effect=visible))
    monkeypatch.setattr(session, "_find_exact_visible", AsyncMock(side_effect=find))
    monkeypatch.setattr(session, "_snapshot", AsyncMock(side_effect=snapshot))
    monkeypatch.setattr(session, "_wait_for_value", AsyncMock())
    session._search_driver._monotonic = lambda: state["clock"]
    session._search_driver._sleep = sleep
    return session, events, trigger, close, target


@pytest.mark.parametrize("outcome", ["retry", "opened_during_close"])
async def test_notice_arriving_after_station_click_is_resolved_without_duplicate_selection(
    monkeypatch, outcome
):
    session, events, trigger, close, target = station_notice_race(monkeypatch, outcome=outcome)

    await session.choose_station("departure", "대전")

    expected = ["station_open", "notice_close"]
    if outcome == "retry":
        expected.append("station_open")
    assert events == expected + ["station_select"]
    assert trigger.click.await_count == (2 if outcome == "retry" else 1)
    close.click.assert_awaited_once()
    target.click.assert_awaited_once()
    session._wait_for_value.assert_awaited_once_with("input[name='txtGoStart']", "대전")
    assert session._submitted is False


@pytest.mark.parametrize(
    ("outcome", "stage", "trigger_count", "close_count"),
    [
        ("no_notice", "dialog", 1, 0),
        ("unrecognized", "search_notice_unrecognized", 1, 0),
        ("uncertain_close", "search_notice_close_unknown", 1, 1),
        ("second_notice", "station_notice_action_limit", 2, 2),
    ],
)
async def test_station_click_is_repeated_only_once_after_verified_notice(
    monkeypatch, outcome, stage, trigger_count, close_count
):
    session, _, trigger, close, target = station_notice_race(monkeypatch, outcome=outcome)

    with pytest.raises(BrowserSourceUnavailable) as raised:
        await session.choose_station("departure", "대전")

    assert raised.value.stage == stage
    assert trigger.click.await_count == trigger_count
    assert close.click.await_count == close_count
    target.click.assert_not_awaited()
    session._wait_for_value.assert_not_awaited()
    assert session._submitted is False


@pytest.mark.parametrize(
    ("network", "exception_type"),
    [
        ((403, "document"), BrowserProtectionDetected),
        ((429, "fetch"), BrowserRateLimited),
        ((500, "business_xhr"), BrowserProviderUnavailable),
    ],
)
async def test_station_wait_block_preempts_late_notice_and_trigger_retry(
    monkeypatch, network, exception_type
):
    session, _, trigger, close, target = station_notice_race(monkeypatch, network=network)

    with pytest.raises(exception_type):
        await session.choose_station("departure", "대전")

    trigger.click.assert_awaited_once()
    close.click.assert_not_awaited()
    target.click.assert_not_awaited()
    assert session._submitted is False


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
            script_result("public_search_notice"),
            script_result("public_search_notice"),
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
    assert "event=search_notice_dismissed strict=1 fallback=0" in caplog.text
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
                script_result("public_search_notice"),
                script_result("public_search_notice"),
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
        await dismiss_public_search_notices(
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
        await dismiss_public_search_notices(
            execute_script=AsyncMock(return_value=script_result("public_search_notice")),
            find_controls=AsyncMock(return_value=[close] * count),
            monotonic=lambda: 0,
            sleep=AsyncMock(),
            timeout_seconds=5,
        )
    assert raised.value.stage == "search_notice_unrecognized"
    close.click.assert_not_awaited()


async def test_persisting_notice_stops_without_repeating_the_close_click():
    close = SimpleNamespace(click=AsyncMock())
    times = iter([0.0, 0.0, 6.0])
    with pytest.raises(BrowserSourceUnavailable) as raised:
        await dismiss_public_search_notices(
            execute_script=AsyncMock(return_value=script_result("public_search_notice")),
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


async def test_changed_notice_is_rejected_after_resolving_close_controls():
    close = SimpleNamespace(click=AsyncMock())
    with pytest.raises(BrowserSourceUnavailable) as raised:
        await dismiss_public_search_notices(
            execute_script=AsyncMock(
                side_effect=[script_result("public_search_notice"), script_result("unrecognized")]
            ),
            find_controls=AsyncMock(return_value=[close]),
            monotonic=lambda: 0,
            sleep=AsyncMock(),
            timeout_seconds=5,
        )
    close.click.assert_not_awaited()
    assert raised.value.stage == "search_notice_changed"


async def test_uncertain_close_dispatch_is_not_repeated():
    close = SimpleNamespace(click=AsyncMock(side_effect=RuntimeError("opaque fixture failure")))
    with pytest.raises(BrowserSourceUnavailable) as raised:
        await dismiss_public_search_notices(
            execute_script=AsyncMock(return_value=script_result("public_search_notice")),
            find_controls=AsyncMock(return_value=[close]),
            monotonic=lambda: 0,
            sleep=AsyncMock(),
            timeout_seconds=5,
        )
    close.click.assert_awaited_once()
    assert raised.value.stage == "search_notice_close_unknown"


async def test_successive_notices_stop_at_the_total_action_limit():
    close = SimpleNamespace(click=AsyncMock())
    responses = [script_result("public_search_notice", key="1:abc")]
    for index in range(1, MAX_NOTICE_CLOSE_ACTIONS + 1):
        responses.extend(
            [
                script_result("public_search_notice", key=f"{index}:abc"),
                script_result("public_search_notice", key=f"{index + 1}:abc"),
            ]
        )
    with pytest.raises(BrowserSourceUnavailable) as raised:
        await dismiss_public_search_notices(
            execute_script=AsyncMock(side_effect=responses),
            find_controls=AsyncMock(return_value=[close]),
            monotonic=lambda: 0,
            sleep=AsyncMock(),
            timeout_seconds=5,
        )
    assert close.click.await_count == MAX_NOTICE_CLOSE_ACTIONS
    assert raised.value.stage == "search_notice_action_limit"


@pytest.fixture(scope="module")
async def notice_browser():
    playwright = pytest.importorskip("playwright.async_api")
    async with playwright.async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        yield browser
        await browser.close()


def notice_markup(content: str, *, close: str = "창닫기", extra: str = "") -> str:
    return f"""
    <div class="ReactModal__Content" role="dialog" aria-modal="true"
      style="position:fixed;inset:10px;background:white;z-index:10">
      <div class="layerWrap emer_pop">
        <div class="pop_content" style="min-height:40px">{content}</div>
        {extra}<button onclick="this.closest('[role=dialog]').remove()">{close}</button>
      </div>
    </div>"""


async def notice_fixture_page(browser, markup: str, *, path: str = "/ticket/search/general"):
    page = await browser.new_page()
    url = f"https://www.korail.com{path}"

    async def route_request(route):
        if route.request.url == url and route.request.is_navigation_request():
            await route.fulfill(
                content_type="text/html; charset=utf-8",
                body=f"<!doctype html><html><body>{markup}</body></html>",
            )
        else:
            await route.abort()

    await page.route("**/*", route_request)
    await page.goto(url)
    return page


@pytest.mark.parametrize(
    ("content", "close", "extra"),
    [
        ('<img alt="새로운 겨울 운행 공지">', "창닫기", ""),
        ('<img alt=""><img alt="추가 공지">', "창닫기", ""),
        ("<h2>시스템 점검 안내</h2><p>새 공지 내용입니다.</p>", "닫기", ""),
        ('<a href="https://www.korail.com/notice">공지 자세히 보기</a>', "창닫기", ""),
        (
            "<p>새 행사 안내</p>",
            "창닫기",
            '<label><input type="checkbox" checked>오늘 그만 보기</label>',
        ),
    ],
)
@pytest.mark.parametrize("path", ["/ticket/search/general", "/ticket/search/list"])
async def test_new_public_notice_content_is_closed_using_real_dom(
    notice_browser, content, close, extra, path
):
    page = await notice_fixture_page(
        notice_browser, notice_markup(content, close=close, extra=extra), path=path
    )
    try:
        before = await page.evaluate(OBSERVE_NOTICE_SCRIPT)
        assert before["state"] == "public_search_notice"
        await page.evaluate(
            """() => {window.checkboxChanges = 0; window.linkClicks = 0;
            document.addEventListener('change', () => window.checkboxChanges++);
            document.querySelectorAll('a').forEach(a => a.onclick = () => window.linkClicks++);} """
        )
        assert await dismiss_fixture_notices(page)
        assert await page.evaluate(OBSERVE_NOTICE_SCRIPT) == "absent"
        assert await page.evaluate("window.checkboxChanges") == 0
        assert await page.evaluate("window.linkClicks") == 0
    finally:
        await page.close()


async def dismiss_fixture_notices(page, *, context="search", report_unrecognized=None):
    async def execute(script, *, return_by_value):
        assert return_by_value
        return script_result(await page.evaluate(script))

    async def controls(selector):
        return [
            control for control in await page.locator(selector).all() if await control.is_visible()
        ]

    return await dismiss_public_search_notices(
        execute_script=execute,
        find_controls=controls,
        monotonic=time.monotonic,
        sleep=asyncio.sleep,
        timeout_seconds=5,
        context=context,
        report_unrecognized=report_unrecognized,
    )


async def test_reservation_notice_closes_only_public_popup_over_booking_dialog(notice_browser):
    booking = """<div role="dialog" aria-modal="true" id="booking"
      style="position:fixed;inset:30px;background:white;z-index:5">
      <div class="tit_wrap"><h1 class="tit">이용안내</h1></div>
      <div class="confirm_message">승차할 열차를 확인해 주세요.</div>
      <button onclick="window.bookingClicks=(window.bookingClicks||0)+1">확인</button></div>"""
    public = notice_markup(
        '<img alt="운행 공지">',
        extra='<label><input type="checkbox">오늘 그만 보기</label>'
        '<button onclick="window.surveyClicks=(window.surveyClicks||0)+1">설문조사 참여</button>',
    )
    page = await notice_fixture_page(notice_browser, booking + public)
    try:
        assert (await page.evaluate(RESERVATION_NOTICE_SCRIPT))["state"] == "public_search_notice"
        assert await dismiss_fixture_notices(page, context="reservation") is True
        assert await page.locator("#booking").count() == 1
        assert await page.evaluate("window.bookingClicks || 0") == 0
        assert await page.evaluate("window.surveyClicks || 0") == 0
        assert await page.evaluate(RESERVATION_NOTICE_SCRIPT) == "absent"
    finally:
        await page.close()


async def test_reservation_context_never_falls_back_to_generic_dialog_dismissal(notice_browser):
    page = await notice_fixture_page(
        notice_browser,
        generic_modal_markup("예약 조치 안내").replace(
            '<label><input type="checkbox">오늘 그만 보기</label>', ""
        ),
    )
    try:
        assert await dismiss_fixture_notices(page, context="reservation") is False
        assert await page.locator('[role="dialog"]').count() == 1
    finally:
        await page.close()


async def test_reservation_dispatch_never_clicks_through_popup_using_real_dom(notice_browser):
    page = await notice_fixture_page(
        notice_browser,
        """<button class="reservbtn" style="position:fixed;left:30px;top:30px"
          onclick="window.requests=(window.requests||0)+1">예매</button>
        <div id="cover" style="position:fixed;inset:0;background:white;z-index:20"></div>""",
    )
    dispatch = (
        "(() => { const button = document.querySelector('button.reservbtn'); return ("
        + _RESERVATION_CLICK_PREPARE_SCRIPT
        + ").call(button); })()"
    )
    try:
        assert await page.evaluate(dispatch) == "covered"
        assert await page.evaluate("window.requests || 0") == 0
        await page.locator("#cover").evaluate("node => node.remove()")
        assert await page.evaluate(dispatch) == "ready"
        await page.evaluate("""() => {const cover=document.createElement('div');cover.id='late';
          cover.style='position:fixed;inset:0;background:white;z-index:20';
          document.body.append(cover);} """)
        await page.mouse.click(50, 40)
        observed = dispatch.replace(
            _RESERVATION_CLICK_PREPARE_SCRIPT, _RESERVATION_CLICK_OBSERVATION_SCRIPT
        )
        assert await page.evaluate(observed) == "intercepted"
        assert await page.evaluate("window.requests || 0") == 0
        await page.locator("#late").evaluate("node => node.remove()")
        assert await page.evaluate(dispatch) == "ready"
        await page.locator("button.reservbtn").click()
        observed = dispatch.replace(
            _RESERVATION_CLICK_PREPARE_SCRIPT, _RESERVATION_CLICK_OBSERVATION_SCRIPT
        )
        assert await page.evaluate(observed) == "dispatched"
        assert await page.evaluate(observed) == "unconfirmed"
        assert await page.evaluate("window.requests") == 1
    finally:
        await page.close()


async def test_stacked_public_notices_close_topmost_then_next_using_real_dom(notice_browser):
    page = await notice_fixture_page(
        notice_browser, notice_markup("첫 공지") + notice_markup("두 번째 공지")
    )
    try:
        assert (await page.evaluate(OBSERVE_NOTICE_SCRIPT))["close_index"] == 1
        assert await dismiss_fixture_notices(page)
        assert await page.evaluate(OBSERVE_NOTICE_SCRIPT) == "absent"
    finally:
        await page.close()


async def test_new_content_in_same_modal_is_closed_using_real_dom(notice_browser):
    page = await notice_fixture_page(notice_browser, notice_markup("첫 공지"))
    try:
        await page.evaluate(
            """() => document.querySelector('button').onclick = function() {
              document.querySelector('.pop_content').innerText = '다음 공지';
              this.onclick = () => this.closest('[role=dialog]').remove();
            }"""
        )
        assert await dismiss_fixture_notices(page)
        assert await page.evaluate(OBSERVE_NOTICE_SCRIPT) == "absent"
    finally:
        await page.close()


async def test_survey_notice_with_call_to_action_is_closed_using_real_dom(notice_browser):
    # Captured 2026-11-10 from www.korail.com: a national-railway-plan survey
    # announcement pairs "설문 참여하기" with "창닫기" beside the "1일간 그만보기"
    # checkbox. Only the close control may be clicked; the survey CTA must stay
    # untouched so the popup dismissal never navigates or submits anything.
    markup = notice_markup(
        '<p><img alt="제5차 국가철도망 구축계획 대국민 설문조사"></p>',
        extra=(
            '<input type="checkbox" id="popClose121">'
            '<button title="새 창으로 열기" '
            'onclick="window.surveyClicks=(window.surveyClicks||0)+1">설문 참여하기</button>'
        ),
    )
    page = await notice_fixture_page(notice_browser, markup)
    try:
        observed = await page.evaluate(OBSERVE_NOTICE_SCRIPT)
        assert observed["state"] == "public_search_notice"
        assert observed["close_index"] == 1
        assert observed["control_count"] == 2
        assert await dismiss_fixture_notices(page)
        assert await page.evaluate(OBSERVE_NOTICE_SCRIPT) == "absent"
        assert await page.evaluate("window.surveyClicks || 0") == 0
    finally:
        await page.close()


async def test_long_notice_scrolls_to_close_without_clicking_its_content(notice_browser):
    markup = notice_markup('<div style="height:1200px">긴 신규 공지</div>').replace(
        "background:white;", "background:white;overflow:auto;"
    )
    page = await notice_fixture_page(notice_browser, markup)
    try:
        assert await dismiss_fixture_notices(page)
        assert await page.evaluate(OBSERVE_NOTICE_SCRIPT) == "absent"
    finally:
        await page.close()


@pytest.mark.parametrize(
    "markup",
    [
        notice_markup("예약 동의", close="네"),
        notice_markup("동의 확인", close="확인"),
        notice_markup("입력 필요", extra='<input type="text">'),
        notice_markup("선택 필요", extra="<select><option>선택</option></select>"),
        notice_markup("폼 처리", extra="<form></form>"),
        notice_markup("외부 내용", extra="<iframe></iframe>"),
        notice_markup("공지").replace("onclick=", "disabled onclick="),
        notice_markup("공지") + '<div role="dialog" style="position:fixed;inset:0">인증 확인</div>',
    ],
)
async def test_non_public_or_action_dialog_is_never_closed_using_real_dom(notice_browser, markup):
    page = await notice_fixture_page(notice_browser, markup)
    try:
        assert (await page.evaluate(OBSERVE_NOTICE_SCRIPT))["state"] == "unrecognized"
        assert (await page.evaluate(FALLBACK_OBSERVE_SCRIPT))["state"] == "unrecognized"
        controls = AsyncMock()

        async def execute(script, *, return_by_value):
            return script_result(await page.evaluate(script))

        with pytest.raises(BrowserSourceUnavailable) as raised:
            await dismiss_public_search_notices(
                execute_script=execute,
                find_controls=controls,
                monotonic=lambda: 0,
                sleep=AsyncMock(),
                timeout_seconds=5,
            )
        controls.assert_not_awaited()
        assert raised.value.stage == "search_notice_unrecognized"
    finally:
        await page.close()


async def test_notice_on_reservation_page_is_never_closed_using_real_dom(notice_browser):
    page = await notice_fixture_page(
        notice_browser, notice_markup("안내"), path="/ticket/reservation/list"
    )
    try:
        assert await page.evaluate(OBSERVE_NOTICE_SCRIPT) == "unrecognized"
        assert await page.evaluate(FALLBACK_OBSERVE_SCRIPT) == "unrecognized"
    finally:
        await page.close()


def generic_modal_markup(content: str, *, extra: str = "") -> str:
    return f"""
    <div role="dialog" aria-modal="true" class="korailPopup2026"
      style="position:fixed;inset:20px;background:white;z-index:20">
      <p>{content}</p>
      <label><input type="checkbox">오늘 그만 보기</label>
      {extra}<button onclick="this.closest('[role=dialog]').remove()">창닫기</button>
    </div>"""


@pytest.mark.parametrize(
    "markup",
    [
        notice_markup("새 이미지 공지").replace("ReactModal__Content", "popup-v2"),
        generic_modal_markup("새 프로모션"),
        generic_modal_markup("프로모션 링크", extra='<a href="/event">이벤트 보기</a>'),
        generic_modal_markup("설문", extra="<button>설문 참여하기</button>"),
    ],
)
async def test_fallback_closes_variants_with_one_close_control_using_real_dom(
    notice_browser, markup
):
    page = await notice_fixture_page(notice_browser, markup)
    try:
        assert (await page.evaluate(FALLBACK_OBSERVE_SCRIPT))["state"] == "fallback_notice"
        await page.evaluate("""() => {window.ctaClicks = 0; window.checkboxChanges = 0;
          document.addEventListener('change', () => window.checkboxChanges++);
          document.querySelectorAll('a, button').forEach(node => node.addEventListener(
          'click', () => {if (!/창닫기|닫기/.test(node.innerText)) window.ctaClicks++;}));}""")
        assert await dismiss_fixture_notices(page)
        assert await page.evaluate("window.ctaClicks") == 0
        assert await page.evaluate("window.checkboxChanges") == 0
    finally:
        await page.close()


async def test_fallback_marks_close_control_for_verified_click_using_real_dom(notice_browser):
    page = await notice_fixture_page(notice_browser, generic_modal_markup("새 형태로 바뀐 공지"))
    try:
        strict = await page.evaluate(OBSERVE_NOTICE_SCRIPT)
        assert strict["state"] == "unrecognized"
        observed = await page.evaluate(FALLBACK_OBSERVE_SCRIPT)
        assert observed["state"] == "fallback_notice"
        seen = []

        async def execute(script, *, return_by_value):
            return script_result(await page.evaluate(script))

        async def controls(selector):
            seen.append(selector)
            return [
                control
                for control in await page.locator(selector).all()
                if await control.is_visible()
            ]

        assert await dismiss_public_search_notices(
            execute_script=execute,
            find_controls=controls,
            monotonic=time.monotonic,
            sleep=asyncio.sleep,
            timeout_seconds=5,
        )
        assert FALLBACK_CLOSE_SELECTOR in seen
        assert await page.evaluate(OBSERVE_NOTICE_SCRIPT) == "absent"
    finally:
        await page.close()


async def test_stacked_fallback_dialogs_close_topmost_then_next_using_real_dom(notice_browser):
    page = await notice_fixture_page(
        notice_browser,
        generic_modal_markup("첫 폴백 공지") + generic_modal_markup("둘째 폴백 공지"),
    )
    try:
        assert await dismiss_fixture_notices(page)
        assert await page.evaluate(OBSERVE_NOTICE_SCRIPT) == "absent"
        assert await page.evaluate("document.querySelectorAll('[role=dialog]').length") == 0
    finally:
        await page.close()


async def test_strict_and_fallback_dialogs_close_in_any_order_using_real_dom(notice_browser):
    page = await notice_fixture_page(
        notice_browser, generic_modal_markup("폴백 공지") + notice_markup("공지")
    )
    try:
        assert await dismiss_fixture_notices(page)
        assert await page.evaluate(OBSERVE_NOTICE_SCRIPT) == "absent"
    finally:
        await page.close()


async def test_equivalent_close_controls_close_the_notice_once_using_real_dom(notice_browser):
    page = await notice_fixture_page(
        notice_browser,
        generic_modal_markup(
            "닫기가 둘",
            extra='<button aria-label="팝업 닫기" '
            "onclick=\"this.closest('[role=dialog]').remove()\">×</button>",
        ),
    )
    try:
        observed = await page.evaluate(FALLBACK_OBSERVE_SCRIPT)
        assert observed["state"] == "fallback_notice"
        assert observed["control_count"] == 1
        await page.evaluate("""() => {window.closeClicks = 0;
          document.querySelectorAll('button').forEach(node =>
            node.addEventListener('click', () => window.closeClicks++));}""")
        assert await dismiss_fixture_notices(page)
        assert await page.evaluate("window.closeClicks") == 1
        assert await page.evaluate("document.querySelectorAll('[role=dialog]').length") == 0
    finally:
        await page.close()


async def test_hidden_close_control_blocks_fallback_without_escape_guess(notice_browser):
    page = await notice_fixture_page(
        notice_browser,
        generic_modal_markup("은닉 닫기").replace("<button ", "<button hidden "),
    )
    try:
        observed = await page.evaluate(FALLBACK_OBSERVE_SCRIPT)
        assert observed["state"] == "unrecognized"
        controls = AsyncMock()

        async def execute(script, *, return_by_value):
            return script_result(await page.evaluate(script))

        with pytest.raises(BrowserSourceUnavailable) as raised:
            await dismiss_public_search_notices(
                execute_script=execute,
                find_controls=controls,
                monotonic=lambda: 0,
                sleep=AsyncMock(),
                timeout_seconds=5,
            )
        assert raised.value.stage == "search_notice_unrecognized"
        controls.assert_not_awaited()
        assert await page.evaluate("document.querySelectorAll('[role=dialog]').length") == 1
    finally:
        await page.close()


@pytest.mark.parametrize(
    "markup",
    [
        '<dialog open style="position:fixed;inset:30px">제휴 서비스 안내</dialog>',
        '<div role="dialog" aria-modal="true" style="position:fixed;inset:20px">'
        "알 수 없는 안내</div>",
    ],
)
async def test_control_free_dialogs_are_observed_without_escape_or_native_close(
    notice_browser, markup
):
    page = await notice_fixture_page(notice_browser, markup)
    try:
        await page.evaluate("""() => {window.escapes = 0;
          document.addEventListener('keydown', () => window.escapes++);}""")
        assert (await page.evaluate(FALLBACK_OBSERVE_SCRIPT))["state"] == "unrecognized"
        with pytest.raises(BrowserSourceUnavailable):
            await dismiss_fixture_notices(page)
        assert await page.evaluate("window.escapes") == 0
        assert await page.locator("[role=dialog], dialog[open]").count() == 1
    finally:
        await page.close()


async def test_unrecognized_failure_reports_structural_summary():
    reports = []
    with pytest.raises(BrowserSourceUnavailable) as raised:
        await dismiss_public_search_notices(
            execute_script=AsyncMock(
                return_value=script_result(
                    {
                        "state": "unrecognized",
                        "shape": "div.layerWrap koreaPop aria-modal=true 2 controls"
                        " [확인 | 닫기] risky:input",
                    }
                )
            ),
            find_controls=AsyncMock(),
            monotonic=lambda: 0,
            sleep=AsyncMock(),
            timeout_seconds=5,
            report_unrecognized=reports.append,
        )
    assert raised.value.stage == "search_notice_unrecognized"
    assert [report.stage for report in reports] == ["search_notice_unrecognized"]
    assert reports[0].attempts == 0
    assert "koreaPop" in reports[0].shape_summary


async def test_closed_notice_that_persists_reports_attempt_and_method():
    close = SimpleNamespace(click=AsyncMock())
    times = iter([0.0, 0.0, 6.0, 6.0, 6.0])
    reports = []
    with pytest.raises(BrowserSourceUnavailable) as raised:
        await dismiss_public_search_notices(
            execute_script=AsyncMock(return_value=script_result("public_search_notice")),
            find_controls=AsyncMock(return_value=[close]),
            monotonic=lambda: next(times),
            sleep=AsyncMock(),
            timeout_seconds=5,
            report_unrecognized=reports.append,
        )
    assert raised.value.stage == "search_notice_persisted"
    assert reports[-1].attempts == 1
    assert reports[-1].close_method == "strict_close_button"


async def test_session_passes_notice_reports_to_the_configured_sink(monkeypatch):
    reports = []
    session = _PydollSession(
        "https://www.korail.com/ticket/search/general",
        1_000,
        True,
        notice_report_sink=reports.append,
    )
    session._tab = SimpleNamespace(
        execute_script=AsyncMock(
            return_value=script_result(
                {
                    "state": "unrecognized",
                    "shape": "div.korailPop2027 aria-modal=true 0 controls [] no risky tags",
                }
            )
        )
    )
    monkeypatch.setattr(
        session, "_snapshot", AsyncMock(return_value=PydollPageSnapshot("닫기 안내", ()))
    )

    with pytest.raises(BrowserSourceUnavailable) as raised:
        await session.dismiss_search_notice(PydollPageSnapshot("닫기 안내", ()))

    assert raised.value.stage == "search_notice_unrecognized"
    assert reports[-1].shape_summary.startswith("div.korailPop2027")


@pytest.mark.parametrize(
    "markup",
    [
        notice_markup("공지", extra='<input type="checkbox" aria-label="약관 동의">'),
        notice_markup("공지", extra='<input type="checkbox"><span>오늘 그만 보기</span>'),
        notice_markup(
            "공지", extra='<label><input type="checkbox">약관 동의</label><p>오늘 그만 보기</p>'
        ),
        notice_markup("공지", close="동의하고 닫기"),
        notice_markup("공지", close="결제 후 닫기"),
        notice_markup("공지").replace(">창닫기</button>", ' aria-label="결제">X</button>'),
        notice_markup("공지").replace(">창닫기</button>", ' title="동의">창닫기</button>'),
        '<form id="payment"></form>'
        + notice_markup("공지").replace("<button ", '<button form="payment" '),
        notice_markup("공지", extra="<div contenteditable>편집</div>"),
        notice_markup("공지", extra='<div contenteditable="plaintext-only">편집</div>'),
        notice_markup("공지", extra='<div role="checkbox">동의</div>'),
        notice_markup("공지", extra='<div role="textbox"></div>'),
        generic_modal_markup("공지").replace(
            '<label><input type="checkbox">오늘 그만 보기</label>', ""
        ),
        generic_modal_markup("공지")
        .replace("<button ", '<a href="/event" ')
        .replace("</button>", "</a>"),
    ],
)
async def test_ambiguous_payment_consent_and_unknown_controls_never_close(notice_browser, markup):
    page = await notice_fixture_page(notice_browser, markup)
    try:
        await page.evaluate(
            "() => {window.clicks=0; document.addEventListener('click', () => window.clicks++);}"
        )
        assert (await page.evaluate(FALLBACK_OBSERVE_SCRIPT))["state"] == "unrecognized"
        with pytest.raises(BrowserSourceUnavailable):
            await dismiss_fixture_notices(page)
        assert await page.evaluate("window.clicks") == 0
    finally:
        await page.close()


@pytest.mark.parametrize(
    "markup",
    [
        notice_markup("공지", extra="<button>새 예약</button>"),
        notice_markup("공지", extra='<a href="/ticket/reservation/list">예약 확인</a>'),
        notice_markup("결제 진행에 동의해 주세요"),
        notice_markup("새 설문", extra="<button>설문 참여하기</button>"),
        generic_modal_markup("공지", extra="<button>확인</button>"),
        generic_modal_markup("공지", extra='<a href="/ticket/payment">자세히 보기</a>'),
        generic_modal_markup("공지", extra="<button>처리</button>"),
        generic_modal_markup("특가", extra='<a href="/ticket/search/general">예매하러 가기</a>'),
    ],
)
@pytest.mark.parametrize("context", ["search", "reservation"])
async def test_verified_notice_closes_without_pressing_its_other_controls(
    notice_browser, markup, context
):
    # Promotions add calls to action every few weeks. With public-notice evidence and an
    # unambiguous close, their wording never blocks the search, and they are never pressed.
    page = await notice_fixture_page(notice_browser, markup)
    try:
        await page.evaluate("""() => {window.otherClicks = 0;
          document.addEventListener('click', event => {
            if (event.target.innerText !== '창닫기') window.otherClicks++;
          }, true);}""")
        assert await dismiss_fixture_notices(page, context=context)
        assert await page.locator("[role=dialog]").count() == 0
        assert await page.evaluate("window.otherClicks") == 0
    finally:
        await page.close()


@pytest.mark.parametrize("label", ['aria-label="닫기"', 'title="Close"', 'aria-label="창 닫기"'])
@pytest.mark.parametrize("context", ["search", "reservation"])
async def test_changed_promotion_layout_with_icon_close(notice_browser, label, context):
    markup = (
        generic_modal_markup("새 시즌 할인 안내")
        .replace("<button ", f"<button {label} ")
        .replace(">창닫기</button>", ">×</button>")
    )
    page = await notice_fixture_page(notice_browser, markup)
    try:
        assert await dismiss_fixture_notices(page, context=context)
        assert await page.locator("[role=dialog]").count() == 0
    finally:
        await page.close()


async def test_changed_promotion_in_reservation_preserves_unknown_booking_dialog(notice_browser):
    booking = (
        '<div role="dialog" aria-modal="true" id="booking" '
        'style="position:fixed;inset:30px;z-index:5">'
        "<p>결제 안내</p><button>동의</button></div>"
    )
    page = await notice_fixture_page(notice_browser, booking + generic_modal_markup("새 프로모션"))
    try:
        assert await dismiss_fixture_notices(page, context="reservation")
        assert await page.locator("#booking").count() == 1
    finally:
        await page.close()


@pytest.mark.parametrize(
    "mutation",
    [
        "button.replaceWith(button.cloneNode(true))",
        "button.setAttribute('aria-label', '결제')",
        "document.querySelector('[role=dialog]').append("
        "Object.assign(document.createElement('button'), {innerText:'동의'}))",
    ],
)
async def test_changed_close_target_is_not_clicked_after_lookup(notice_browser, mutation):
    page = await notice_fixture_page(notice_browser, generic_modal_markup("새 프로모션"))
    try:

        async def execute(script, *, return_by_value):
            return script_result(await page.evaluate(script))

        handles = []

        async def controls(selector):
            found = await page.locator(selector).element_handles()
            handles.extend(found)
            await page.evaluate(
                "() => {const button=document.querySelector('button'); " + mutation + ";}"
            )
            return found

        await page.evaluate(
            "() => {window.clicks=0; document.addEventListener('click', () => window.clicks++);}"
        )
        reports = []
        with pytest.raises(BrowserSourceUnavailable) as raised:
            await dismiss_public_search_notices(
                execute_script=execute,
                find_controls=controls,
                monotonic=time.monotonic,
                sleep=asyncio.sleep,
                timeout_seconds=5,
                report_unrecognized=reports.append,
            )
        assert await page.evaluate("window.clicks") == 0
        assert raised.value.stage == "search_notice_changed"
        assert reports[-1].stage == "search_notice_changed"
    finally:
        await page.close()


async def test_unknown_labels_and_class_names_never_leave_structural_summary(notice_browser):
    markup = (
        generic_modal_markup("민감한 본문")
        .replace("korailPopup2026", "private-account-id")
        .replace(">창닫기</button>", ">private-user-token</button>")
    )
    page = await notice_fixture_page(notice_browser, markup)
    try:
        observed = await page.evaluate(FALLBACK_OBSERVE_SCRIPT)
        assert observed["state"] == "unrecognized"
        assert "private" not in observed["shape"]
        assert "민감한" not in observed["shape"]
        assert "other" in observed["shape"]
    finally:
        await page.close()


@pytest.mark.parametrize(
    "input_markup",
    [
        '<input type="checkbox" id="popClose121" aria-labelledby="external">',
        '<label><input type="checkbox" aria-labelledby="external">오늘 그만 보기</label>',
        '<label><input type="checkbox" title="동의">오늘 그만 보기</label>',
        '<input type="checkbox" id="popClose121" aria-labelledby="missing">',
    ],
)
async def test_conflicting_or_unresolved_reminder_labels_never_close(notice_browser, input_markup):
    page = await notice_fixture_page(
        notice_browser, '<p id="external">동의</p>' + notice_markup("새 공지", extra=input_markup)
    )
    try:
        assert (await page.evaluate(OBSERVE_NOTICE_SCRIPT))["state"] == "unrecognized"
        assert (await page.evaluate(FALLBACK_OBSERVE_SCRIPT))["state"] == "unrecognized"
    finally:
        await page.close()


async def test_survey_in_known_layer_closes_through_the_fixed_selector(notice_browser):
    page = await notice_fixture_page(
        notice_browser,
        notice_markup(
            "새 설문",
            extra='<button onclick="window.surveyClicks=(window.surveyClicks||0)+1">'
            "설문 참여하기</button>",
        ),
    )
    try:
        observed = await page.evaluate(OBSERVE_NOTICE_SCRIPT)
        assert observed["state"] == "public_search_notice"
        assert (observed["close_index"], observed["control_count"]) == (1, 2)
        assert await dismiss_fixture_notices(page)
        assert await page.evaluate("window.surveyClicks || 0") == 0
    finally:
        await page.close()


@pytest.mark.parametrize("context", ["search", "reservation"])
async def test_nested_unknown_dialog_never_closes_via_public_outer_layer(notice_browser, context):
    page = await notice_fixture_page(
        notice_browser,
        notice_markup(
            '<div role="dialog" aria-modal="true">알 수 없는 화면<button>처리</button></div>'
        ),
    )
    try:
        with pytest.raises(BrowserSourceUnavailable):
            await dismiss_fixture_notices(page, context=context)
        assert await page.locator("[role=dialog]").count() == 2
    finally:
        await page.close()


async def test_fallback_same_layer_new_promotion_is_revalidated(notice_browser):
    page = await notice_fixture_page(notice_browser, generic_modal_markup("첫 프로모션"))
    try:
        await page.evaluate("""() => {document.querySelector('button').onclick = function() {
          document.querySelector('p').textContent = '다음 프로모션';
          this.onclick = () => this.closest('[role=dialog]').remove();};}""")
        assert await dismiss_fixture_notices(page)
        assert await page.locator("[role=dialog]").count() == 0
    finally:
        await page.close()


# Captured 2026-11-17 from www.korail.com/ticket/search/general. The announcement kept the
# known layer, but its reminder checkbox gained title="1일간 그만보기 선택". Requiring every
# label to equal the reminder vocabulary rejected it and stopped every KORAIL search.
LIVE_2026_11_17_NOTICE = """
<div class="ReactModalPortal">
  <div class="ReactModal__Overlay ReactModal__Overlay--after-open modal layerPopup m-full"
    style="position:fixed;inset:0;z-index:9999;overflow:auto;background:rgba(0,0,0,.6)">
    <div class="ReactModal__Content ReactModal__Content--after-open" tabindex="-1"
      role="dialog" aria-modal="true"
      style="position:static;inset:40px;background:none;overflow:auto;padding:20px">
      <div class="layerWrap emer_pop" style="position:absolute;left:50%;top:40px;
        transform:translateX(-50%);width:720px;background:white;padding:20px">
        <div class="pop_content"><p><img alt="철도지하화통합개발 종합계획 공청회"
          style="display:block;width:680px;height:700px"></p></div>
        <div class="chkecboxWrap"><input type="checkbox" id="popClose124"
          title="1일간 그만보기 선택"><label for="popClose124" class="txt">1일간 그만보기</label>
        </div>
        <ul class="btnWrap clear"><li><button class="btn_by-blue btn_pop-close"
          onclick="this.closest('.ReactModalPortal').remove()">창닫기</button></li></ul>
      </div>
    </div>
  </div>
</div>"""


@pytest.mark.parametrize("context", ["search", "reservation"])
async def test_live_notice_with_decorated_reminder_title_is_closed_using_real_dom(
    notice_browser, context
):
    page = await notice_fixture_page(notice_browser, LIVE_2026_11_17_NOTICE)
    try:
        script = OBSERVE_NOTICE_SCRIPT if context == "search" else RESERVATION_NOTICE_SCRIPT
        assert (await page.evaluate(script))["state"] == "public_search_notice"
        await page.evaluate("""() => {window.checkboxChanges = 0; window.otherClicks = 0;
          document.addEventListener('change', () => window.checkboxChanges++);
          document.addEventListener('click', event => {
            if (event.target.innerText !== '창닫기') window.otherClicks++;
          }, true);}""")
        assert await dismiss_fixture_notices(page, context=context)
        assert await page.evaluate(script) == "absent"
        assert await page.evaluate("window.checkboxChanges") == 0
        assert await page.evaluate("window.otherClicks") == 0
    finally:
        await page.close()


@pytest.mark.parametrize(
    ("label", "attributes"),
    [
        ("1일간 그만보기", 'title="1일간 그만보기 선택"'),
        ("오늘 하루 보지 않기", ""),
        ("오늘 하루 이 창을 열지 않음", ""),
        ("7일간 보지 않기", 'title="7일간 보지 않기 체크"'),
        ("일주일 동안 다시 보지 않기", ""),
        ("[오늘 그만 보기]", 'aria-label="오늘 그만 보기 선택"'),
    ],
)
async def test_reminder_wording_variants_prove_a_public_notice(notice_browser, label, attributes):
    markup = generic_modal_markup("새 프로모션").replace(
        '<label><input type="checkbox">오늘 그만 보기</label>',
        f'<label><input type="checkbox" {attributes}>{label}</label>',
    )
    page = await notice_fixture_page(notice_browser, markup)
    try:
        assert (await page.evaluate(FALLBACK_OBSERVE_SCRIPT))["state"] == "fallback_notice"
        assert await dismiss_fixture_notices(page)
        assert await page.locator("[role=dialog]").count() == 0
    finally:
        await page.close()


@pytest.mark.parametrize("label", ["이메일 수신 동의", "자세히 보기", "오늘 하루 보기"])
async def test_other_checkbox_wording_is_reported_with_closed_reasons(notice_browser, label):
    markup = generic_modal_markup("새 프로모션").replace("오늘 그만 보기", label)
    page = await notice_fixture_page(notice_browser, markup)
    try:
        observed = await page.evaluate(FALLBACK_OBSERVE_SCRIPT)
        assert observed["state"] == "unrecognized"
        assert observed["reasons"] == ["no_public_evidence", "reminder_label_conflict"]
        reports = []
        with pytest.raises(BrowserSourceUnavailable):
            await dismiss_fixture_notices(page, report_unrecognized=reports.append)
        assert reports[-1].reasons == ("no_public_evidence", "reminder_label_conflict")
        assert label.replace(" ", "") not in reports[-1].shape_summary.replace(" ", "")
        assert await page.locator("[role=dialog]").count() == 1
    finally:
        await page.close()


async def test_rejection_reasons_reach_the_report_without_page_text(notice_browser):
    page = await notice_fixture_page(
        notice_browser,
        notice_markup("회원번호 1234 안내", extra='<input type="text" value="private-value">'),
    )
    try:
        reports = []
        with pytest.raises(BrowserSourceUnavailable) as raised:
            await dismiss_fixture_notices(page, report_unrecognized=reports.append)
        assert raised.value.stage == "search_notice_unrecognized"
        assert reports[-1].reasons == ("input_field",)
        summary = reports[-1].shape_summary
        assert "evidence=layer" in summary
        assert "inputs [field]" in summary
        assert "1234" not in summary
        assert "private" not in summary
    finally:
        await page.close()


async def test_unknown_reason_values_never_reach_reports():
    reports = []
    with pytest.raises(BrowserSourceUnavailable):
        await dismiss_public_search_notices(
            execute_script=AsyncMock(
                return_value=script_result(
                    {
                        "state": "unrecognized",
                        "shape": "div aria-modal=true dialog evidence=none 0 controls []",
                        "reasons": ["input_field", "홍길동 010-0000-0000", "input_field", 7],
                    }
                )
            ),
            find_controls=AsyncMock(),
            monotonic=lambda: 0,
            sleep=AsyncMock(),
            timeout_seconds=5,
            report_unrecognized=reports.append,
        )
    assert reports[-1].reasons == ("input_field",)


NON_ARIA_LAYER = """
<div class="popup-layer" style="position:fixed;inset:0;z-index:50;background:rgba(0,0,0,.5)">
  <div class="popup-panel" style="position:absolute;left:200px;top:120px;width:480px;
    background:white;padding:16px">
    <p>새 형식의 공지</p>
    <label><input type="checkbox">오늘 하루 보지 않기</label>
    CLOSE
  </div>
</div>"""


async def test_covering_layer_without_dialog_role_is_closed_from_its_reminder(notice_browser):
    markup = NON_ARIA_LAYER.replace(
        "CLOSE", "<button onclick=\"this.closest('.popup-layer').remove()\">닫기</button>"
    )
    page = await notice_fixture_page(notice_browser, markup)
    try:
        assert (await page.evaluate(OBSERVE_NOTICE_SCRIPT))["state"] == "unrecognized"
        assert (await page.evaluate(FALLBACK_OBSERVE_SCRIPT))["state"] == "fallback_notice"
        assert await dismiss_fixture_notices(page)
        assert await page.locator(".popup-layer").count() == 0
    finally:
        await page.close()


@pytest.mark.parametrize(
    "markup",
    [
        # A fixed banner beside the search form does not block it and stays untouched.
        '<div style="position:fixed;left:0;right:0;bottom:0;height:56px;z-index:5;'
        'background:#eee"><label><input type="checkbox">오늘 하루 보지 않기</label>'
        "<button>닫기</button></div>",
        # A covering layer without a verified close is left to the caller's own checks.
        NON_ARIA_LAYER.replace("CLOSE", "<button>설문 참여</button>"),
    ],
)
async def test_unannounced_layers_stay_untouched_unless_verified(notice_browser, markup):
    page = await notice_fixture_page(notice_browser, markup)
    try:
        await page.evaluate(
            "() => {window.clicks=0; document.addEventListener('click', () => window.clicks++);}"
        )
        assert await page.evaluate(FALLBACK_OBSERVE_SCRIPT) == "absent"
        assert await dismiss_fixture_notices(page) is False
        assert await page.evaluate("window.clicks") == 0
    finally:
        await page.close()


@pytest.mark.parametrize(
    "close",
    [
        '<button class="btn_close" onclick="this.closest(\'[role=dialog]\').remove()"></button>',
        "<button onclick=\"this.closest('[role=dialog]').remove()\">"
        '<img alt="닫기" width="16" height="16"></button>',
        '<a href="#" onclick="this.closest(\'[role=dialog]\').remove(); return false;">닫기</a>',
        '<a href="javascript:void(0)" onclick="this.closest(\'[role=dialog]\').remove()">'
        "팝업 닫기</a>",
        "<script>function closePop() {document.querySelector('[role=dialog]').remove();}</script>"
        '<a href="javascript:closePop();">닫기</a>',
    ],
)
async def test_icon_and_non_navigating_anchor_closes_are_recognized(notice_browser, close):
    markup = generic_modal_markup("새 프로모션").replace(
        "<button onclick=\"this.closest('[role=dialog]').remove()\">창닫기</button>", close
    )
    page = await notice_fixture_page(notice_browser, markup)
    try:
        assert (await page.evaluate(FALLBACK_OBSERVE_SCRIPT))["state"] == "fallback_notice"
        assert await dismiss_fixture_notices(page)
        assert await page.locator("[role=dialog]").count() == 0
    finally:
        await page.close()
