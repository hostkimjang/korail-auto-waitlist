from __future__ import annotations

import asyncio
import logging
import re
import threading
from contextlib import contextmanager
from datetime import date, time
from html import unescape
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Self
from unittest.mock import AsyncMock

import pytest

import rail_waitlist.korail_pydoll_browser as pydoll_module
from rail_waitlist.korail_browser_automation import (
    FULLSTACK_E2E_PAGE_URL,
    BrowserProtectionDetected,
    BrowserRateLimited,
    BrowserSeatSearchRequest,
    BrowserSourceUnavailable,
)
from rail_waitlist.korail_http_replay import (
    HttpReplayProtectionDetected,
    HttpReplaySessionInvalid,
)
from rail_waitlist.korail_pydoll_browser import (
    KorailCredentialInput,
    PydollKorailBrowserClient,
    PydollPageSnapshot,
    PydollSeatBox,
    PydollTrainRow,
    _configure_chromium_options,
    _merge_page_snapshots,
    _PydollSession,
    _set_chromium_binary,
)
from rail_waitlist.korail_search_bootstrap import KorailStationIdentity


def search_request() -> BrowserSeatSearchRequest:
    return BrowserSeatSearchRequest(
        origin="서울",
        destination="부산",
        travel_date=date(2026, 8, 3),
        departure_from=time(14),
        departure_to=time(18),
        passenger_count=1,
    )


def credential_input(version: str = "credential-v1") -> KorailCredentialInput:
    return KorailCredentialInput(
        login_id="fixture-account",
        password="fixture-password",
        version=version,
    )


