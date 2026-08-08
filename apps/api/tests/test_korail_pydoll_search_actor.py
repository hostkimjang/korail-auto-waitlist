from __future__ import annotations

import ast
import logging
from collections.abc import Awaitable
from dataclasses import dataclass, field
from datetime import date, time
from pathlib import Path

import pytest

from rail_waitlist.korail_browser_automation import BrowserSeatSearchRequest
from rail_waitlist.korail_pydoll_contracts import PydollPageSnapshot, PydollSeatBox, PydollTrainRow
from rail_waitlist.korail_sidecar.browser_contracts import BrowserSourceUnavailable
from rail_waitlist.korail_sidecar.pydoll.search_actor import (
    KorailPydollReadOnlySearchSession,
    PydollReadOnlySearchActor,
)


def _request() -> BrowserSeatSearchRequest:
    return BrowserSeatSearchRequest(
        origin="서울",
        destination="부산",
        travel_date=date(2026, 8, 3),
        departure_from=time(14),
        departure_to=time(18),
        passenger_count=1,
    )


def _snapshot() -> PydollPageSnapshot:
    return PydollPageSnapshot(
        body_text="KORAIL 열차 조회 결과",
        rows=(
            PydollTrainRow(
                kind_text="KTX 43",
                train_number="0043",
                route_text="서울 → 부산(14:30 ~ 17:00)",
                seats=(
                    PydollSeatBox("예약 가능", frozenset()),
                    PydollSeatBox("매진", frozenset()),
                ),
            ),
        ),
    )


@dataclass
class _ReadOnlySession:
    snapshot: PydollPageSnapshot
    events: list[str] = field(default_factory=list)
    stations: dict[str, str] = field(default_factory=lambda: {"departure": "", "arrival": ""})
    schedule: tuple[date, int] = (date(2026, 8, 3), 14)

    async def open(self) -> PydollPageSnapshot:
        self.events.append("open")
        return PydollPageSnapshot(body_text="KORAIL 열차 조회", rows=())

    async def navigate(self, _url: str) -> PydollPageSnapshot:
        raise AssertionError("direct navigation is not expected")

    async def navigate_fresh(self, _url: str) -> PydollPageSnapshot:
        raise AssertionError("direct navigation is not expected")

    async def choose_station(self, kind: str, station: str) -> None:
        self.events.append(f"station:{kind}:{station}")
        self.stations[kind] = station

    async def choose_schedule(self, travel_date: date, departure_hour: int) -> None:
        self.events.append(f"schedule:{travel_date.isoformat()}:{departure_hour}")
        self.schedule = travel_date, departure_hour

    async def current_station(self, kind: str) -> str:
        return self.stations[kind]

    async def current_schedule(self) -> tuple[date, int]:
        return self.schedule

    async def current_passenger(self) -> str:
        return "총 1명"

    async def begin_http_replay_capture(self) -> None:
        raise AssertionError("reuse is disabled")

    async def export_http_replay_plan(self, **_kwargs: object) -> object:
        raise AssertionError("reuse is disabled")

    async def submit_once(self) -> None:
        self.events.append("submit")

    async def wait_for_result(self) -> PydollPageSnapshot:
        self.events.append("wait")
        return self.snapshot

    async def expand_results(
        self,
        snapshot: PydollPageSnapshot,
        _max_actions: int,
    ) -> PydollPageSnapshot:
        self.events.append("expand")
        return snapshot


class _SessionContext:
    def __init__(
        self,
        session: _ReadOnlySession,
        *,
        fail_on_clean_exit: bool = False,
    ) -> None:
        self.session = session
        self.fail_on_clean_exit = fail_on_clean_exit
        self.exit_exc_type: type[BaseException] | None = None

    async def __aenter__(self) -> _ReadOnlySession:
        self.session.events.append("enter")
        return self.session

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: object,
    ) -> None:
        self.exit_exc_type = _exc_type
        self.session.events.append("exit")
        if self.fail_on_clean_exit and _exc_type is None:
            raise BrowserSourceUnavailable("browser_close")


async def _cleanup(awaitable: Awaitable[object]) -> None:
    await awaitable


@pytest.mark.asyncio
async def test_search_actor_uses_only_the_read_only_session_protocol() -> None:
    request = _request()
    concrete_session = _ReadOnlySession(_snapshot())
    session: KorailPydollReadOnlySearchSession = concrete_session
    actor = PydollReadOnlySearchActor(
        page_url="https://www.korail.com/ticket/search/general",
        timeout_ms=1_000,
        headless=True,
        session_factory=lambda *_: _SessionContext(concrete_session),
        session_reuse_ttl_seconds=0,
        session_reuse_max_searches=1,
        station_identity_resolver=None,
        monotonic=lambda: 0,
        cleanup=_cleanup,
        response_safety_guard=lambda _snapshot, _stage: None,
        http_replay_client_factory=lambda *_args, **_kwargs: object(),
        http_replay_route_cache_size=4,
        event_logger=logging.getLogger(__name__),
    )

    result = await actor.search(request)

    assert [train.train_number for train in result.trains] == ["43"]
    assert actor.active_session is None
    assert session is concrete_session
    assert concrete_session.events == [
        "enter",
        "open",
        "station:departure:서울",
        "station:arrival:부산",
        "schedule:2026-08-03:14",
        "submit",
        "wait",
        "expand",
        "exit",
    ]


def test_search_actor_does_not_reverse_depend_on_pydoll_browser_facade() -> None:
    module_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "rail_waitlist"
        / "korail_sidecar"
        / "pydoll"
        / "search_actor.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert "korail_pydoll_browser" not in imported_modules


@pytest.mark.asyncio
async def test_search_actor_passes_the_primary_error_to_context_cleanup() -> None:
    concrete_session = _ReadOnlySession(_snapshot())
    context = _SessionContext(concrete_session, fail_on_clean_exit=True)

    def reject_loaded_page(_snapshot: PydollPageSnapshot, stage: str) -> None:
        if stage == "load_page":
            raise BrowserSourceUnavailable(stage)

    actor = PydollReadOnlySearchActor(
        page_url="https://www.korail.com/ticket/search/general",
        timeout_ms=1_000,
        headless=True,
        session_factory=lambda *_: context,
        session_reuse_ttl_seconds=0,
        session_reuse_max_searches=1,
        station_identity_resolver=None,
        monotonic=lambda: 0,
        cleanup=_cleanup,
        response_safety_guard=reject_loaded_page,
        http_replay_client_factory=lambda *_args, **_kwargs: object(),
        http_replay_route_cache_size=4,
        event_logger=logging.getLogger(__name__),
    )

    with pytest.raises(BrowserSourceUnavailable) as raised:
        await actor.search(_request())

    assert raised.value.stage == "load_page"
    assert context.exit_exc_type is BrowserSourceUnavailable
