from __future__ import annotations

import asyncio
import logging
import re
import sys
import threading
from contextlib import contextmanager
from datetime import UTC, date, datetime, time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import rail_waitlist.korail_browser_adapter_service as adapter_service
import rail_waitlist.korail_sidecar.playwright.client as playwright_client_module
import rail_waitlist.korail_sidecar.pydoll.chromium_lifecycle as pydoll_lifecycle
from rail_waitlist.korail_browser_adapter_service import (
    KorailBrowserEngine,
    create_adapter_app,
)
from rail_waitlist.korail_browser_automation import (
    FULLSTACK_E2E_PAGE_URL,
    OFFICIAL_KORAIL_SEARCH_URL,
    BrowserProtectionDetected,
    BrowserRateLimited,
    BrowserSeatSearchRequest,
    BrowserSeatSearchResult,
    BrowserSourceUnavailable,
    BrowserTrainSnapshot,
    KorailBrowserAutomation,
    PlaywrightKorailBrowserClient,
    is_rate_limit_response,
    is_supported_korail_train_kind,
    parse_expected_delay_minutes,
    parse_official_train_type,
    parse_unambiguous_adult_fare,
    protection_trigger_from_http_response,
    protection_trigger_from_text,
    status_from_seat_box,
    visible_departure_matches,
)
from rail_waitlist.korail_search_bootstrap import KorailStationIdentityResolver
from rail_waitlist.korail_sidecar.browser_service_availability import (
    BrowserProviderUnavailable,
)
from rail_waitlist.korail_sidecar.playwright import search_form
from rail_waitlist.provider_call_context import bind_request_id


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("12분 지연 예상", 12),
        ("12분 소요 예상", None),
        ("지연 12분", None),
        ("5분 지연 예상 / 7분 지연 예상", None),
    ],
)
def test_expected_delay_parser_requires_one_exact_marker(
    text: str,
    expected: int | None,
) -> None:
    assert parse_expected_delay_minutes(text) == expected


def request() -> BrowserSeatSearchRequest:
    return BrowserSeatSearchRequest(
        origin="서울",
        destination="부산",
        travel_date=date(2026, 8, 3),
        departure_from=time(14),
        departure_to=time(18),
        passenger_count=1,
    )


def result() -> BrowserSeatSearchResult:
    return BrowserSeatSearchResult(
        origin="서울",
        destination="부산",
        travel_date=date(2026, 8, 3),
        passenger_count=1,
        observed_at=datetime(2026, 8, 1, 4, tzinfo=UTC),
        trains=[
            BrowserTrainSnapshot(
                train_number="43",
                train_type="KTX",
                departure_at=datetime.fromisoformat("2026-08-03T15:45:00+09:00"),
                arrival_at=datetime.fromisoformat("2026-08-03T18:30:00+09:00"),
                standard="standing_plus_seat",
                first="sold_out",
            )
        ],
    )


class FakeClient:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.calls = 0
        self.failure = failure
        self.gate = asyncio.Event()
        self.gate.set()

    async def search(self, data: BrowserSeatSearchRequest) -> BrowserSeatSearchResult:
        self.calls += 1
        await self.gate.wait()
        if self.failure is not None:
            raise self.failure
        return result()


class CancellationIgnoringClient:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def search(self, data: BrowserSeatSearchRequest) -> BrowserSeatSearchResult:
        self.started.set()
        while not self.release.is_set():
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                continue
        return result()


class CloseTrackingCancellationIgnoringClient(CancellationIgnoringClient):
    def __init__(self) -> None:
        super().__init__()
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


class FakeReadinessProbe:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.calls = 0
        self.failure = failure

    async def __call__(self) -> None:
        self.calls += 1
        if self.failure is not None:
            raise self.failure


class RecoveringReadinessProbe:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self) -> None:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient chromium startup failure")


def test_browser_engine_defaults_to_pydoll(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KORAIL_BROWSER_ENGINE", raising=False)

    assert adapter_service._browser_engine_setting() is KorailBrowserEngine.PYDOLL


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("pydoll", KorailBrowserEngine.PYDOLL),
        ("PLAYWRIGHT_DIRECT_CDP", KorailBrowserEngine.PLAYWRIGHT_DIRECT_CDP),
    ],
)
def test_browser_engine_accepts_only_known_values(
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
    expected: KorailBrowserEngine,
) -> None:
    monkeypatch.setenv("KORAIL_BROWSER_ENGINE", configured)

    assert adapter_service._browser_engine_setting() is expected


def test_invalid_browser_engine_fails_sidecar_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KORAIL_BROWSER_ENGINE", "unknown-engine")
    app = create_adapter_app(
        KorailBrowserAutomation(FakeClient()),
        token="t" * 32,
        readiness_probe=FakeReadinessProbe(),
    )

    with (
        pytest.raises(RuntimeError, match="KORAIL_BROWSER_ENGINE must be one of"),
        TestClient(app),
    ):
        pass


def test_pydoll_engine_factory_and_probe_are_selected_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_calls: list[dict[str, object]] = []

    class FakePydollClient:
        def __init__(self, **kwargs: object) -> None:
            init_calls.append(kwargs)

        async def search(self, data: BrowserSeatSearchRequest) -> BrowserSeatSearchResult:
            return result()

    probe_calls: list[bool] = []

    async def probe(*, headless: bool = True) -> None:
        probe_calls.append(headless)

    monkeypatch.setitem(
        sys.modules,
        "rail_waitlist.korail_pydoll_browser",
        SimpleNamespace(
            PydollKorailBrowserClient=FakePydollClient,
            probe_pydoll_chromium=probe,
        ),
    )
    monkeypatch.setattr(pydoll_lifecycle, "probe_pydoll_chromium", probe)

    client = adapter_service._build_browser_client(
        KorailBrowserEngine.PYDOLL,
        page_url=OFFICIAL_KORAIL_SEARCH_URL,
        timeout_seconds=25,
        allow_fullstack_fixture=False,
    )
    selected_probe = adapter_service._readiness_probe_for_engine(KorailBrowserEngine.PYDOLL)

    assert isinstance(client, FakePydollClient)
    assert len(init_calls) == 1
    assert isinstance(init_calls[0].pop("station_identity_resolver"), KorailStationIdentityResolver)
    assert init_calls == [
        {
            "page_url": OFFICIAL_KORAIL_SEARCH_URL,
            "timeout_seconds": 25,
            "headless": True,
            "auto_handle_dialogs": False,
            "allow_fullstack_fixture": False,
            "session_reuse_ttl_seconds": 1800,
            "session_reuse_max_searches": 100,
        }
    ]
    asyncio.run(selected_probe())
    assert probe_calls == [True]