def _text(fragment: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _fixture_snapshot() -> PydollPageSnapshot:
    source = (Path(__file__).parent / "fixtures" / "korail_browser_page.html").read_text(
        encoding="utf-8"
    )
    rows: list[PydollTrainRow] = []
    for attributes, content in re.findall(
        r'<li\s+([^>]*class="[^"]*tckList[^"]*"[^>]*)>(.*?)</li>',
        source,
        flags=re.DOTALL,
    ):
        if "fixed-result" not in attributes or re.search(r"\bhidden\b", attributes):
            continue
        kind = re.search(r'<div\s+class="tit_box"[^>]*>(.*?)</div>', content, flags=re.DOTALL)
        number = re.search(r'<span\s+class="num"[^>]*>(.*?)</span>', content, flags=re.DOTALL)
        route = re.search(
            r'<div\s+class="data_box right"[^>]*>(.*?)</div>', content, flags=re.DOTALL
        )
        seat_matches = re.findall(
            r'<div\s+class="([^"]*price_box[^"]*)"[^>]*>(.*?)</div>',
            content,
            flags=re.DOTALL,
        )
        assert kind is not None and number is not None and route is not None
        rows.append(
            PydollTrainRow(
                kind_text=_text(kind.group(1)),
                train_number=_text(number.group(1)),
                route_text=_text(route.group(1)),
                seats=tuple(
                    PydollSeatBox(
                        text=_text(seat_text),
                        classes=frozenset(classes.split()),
                    )
                    for classes, seat_text in seat_matches
                ),
            )
        )
    return PydollPageSnapshot(body_text="KORAIL 열차 조회 결과", rows=tuple(rows))


def _fixture_snapshot_for_route(origin: str, destination: str) -> PydollPageSnapshot:
    snapshot = _fixture_snapshot()
    return PydollPageSnapshot(
        body_text=snapshot.body_text,
        rows=tuple(
            PydollTrainRow(
                kind_text=row.kind_text,
                train_number=row.train_number,
                route_text=row.route_text.replace(
                    "서울 → 부산",
                    f"{origin} → {destination}",
                ),
                seats=row.seats,
            )
            for row in snapshot.rows
        ),
    )


def test_pydoll_snapshot_preserves_one_exact_delay_estimate() -> None:
    snapshot = PydollPageSnapshot(
        body_text="KORAIL 열차 조회 결과",
        rows=(
            PydollTrainRow(
                kind_text="KTX",
                train_number="123",
                route_text="서울 → 부산(15:00 ~ 17:40)",
                seats=(
                    PydollSeatBox(text="예약 가능", classes=frozenset()),
                    PydollSeatBox(text="매진", classes=frozenset()),
                ),
                full_text="KTX 123 서울 15:00 부산 17:40 12분 지연 예상",
            ),
        ),
    )

    result = PydollKorailBrowserClient._read_result(snapshot, search_request())

    assert result.trains[0].expected_delay_minutes == 12
    assert result.trains[0].train_type == "KTX"
    assert result.trains[0].arrival_at.isoformat() == "2026-08-03T17:40:00+09:00"


def test_pydoll_snapshot_preserves_overnight_arrival_and_only_one_fare() -> None:
    snapshot = PydollPageSnapshot(
        body_text="KORAIL 열차 조회 결과",
        rows=(
            PydollTrainRow(
                kind_text="KTX-청룡 123",
                train_number="123",
                route_text="서울 → 부산(23:30 ~ 01:15)",
                seats=(
                    PydollSeatBox(text="일반실 59,800원", classes=frozenset()),
                    PydollSeatBox(text="특실 83,700원", classes=frozenset()),
                ),
            ),
            PydollTrainRow(
                kind_text="KTX 125",
                train_number="125",
                route_text="서울 → 부산(23:40 ~ 02:00)",
                seats=(
                    PydollSeatBox(
                        text="성인 59,800원 어린이 29,900원 예약 가능",
                        classes=frozenset(),
                    ),
                    PydollSeatBox(text="매진", classes=frozenset()),
                ),
            ),
        ),
    )
    request = BrowserSeatSearchRequest(
        origin="서울",
        destination="부산",
        travel_date=date(2026, 8, 3),
        departure_from=time(23),
        departure_to=time(23, 59),
        passenger_count=1,
    )

    result = PydollKorailBrowserClient._read_result(snapshot, request)

    assert result.trains[0].train_type == "KTX-청룡"
    assert result.trains[0].arrival_at.isoformat() == "2026-08-04T01:15:00+09:00"
    assert [train.adult_fare for train in result.trains] == [59_800, None]


@contextmanager
def serve_pydoll_fixture(monkeypatch: pytest.MonkeyPatch):
    fixture_directory = Path(__file__).parent / "fixtures"

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

    monkeypatch.chdir(fixture_directory)
    server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


class FixtureSession:
    def __init__(
        self,
        snapshot: PydollPageSnapshot,
        *,
        mismatch: bool = False,
        result_hides_station_inputs: bool = False,
        expanded_snapshot: PydollPageSnapshot | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.mismatch = mismatch
        self.result_hides_station_inputs = result_hides_station_inputs
        self.expanded_snapshot = expanded_snapshot
        self.events: list[str] = []
        self.stations = {"departure": "기존출발", "arrival": "기존도착"}
        self.schedule = (date(2026, 8, 1), 0)
        self.submit_count = 0
        self._submitted = False

    async def __aenter__(self) -> Self:
        self.events.append("enter")
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.events.append("exit")

    async def open(self) -> PydollPageSnapshot:
        self.events.append("open")
        self._submitted = False
        return PydollPageSnapshot(body_text="KORAIL 열차 조회", rows=())

    async def choose_station(self, kind: str, station: str) -> None:
        self.events.append(f"station:{kind}:{station}")
        self.stations[kind] = station

    async def choose_schedule(self, travel_date: date, departure_hour: int) -> None:
        self.events.append(f"schedule:{travel_date.isoformat()}:{departure_hour}")
        self.schedule = (travel_date, departure_hour)

    async def current_station(self, kind: str) -> str:
        if self.result_hides_station_inputs and self.submit_count:
            return ""
        if self.mismatch and kind == "arrival":
            return "대전"
        return self.stations[kind]

    async def current_schedule(self) -> tuple[date, int]:
        return self.schedule

    async def current_passenger(self) -> str:
        return "총 1명"

    async def submit_once(self) -> None:
        if self._submitted:
            raise AssertionError("duplicate submit")
        self._submitted = True
        self.events.append("submit")
        self.submit_count += 1

    async def wait_for_result(self) -> PydollPageSnapshot:
        self.events.append("wait")
        return self.snapshot

    async def navigate(self, url: str) -> PydollPageSnapshot:
        self.events.append("navigate")
        self.stations = {"departure": "서울", "arrival": "부산"}
        self.schedule = (date(2026, 8, 3), 14)
        self._submitted = True
        return self.snapshot

    async def navigate_fresh(self, url: str) -> PydollPageSnapshot:
        self.events.append("navigate_fresh")
        return await self.navigate(url)

    async def expand_results(
        self,
        snapshot: PydollPageSnapshot,
        max_actions: int,
    ) -> PydollPageSnapshot:
        self.events.append(f"expand:{max_actions}")
        if self.expanded_snapshot is None:
            return snapshot
        return _merge_page_snapshots(snapshot, self.expanded_snapshot)


class FixtureSessionFactory:
    def __init__(self, session: FixtureSession) -> None:
        self.session = session
        self.calls: list[tuple[str, int, bool]] = []

    def __call__(self, page_url: str, timeout_ms: int, headless: bool) -> FixtureSession:
        self.calls.append((page_url, timeout_ms, headless))
        return self.session


class StaticStationResolver:
    async def resolve_pair(self, origin: str, destination: str):
        assert (origin, destination) == ("서울", "부산")
        return KorailStationIdentity("0001", "서울"), KorailStationIdentity("0020", "부산")


class ProtectedDirectSession(FixtureSession):
    async def navigate(self, url: str) -> PydollPageSnapshot:
        await super().navigate(url)
        return PydollPageSnapshot(body_text="CODE -8003", rows=())


@pytest.mark.asyncio
async def test_direct_bootstrap_skips_picker_input_and_submit() -> None:
    session = FixtureSession(_fixture_snapshot())
    client = PydollKorailBrowserClient(
        page_url="http://127.0.0.1:8011/korail_browser_page.html",
        timeout_seconds=3,
        allow_test_loopback=True,
        session_factory=FixtureSessionFactory(session),
        station_identity_resolver=StaticStationResolver(),  # type: ignore[arg-type]
    )

    result = await client.search(search_request())

    assert "navigate_fresh" in session.events
    assert "navigate" in session.events
    assert "open" not in session.events
    assert not any(event.startswith("station:") for event in session.events)
    assert not any(event.startswith("schedule:") for event in session.events)
    assert "submit" not in session.events
    assert result.official_search_url is not None
    assert "txtGoStartCode=0001" in result.official_search_url


@pytest.mark.asyncio
async def test_direct_bootstrap_protection_never_falls_back_to_ui_submit() -> None:
    session = ProtectedDirectSession(_fixture_snapshot())
    client = PydollKorailBrowserClient(
        page_url="http://127.0.0.1:8011/korail_browser_page.html",
        timeout_seconds=3,
        allow_test_loopback=True,
        session_factory=FixtureSessionFactory(session),
        station_identity_resolver=StaticStationResolver(),  # type: ignore[arg-type]
    )

    with pytest.raises(BrowserProtectionDetected):
        await client.search(search_request())

    assert session.events.count("navigate") == 1
    assert "open" not in session.events
    assert "submit" not in session.events


@pytest.mark.asyncio
async def test_fresh_direct_navigation_rebases_an_active_capture_on_the_new_tab() -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 5_000, True)
    replacement_tab = SimpleNamespace(
        get_network_logs=AsyncMock(return_value=[{"event": 1}, {"event": 2}])
    )

    async def replace_tab() -> None:
        session._tab = replacement_tab

    session._opened_once = True
    session._http_capture_start = 7
    session._replace_tab = AsyncMock(side_effect=replace_tab)  # type: ignore[method-assign]
    expected = PydollPageSnapshot("열차 조회 결과", ())
    session.navigate = AsyncMock(return_value=expected)  # type: ignore[method-assign]
    direct_url = pydoll_module.build_korail_general_search_url(
        origin=KorailStationIdentity("0001", "서울"),
        destination=KorailStationIdentity("0020", "부산"),
        travel_date=date(2026, 8, 3),
        departure_time=time(14),
    )

    result = await session.navigate_fresh(direct_url)

    assert result is expected
    assert session._http_capture_start == 2
    session._replace_tab.assert_awaited_once_with()
    session.navigate.assert_awaited_once_with(direct_url)


class SequenceSessionFactory:
    def __init__(self, *sessions: FixtureSession) -> None:
        self.sessions = list(sessions)
        self.calls = 0

    def __call__(self, page_url: str, timeout_ms: int, headless: bool) -> FixtureSession:
        session = self.sessions[self.calls]
        self.calls += 1
        return session


class WarmStaleScheduleSession(FixtureSession):
    def __init__(self, snapshot: PydollPageSnapshot) -> None:
        super().__init__(snapshot)
        self.schedule_calls = 0

    async def choose_schedule(self, travel_date: date, departure_hour: int) -> None:
        self.schedule_calls += 1
        if self.schedule_calls > 1:
            raise BrowserSourceUnavailable("departure_date_disabled")
        await super().choose_schedule(travel_date, departure_hour)


class WarmProtectionSession(FixtureSession):
    async def wait_for_result(self) -> PydollPageSnapshot:
        self.events.append("wait")
        if self.submit_count > 1:
            return PydollPageSnapshot(body_text="CODE -8003", rows=())
        return self.snapshot


class CaptureFixtureSession(FixtureSession):
    def __init__(self, snapshot: PydollPageSnapshot) -> None:
        super().__init__(snapshot)
        self.capture_started = 0
        self.capture_exported = 0

    async def begin_http_replay_capture(self) -> None:
        self.capture_started += 1

    async def export_http_replay_plan(
        self,
        *,
        origin: str,
        destination: str,
        captured_date: date,
    ) -> object:
        assert (origin, destination, captured_date) == ("서울", "부산", date(2026, 8, 3))
        self.capture_exported += 1
        return SimpleNamespace(captured_request_count=1)


class AnyRouteCaptureFixtureSession(FixtureSession):
    def __init__(self, snapshot: PydollPageSnapshot) -> None:
        super().__init__(snapshot)
        self.capture_started = 0
        self.captured_routes: list[tuple[str, str, date]] = []

    async def begin_http_replay_capture(self) -> None:
        self.capture_started += 1

    async def export_http_replay_plan(
        self,
        *,
        origin: str,
        destination: str,
        captured_date: date,
    ) -> object:
        self.captured_routes.append((origin, destination, captured_date))
        return SimpleNamespace(captured_request_count=1)


class AuthenticatedCaptureFixtureSession(CaptureFixtureSession):
    def __init__(self, snapshot: PydollPageSnapshot) -> None:
        super().__init__(snapshot)
        self.authentication_attempts = 0

    async def ensure_authenticated(self, _credential: object) -> bool:
        self.authentication_attempts += 1
        return True


class FakeReplayClient:
    def __init__(
        self,
        failure: Exception | None = None,
        *,
        snapshot: PydollPageSnapshot | None = None,
    ) -> None:
        self.failure = failure
        self.snapshot = snapshot or _fixture_snapshot()
        self.requests: list[BrowserSeatSearchRequest] = []
        self.closed = 0

    async def search(self, request: BrowserSeatSearchRequest):
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        return PydollKorailBrowserClient._read_result(self.snapshot, request)

    async def close(self) -> None:
        self.closed += 1


class BlockingExitSession(FixtureSession):
    def __init__(self, snapshot: PydollPageSnapshot) -> None:
        super().__init__(snapshot)
        self.exit_started = asyncio.Event()
        self.release_exit = asyncio.Event()
        self.exit_completed = 0

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.exit_started.set()
        await self.release_exit.wait()
        self.events.append("exit")
        self.exit_completed += 1


class DynamicControl:
    def __init__(
        self,
        text: str,
        *,
        aria_disabled: str,
        disabled_attribute: bool,
        class_name: str = "",
        container_class_name: str = "slick-slide slick-active",
    ) -> None:
        self._text = text
        self.is_enabled = False  # Cached discovery metadata must not decide live state.
        self._state = {
            "ariaDisabled": aria_disabled,
            "disabledAttribute": disabled_attribute,
            "className": class_name,
            "containerClassName": container_class_name,
        }

    @property
    def text(self):
        async def read() -> str:
            return self._text

        return read()

    async def execute_script(self, *args: object, **kwargs: object) -> dict[str, object]:
        return {"result": {"result": {"value": self._state}}}


@pytest.mark.asyncio
async def test_pydoll_client_reads_fixture_once_and_keeps_strict_ktx_seats() -> None:
    session = FixtureSession(_fixture_snapshot())
    factory = FixtureSessionFactory(session)
    client = PydollKorailBrowserClient(
        page_url="http://127.0.0.1:8011/korail_browser_page.html",
        timeout_seconds=3,
        allow_test_loopback=True,
        session_factory=factory,
    )

    result = await client.search(search_request())

    assert factory.calls == [("http://127.0.0.1:8011/korail_browser_page.html", 3000, True)]
    assert session.submit_count == 1
    assert session.events == [
        "enter",
        "open",
        "station:departure:서울",
        "station:arrival:부산",
        "schedule:2026-08-03:14",
        "submit",
        "wait",
        "expand:19",
        "exit",
    ]
    assert [(train.train_number, train.standard, train.first) for train in result.trains] == [
        ("43", "standing_plus_seat", "sold_out"),
        ("47", "limited", "sold_out"),
    ]
    assert [train.train_type for train in result.trains] == ["KTX", "KTX"]
    assert [train.arrival_at.isoformat() for train in result.trains] == [
        "2026-08-03T18:30:00+09:00",
        "2026-08-03T19:05:00+09:00",
    ]
    assert [train.adult_fare for train in result.trains] == [None, 59_800]


@pytest.mark.asyncio
async def test_search_uses_response_safety_guard_patched_before_client_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked_stages: list[str] = []

    def patched_guard(_snapshot: PydollPageSnapshot, stage: str) -> None:
        checked_stages.append(stage)

    monkeypatch.setattr(
        PydollKorailBrowserClient,
        "_assert_response_allowed",
        staticmethod(patched_guard),
    )
    session = FixtureSession(_fixture_snapshot())
    client = PydollKorailBrowserClient(
        page_url="http://127.0.0.1:8011/korail_browser_page.html",
        timeout_seconds=3,
        allow_test_loopback=True,
        session_factory=FixtureSessionFactory(session),
    )

    await client.search(search_request())

    assert checked_stages == ["load_page", "wait_result", "expand_results"]


@pytest.mark.asyncio
async def test_search_resolves_module_global_response_guard_after_client_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked_stages: list[str] = []
    session = FixtureSession(_fixture_snapshot())
    client = PydollKorailBrowserClient(
        page_url="http://127.0.0.1:8011/korail_browser_page.html",
        timeout_seconds=3,
        allow_test_loopback=True,
        session_factory=FixtureSessionFactory(session),
    )

    def patched_guard(
        _snapshot: PydollPageSnapshot,
        stage: str,
        *,
        event_logger: logging.Logger,
    ) -> None:
        assert event_logger is pydoll_module.logger
        checked_stages.append(stage)

    monkeypatch.setattr(pydoll_module, "assert_pydoll_response_allowed", patched_guard)

    await client.search(search_request())

    assert checked_stages == ["load_page", "wait_result", "expand_results"]


@pytest.mark.asyncio
async def test_pydoll_client_reuses_one_browser_for_changed_search_conditions() -> None:
    session = FixtureSession(_fixture_snapshot())
    factory = FixtureSessionFactory(session)
    client = PydollKorailBrowserClient(
        page_url="http://127.0.0.1:8011/korail_browser_page.html",
        timeout_seconds=3,
        allow_test_loopback=True,
        session_factory=factory,
        session_reuse_ttl_seconds=300,
        session_reuse_max_searches=20,
    )

    first = await client.search(search_request())
    changed = search_request().model_copy(update={"departure_from": time(15)})
    second = await client.search(changed)
    await client.close()

    assert factory.calls == [("http://127.0.0.1:8011/korail_browser_page.html", 3000, True)]
    assert session.events.count("enter") == 1
    assert session.events.count("open") == 2
    assert session.events.count("submit") == 2
    assert session.events.count("exit") == 1
    assert [train.train_number for train in first.trains] == ["43", "47"]
    assert [train.train_number for train in second.trains] == ["43", "47"]


@pytest.mark.asyncio
async def test_pydoll_client_cold_reinitializes_once_when_warm_pre_submit_state_expires() -> None:
    warm = WarmStaleScheduleSession(_fixture_snapshot())
    cold = FixtureSession(_fixture_snapshot())
    factory = SequenceSessionFactory(warm, cold)
    client = PydollKorailBrowserClient(
        page_url="http://127.0.0.1:8011/korail_browser_page.html",
        timeout_seconds=3,
        allow_test_loopback=True,
        session_factory=factory,
        session_reuse_ttl_seconds=300,
        session_reuse_max_searches=20,
    )

    await client.search(search_request())
    result = await client.search(search_request().model_copy(update={"departure_from": time(15)}))
    await client.close()

    assert factory.calls == 2
    assert warm.submit_count == 1
    assert warm.events.count("exit") == 1
    assert cold.submit_count == 1
    assert cold.events.count("exit") == 1
    assert [train.train_number for train in result.trains] == ["43", "47"]


@pytest.mark.asyncio
async def test_pydoll_client_does_not_cold_retry_warm_protection_after_submit() -> None:
    warm = WarmProtectionSession(_fixture_snapshot())
    factory = SequenceSessionFactory(warm)
    client = PydollKorailBrowserClient(
        page_url="http://127.0.0.1:8011/korail_browser_page.html",
        timeout_seconds=3,
        allow_test_loopback=True,
        session_factory=factory,
        session_reuse_ttl_seconds=300,
        session_reuse_max_searches=20,
    )

    await client.search(search_request())
    with pytest.raises(BrowserProtectionDetected):
        await client.search(search_request().model_copy(update={"departure_from": time(15)}))

    assert factory.calls == 1
    assert warm.submit_count == 2
    assert warm.events.count("exit") == 1


@pytest.mark.asyncio
async def test_pydoll_client_reuses_captured_http_lease_for_changed_time(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=pydoll_module.__name__)
    session = CaptureFixtureSession(_fixture_snapshot())
    factory = FixtureSessionFactory(session)
    replay = FakeReplayClient()
    monkeypatch.setattr(
        pydoll_module,
        "KorailHttpReplayClient",
        lambda plan, timeout_seconds, lease_is_current: replay,
    )
    client = PydollKorailBrowserClient(
        page_url="http://127.0.0.1:8011/korail_browser_page.html",
        timeout_seconds=3,
        allow_test_loopback=True,
        session_factory=factory,
        session_reuse_ttl_seconds=300,
        session_reuse_max_searches=20,
    )

    await client.search(search_request())
    changed = search_request().model_copy(update={"departure_from": time(15)})
    result = await client.search(changed)
    await client.close()

    assert factory.calls == [("http://127.0.0.1:8011/korail_browser_page.html", 3000, True)]
    assert session.capture_started == 1
    assert session.capture_exported == 1
    assert session.submit_count == 1
    assert replay.requests == [changed]
    assert replay.closed == 1
    assert [train.train_number for train in result.trains] == ["43", "47"]
    assert "event=lease_created captured_requests=1" in caplog.text
    assert "event=search_succeeded lease_search_index=2" in caplog.text
    assert "/web_s/" not in caplog.text
    assert "cookie" not in caplog.text.casefold()


@pytest.mark.asyncio
async def test_http_replay_pool_reuses_first_route_after_second_route_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_session = AnyRouteCaptureFixtureSession(_fixture_snapshot())
    second_session = AnyRouteCaptureFixtureSession(_fixture_snapshot_for_route("서울", "대전"))
    factory = SequenceSessionFactory(first_session, second_session)
    first_replay = FakeReplayClient()
    second_replay = FakeReplayClient(snapshot=_fixture_snapshot_for_route("서울", "대전"))
    replay_clients = iter((first_replay, second_replay))
    monkeypatch.setattr(
        pydoll_module,
        "KorailHttpReplayClient",
        lambda plan, timeout_seconds, lease_is_current: next(replay_clients),
    )
    client = PydollKorailBrowserClient(
        page_url="http://127.0.0.1:8011/korail_browser_page.html",
        timeout_seconds=3,
        allow_test_loopback=True,
        session_factory=factory,
        session_reuse_ttl_seconds=300,
        session_reuse_max_searches=20,
    )
    first_request = search_request()
    second_request = search_request().model_copy(update={"destination": "대전"})
    repeated_first = first_request.model_copy(update={"departure_from": time(15)})

    await client.search(first_request)
    await client.search(second_request)
    await client.search(repeated_first)
    await client.close()

    assert factory.calls == 2
    assert first_session.submit_count == 1
    assert second_session.submit_count == 1
    assert first_replay.requests == [repeated_first]
    assert second_replay.requests == []
    assert first_replay.closed == 1
    assert second_replay.closed == 1


@pytest.mark.asyncio
async def test_http_replay_pool_evicts_only_least_recent_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pydoll_module, "_HTTP_REPLAY_ROUTE_CACHE_SIZE", 2)
    routes = (("서울", "부산"), ("서울", "대전"), ("서울", "광주송정"), ("서울", "부산"))
    sessions = [
        AnyRouteCaptureFixtureSession(_fixture_snapshot_for_route(*route)) for route in routes
    ]
    factory = SequenceSessionFactory(*sessions)
    replay_clients = [
        FakeReplayClient(snapshot=_fixture_snapshot_for_route(*route)) for route in routes
    ]
    replay_iterator = iter(replay_clients)
    monkeypatch.setattr(
        pydoll_module,
        "KorailHttpReplayClient",
        lambda plan, timeout_seconds, lease_is_current: next(replay_iterator),
    )
    client = PydollKorailBrowserClient(
        page_url="http://127.0.0.1:8011/korail_browser_page.html",
        timeout_seconds=3,
        allow_test_loopback=True,
        session_factory=factory,
        session_reuse_ttl_seconds=300,
        session_reuse_max_searches=20,
    )
    first_request = search_request()

    await client.search(first_request)
    await client.search(first_request.model_copy(update={"destination": "대전"}))
    await client.search(first_request.model_copy(update={"destination": "광주송정"}))
    assert replay_clients[0].closed == 1
    assert replay_clients[1].closed == 0

    await client.search(first_request.model_copy(update={"departure_from": time(15)}))
    assert factory.calls == 4
    assert replay_clients[1].closed == 1
    assert replay_clients[2].closed == 0
    assert replay_clients[3].closed == 0
    await client.close()

    assert all(replay.closed == 1 for replay in replay_clients)


