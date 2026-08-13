from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from rail_waitlist.korail_browser_automation import (
    BrowserProtectionDetected,
    BrowserSeatSearchRequest,
    BrowserSeatSearchResult,
)
from rail_waitlist.korail_http_replay import (
    HttpReplayProtectionDetected,
    HttpReplayProviderUnavailable,
    KorailHttpReplayPlan,
)
from rail_waitlist.korail_pydoll_http_replay import (
    KorailHttpReplayCaptureSession,
    PydollHttpReplayManager,
)
from rail_waitlist.korail_sidecar.browser_service_availability import (
    BrowserProviderUnavailable,
)


def _request(origin: str = "서울", destination: str = "부산") -> BrowserSeatSearchRequest:
    return BrowserSeatSearchRequest(
        origin=origin,
        destination=destination,
        travel_date=date(2026, 8, 3),
        departure_from=time(14),
        departure_to=time(18),
        passenger_count=1,
    )


def _result(request: BrowserSeatSearchRequest) -> BrowserSeatSearchResult:
    return BrowserSeatSearchResult(
        origin=request.origin,
        destination=request.destination,
        travel_date=request.travel_date,
        passenger_count=request.passenger_count,
        observed_at=datetime(2026, 8, 3, tzinfo=UTC),
        trains=[],
    )


@dataclass
class _CaptureSession:
    captures_started: int = 0
    captured_arguments: tuple[str, str, date] | None = None

    async def begin_http_replay_capture(self) -> None:
        self.captures_started += 1

    async def export_http_replay_plan(
        self,
        *,
        origin: str,
        destination: str,
        captured_date: date,
    ) -> KorailHttpReplayPlan:
        self.captured_arguments = origin, destination, captured_date
        return cast(KorailHttpReplayPlan, SimpleNamespace(captured_request_count=1))


class _ReplayClient:
    def __init__(self, result: BrowserSeatSearchResult, failure: Exception | None = None) -> None:
        self.result = result
        self.failure = failure
        self.requests: list[BrowserSeatSearchRequest] = []
        self.closed = 0

    async def search(self, request: BrowserSeatSearchRequest) -> BrowserSeatSearchResult:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        return self.result

    async def close(self) -> None:
        self.closed += 1


async def _cleanup(awaitable):
    await awaitable


def _manager(
    *,
    factory,
    monotonic=lambda: 0.0,
    route_cache_size: int = 4,
) -> PydollHttpReplayManager:
    return PydollHttpReplayManager(
        timeout_seconds=25,
        reuse_ttl_seconds=60,
        reuse_max_searches=3,
        route_cache_size=route_cache_size,
        monotonic=monotonic,
        client_factory=factory,
        cleanup=_cleanup,
    )


@pytest.mark.asyncio
async def test_manager_uses_only_capture_protocol_and_exposes_read_only_lease_inspection() -> None:
    request = _request()
    session: KorailHttpReplayCaptureSession = _CaptureSession()
    replay = _ReplayClient(_result(request))
    manager = _manager(factory=lambda *_args, **_kwargs: replay)

    assert await manager.begin_capture(session) is True
    assert (
        await manager.install_capture(
            session=session,
            request=request,
            created_at=0,
            searches_started=1,
        )
        is True
    )
    assert cast(_CaptureSession, session).captures_started == 1
    assert cast(_CaptureSession, session).captured_arguments == ("서울", "부산", date(2026, 8, 3))
    assert await manager.try_search(request) == _result(request)
    assert replay.requests == [request]
    assert len(manager.active_leases) == 1
    with pytest.raises(TypeError):
        manager.active_leases[("서울", "부산")] = object()  # type: ignore[index]


@pytest.mark.asyncio
async def test_manager_retires_only_failed_route_and_maps_the_existing_protection_trigger() -> None:
    first = _request("서울", "부산")
    second = _request("서울", "대전")
    clients = [
        _ReplayClient(_result(first), HttpReplayProtectionDetected("code_8003")),
        _ReplayClient(_result(second)),
    ]
    manager = _manager(factory=lambda *_args, **_kwargs: clients.pop(0))

    for request in (first, second):
        session = _CaptureSession()
        assert await manager.begin_capture(session) is True
        assert (
            await manager.install_capture(
                session=session,
                request=request,
                created_at=0,
                searches_started=1,
            )
            is True
        )

    with pytest.raises(BrowserProtectionDetected) as raised:
        await manager.try_search(first)

    assert raised.value.trigger == "marker_code_8003"
    assert len(manager.active_leases) == 1
    assert manager.route_key(second) in manager.active_leases
    assert await manager.try_search(second) == _result(second)


@pytest.mark.asyncio
async def test_manager_maps_provider_outage_without_falling_back_to_browser() -> None:
    request = _request()
    replay = _ReplayClient(
        _result(request),
        HttpReplayProviderUnavailable("maintenance_page"),
    )
    manager = _manager(factory=lambda *_args, **_kwargs: replay)
    session = _CaptureSession()
    assert await manager.begin_capture(session) is True
    assert await manager.install_capture(
        session=session,
        request=request,
        created_at=0,
        searches_started=1,
    )

    with pytest.raises(BrowserProviderUnavailable) as raised:
        await manager.try_search(request)

    assert raised.value.trigger == "maintenance_page"
    assert raised.value.stage == "http_replay"
    assert manager.active_leases == {}


@pytest.mark.asyncio
async def test_manager_defers_lru_eviction_until_facade_finalizes_a_successful_install() -> None:
    first = _request("서울", "부산")
    second = _request("서울", "대전")
    clients = [_ReplayClient(_result(first)), _ReplayClient(_result(second))]
    manager = _manager(factory=lambda *_args, **_kwargs: clients.pop(0), route_cache_size=1)

    for request in (first, second):
        session = _CaptureSession()
        assert await manager.begin_capture(session) is True
        assert (
            await manager.install_capture(
                session=session,
                request=request,
                created_at=0,
                searches_started=1,
            )
            is True
        )

    assert set(manager.active_leases) == {manager.route_key(first), manager.route_key(second)}
    await manager.finalize_install(second)

    assert set(manager.active_leases) == {manager.route_key(second)}


def test_manager_does_not_reverse_depend_on_pydoll_browser_facade() -> None:
    module_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "rail_waitlist"
        / "korail_pydoll_http_replay.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert "korail_pydoll_browser" not in imported_modules