def test_pydoll_gui_mode_uses_headed_client_and_readiness_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_calls: list[dict[str, object]] = []
    probe_calls: list[bool] = []

    class FakePydollClient:
        def __init__(self, **kwargs: object) -> None:
            init_calls.append(kwargs)

    async def probe(*, headless: bool = True) -> None:
        probe_calls.append(headless)

    monkeypatch.setenv("KORAIL_BROWSER_GUI_ENABLED", "true")
    monkeypatch.setitem(
        sys.modules,
        "rail_waitlist.korail_pydoll_browser",
        SimpleNamespace(
            PydollKorailBrowserClient=FakePydollClient,
            probe_pydoll_chromium=probe,
        ),
    )
    monkeypatch.setattr(pydoll_lifecycle, "probe_pydoll_chromium", probe)

    adapter_service._build_browser_client(
        KorailBrowserEngine.PYDOLL,
        page_url=OFFICIAL_KORAIL_SEARCH_URL,
        timeout_seconds=25,
        allow_fullstack_fixture=False,
    )
    selected_probe = adapter_service._readiness_probe_for_engine(KorailBrowserEngine.PYDOLL)
    asyncio.run(selected_probe())

    assert init_calls[0]["headless"] is False
    assert probe_calls == [False]


def test_gui_mode_rejects_the_legacy_playwright_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KORAIL_BROWSER_GUI_ENABLED", "true")

    with pytest.raises(RuntimeError, match="GUI mode requires the pydoll engine"):
        adapter_service._build_browser_client(
            KorailBrowserEngine.PLAYWRIGHT_DIRECT_CDP,
            page_url=OFFICIAL_KORAIL_SEARCH_URL,
            timeout_seconds=25,
            allow_fullstack_fixture=False,
        )


def test_browser_automation_uses_operational_cache_and_cooldown_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KORAIL_BROWSER_CACHE_TTL_SECONDS", raising=False)
    monkeypatch.delenv("SEAT_STATUS_RATE_LIMIT_COOLDOWN_SECONDS", raising=False)
    monkeypatch.delenv("SEAT_STATUS_PROTECTION_COOLDOWN_SECONDS", raising=False)
    monkeypatch.delenv("SEAT_STATUS_PROVIDER_UNAVAILABLE_COOLDOWN_SECONDS", raising=False)
    monkeypatch.delenv("KORAIL_BROWSER_SEARCH_TIMEOUT_SECONDS", raising=False)

    automation = adapter_service.build_automation(browser_client=FakeClient())

    assert automation._cache_ttl_seconds == 1
    assert automation._rate_limit_cooldown_seconds == 300
    assert automation._protection_cooldown_seconds == 60
    assert automation._provider_unavailable_cooldown_seconds == 300
    assert automation._search_timeout_seconds == 80


def test_pydoll_engine_readiness_uses_selected_probe_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe_calls: list[bool] = []

    class FakePydollClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def search(self, data: BrowserSeatSearchRequest) -> BrowserSeatSearchResult:
            return result()

    async def probe(*, headless: bool = True) -> None:
        probe_calls.append(headless)

    monkeypatch.setenv("KORAIL_BROWSER_ENGINE", "pydoll")
    monkeypatch.setitem(
        sys.modules,
        "rail_waitlist.korail_pydoll_browser",
        SimpleNamespace(
            PydollKorailBrowserClient=FakePydollClient,
            probe_pydoll_chromium=probe,
        ),
    )
    monkeypatch.setattr(pydoll_lifecycle, "probe_pydoll_chromium", probe)
    app = create_adapter_app(token="t" * 32)

    with TestClient(app) as http:
        response = http.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert probe_calls == [True]


def test_direct_cdp_engine_keeps_existing_client_and_probe() -> None:
    client = adapter_service._build_browser_client(
        KorailBrowserEngine.PLAYWRIGHT_DIRECT_CDP,
        page_url=OFFICIAL_KORAIL_SEARCH_URL,
        timeout_seconds=25,
        allow_fullstack_fixture=False,
    )

    assert isinstance(client, PlaywrightKorailBrowserClient)
    assert (
        adapter_service._readiness_probe_for_engine(KorailBrowserEngine.PLAYWRIGHT_DIRECT_CDP)
        is adapter_service.probe_chromium
    )


def test_seat_text_contract_is_fail_closed() -> None:
    assert status_from_seat_box("일반실 59,800원", set()) == "available"
    assert status_from_seat_box("매진", {"sold_out"}) == "sold_out"
    assert status_from_seat_box("특실 매진임박", {"sold_out_soon"}) == "limited"
    assert status_from_seat_box("입석 + 예매", set()) == "standing_plus_seat"
    assert status_from_seat_box("예약대기", set()) == "waitlist_available"
    assert status_from_seat_box("새로운 알 수 없는 문구", set()) is None


def test_primary_timetable_fields_are_exact_and_fare_is_fail_closed() -> None:
    assert parse_official_train_type("KTX 043") == "KTX"
    assert parse_official_train_type("KTX-산천 193") == "KTX-산천"
    assert parse_official_train_type("KTX 청룡 181") == "KTX-청룡"
    assert parse_official_train_type("ITX-KTX 혼합 1") is None
    assert parse_unambiguous_adult_fare("일반실 59,800원") == 59_800
    assert parse_unambiguous_adult_fare("매진") is None
    assert parse_unambiguous_adult_fare("성인 59,800원 어린이 29,900원") is None


def test_browser_train_snapshot_requires_aware_exact_schedule_and_forbids_extra() -> None:
    valid = {
        "train_number": "43",
        "train_type": "KTX",
        "departure_at": "2026-08-03T23:45:00+09:00",
        "arrival_at": "2026-08-04T02:30:00+09:00",
        "adult_fare": 59_800,
        "standard": "available",
        "first": "sold_out",
    }

    snapshot = BrowserTrainSnapshot.model_validate(valid)

    assert snapshot.arrival_at.utcoffset() is not None
    with pytest.raises(ValidationError, match="extra_forbidden"):
        BrowserTrainSnapshot.model_validate({**valid, "raw_provider_row": "must-not-pass"})
    with pytest.raises(ValidationError, match="literal_error"):
        BrowserTrainSnapshot.model_validate({**valid, "train_type": "ITX-새마을"})
    with pytest.raises(ValidationError, match="schedule datetimes"):
        BrowserTrainSnapshot.model_validate({**valid, "arrival_at": "2026-08-04T02:30:00"})
    with pytest.raises(ValidationError, match="later than departure"):
        BrowserTrainSnapshot.model_validate({**valid, "arrival_at": "2026-08-03T22:30:00+09:00"})