@pytest.mark.asyncio
async def test_http_replay_ttl_expiration_is_isolated_by_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = (("서울", "부산"), ("서울", "대전"), ("서울", "부산"))
    sessions = [
        AnyRouteCaptureFixtureSession(_fixture_snapshot_for_route(*route)) for route in routes
    ]
    factory = SequenceSessionFactory(*sessions)
    replay_clients = [
        FakeReplayClient(snapshot=_fixture_snapshot_for_route(*route)) for route in routes
    ]
    replay_iterator = iter(replay_clients)
    monkeypatch.setattr(
        pydoll_module,
        "KorailHttpReplayClient",
        lambda plan, timeout_seconds, lease_is_current: next(replay_iterator),
    )
    now = [0.0]
    client = PydollKorailBrowserClient(
        page_url="http://127.0.0.1:8011/korail_browser_page.html",
        timeout_seconds=3,
        allow_test_loopback=True,
        session_factory=factory,
        session_reuse_ttl_seconds=300,
        session_reuse_max_searches=20,
        monotonic=lambda: now[0],
    )
    first_request = search_request()
    second_request = first_request.model_copy(update={"destination": "대전"})

    await client.search(first_request)
    now[0] = 100
    await client.search(second_request)
    now[0] = 301
    await client.search(first_request.model_copy(update={"departure_from": time(15)}))
    await client.search(second_request.model_copy(update={"departure_from": time(15)}))
    await client.close()

    assert factory.calls == 3
    assert replay_clients[0].closed == 1
    assert replay_clients[1].requests == [
        second_request.model_copy(update={"departure_from": time(15)})
    ]
    assert replay_clients[1].closed == 1
    assert replay_clients[2].closed == 1


