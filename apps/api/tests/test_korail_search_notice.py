from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rail_waitlist.korail_pydoll_browser import _PydollSession
from rail_waitlist.korail_sidecar.browser_contracts import BrowserSourceUnavailable
from rail_waitlist.korail_sidecar.pydoll.page_contracts import PydollPageSnapshot, PydollTrainRow
from rail_waitlist.korail_sidecar.pydoll.search_notice import (
    CLOSE_SELECTOR,
    MAX_NOTICE_CLOSE_ACTIONS,
    OBSERVE_NOTICE_SCRIPT,
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
    assert "event=search_notice_dismissed kind=public_search_notice count=1" in caplog.text
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
        ("<p>새 행사 안내</p>", "창닫기", '<input type="checkbox" checked>오늘 그만 보기'),
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


async def dismiss_fixture_notices(page):
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
    )


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
        notice_markup("기존 예약 선택", extra="<button>새 예약</button>"),
        notice_markup("입력 필요", extra='<input type="text">'),
        notice_markup("선택 필요", extra="<select><option>선택</option></select>"),
        notice_markup("폼 처리", extra="<form></form>"),
        notice_markup("외부 내용", extra="<iframe></iframe>"),
        notice_markup("공지", extra='<a href="/ticket/reservation/list">예약 확인</a>'),
        notice_markup("공지").replace("emer_pop", "reservation_pop"),
        notice_markup("공지").replace("onclick=", "disabled onclick="),
        notice_markup("공지") + '<div role="dialog" style="position:fixed;inset:0">인증 확인</div>',
    ],
)
async def test_non_public_or_action_dialog_is_never_closed_using_real_dom(notice_browser, markup):
    page = await notice_fixture_page(notice_browser, markup)
    try:
        assert await page.evaluate(OBSERVE_NOTICE_SCRIPT) == "unrecognized"
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
    finally:
        await page.close()