def test_official_visible_departure_format_matches_exact_date_and_hour() -> None:
    travel_date = date(2026, 8, 3)

    assert visible_departure_matches("2026-08-03(월) 14:00", travel_date, 14)
    assert visible_departure_matches("2026-08-03", travel_date, 14)
    assert not visible_departure_matches("2026-08-03(월) 15:00", travel_date, 14)
    assert not visible_departure_matches("2026-08-04(화) 14:00", travel_date, 14)


def test_korail_row_filter_accepts_only_ktx_family_names() -> None:
    assert is_supported_korail_train_kind("KTX 043")
    assert is_supported_korail_train_kind("KTX-산천 193")
    assert is_supported_korail_train_kind("KTX-청룡 181")
    assert not is_supported_korail_train_kind("무궁화호 1161")
    assert not is_supported_korail_train_kind("ITX-마음 1021")


def test_fullstack_browser_page_requires_exact_explicit_test_gate() -> None:
    with pytest.raises(ValueError, match="official KORAIL HTTPS host"):
        PlaywrightKorailBrowserClient(page_url=FULLSTACK_E2E_PAGE_URL)

    PlaywrightKorailBrowserClient(
        page_url=FULLSTACK_E2E_PAGE_URL,
        allow_fullstack_fixture=True,
    )

    with pytest.raises(ValueError, match="official KORAIL HTTPS host"):
        PlaywrightKorailBrowserClient(
            page_url="http://e2e-korail-page:8080/other.html",
            allow_fullstack_fixture=True,
        )


def test_browser_client_uses_only_the_official_search_form_entrypoint() -> None:
    client = PlaywrightKorailBrowserClient()

    assert client.page_url == OFFICIAL_KORAIL_SEARCH_URL
    with pytest.raises(ValueError, match="official KORAIL HTTPS host"):
        PlaywrightKorailBrowserClient(page_url="https://www.korail.com/ticket/main")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("CODE -8002", "marker_code_8002"),
        ("CODE -8003", "marker_code_8003"),
        ("CODE : -1405", "marker_code_1405"),
        ("macro_err1", "marker_macro_err1"),
        ("CAPTCHA 확인", "marker_captcha"),
        ("NetFUNNEL 대기", "marker_netfunnel"),
        ("비정상 접근입니다", "marker_abnormal_access"),
        ("미허가 도구 사용", "marker_unauthorized_tool"),
    ],
)
def test_protection_marker_is_reduced_to_sanitized_trigger(value: str, expected: str) -> None:
    assert protection_trigger_from_text(value) == expected


def test_http_403_trigger_keeps_only_resource_classification() -> None:
    assert protection_trigger_from_http_response(403, "document") == "http_403_main"
    assert protection_trigger_from_http_response(403, "xhr") == "http_403_subresource"
    assert protection_trigger_from_http_response(429, "document") is None


@pytest.mark.parametrize("resource_type", ["document", "fetch", "xhr"])
def test_only_business_resource_429_is_rate_limited(resource_type: str) -> None:
    assert is_rate_limit_response(429, resource_type)


@pytest.mark.parametrize("resource_type", ["font", "image", "script", "stylesheet"])
def test_static_subresource_429_is_not_rate_limited(resource_type: str) -> None:
    assert not is_rate_limit_response(429, resource_type)


class RecordingCdpSession:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, object]]] = []
        self.detach_count = 0

    async def send(self, method: str, params: dict[str, object]) -> None:
        self.commands.append((method, params))

    async def detach(self) -> None:
        self.detach_count += 1


class BlockingReleaseCdpSession(RecordingCdpSession):
    def __init__(self) -> None:
        super().__init__()
        self.release_started = asyncio.Event()
        self.allow_release = asyncio.Event()

    async def send(self, method: str, params: dict[str, object]) -> None:
        self.commands.append((method, params))
        if params["type"] == "mouseReleased":
            self.release_started.set()
            await self.allow_release.wait()


class RecordingContext:
    def __init__(self, session: RecordingCdpSession) -> None:
        self.session = session

    async def new_cdp_session(self, page) -> RecordingCdpSession:
        return self.session


class VisibleControl:
    async def is_visible(self) -> bool:
        return True

    async def is_enabled(self) -> bool:
        return True

    async def scroll_into_view_if_needed(self) -> None:
        return None

    async def bounding_box(self) -> dict[str, float]:
        return {"x": 10, "y": 20, "width": 30, "height": 40}


@pytest.mark.asyncio
async def test_canonical_submit_search_orchestrates_the_client_form_seams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = PlaywrightKorailBrowserClient()
    search_request = request()
    events: list[tuple[object, ...]] = []
    button = SimpleNamespace(
        is_visible=AsyncMock(return_value=True),
        is_enabled=AsyncMock(return_value=True),
    )
    buttons = SimpleNamespace(count=AsyncMock(return_value=1), first=button)
    role_queries: list[tuple[str, str]] = []

    def get_by_role(role: str, *, name) -> SimpleNamespace:
        role_queries.append((role, name.pattern))
        return buttons

    page = SimpleNamespace(get_by_role=get_by_role)

    async def choose_station(actual_page, label: str, value: str) -> None:
        events.append(("station", actual_page, label, value))

    async def choose_departure(actual_page, travel_date: date, hour: int) -> None:
        events.append(("departure", actual_page, travel_date, hour))

    async def assert_identity(actual_page, actual_request) -> None:
        events.append(("identity", actual_page, actual_request))

    async def click_control(actual_page, control, stage: str) -> None:
        events.append(("click", actual_page, control, stage))

    monkeypatch.setattr(client, "_choose_station", choose_station)
    monkeypatch.setattr(client, "_choose_departure", choose_departure)
    monkeypatch.setattr(client, "_assert_pre_submit_identity", assert_identity)
    monkeypatch.setattr(client, "_click_visible_control", click_control)

    await search_form.submit_search(client, page, search_request)

    assert events == [
        ("station", page, "출발역", "서울"),
        ("station", page, "도착역", "부산"),
        ("departure", page, date(2026, 8, 3), 14),
        ("identity", page, search_request),
        ("click", page, button, "submit_button_click"),
    ]
    assert role_queries == [("button", r"(?:열차\s*)?조회|조회하기")]
    buttons.count.assert_awaited_once_with()
    button.is_visible.assert_awaited_once_with()
    button.is_enabled.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_playwright_page_guard_classifies_official_maintenance_page() -> None:
    body = SimpleNamespace(inner_text=AsyncMock(return_value="점검 안내"))
    rows = SimpleNamespace(count=AsyncMock(return_value=0))
    page = SimpleNamespace(
        url="https://www.korail.com/rejectservice_job.html",
        locator=lambda selector: body if selector == "body" else rows,
    )
    client = PlaywrightKorailBrowserClient()

    with pytest.raises(BrowserProviderUnavailable) as raised:
        await client._assert_not_protected(page, [], "load_page")

    assert raised.value.trigger == "maintenance_page"
    assert raised.value.stage == "load_page"