@pytest.mark.asyncio
async def test_http_replay_search_limit_is_isolated_by_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = (("서울", "부산"), ("서울", "대전"), ("서울", "부산"))
    sessions = [
        AnyRouteCaptureFixtureSession(_fixture_snapshot_for_route(*route)) for route in routes
    ]
    factory = SequenceSessionFactory(*sessions)
    replay_clients = [
        FakeReplayClient(snapshot=_fixture_snapshot_for_route(*route)) for route in routes
    ]
    replay_iterator = iter(replay_clients)
    monkeypatch.setattr(
        pydoll_module,
        "KorailHttpReplayClient",
        lambda plan, timeout_seconds, lease_is_current: next(replay_iterator),
    )
    client = PydollKorailBrowserClient(
        page_url="http://127.0.0.1:8011/korail_browser_page.html",
        timeout_seconds=3,
        allow_test_loopback=True,
        session_factory=factory,
        session_reuse_ttl_seconds=300,
        session_reuse_max_searches=2,
    )
    first_request = search_request()
    second_request = first_request.model_copy(update={"destination": "대전"})

    await client.search(first_request)
    await client.search(second_request)
    await client.search(first_request.model_copy(update={"departure_from": time(15)}))
    await client.search(second_request.model_copy(update={"departure_from": time(15)}))
    await client.search(first_request.model_copy(update={"departure_from": time(16)}))
    await client.close()

    assert factory.calls == 3
    assert replay_clients[0].requests == [
        first_request.model_copy(update={"departure_from": time(15)})
    ]
    assert replay_clients[1].requests == [
        second_request.model_copy(update={"departure_from": time(15)})
    ]
    assert all(replay.closed == 1 for replay in replay_clients)


@pytest.mark.asyncio
async def test_http_replay_protection_retires_only_selected_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_session = AnyRouteCaptureFixtureSession(_fixture_snapshot())
    second_snapshot = _fixture_snapshot_for_route("서울", "대전")
    second_session = AnyRouteCaptureFixtureSession(second_snapshot)
    factory = SequenceSessionFactory(first_session, second_session)
    first_replay = FakeReplayClient(HttpReplayProtectionDetected("code_8003"))
    second_replay = FakeReplayClient(snapshot=second_snapshot)
    replay_clients = iter((first_replay, second_replay))
    monkeypatch.setattr(
        pydoll_module,
        "KorailHttpReplayClient",
        lambda plan, timeout_seconds, lease_is_current: next(replay_clients),
    )
    client = PydollKorailBrowserClient(
        page_url="http://127.0.0.1:8011/korail_browser_page.html",
        timeout_seconds=3,
        allow_test_loopback=True,
        session_factory=factory,
        session_reuse_ttl_seconds=300,
        session_reuse_max_searches=20,
    )
    first_request = search_request()
    second_request = first_request.model_copy(update={"destination": "대전"})

    await client.search(first_request)
    await client.search(second_request)
    with pytest.raises(BrowserProtectionDetected):
        await client.search(first_request.model_copy(update={"departure_from": time(15)}))
    assert first_replay.closed == 1
    assert second_replay.closed == 0

    repeated_second = second_request.model_copy(update={"departure_from": time(15)})
    await client.search(repeated_second)
    assert factory.calls == 2
    assert second_replay.requests == [repeated_second]
    await client.close()
    assert second_replay.closed == 1


@pytest.mark.asyncio
async def test_login_transition_preserves_independent_http_replay_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_session = AnyRouteCaptureFixtureSession(_fixture_snapshot())
    second_session = AnyRouteCaptureFixtureSession(_fixture_snapshot_for_route("서울", "대전"))
    authenticated_session = AuthenticatedCaptureFixtureSession(_fixture_snapshot())
    factory = SequenceSessionFactory(first_session, second_session, authenticated_session)
    replay_clients = [FakeReplayClient(), FakeReplayClient()]
    replay_iterator = iter(replay_clients)
    monkeypatch.setattr(
        pydoll_module,
        "KorailHttpReplayClient",
        lambda plan, timeout_seconds, lease_is_current: next(replay_iterator),
    )
    client = PydollKorailBrowserClient(
        page_url="http://127.0.0.1:8011/korail_browser_page.html",
        timeout_seconds=3,
        allow_test_loopback=True,
        session_factory=factory,
        session_reuse_ttl_seconds=300,
        session_reuse_max_searches=20,
    )

    await client.search(search_request())
    await client.search(search_request().model_copy(update={"destination": "대전"}))
    assert await client.verify_credentials(credential_input()) is True

    assert factory.calls == 3
    assert all(replay.closed == 0 for replay in replay_clients)
    assert authenticated_session.authentication_attempts == 1
    await client.close()
    assert all(replay.closed == 1 for replay in replay_clients)


@pytest.mark.asyncio
async def test_authenticated_browser_session_is_not_converted_into_a_timetable_replay_lease() -> (
    None
):
    authenticated_session = AuthenticatedCaptureFixtureSession(_fixture_snapshot())
    search_session = AuthenticatedCaptureFixtureSession(_fixture_snapshot())
    factory = SequenceSessionFactory(authenticated_session, search_session)
    client = PydollKorailBrowserClient(
        page_url="http://127.0.0.1:8011/korail_browser_page.html",
        timeout_seconds=3,
        allow_test_loopback=True,
        session_factory=factory,
        session_reuse_ttl_seconds=300,
        session_reuse_max_searches=20,
    )
    credential = credential_input()

    await client.verify_credentials(credential)
    await client.search(search_request())
    await client.close()

    assert authenticated_session.authentication_attempts == 1
    assert authenticated_session.capture_started == 0
    assert authenticated_session.capture_exported == 0
    assert search_session.authentication_attempts == 0
    assert search_session.capture_started == 1


@pytest.mark.asyncio
async def test_http_handoff_keeps_original_search_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = CaptureFixtureSession(_fixture_snapshot())
    replacement = CaptureFixtureSession(_fixture_snapshot())
    factory = SequenceSessionFactory(first, replacement)
    first_replay = FakeReplayClient()
    replacement_replay = FakeReplayClient()
    replay_clients = iter((first_replay, replacement_replay))
    monkeypatch.setattr(
        pydoll_module,
        "KorailHttpReplayClient",
        lambda plan, timeout_seconds, lease_is_current: next(replay_clients),
    )
    client = PydollKorailBrowserClient(
        page_url="http://127.0.0.1:8011/korail_browser_page.html",
        timeout_seconds=3,
        allow_test_loopback=True,
        session_factory=factory,
        session_reuse_ttl_seconds=300,
        session_reuse_max_searches=2,
    )

    await client.search(search_request())
    second_request = search_request().model_copy(update={"departure_from": time(15)})
    await client.search(second_request)
    third_request = search_request().model_copy(update={"departure_from": time(16)})
    await client.search(third_request)
    await client.close()

    assert factory.calls == 2
    assert first.submit_count == 1
    assert first_replay.requests == [second_request]
    assert first_replay.closed == 1
    assert replacement.submit_count == 1


@pytest.mark.asyncio
async def test_pydoll_client_reinitializes_after_http_session_expiry(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=pydoll_module.__name__)
    first = CaptureFixtureSession(_fixture_snapshot())
    cold = FixtureSession(_fixture_snapshot())
    factory = SequenceSessionFactory(first, cold)
    expired = FakeReplayClient(HttpReplaySessionInvalid())
    replacement = FakeReplayClient()
    replay_clients = iter((expired, replacement))
    monkeypatch.setattr(
        pydoll_module,
        "KorailHttpReplayClient",
        lambda plan, timeout_seconds, lease_is_current: next(replay_clients),
    )
    client = PydollKorailBrowserClient(
        page_url="http://127.0.0.1:8011/korail_browser_page.html",
        timeout_seconds=3,
        allow_test_loopback=True,
        session_factory=factory,
        session_reuse_ttl_seconds=300,
        session_reuse_max_searches=20,
    )

    await client.search(search_request())
    changed = search_request().model_copy(update={"departure_from": time(15)})
    result = await client.search(changed)
    await client.close()

    assert factory.calls == 2
    assert expired.requests == [changed]
    assert expired.closed == 1
    assert cold.submit_count == 1
    assert [train.train_number for train in result.trains] == ["43", "47"]
    assert "event=cold_reinit source=http_replay reason=session_invalid" in caplog.text


@pytest.mark.asyncio
async def test_pydoll_client_does_not_cold_retry_http_protection(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=pydoll_module.__name__)
    session = CaptureFixtureSession(_fixture_snapshot())
    factory = FixtureSessionFactory(session)
    replay = FakeReplayClient(HttpReplayProtectionDetected("code_8003"))
    monkeypatch.setattr(
        pydoll_module,
        "KorailHttpReplayClient",
        lambda plan, timeout_seconds, lease_is_current: replay,
    )
    client = PydollKorailBrowserClient(
        page_url="http://127.0.0.1:8011/korail_browser_page.html",
        timeout_seconds=3,
        allow_test_loopback=True,
        session_factory=factory,
        session_reuse_ttl_seconds=300,
        session_reuse_max_searches=20,
    )

    await client.search(search_request())
    with pytest.raises(BrowserProtectionDetected) as raised:
        await client.search(search_request().model_copy(update={"departure_from": time(15)}))

    assert raised.value.trigger == "marker_code_8003"
    assert raised.value.stage == "http_replay"
    assert len(factory.calls) == 1
    assert replay.closed == 1
    assert "event=cold_reinit" not in caplog.text


@pytest.mark.asyncio
async def test_pydoll_client_replaces_browser_after_bounded_search_count() -> None:
    first_session = FixtureSession(_fixture_snapshot())
    second_session = FixtureSession(_fixture_snapshot())
    factory = SequenceSessionFactory(first_session, second_session)
    client = PydollKorailBrowserClient(
        page_url="http://127.0.0.1:8011/korail_browser_page.html",
        timeout_seconds=3,
        allow_test_loopback=True,
        session_factory=factory,
        session_reuse_ttl_seconds=300,
        session_reuse_max_searches=2,
    )

    await client.search(search_request())
    await client.search(search_request())
    await client.search(search_request())
    await client.close()

    assert factory.calls == 2
    assert first_session.events.count("enter") == 1
    assert first_session.events.count("open") == 2
    assert first_session.events.count("exit") == 1
    assert second_session.events.count("enter") == 1
    assert second_session.events.count("open") == 1
    assert second_session.events.count("exit") == 1


@pytest.mark.asyncio
async def test_pydoll_client_finishes_session_cleanup_after_repeated_cancellation() -> None:
    session = BlockingExitSession(_fixture_snapshot())
    client = PydollKorailBrowserClient(
        page_url="http://127.0.0.1:8011/korail_browser_page.html",
        timeout_seconds=3,
        allow_test_loopback=True,
        session_factory=FixtureSessionFactory(session),
        session_reuse_ttl_seconds=300,
        session_reuse_max_searches=20,
    )
    await client.search(search_request())

    close_task = asyncio.create_task(client.close())
    await session.exit_started.wait()
    close_task.cancel()
    await asyncio.sleep(0)
    close_task.cancel()
    await asyncio.sleep(0)

    assert not close_task.done()
    session.release_exit.set()
    with pytest.raises(asyncio.CancelledError):
        await close_task
    assert session.exit_completed == 1
    assert client._active_session is None
    assert client._active_search_session is None


@pytest.mark.asyncio
async def test_pydoll_client_closes_both_actor_owners_before_raising_first_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = PydollKorailBrowserClient()
    events: list[str] = []

    async def close_search() -> None:
        events.append("search")
        raise BrowserSourceUnavailable("browser_close")

    async def close_auth() -> None:
        events.append("auth")

    monkeypatch.setattr(client._search_actor, "close", close_search)
    monkeypatch.setattr(client._auth_actor, "close_locked", close_auth)

    with pytest.raises(BrowserSourceUnavailable) as raised:
        await client.close()

    assert raised.value.stage == "browser_close"
    assert events == ["search", "auth"]


@pytest.mark.asyncio
async def test_enabled_control_uses_live_dom_state_instead_of_cached_attributes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    stale_cached_control = DynamicControl(
        "05시",
        aria_disabled="false",
        disabled_attribute=False,
    )
    monkeypatch.setattr(
        session,
        "_visible_elements",
        AsyncMock(return_value=[stale_cached_control]),
    )

    selected = await session._wait_for_enabled_exact_text(
        ".slideWrap .slick-slide.slick-active a",
        "05시",
        failure_stage="departure_hour_disabled",
    )

    assert selected is stale_cached_control