@pytest.mark.asyncio
async def test_playwright_navigation_timeout_still_classifies_maintenance_dom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    playwright_api = pytest.importorskip("playwright.async_api")

    class AsyncContext:
        def __init__(self, value: object) -> None:
            self.value = value

        async def __aenter__(self) -> object:
            return self.value

        async def __aexit__(self, *_args: object) -> None:
            return None

    body = SimpleNamespace(inner_text=AsyncMock(return_value="점검 안내"))
    rows = SimpleNamespace(count=AsyncMock(return_value=0))
    page = SimpleNamespace(
        url="https://www.korail.com/rejectservice_job.html",
        set_viewport_size=AsyncMock(),
        on=lambda *_args: None,
        goto=AsyncMock(side_effect=playwright_api.TimeoutError("navigation timeout")),
        locator=lambda selector: body if selector == "body" else rows,
    )
    context = SimpleNamespace(pages=[page])
    browser = SimpleNamespace(contexts=[context])
    playwright = SimpleNamespace(chromium=object())
    monkeypatch.setattr(playwright_api, "async_playwright", lambda: AsyncContext(playwright))
    monkeypatch.setattr(
        playwright_client_module,
        "open_direct_cdp_browser",
        lambda *_args, **_kwargs: AsyncContext(browser),
    )
    client = PlaywrightKorailBrowserClient()

    with pytest.raises(BrowserProviderUnavailable) as raised:
        await client.search(request())

    assert raised.value.trigger == "maintenance_page"
    assert raised.value.stage == "load_page"
    page.set_viewport_size.assert_awaited_once_with({"width": 1440, "height": 1000})


@pytest.mark.asyncio
async def test_visible_control_click_uses_cdp_press_hold_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr(
        "rail_waitlist.korail_sidecar.playwright.search_form.asyncio.sleep",
        sleep,
    )
    session = RecordingCdpSession()
    page = SimpleNamespace(context=RecordingContext(session))
    client = PlaywrightKorailBrowserClient()

    await client._click_visible_control(page, VisibleControl(), "click")

    assert [params["type"] for _, params in session.commands] == [
        "mouseMoved",
        "mousePressed",
        "mouseReleased",
    ]
    assert all(method == "Input.dispatchMouseEvent" for method, _ in session.commands)
    assert session.commands[1][1]["buttons"] == 1
    assert session.commands[2][1]["buttons"] == 0
    assert session.detach_count == 1
    sleep.assert_awaited_once_with(0.1)


@pytest.mark.asyncio
async def test_visible_control_click_releases_mouse_when_hold_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep = AsyncMock(side_effect=RuntimeError("hold failed"))
    monkeypatch.setattr(
        "rail_waitlist.korail_sidecar.playwright.search_form.asyncio.sleep",
        sleep,
    )
    session = RecordingCdpSession()
    page = SimpleNamespace(context=RecordingContext(session))
    client = PlaywrightKorailBrowserClient()

    with pytest.raises(BrowserSourceUnavailable) as raised:
        await client._click_visible_control(
            page,
            VisibleControl(),
            "test_click",
        )

    assert raised.value.stage == "test_click"
    assert session.commands[-1][1]["type"] == "mouseReleased"
    assert session.detach_count == 1


@pytest.mark.asyncio
async def test_visible_control_click_releases_mouse_after_repeated_cancellation() -> None:
    session = BlockingReleaseCdpSession()
    page = SimpleNamespace(context=RecordingContext(session))
    client = PlaywrightKorailBrowserClient()

    task = asyncio.create_task(client._click_visible_control(page, VisibleControl(), "test_click"))
    await session.release_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)

    assert not task.done()
    session.allow_release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert session.commands[-1][1]["type"] == "mouseReleased"
    assert session.detach_count == 1


async def test_singleflight_and_cache_run_one_browser_search() -> None:
    client = FakeClient()
    client.gate.clear()
    automation = KorailBrowserAutomation(client)
    first = asyncio.create_task(automation.search(request()))
    second = asyncio.create_task(automation.search(request()))
    await asyncio.sleep(0)
    client.gate.set()

    left, right = await asyncio.gather(first, second)
    cached = await automation.search(request())

    assert left == right == cached
    assert client.calls == 1


async def test_singleflight_links_two_request_ids_to_one_provider_call_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = FakeClient()
    client.gate.clear()
    automation = KorailBrowserAutomation(client)
    first_request_id = "11111111111141118111111111111111"
    second_request_id = "22222222222242228222222222222222"
    caplog.set_level(logging.INFO, logger="rail_waitlist.korail_browser_automation")

    with bind_request_id(first_request_id):
        first = asyncio.create_task(automation.search(request()))
    while client.calls == 0:
        await asyncio.sleep(0)
    with bind_request_id(second_request_id):
        second = asyncio.create_task(automation.search(request()))
    await asyncio.sleep(0)

    created = next(
        record.message
        for record in caplog.records
        if "event=provider_call_created" in record.message
    )
    joined = next(
        record.message
        for record in caplog.records
        if "event=provider_query_singleflight_join" in record.message
    )
    created_call_id = re.search(r"provider_call_id=([0-9a-f]{32})", created)
    joined_call_id = re.search(r"provider_call_id=([0-9a-f]{32})", joined)
    assert created_call_id is not None
    assert joined_call_id is not None
    assert created_call_id.group(1) == joined_call_id.group(1)
    assert f"request_id={first_request_id}" in created
    assert f"request_id={second_request_id}" in joined

    client.gate.set()
    await asyncio.gather(first, second)
    assert client.calls == 1


async def test_shorter_singleflight_waiter_times_out_without_cancelling_shared_search() -> None:
    client = FakeClient()
    client.gate.clear()
    automation = KorailBrowserAutomation(client, search_timeout_seconds=1)
    first = asyncio.create_task(automation.search(request(), timeout_seconds=1))
    while client.calls == 0:
        await asyncio.sleep(0)

    with pytest.raises(BrowserSourceUnavailable) as shorter:
        await automation.search(request(), timeout_seconds=0.01)

    assert shorter.value.stage == "caller_deadline"
    assert not first.done()
    assert client.calls == 1

    client.gate.set()
    assert await first == result()