@pytest.mark.asyncio
async def test_pydoll_client_blocks_submit_when_visible_identity_differs() -> None:
    session = FixtureSession(_fixture_snapshot(), mismatch=True)
    client = PydollKorailBrowserClient(session_factory=FixtureSessionFactory(session))

    with pytest.raises(BrowserSourceUnavailable) as raised:
        await client.search(search_request())

    assert raised.value.stage == "pre_submit_identity_check"
    assert session.submit_count == 0


@pytest.mark.asyncio
async def test_pydoll_client_maps_result_protection_marker_without_retry() -> None:
    snapshot = PydollPageSnapshot(body_text="CODE -8003", rows=())
    session = FixtureSession(snapshot)
    client = PydollKorailBrowserClient(session_factory=FixtureSessionFactory(session))

    with pytest.raises(BrowserProtectionDetected) as raised:
        await client.search(search_request())

    assert raised.value.trigger == "marker_code_8003"
    assert raised.value.stage == "wait_result"
    assert session.submit_count == 1


@pytest.mark.asyncio
async def test_pydoll_logs_only_sanitized_protection_snapshot_counts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    snapshot = PydollPageSnapshot(
        body_text="CODE -8002 secret-body",
        rows=(),
        protection_texts=("CODE -8002 secret-surface",),
        network_responses=(),
    )
    session = FixtureSession(snapshot)
    client = PydollKorailBrowserClient(session_factory=FixtureSessionFactory(session))

    with pytest.raises(BrowserProtectionDetected):
        await client.search(search_request())

    assert "stage=wait_result trigger=marker_code_8002" in caplog.text
    assert "rows=0 visible_surfaces=1 marker_surfaces=1 network=()" in caplog.text
    assert "secret-body" not in caplog.text
    assert "secret-surface" not in caplog.text


@pytest.mark.asyncio
async def test_pydoll_client_includes_more_results_and_deduplicates_rows() -> None:
    complete = _fixture_snapshot()
    initial = PydollPageSnapshot(body_text="결과", rows=(complete.rows[1],))
    expanded = PydollPageSnapshot(
        body_text="결과",
        rows=(complete.rows[1], complete.rows[1], complete.rows[2]),
    )
    session = FixtureSession(initial, expanded_snapshot=expanded)
    client = PydollKorailBrowserClient(session_factory=FixtureSessionFactory(session))

    result = await client.search(search_request())

    assert [train.train_number for train in result.trains] == ["43", "47"]
    assert session.events.count("submit") == 1
    assert session.events.count("expand:19") == 1


@pytest.mark.asyncio
async def test_pydoll_result_expansion_stops_after_one_stalled_click(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 5_000, True)
    snapshot = _fixture_snapshot()
    more = SimpleNamespace(click=AsyncMock())
    monkeypatch.setattr(session, "_find_exact_visible", AsyncMock(return_value=more))
    growth = AsyncMock(return_value=(snapshot, False))
    monkeypatch.setattr(session, "_wait_for_result_growth", growth)

    result = await session.expand_results(snapshot, 19)

    assert [row.train_number for row in result.rows] == [
        "무궁화호 1161",
        "KTX 043",
        "KTX 047",
    ]
    more.click.assert_awaited_once_with()
    growth.assert_awaited_once()


@pytest.mark.asyncio
async def test_pydoll_result_expansion_stops_on_network_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 5_000, True)
    snapshot = _fixture_snapshot()
    added = PydollTrainRow("KTX", "999", "서울 → 부산(17:00 ~ 19:30)", ())
    restricted = PydollPageSnapshot(
        body_text="결과",
        rows=(*snapshot.rows, added),
        network_responses=((429, "xhr"),),
    )
    more = SimpleNamespace(click=AsyncMock())
    monkeypatch.setattr(session, "_find_exact_visible", AsyncMock(return_value=more))
    growth = AsyncMock(return_value=(restricted, True))
    monkeypatch.setattr(session, "_wait_for_result_growth", growth)

    result = await session.expand_results(snapshot, 19)

    assert result.network_responses == ((429, "xhr"),)
    assert result.rows[-1] == added
    more.click.assert_awaited_once_with()
    growth.assert_awaited_once()


@pytest.mark.asyncio
async def test_pydoll_client_maps_business_429_without_retry() -> None:
    snapshot = PydollPageSnapshot(
        body_text="결과",
        rows=_fixture_snapshot().rows,
        network_responses=((429, "fetch"),),
    )
    session = FixtureSession(snapshot)
    client = PydollKorailBrowserClient(session_factory=FixtureSessionFactory(session))

    with pytest.raises(BrowserRateLimited):
        await client.search(search_request())

    assert session.submit_count == 1


@pytest.mark.asyncio
async def test_pydoll_client_maps_document_403_to_sanitized_protection() -> None:
    snapshot = PydollPageSnapshot(
        body_text="결과",
        rows=_fixture_snapshot().rows,
        network_responses=((403, "document"),),
    )
    session = FixtureSession(snapshot)
    client = PydollKorailBrowserClient(session_factory=FixtureSessionFactory(session))

    with pytest.raises(BrowserProtectionDetected) as raised:
        await client.search(search_request())

    assert raised.value.trigger == "http_403_main"
    assert raised.value.stage == "wait_result"
    assert session.submit_count == 1


def test_pydoll_network_listener_keeps_only_business_failures() -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 5_000, True)

    for status, resource_type in (
        (429, "Fetch"),
        (429, "Font"),
        (403, "Document"),
        (403, "XHR"),
        (200, "Document"),
    ):
        session._on_response_received(
            {
                "params": {
                    "type": resource_type,
                    "response": {"status": status},
                }
            }
        )

    assert tuple(session._network_responses) == ((429, "fetch"), (403, "document"))


@pytest.mark.asyncio
async def test_pydoll_wait_result_returns_immediately_for_network_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 5_000, True)
    snapshot = PydollPageSnapshot(
        body_text="",
        rows=(),
        network_responses=((429, "xhr"),),
    )
    snapshot_reader = AsyncMock(return_value=snapshot)
    monkeypatch.setattr(session, "_snapshot", snapshot_reader)

    assert await session.wait_for_result() == snapshot
    snapshot_reader.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_pydoll_result_uses_exact_rows_when_station_inputs_disappear() -> None:
    session = FixtureSession(_fixture_snapshot(), result_hides_station_inputs=True)
    client = PydollKorailBrowserClient(session_factory=FixtureSessionFactory(session))

    result = await client.search(search_request())

    assert [train.train_number for train in result.trains] == ["43", "47"]
    assert session.submit_count == 1


@pytest.mark.asyncio
async def test_pydoll_client_fails_closed_for_unknown_seat_wording() -> None:
    valid = _fixture_snapshot().rows[1]
    unknown = PydollTrainRow(
        kind_text=valid.kind_text,
        train_number=valid.train_number,
        route_text=valid.route_text,
        seats=(
            PydollSeatBox("새로운 상태", frozenset()),
            valid.seats[1],
        ),
    )
    session = FixtureSession(PydollPageSnapshot(body_text="결과", rows=(unknown,)))
    client = PydollKorailBrowserClient(session_factory=FixtureSessionFactory(session))

    with pytest.raises(BrowserSourceUnavailable) as raised:
        await client.search(search_request())

    assert raised.value.stage == "read_result"


def test_pydoll_fullstack_fixture_requires_exact_explicit_gate() -> None:
    with pytest.raises(ValueError, match="official KORAIL HTTPS host"):
        PydollKorailBrowserClient(page_url=FULLSTACK_E2E_PAGE_URL)

    client = PydollKorailBrowserClient(
        page_url=FULLSTACK_E2E_PAGE_URL,
        allow_fullstack_fixture=True,
    )

    assert client.page_url == FULLSTACK_E2E_PAGE_URL


def test_pydoll_uses_explicit_container_chromium_binary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    binary = tmp_path / "chrome"
    binary.write_bytes(b"fixture")
    monkeypatch.setenv("KORAIL_BROWSER_CHROMIUM_EXECUTABLE_PATH", str(binary))
    options = SimpleNamespace(binary_location=None)

    _set_chromium_binary(options)

    assert options.binary_location == str(binary)


def test_pydoll_disables_password_manager_surfaces(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    binary = tmp_path / "chrome"
    binary.write_bytes(b"fixture")
    monkeypatch.setenv("KORAIL_BROWSER_CHROMIUM_EXECUTABLE_PATH", str(binary))

    class Options:
        def __init__(self) -> None:
            self.headless: bool | None = None
            self.browser_preferences: dict[str, bool] = {}
            self.arguments: list[str] = []
            self.binary_location: str | None = None

        def add_argument(self, argument: str) -> None:
            self.arguments.append(argument)

    options = Options()
    _configure_chromium_options(options, headless=False)

    assert options.headless is False
    assert options.browser_preferences == {
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
        "profile.password_manager_leak_detection": False,
    }
    assert "--disable-save-password-bubble" in options.arguments
    assert options.binary_location == str(binary)


@pytest.mark.asyncio
async def test_pydoll_real_browser_uses_visible_fixture_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("pydoll.browser")
    with serve_pydoll_fixture(monkeypatch) as base_url:
        client = PydollKorailBrowserClient(
            page_url=(f"{base_url}/korail_browser_page.html?today=2026-07-30"),
            timeout_seconds=15,
            allow_test_loopback=True,
        )
        result = await client.search(
            BrowserSeatSearchRequest(
                origin="서울",
                destination="부산",
                travel_date=date(2026, 7, 31),
                departure_from=time(12),
                departure_to=time(18),
                passenger_count=1,
            )
        )

    assert [(train.train_number, train.standard, train.first) for train in result.trains] == [
        ("9001", "available", "sold_out")
    ]


@pytest.mark.asyncio
async def test_pydoll_real_browser_selects_midnight_kst_departure_day_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("pydoll.browser")
    with serve_pydoll_fixture(monkeypatch) as base_url:
        client = PydollKorailBrowserClient(
            page_url=f"{base_url}/korail_browser_page.html?today=2026-08-01",
            timeout_seconds=15,
            allow_test_loopback=True,
        )
        result = await client.search(
            BrowserSeatSearchRequest(
                origin="서울",
                destination="부산",
                travel_date=date(2026, 8, 2),
                departure_from=time(12),
                departure_to=time(18),
                passenger_count=1,
            )
        )

    assert [train.train_number for train in result.trains] == ["9001"]


@pytest.mark.asyncio
async def test_pydoll_real_browser_keeps_selected_same_day_when_picker_omits_day_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("pydoll.browser")
    with serve_pydoll_fixture(monkeypatch) as base_url:
        client = PydollKorailBrowserClient(
            page_url=(
                f"{base_url}/korail_browser_page.html?today=2026-08-02&scenario=same_day_morning"
            ),
            timeout_seconds=15,
            allow_test_loopback=True,
        )
        result = await client.search(
            BrowserSeatSearchRequest(
                origin="서울",
                destination="부산",
                travel_date=date(2026, 8, 2),
                departure_from=time(5),
                departure_to=time(9),
                passenger_count=1,
            )
        )

    assert [train.train_number for train in result.trains] == ["9001"]


@pytest.mark.asyncio
async def test_pydoll_real_browser_rejects_schedule_readback_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("pydoll.browser")
    with serve_pydoll_fixture(monkeypatch) as base_url:
        client = PydollKorailBrowserClient(
            page_url=(
                f"{base_url}/korail_browser_page.html?today=2026-07-30&scenario=pre_submit_mismatch"
            ),
            timeout_seconds=2,
            allow_test_loopback=True,
        )
        with pytest.raises(BrowserSourceUnavailable) as raised:
            await client.search(
                BrowserSeatSearchRequest(
                    origin="서울",
                    destination="부산",
                    travel_date=date(2026, 7, 31),
                    departure_from=time(12),
                    departure_to=time(18),
                    passenger_count=1,
                )
            )

    assert raised.value.stage == "departure_schedule_readback"


@pytest.mark.asyncio
async def test_pydoll_real_browser_expands_visible_more_results_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("pydoll.browser")
    with serve_pydoll_fixture(monkeypatch) as base_url:
        client = PydollKorailBrowserClient(
            page_url=(
                f"{base_url}/korail_browser_page.html?today=2026-07-30&scenario=more_results"
            ),
            timeout_seconds=15,
            allow_test_loopback=True,
        )
        result = await client.search(search_request())

    assert [train.train_number for train in result.trains] == ["43", "47", "49"]


@pytest.mark.asyncio
async def test_pydoll_real_browser_selects_enabled_duplicate_hour_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("pydoll.browser")
    with serve_pydoll_fixture(monkeypatch) as base_url:
        client = PydollKorailBrowserClient(
            page_url=(
                f"{base_url}/korail_browser_page.html"
                "?today=2026-08-02&scenario=duplicate_hour_control"
            ),
            timeout_seconds=15,
            allow_test_loopback=True,
        )
        result = await client.search(
            BrowserSeatSearchRequest(
                origin="서울",
                destination="부산",
                travel_date=date(2026, 8, 3),
                departure_from=time(5),
                departure_to=time(9),
                passenger_count=1,
            )
        )

    assert [train.train_number for train in result.trains] == ["9001"]


@pytest.mark.asyncio
async def test_pydoll_real_browser_clicks_visible_soft_aria_hour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("pydoll.browser")
    with serve_pydoll_fixture(monkeypatch) as base_url:
        client = PydollKorailBrowserClient(
            page_url=(
                f"{base_url}/korail_browser_page.html"
                "?today=2026-08-02&scenario=soft_aria_hour_click"
            ),
            timeout_seconds=15,
            allow_test_loopback=True,
        )
        result = await client.search(
            BrowserSeatSearchRequest(
                origin="서울",
                destination="부산",
                travel_date=date(2026, 8, 3),
                departure_from=time(5),
                departure_to=time(9),
                passenger_count=1,
            )
        )

    assert [train.train_number for train in result.trains] == ["9001"]


@pytest.mark.asyncio
async def test_pydoll_real_browser_selects_exact_hour_from_full_dom_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("pydoll.browser")
    with serve_pydoll_fixture(monkeypatch) as base_url:
        client = PydollKorailBrowserClient(
            page_url=(
                f"{base_url}/korail_browser_page.html?today=2026-08-02&scenario=all_hour_dom_click"
            ),
            timeout_seconds=15,
            allow_test_loopback=True,
        )
        result = await client.search(
            BrowserSeatSearchRequest(
                origin="서울",
                destination="부산",
                travel_date=date(2026, 8, 3),
                departure_from=time(12),
                departure_to=time(18),
                passenger_count=1,
            )
        )

    assert [train.train_number for train in result.trains] == ["9001"]


@pytest.mark.asyncio
async def test_pydoll_real_browser_rejects_ignored_full_dom_hour_click(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("pydoll.browser")
    with serve_pydoll_fixture(monkeypatch) as base_url:
        client = PydollKorailBrowserClient(
            page_url=(
                f"{base_url}/korail_browser_page.html"
                "?today=2026-08-02&scenario=all_hour_dom_click_ignored"
            ),
            timeout_seconds=2,
            allow_test_loopback=True,
        )
        with pytest.raises(BrowserSourceUnavailable) as raised:
            await client.search(
                BrowserSeatSearchRequest(
                    origin="서울",
                    destination="부산",
                    travel_date=date(2026, 8, 3),
                    departure_from=time(12),
                    departure_to=time(18),
                    passenger_count=1,
                )
            )

    assert raised.value.stage == "departure_hour_navigate"


@pytest.mark.asyncio
async def test_pydoll_real_browser_rejects_non_hour_schedule_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("pydoll.browser")
    with serve_pydoll_fixture(monkeypatch) as base_url:
        client = PydollKorailBrowserClient(
            page_url=(
                f"{base_url}/korail_browser_page.html"
                "?today=2026-08-02&scenario=all_hour_dom_minute_mismatch"
            ),
            timeout_seconds=2,
            allow_test_loopback=True,
        )
        with pytest.raises(BrowserSourceUnavailable) as raised:
            await client.search(
                BrowserSeatSearchRequest(
                    origin="서울",
                    destination="부산",
                    travel_date=date(2026, 8, 3),
                    departure_from=time(12),
                    departure_to=time(18),
                    passenger_count=1,
                )
            )

    assert raised.value.stage == "departure_schedule_readback"


@pytest.mark.asyncio
async def test_pydoll_real_browser_navigates_from_adjacent_disabled_hour_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("pydoll.browser")
    with serve_pydoll_fixture(monkeypatch) as base_url:
        client = PydollKorailBrowserClient(
            page_url=(
                f"{base_url}/korail_browser_page.html"
                "?today=2026-08-02&scenario=adjacent_hour_window"
            ),
            timeout_seconds=15,
            allow_test_loopback=True,
        )
        result = await client.search(
            BrowserSeatSearchRequest(
                origin="서울",
                destination="부산",
                travel_date=date(2026, 8, 3),
                departure_from=time(5),
                departure_to=time(9),
                passenger_count=1,
            )
        )

    assert [train.train_number for train in result.trains] == ["9001"]


@pytest.mark.asyncio
async def test_pydoll_real_browser_navigates_to_an_earlier_hour_with_time_owned_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("pydoll.browser")
    with serve_pydoll_fixture(monkeypatch) as base_url:
        client = PydollKorailBrowserClient(
            page_url=(
                f"{base_url}/korail_browser_page.html"
                "?today=2026-08-02&scenario=previous_hour_window_anchor_arrow"
            ),
            timeout_seconds=15,
            allow_test_loopback=True,
        )
        result = await client.search(
            BrowserSeatSearchRequest(
                origin="서울",
                destination="부산",
                travel_date=date(2026, 8, 3),
                departure_from=time(12),
                departure_to=time(18),
                passenger_count=1,
            )
        )

    assert [train.train_number for train in result.trains] == ["9001"]


@pytest.mark.asyncio
async def test_pydoll_real_browser_navigates_to_an_earlier_hour_with_mouse_drag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("pydoll.browser")
    with serve_pydoll_fixture(monkeypatch) as base_url:
        client = PydollKorailBrowserClient(
            page_url=(
                f"{base_url}/korail_browser_page.html"
                "?today=2026-08-02&scenario=previous_hour_window_mouse"
            ),
            timeout_seconds=15,
            allow_test_loopback=True,
        )
        result = await client.search(
            BrowserSeatSearchRequest(
                origin="서울",
                destination="부산",
                travel_date=date(2026, 8, 3),
                departure_from=time(12),
                departure_to=time(18),
                passenger_count=1,
            )
        )

    assert [train.train_number for train in result.trains] == ["9001"]


@pytest.mark.asyncio
async def test_pydoll_real_browser_navigates_to_an_earlier_hour_with_keyboard_after_query_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("pydoll.browser")
    with serve_pydoll_fixture(monkeypatch) as base_url:
        client = PydollKorailBrowserClient(
            page_url=(
                f"{base_url}/korail_browser_page.html"
                "?today=2026-08-02&scenario=keyboard_previous_hour_window"
            ),
            timeout_seconds=15,
            allow_test_loopback=True,
        )
        await client.search(
            BrowserSeatSearchRequest(
                origin="서울",
                destination="부산",
                travel_date=date(2026, 8, 2),
                departure_from=time(20),
                departure_to=time(23),
                passenger_count=1,
            )
        )
        result = await client.search(
            BrowserSeatSearchRequest(
                origin="서울",
                destination="부산",
                travel_date=date(2026, 8, 3),
                departure_from=time(18),
                departure_to=time(23),
                passenger_count=1,
            )
        )

    assert [train.train_number for train in result.trains] == ["9001"]


@pytest.mark.asyncio
async def test_pydoll_real_browser_reopens_picker_after_date_change_refreshes_disabled_hour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("pydoll.browser")
    with serve_pydoll_fixture(monkeypatch) as base_url:
        client = PydollKorailBrowserClient(
            page_url=(
                f"{base_url}/korail_browser_page.html"
                "?today=2026-08-02&scenario=date_change_stale_disabled_hour"
            ),
            timeout_seconds=15,
            allow_test_loopback=True,
        )
        result = await client.search(
            BrowserSeatSearchRequest(
                origin="서울",
                destination="부산",
                travel_date=date(2026, 8, 3),
                departure_from=time(18),
                departure_to=time(23),
                passenger_count=1,
            )
        )

    assert [train.train_number for train in result.trains] == ["9001"]


@pytest.mark.asyncio
async def test_pydoll_real_browser_rejects_adjacent_hour_window_without_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("pydoll.browser")
    with serve_pydoll_fixture(monkeypatch) as base_url:
        client = PydollKorailBrowserClient(
            page_url=(
                f"{base_url}/korail_browser_page.html"
                "?today=2026-08-02&scenario=adjacent_hour_window_no_progress"
            ),
            timeout_seconds=2,
            allow_test_loopback=True,
        )
        with pytest.raises(BrowserSourceUnavailable) as raised:
            await client.search(
                BrowserSeatSearchRequest(
                    origin="서울",
                    destination="부산",
                    travel_date=date(2026, 8, 3),
                    departure_from=time(5),
                    departure_to=time(9),
                    passenger_count=1,
                )
            )

    assert raised.value.stage == "departure_hour_navigate"


@pytest.mark.asyncio
async def test_pydoll_real_browser_keeps_selected_hour_after_adjacent_window_navigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("pydoll.browser")
    with serve_pydoll_fixture(monkeypatch) as base_url:
        client = PydollKorailBrowserClient(
            page_url=(
                f"{base_url}/korail_browser_page.html"
                "?today=2026-08-02&scenario=adjacent_selected_hour"
            ),
            timeout_seconds=15,
            allow_test_loopback=True,
        )
        result = await client.search(
            BrowserSeatSearchRequest(
                origin="서울",
                destination="부산",
                travel_date=date(2026, 8, 3),
                departure_from=time(5),
                departure_to=time(9),
                passenger_count=1,
            )
        )

    assert [train.train_number for train in result.trains] == ["9001"]


@pytest.mark.asyncio
async def test_pydoll_real_browser_keeps_exact_already_selected_disabled_hour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("pydoll.browser")
    with serve_pydoll_fixture(monkeypatch) as base_url:
        client = PydollKorailBrowserClient(
            page_url=(
                f"{base_url}/korail_browser_page.html"
                "?today=2026-08-02&scenario=selected_disabled_hour_control"
            ),
            timeout_seconds=15,
            allow_test_loopback=True,
        )
        result = await client.search(
            BrowserSeatSearchRequest(
                origin="서울",
                destination="부산",
                travel_date=date(2026, 8, 3),
                departure_from=time(5),
                departure_to=time(9),
                passenger_count=1,
            )
        )

    assert [train.train_number for train in result.trains] == ["9001"]


@pytest.mark.asyncio
async def test_pydoll_real_browser_rejects_disabled_hour_when_preselection_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("pydoll.browser")
    with serve_pydoll_fixture(monkeypatch) as base_url:
        client = PydollKorailBrowserClient(
            page_url=(
                f"{base_url}/korail_browser_page.html"
                "?today=2026-08-02&scenario=disabled_hour_preselection_mismatch"
            ),
            timeout_seconds=2,
            allow_test_loopback=True,
        )
        with pytest.raises(BrowserSourceUnavailable) as raised:
            await client.search(
                BrowserSeatSearchRequest(
                    origin="서울",
                    destination="부산",
                    travel_date=date(2026, 8, 3),
                    departure_from=time(5),
                    departure_to=time(9),
                    passenger_count=1,
                )
            )

    assert raised.value.stage == "departure_hour_disabled"


@pytest.mark.asyncio
async def test_pydoll_real_browser_rejects_multiple_disabled_hours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("pydoll.browser")
    with serve_pydoll_fixture(monkeypatch) as base_url:
        client = PydollKorailBrowserClient(
            page_url=(
                f"{base_url}/korail_browser_page.html"
                "?today=2026-08-02&scenario=multiple_disabled_hours"
            ),
            timeout_seconds=2,
            allow_test_loopback=True,
        )
        with pytest.raises(BrowserSourceUnavailable) as raised:
            await client.search(
                BrowserSeatSearchRequest(
                    origin="서울",
                    destination="부산",
                    travel_date=date(2026, 8, 3),
                    departure_from=time(5),
                    departure_to=time(9),
                    passenger_count=1,
                )
            )

    assert raised.value.stage == "departure_hour_disabled"