async def test_cancelled_different_key_waiter_is_discarded_before_browser_io(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = FakeClient()
    client.gate.clear()
    automation = KorailBrowserAutomation(client)
    next_request = request().model_copy(update={"travel_date": date(2026, 8, 4)})
    caplog.set_level(logging.INFO, logger="rail_waitlist.korail_browser_automation")
    first = asyncio.create_task(automation.search(request()))
    while client.calls == 0:
        await asyncio.sleep(0)
    queued = asyncio.create_task(automation.search(next_request))
    await asyncio.sleep(0)

    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    await asyncio.sleep(0)

    assert client.calls == 1
    assert any(
        "event=provider_query_skipped reason=no_active_waiters" in record.message
        and "provider_call_id=" in record.message
        for record in caplog.records
    )

    client.gate.set()
    assert await first == result()
    assert await automation.drain_pending_calls()


async def test_different_key_expires_behind_browser_gate_without_provider_io(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = FakeClient()
    client.gate.clear()
    automation = KorailBrowserAutomation(client, search_timeout_seconds=1)
    next_request = request().model_copy(update={"travel_date": date(2026, 8, 4)})
    caplog.set_level(logging.INFO, logger="rail_waitlist.korail_browser_automation")

    first = asyncio.create_task(automation.search(request(), timeout_seconds=1))
    while client.calls == 0:
        await asyncio.sleep(0)

    with pytest.raises(BrowserSourceUnavailable) as expired:
        await automation.search(next_request, timeout_seconds=0.01)

    client.gate.set()
    assert await first == result()
    assert await automation.drain_pending_calls()

    assert expired.value.stage in {"caller_deadline", "search_deadline"}
    assert client.calls == 1
    assert next_request.cache_key() not in automation._failure_backoffs
    assert any(
        "event=provider_query_skipped" in record.message and "provider_call_id=" in record.message
        for record in caplog.records
    )


async def test_running_search_deadline_cancels_client_without_opening_backoff(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class DeadlineClient:
        def __init__(self) -> None:
            self.calls = 0
            self.started = asyncio.Event()
            self.cancelled = asyncio.Event()
            self.release = asyncio.Event()

        async def search(self, data: BrowserSeatSearchRequest) -> BrowserSeatSearchResult:
            self.calls += 1
            self.started.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
            return result()

    client = DeadlineClient()
    automation = KorailBrowserAutomation(client, search_timeout_seconds=0.02)
    caplog.set_level(logging.INFO, logger="rail_waitlist.korail_browser_automation")

    with pytest.raises(BrowserSourceUnavailable) as expired:
        await automation.search(request())
    assert expired.value.stage in {"caller_deadline", "search_deadline"}
    await automation.drain_pending_calls()

    assert client.cancelled.is_set()
    assert automation._failure_backoffs == {}
    assert any(
        "event=provider_query_deadline" in record.message and "provider_call_id=" in record.message
        for record in caplog.records
    )

    client.release.set()
    assert await automation.search(request()) == result()
    assert client.calls == 2


async def test_search_deadline_waits_for_browser_cancellation_cleanup() -> None:
    class SlowCleanupClient:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.cleanup_started = asyncio.Event()
            self.cleanup_finished = asyncio.Event()

        async def search(self, data: BrowserSeatSearchRequest) -> BrowserSeatSearchResult:
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cleanup_started.set()
                await asyncio.sleep(0.01)
                self.cleanup_finished.set()
                raise

    client = SlowCleanupClient()
    automation = KorailBrowserAutomation(client, search_timeout_seconds=0.3)

    with pytest.raises(BrowserSourceUnavailable) as expired:
        await automation.search(request())

    assert expired.value.stage == "search_deadline"
    assert client.cleanup_started.is_set()
    assert client.cleanup_finished.is_set()
    assert automation._inflight == {}


async def test_last_cancelled_waiter_leaves_started_search_owned_until_deadline() -> None:
    class SlowCleanupClient:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.cleanup_finished = asyncio.Event()

        async def search(self, data: BrowserSeatSearchRequest) -> BrowserSeatSearchResult:
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.sleep(0.01)
                self.cleanup_finished.set()
                raise

    client = SlowCleanupClient()
    automation = KorailBrowserAutomation(client, search_timeout_seconds=0.1)
    caller = asyncio.create_task(automation.search(request()))
    await client.started.wait()

    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller

    assert not client.cleanup_finished.is_set()
    assert await automation.drain_pending_calls()
    assert client.cleanup_finished.is_set()
    assert automation._inflight == {}


async def test_cancellation_resistant_search_keeps_caller_bounded_and_rejects_late_success() -> (
    None
):
    client = CancellationIgnoringClient()
    automation = KorailBrowserAutomation(client, search_timeout_seconds=0.03)
    caller = asyncio.create_task(automation.search(request()))
    await client.started.wait()

    with pytest.raises(BrowserSourceUnavailable) as expired:
        await asyncio.wait_for(caller, timeout=0.15)

    assert expired.value.stage == "caller_deadline"
    assert request().cache_key() in automation._inflight

    client.release.set()
    assert await automation.drain_pending_calls()
    assert request().cache_key() not in automation._cache

    assert await automation.search(request()) == result()


async def test_provider_query_logs_actual_start_and_success_without_request_identity(
    caplog: pytest.LogCaptureFixture,
) -> None:
    automation = KorailBrowserAutomation(FakeClient())

    with caplog.at_level(logging.INFO, logger="rail_waitlist.korail_browser_automation"):
        await automation.search(request())

    assert "KORAIL 운영사 조회를 시작합니다" in caplog.text
    assert "event=provider_query_started" in caplog.text
    assert "event=provider_query_completed outcome=success train_count=1" in caplog.text
    assert "서울" not in caplog.text
    assert "부산" not in caplog.text


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (
            BrowserProtectionDetected("marker_netfunnel", "wait_result"),
            (
                "event=provider_query_completed outcome=provider_access_restricted "
                "stage=wait_result "
                "trigger=marker_netfunnel cooldown_seconds=60"
            ),
        ),
        (
            BrowserRateLimited(),
            "event=provider_query_completed outcome=rate_limited cooldown_seconds=300",
        ),
        (
            BrowserSourceUnavailable("result_read"),
            (
                "event=provider_query_completed outcome=source_unavailable "
                "stage=result_read backoff_seconds=30"
            ),
        ),
    ],
)
async def test_provider_query_logs_closed_failure_state_without_exception_text(
    caplog: pytest.LogCaptureFixture,
    failure: Exception,
    expected: str,
) -> None:
    automation = KorailBrowserAutomation(FakeClient(failure=failure))

    with (
        caplog.at_level(logging.INFO, logger="rail_waitlist.korail_browser_automation"),
        pytest.raises(type(failure)),
    ):
        await automation.search(request())

    assert expected in caplog.text
    assert "서울" not in caplog.text
    assert "부산" not in caplog.text


async def test_provider_query_logs_unexpected_failure_without_exception_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    automation = KorailBrowserAutomation(FakeClient(failure=RuntimeError("raw-provider-body")))

    with (
        caplog.at_level(logging.INFO, logger="rail_waitlist.korail_browser_automation"),
        pytest.raises(BrowserSourceUnavailable),
    ):
        await automation.search(request())

    assert (
        "event=provider_query_completed outcome=source_unavailable "
        "stage=unexpected_backend_error backoff_seconds=30" in caplog.text
    )
    assert "raw-provider-body" not in caplog.text


async def test_provider_query_logs_cancellation_and_reraises_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = FakeClient()
    client.gate.clear()
    automation = KorailBrowserAutomation(client)
    caplog.set_level(logging.INFO, logger="rail_waitlist.korail_browser_automation")

    task = asyncio.create_task(automation.search(request()))
    while client.calls == 0:
        await asyncio.sleep(0)
    owned_task = next(iter(automation._inflight.values())).task
    owned_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert "event=provider_query_completed outcome=cancelled" in caplog.text


@pytest.mark.asyncio
async def test_drain_pending_calls_survives_repeated_cancellation() -> None:
    client = FakeClient()
    client.gate.clear()
    automation = KorailBrowserAutomation(client)
    search_task = asyncio.create_task(automation.search(request()))
    await asyncio.sleep(0)
    drain_task = asyncio.create_task(automation.drain_pending_calls())
    await asyncio.sleep(0)

    drain_task.cancel()
    await asyncio.sleep(0)
    drain_task.cancel()
    await asyncio.sleep(0)

    assert not drain_task.done()
    client.gate.set()
    with pytest.raises(asyncio.CancelledError):
        await drain_task
    assert await search_task == result()


@pytest.mark.asyncio
async def test_drain_pending_calls_has_bounded_deadline_for_stuck_client(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = CancellationIgnoringClient()
    automation = KorailBrowserAutomation(
        client,
        shutdown_drain_timeout_seconds=0.01,
        shutdown_cancel_timeout_seconds=0.01,
    )
    search_task = asyncio.create_task(automation.search(request()))
    await client.started.wait()

    with caplog.at_level(logging.ERROR):
        await asyncio.wait_for(automation.drain_pending_calls(), timeout=0.2)

    assert "shutdown drain incomplete pending=1" in caplog.text
    assert not search_task.done()
    client.release.set()
    assert await search_task == result()


@pytest.mark.asyncio
async def test_close_skips_client_lock_when_owned_search_does_not_drain(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = CloseTrackingCancellationIgnoringClient()
    automation = KorailBrowserAutomation(
        client,
        shutdown_drain_timeout_seconds=0.01,
        shutdown_cancel_timeout_seconds=0.01,
    )
    search_task = asyncio.create_task(automation.search(request()))
    await client.started.wait()

    with caplog.at_level(logging.ERROR):
        await asyncio.wait_for(automation.close(), timeout=0.2)

    assert client.close_calls == 0
    assert "client close skipped" in caplog.text
    client.release.set()
    assert await search_task == result()


async def test_protection_result_opens_cooldown_without_second_browser(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = FakeClient(failure=BrowserProtectionDetected())
    automation = KorailBrowserAutomation(client)
    caplog.set_level(logging.INFO, logger="rail_waitlist.korail_browser_automation")

    with pytest.raises(BrowserProtectionDetected):
        await automation.search(request())
    with pytest.raises(BrowserProtectionDetected):
        await automation.search(request())

    assert client.calls == 1
    assert caplog.text.count("event=provider_query_started") == 1
    assert caplog.text.count("event=provider_query_completed") == 1
    assert (
        "event=provider_query_skipped reason=provider_cooldown "
        "outcome=provider_access_restricted" in caplog.text
    )


@pytest.mark.asyncio
async def test_source_failure_backoff_is_scoped_to_exact_query() -> None:
    class DateSpecificFailureClient:
        def __init__(self) -> None:
            self.calls: list[date] = []

        async def search(
            self,
            data: BrowserSeatSearchRequest,
        ) -> BrowserSeatSearchResult:
            self.calls.append(data.travel_date)
            if data.travel_date == date(2026, 8, 3):
                raise BrowserSourceUnavailable("departure_date_disabled")
            return result().model_copy(update={"travel_date": data.travel_date, "trains": []})

    client = DateSpecificFailureClient()
    automation = KorailBrowserAutomation(client)
    failed = request()
    next_date = failed.model_copy(update={"travel_date": date(2026, 8, 4)})

    with pytest.raises(BrowserSourceUnavailable):
        await automation.search(failed)
    with pytest.raises(BrowserSourceUnavailable) as backed_off:
        await automation.search(failed)

    recovered = await automation.search(next_date)

    assert backed_off.value.stage == "query_backoff"
    assert recovered.travel_date == date(2026, 8, 4)
    assert client.calls == [date(2026, 8, 3), date(2026, 8, 4)]


@pytest.mark.asyncio
async def test_protection_cooldown_blocks_a_different_query_globally() -> None:
    client = FakeClient(failure=BrowserProtectionDetected())
    automation = KorailBrowserAutomation(client)
    next_date = request().model_copy(update={"travel_date": date(2026, 8, 4)})

    with pytest.raises(BrowserProtectionDetected):
        await automation.search(request())
    with pytest.raises(BrowserProtectionDetected):
        await automation.search(next_date)

    assert client.calls == 1


@pytest.mark.asyncio
async def test_provider_outage_blocks_a_different_query_globally_with_retry_after() -> None:
    client = FakeClient(failure=BrowserProviderUnavailable("maintenance_page", "wait_result"))
    automation = KorailBrowserAutomation(client, provider_unavailable_cooldown_seconds=300)
    next_date = request().model_copy(update={"travel_date": date(2026, 8, 4)})

    with pytest.raises(BrowserProviderUnavailable) as first:
        await automation.search(request())
    with pytest.raises(BrowserProviderUnavailable) as second:
        await automation.search(next_date)

    assert first.value.retry_after_seconds == 300
    assert second.value.retry_after_seconds == 300
    assert second.value.stage == "provider_cooldown"
    assert client.calls == 1


@pytest.mark.asyncio
async def test_provider_outage_preempts_a_cached_different_query() -> None:
    client = FakeClient()
    automation = KorailBrowserAutomation(client, cache_ttl_seconds=60)
    cached_request = request()
    await automation.search(cached_request)
    client.failure = BrowserProviderUnavailable("maintenance_page", "wait_result")
    outage_request = request().model_copy(update={"travel_date": date(2026, 8, 4)})

    with pytest.raises(BrowserProviderUnavailable):
        await automation.search(outage_request)
    with pytest.raises(BrowserProviderUnavailable) as cached:
        await automation.search(cached_request)

    assert cached.value.stage == "provider_cooldown"
    assert client.calls == 2


@pytest.mark.asyncio
async def test_provider_outage_stops_a_different_query_already_waiting_for_browser() -> None:
    class BlockingOutageClient:
        def __init__(self) -> None:
            self.calls = 0
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def search(
            self,
            _request: BrowserSeatSearchRequest,
        ) -> BrowserSeatSearchResult:
            self.calls += 1
            self.started.set()
            await self.release.wait()
            raise BrowserProviderUnavailable("maintenance_page", "wait_result")

    client = BlockingOutageClient()
    automation = KorailBrowserAutomation(client, provider_unavailable_cooldown_seconds=300)
    next_date = request().model_copy(update={"travel_date": date(2026, 8, 4)})
    first = asyncio.create_task(automation.search(request()))
    await client.started.wait()
    second = asyncio.create_task(automation.search(next_date))
    await asyncio.sleep(0)
    client.release.set()

    results = await asyncio.gather(first, second, return_exceptions=True)

    assert all(isinstance(item, BrowserProviderUnavailable) for item in results)
    assert client.calls == 1


def test_sidecar_requires_internal_bearer_token() -> None:
    client = FakeClient()
    app = create_adapter_app(
        KorailBrowserAutomation(client),
        token="t" * 32,
        readiness_probe=FakeReadinessProbe(),
    )
    with TestClient(app) as http:
        unauthorized = http.post("/v1/seat-snapshot", json=request().model_dump(mode="json"))
        accepted = http.post(
            "/v1/seat-snapshot",
            json=request().model_dump(mode="json"),
            headers={"Authorization": f"Bearer {'t' * 32}"},
        )

    assert unauthorized.status_code == 401
    assert accepted.status_code == 200
    assert accepted.headers["Cache-Control"] == "no-store"


def test_sidecar_logs_one_sanitized_protection_terminal_without_exposing_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    failure = BrowserProtectionDetected("marker_code_8003", "wait_result")
    app = create_adapter_app(
        KorailBrowserAutomation(FakeClient(failure=failure)),
        token="t" * 32,
        readiness_probe=FakeReadinessProbe(),
    )

    with caplog.at_level(logging.WARNING), TestClient(app) as http:
        response = http.post(
            "/v1/seat-snapshot",
            json=request().model_dump(mode="json"),
            headers={"Authorization": f"Bearer {'t' * 32}"},
        )

    assert response.status_code == 423
    assert response.json() == {"detail": {"reason": "provider_access_restricted"}}
    assert response.headers["Cache-Control"] == "no-store"
    assert "stage=wait_result trigger=marker_code_8003" in caplog.text
    assert caplog.text.count("stage=wait_result trigger=marker_code_8003") == 1


def test_sidecar_projects_provider_outage_as_compatible_503_with_retry_after() -> None:
    failure = BrowserProviderUnavailable("maintenance_page", "wait_result")
    app = create_adapter_app(
        KorailBrowserAutomation(
            FakeClient(failure=failure),
            provider_unavailable_cooldown_seconds=300,
        ),
        token="t" * 32,
        readiness_probe=FakeReadinessProbe(),
    )

    with TestClient(app) as http:
        response = http.post(
            "/v1/seat-snapshot",
            json=request().model_dump(mode="json"),
            headers={"Authorization": f"Bearer {'t' * 32}"},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": {"reason": "source_unavailable"}}
    assert response.headers["Retry-After"] == "300"
    assert response.headers["Cache-Control"] == "no-store"


def test_sidecar_is_ready_only_after_one_successful_chromium_probe() -> None:
    probe = FakeReadinessProbe()
    app = create_adapter_app(
        KorailBrowserAutomation(FakeClient()),
        token="t" * 32,
        readiness_probe=probe,
    )

    with TestClient(app) as http:
        first = http.get("/readyz")
        second = http.get("/readyz")

    assert first.status_code == 200
    assert first.json() == {"status": "ready"}
    assert first.headers["Cache-Control"] == "no-store"
    assert second.status_code == 200
    assert probe.calls == 1


def test_sidecar_shutdown_drains_owned_browser_searches() -> None:
    automation = KorailBrowserAutomation(FakeClient())
    drain_pending_calls = AsyncMock()
    automation.drain_pending_calls = drain_pending_calls
    app = create_adapter_app(
        automation,
        token="t" * 32,
        readiness_probe=FakeReadinessProbe(),
    )

    with TestClient(app) as http:
        assert http.get("/readyz").status_code == 200

    drain_pending_calls.assert_awaited_once_with()


def test_sidecar_stays_not_ready_when_chromium_probe_fails() -> None:
    probe = FakeReadinessProbe(failure=RuntimeError("chromium unavailable"))
    client = FakeClient()
    app = create_adapter_app(
        KorailBrowserAutomation(client),
        token="t" * 32,
        readiness_probe=probe,
    )

    with TestClient(app) as http:
        healthy = http.get("/healthz")
        not_ready = http.get("/readyz")
        unavailable = http.post(
            "/v1/seat-snapshot",
            json=request().model_dump(mode="json"),
            headers={"Authorization": f"Bearer {'t' * 32}"},
        )

    assert healthy.status_code == 200
    assert not_ready.status_code == 503
    assert not_ready.headers["Cache-Control"] == "no-store"
    assert unavailable.status_code == 503
    assert unavailable.headers["Cache-Control"] == "no-store"
    assert probe.calls == 1
    assert client.calls == 0


def test_sidecar_readyz_recovers_after_transient_startup_probe_failure() -> None:
    probe = RecoveringReadinessProbe()
    app = create_adapter_app(
        KorailBrowserAutomation(FakeClient()),
        token="t" * 32,
        readiness_probe=probe,
        readiness_retry_interval_seconds=0,
    )

    with TestClient(app) as http:
        recovered = http.get("/readyz")
        cached = http.get("/readyz")

    assert recovered.status_code == 200
    assert recovered.json() == {"status": "ready"}
    assert cached.status_code == 200
    assert probe.calls == 2


def test_sidecar_readyz_probe_timeout_is_bounded_and_remains_fail_closed() -> None:
    async def blocked_probe() -> None:
        await asyncio.Event().wait()

    app = create_adapter_app(
        KorailBrowserAutomation(FakeClient()),
        token="t" * 32,
        readiness_probe=blocked_probe,
        readiness_retry_interval_seconds=0,
        readiness_probe_timeout_seconds=0.01,
    )

    with TestClient(app) as http:
        response = http.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"detail": "not_ready"}


@contextmanager
def serve_korail_fixture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    analytics_status: int,
):
    fixture_directory = Path(__file__).parent / "fixtures"

    class QuietHandler(SimpleHTTPRequestHandler):
        search_clicks = 0

        def log_message(self, format: str, *args: object) -> None:
            return

        def do_GET(self) -> None:
            if urlsplit(self.path).path == "/analytics.png":
                self.send_response(analytics_status)
                self.end_headers()
                return
            super().do_GET()

        def do_POST(self) -> None:
            if urlsplit(self.path).path == "/search-clicked":
                type(self).search_clicks += 1
                self.send_response(204)
                self.end_headers()
                return
            self.send_response(404)
            self.end_headers()

    monkeypatch.chdir(fixture_directory)
    server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", QuietHandler
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("analytics_status", [403, 429])
async def test_playwright_uses_visible_fixture_controls_and_reads_result_dom(
    monkeypatch: pytest.MonkeyPatch,
    analytics_status: int,
) -> None:
    pytest.importorskip("playwright.async_api")
    with serve_korail_fixture(monkeypatch, analytics_status=analytics_status) as (
        base_url,
        handler,
    ):
        client = PlaywrightKorailBrowserClient(
            page_url=f"{base_url}/korail_browser_page.html?track_click=1",
            timeout_seconds=10,
            allow_test_loopback=True,
        )
        snapshot = await client.search(request())

    assert snapshot.source == "korail-official-page-browser"
    assert [(train.train_number, train.standard, train.first) for train in snapshot.trains] == [
        ("43", "standing_plus_seat", "sold_out"),
        ("47", "limited", "sold_out"),
    ]
    assert [train.train_type for train in snapshot.trains] == ["KTX", "KTX"]
    assert [train.arrival_at.isoformat() for train in snapshot.trains] == [
        "2026-08-03T18:30:00+09:00",
        "2026-08-03T19:05:00+09:00",
    ]
    assert [train.adult_fare for train in snapshot.trains] == [None, 59_800]
    assert handler.search_clicks == 1


@pytest.mark.asyncio
async def test_station_search_waits_for_delayed_unique_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("playwright.async_api")
    with serve_korail_fixture(monkeypatch, analytics_status=403) as (base_url, handler):
        client = PlaywrightKorailBrowserClient(
            page_url=(
                f"{base_url}/korail_browser_page.html?scenario=delayed_station_result&track_click=1"
            ),
            timeout_seconds=10,
            allow_test_loopback=True,
        )
        snapshot = await client.search(request())

    assert [train.train_number for train in snapshot.trains] == ["43", "47"]
    assert handler.search_clicks == 1


@pytest.mark.asyncio
async def test_duplicate_async_station_results_fail_before_search_click(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("playwright.async_api")
    with serve_korail_fixture(monkeypatch, analytics_status=403) as (base_url, handler):
        client = PlaywrightKorailBrowserClient(
            page_url=(
                f"{base_url}/korail_browser_page.html"
                "?scenario=duplicate_station_result&track_click=1"
            ),
            timeout_seconds=10,
            allow_test_loopback=True,
        )
        with pytest.raises(BrowserSourceUnavailable) as raised:
            await client.search(request())

    assert raised.value.stage == "station_result"
    assert handler.search_clicks == 0


@pytest.mark.asyncio
async def test_departure_dialog_waits_for_delayed_dom_insertion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("playwright.async_api")
    with serve_korail_fixture(monkeypatch, analytics_status=403) as (base_url, handler):
        client = PlaywrightKorailBrowserClient(
            page_url=(
                f"{base_url}/korail_browser_page.html"
                "?scenario=delayed_departure_dialog&track_click=1"
            ),
            timeout_seconds=10,
            allow_test_loopback=True,
        )
        snapshot = await client.search(request())

    assert [train.train_number for train in snapshot.trains] == ["43", "47"]
    assert handler.search_clicks == 1


@pytest.mark.asyncio
async def test_fixture_merges_rolling_date_into_existing_month_picker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("playwright.async_api")
    with serve_korail_fixture(monkeypatch, analytics_status=403) as (base_url, handler):
        client = PlaywrightKorailBrowserClient(
            page_url=(f"{base_url}/korail_browser_page.html?today=2026-07-31&track_click=1"),
            timeout_seconds=10,
            allow_test_loopback=True,
        )
        snapshot = await client.search(request())

    assert [train.train_number for train in snapshot.trains] == ["43", "47"]
    assert handler.search_clicks == 1


@pytest.mark.asyncio
async def test_result_rows_with_benign_policy_notice_are_not_protection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("playwright.async_api")
    with serve_korail_fixture(monkeypatch, analytics_status=403) as (base_url, _):
        client = PlaywrightKorailBrowserClient(
            page_url=f"{base_url}/korail_browser_page.html?scenario=benign_policy",
            timeout_seconds=10,
            allow_test_loopback=True,
        )
        snapshot = await client.search(request())

    assert [train.train_number for train in snapshot.trains] == ["43", "47"]


@pytest.mark.asyncio
async def test_explicit_protection_surface_wins_over_result_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("playwright.async_api")
    with serve_korail_fixture(monkeypatch, analytics_status=403) as (base_url, handler):
        client = PlaywrightKorailBrowserClient(
            page_url=(f"{base_url}/korail_browser_page.html?scenario=protection&track_click=1"),
            timeout_seconds=10,
            allow_test_loopback=True,
        )
        with pytest.raises(BrowserProtectionDetected) as raised:
            await client.search(request())

    assert raised.value.stage in {"wait_result", "result_protection_check"}
    assert raised.value.trigger == "marker_abnormal_access"
    assert handler.search_clicks == 1


@pytest.mark.asyncio
async def test_pre_submit_identity_mismatch_fails_before_search_click(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("playwright.async_api")
    with serve_korail_fixture(monkeypatch, analytics_status=403) as (
        base_url,
        handler,
    ):
        client = PlaywrightKorailBrowserClient(
            page_url=(
                f"{base_url}/korail_browser_page.html?scenario=pre_submit_mismatch&track_click=1"
            ),
            timeout_seconds=10,
            allow_test_loopback=True,
        )
        with pytest.raises(BrowserSourceUnavailable) as raised:
            await client.search(request())

    assert raised.value.stage == "pre_submit_identity_check"
    assert handler.search_clicks == 0


@pytest.mark.asyncio
async def test_duplicate_visible_station_trigger_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("playwright.async_api")
    with serve_korail_fixture(monkeypatch, analytics_status=403) as (base_url, _):
        client = PlaywrightKorailBrowserClient(
            page_url=(f"{base_url}/korail_browser_page.html?scenario=duplicate_station_trigger"),
            timeout_seconds=10,
            allow_test_loopback=True,
        )
        with pytest.raises(BrowserSourceUnavailable) as raised:
            await client.search(request())

    assert raised.value.stage == "station_trigger"
