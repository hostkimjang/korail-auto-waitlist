"""Run bounded, read-only KORAIL Pydoll searches outside the authenticated actor."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from datetime import time as clock_time
from typing import Protocol

from ...provider_adapters.korail_search_bootstrap import (
    KorailStationIdentityResolver,
    KorailStationIdentityUnavailable,
)
from ...provider_registry.korail_search_url_policy import build_korail_general_search_url
from ..browser_contracts import (
    BrowserProtectionDetected,
    BrowserRateLimited,
    BrowserSeatSearchRequest,
    BrowserSeatSearchResult,
    BrowserSourceUnavailable,
    BrowserTrainSnapshot,
)
from ..http_replay import KorailHttpReplayPlan
from ..search_result_policy import (
    parse_expected_delay_minutes,
    parse_official_train_type,
    parse_unambiguous_adult_fare,
    service_datetimes,
    status_from_seat_box,
)
from .http_replay import (
    KorailHttpReplayClientFactory,
    PydollHttpReplayManager,
)
from .page_contracts import (
    KORAIL_ROUTE_HEADING,
    PydollPageSnapshot,
    normalize_korail_station,
    normalize_korail_train_number,
)

__all__ = (
    "Awaitable",
    "BrowserProtectionDetected",
    "BrowserRateLimited",
    "BrowserSeatSearchRequest",
    "BrowserSeatSearchResult",
    "BrowserSourceUnavailable",
    "BrowserTrainSnapshot",
    "Callable",
    "Cleanup",
    "KORAIL_ROUTE_HEADING",
    "KorailHttpReplayClientFactory",
    "KorailHttpReplayPlan",
    "KorailPydollReadOnlySearchSession",
    "KorailPydollReadOnlySearchSessionContext",
    "KorailPydollReadOnlySearchSessionFactory",
    "KorailStationIdentityResolver",
    "KorailStationIdentityUnavailable",
    "Mapping",
    "Protocol",
    "PydollHttpReplayManager",
    "PydollPageSnapshot",
    "PydollReadOnlySearchActor",
    "ResponseSafetyGuard",
    "UTC",
    "annotations",
    "asyncio",
    "build_korail_general_search_url",
    "clock_time",
    "dataclass",
    "date",
    "datetime",
    "logging",
    "normalize_korail_station",
    "normalize_korail_train_number",
    "parse_expected_delay_minutes",
    "parse_official_train_type",
    "parse_unambiguous_adult_fare",
    "service_datetimes",
    "status_from_seat_box",
    "sys",
)

_MAX_MORE_RESULT_ACTIONS = 19
ResponseSafetyGuard = Callable[[PydollPageSnapshot, str], None]
Cleanup = Callable[[Awaitable[object]], Awaitable[None]]


class KorailPydollReadOnlySearchSession(Protocol):
    """Only the browser actions permitted to a timetable/seat observation search."""

    async def open(self) -> PydollPageSnapshot: ...

    async def navigate(self, url: str) -> PydollPageSnapshot: ...

    async def navigate_fresh(self, url: str) -> PydollPageSnapshot: ...

    async def choose_station(self, kind: str, station: str) -> None: ...

    async def choose_schedule(self, travel_date: date, departure_hour: int) -> None: ...

    async def current_station(self, kind: str) -> str: ...

    async def current_schedule(self) -> tuple[date, int]: ...

    async def current_passenger(self) -> str: ...

    async def begin_http_replay_capture(self) -> None: ...

    async def export_http_replay_plan(
        self,
        *,
        origin: str,
        destination: str,
        captured_date: date,
    ) -> KorailHttpReplayPlan: ...

    async def submit_once(self) -> None: ...

    async def wait_for_result(self) -> PydollPageSnapshot: ...

    async def expand_results(
        self,
        snapshot: PydollPageSnapshot,
        max_actions: int,
    ) -> PydollPageSnapshot: ...


class KorailPydollReadOnlySearchSessionContext(Protocol):
    async def __aenter__(self) -> KorailPydollReadOnlySearchSession: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> bool | None: ...


KorailPydollReadOnlySearchSessionFactory = Callable[
    [str, int, bool], KorailPydollReadOnlySearchSessionContext
]


@dataclass
class _ActiveReadOnlySearchSession:
    context: KorailPydollReadOnlySearchSessionContext
    session: KorailPydollReadOnlySearchSession
    created_at: float
    last_used_at: float
    searches_started: int = 0


@dataclass(frozen=True)
class _ReadOnlySearchSessionLease:
    context: KorailPydollReadOnlySearchSessionContext
    session: KorailPydollReadOnlySearchSession
    created_at: float
    searches_started: int
    persistent: bool
    reused: bool


class PydollReadOnlySearchActor:
    """Own the UI/direct-navigation search lifecycle and its detached replay handoff."""

    def __init__(
        self,
        *,
        page_url: str,
        timeout_ms: int,
        headless: bool,
        session_factory: KorailPydollReadOnlySearchSessionFactory,
        session_reuse_ttl_seconds: float,
        session_reuse_max_searches: int,
        station_identity_resolver: KorailStationIdentityResolver | None,
        monotonic: Callable[[], float],
        cleanup: Cleanup,
        response_safety_guard: ResponseSafetyGuard,
        http_replay_client_factory: KorailHttpReplayClientFactory,
        http_replay_route_cache_size: int,
        event_logger: logging.Logger,
    ) -> None:
        self._page_url = page_url
        self._timeout_ms = timeout_ms
        self._headless = headless
        self._session_factory = session_factory
        self._session_reuse_ttl_seconds = session_reuse_ttl_seconds
        self._session_reuse_max_searches = session_reuse_max_searches
        self._station_identity_resolver = station_identity_resolver
        self._monotonic = monotonic
        self._cleanup = cleanup
        self._response_safety_guard = response_safety_guard
        self._event_logger = event_logger
        self._search_lock = asyncio.Lock()
        self._active_session: _ActiveReadOnlySearchSession | None = None
        self._http_replay_manager = PydollHttpReplayManager(
            timeout_seconds=max(1, timeout_ms / 1000),
            reuse_ttl_seconds=session_reuse_ttl_seconds,
            reuse_max_searches=session_reuse_max_searches,
            route_cache_size=http_replay_route_cache_size,
            monotonic=monotonic,
            client_factory=http_replay_client_factory,
            cleanup=cleanup,
            event_logger=event_logger,
        )

    @property
    def active_session(self) -> object | None:
        """Expose only presence for facade compatibility inspection."""

        return self._active_session

    @property
    def active_http_replays(self) -> Mapping[tuple[str, str], object]:
        return self._http_replay_manager.active_leases

    async def search(self, request: BrowserSeatSearchRequest) -> BrowserSeatSearchResult:
        if request.passenger_count != 1:
            raise BrowserSourceUnavailable("passenger_count_not_supported")
        async with self._search_lock:
            replayed = await self._http_replay_manager.try_search(request)
            if replayed is not None:
                return replayed
            direct_url = await self.direct_search_url(
                request.origin,
                request.destination,
                request.travel_date,
                request.departure_from,
            )
            cold_recovery_used = False
            while True:
                stage = "browser_launch"
                lease: _ReadOnlySearchSessionLease | None = None
                try:
                    lease = await self._acquire_session()
                    session = lease.session
                    if direct_url is None:
                        stage = "load_page"
                        self._response_safety_guard(await session.open(), stage)
                        stage = "choose_origin"
                        await session.choose_station("departure", request.origin)
                        stage = "choose_destination"
                        await session.choose_station("arrival", request.destination)
                        stage = "choose_departure"
                        await session.choose_schedule(
                            request.travel_date,
                            request.departure_from.hour,
                        )
                        stage = "pre_submit_identity_check"
                        await self._assert_identity(session, request, stage)
                        capture_started = await self._http_replay_manager.begin_capture(session)
                        stage = "submit_search"
                        await session.submit_once()
                    else:
                        # Navigation itself starts the one official business lookup.
                        # Capture first, and never retry through the UI after this point.
                        capture_started = await self._http_replay_manager.begin_capture(session)
                        stage = "direct_navigation"
                        self._response_safety_guard(await session.navigate_fresh(direct_url), stage)
                    stage = "wait_result"
                    snapshot = await session.wait_for_result()
                    self._response_safety_guard(snapshot, stage)
                    stage = "expand_results"
                    snapshot = await session.expand_results(snapshot, _MAX_MORE_RESULT_ACTIONS)
                    self._response_safety_guard(snapshot, stage)
                    stage = "result_identity_check"
                    await self._assert_result_identity(session, request)
                    stage = "read_result"
                    result = self.read_result(snapshot, request).model_copy(
                        update={"official_search_url": direct_url}
                    )
                    if capture_started:
                        installed = await self._http_replay_manager.install_capture(
                            session=session,
                            request=request,
                            created_at=lease.created_at,
                            searches_started=lease.searches_started,
                        )
                        if installed and lease.persistent:
                            try:
                                await self._discard_active_session()
                            except BaseException:
                                await self._http_replay_manager.discard(
                                    self._http_replay_manager.route_key(request)
                                )
                                raise
                        if installed:
                            await self._http_replay_manager.finalize_install(request)
                    return result
                except asyncio.CancelledError:
                    if lease is not None and lease.persistent:
                        await self._discard_active_session()
                    raise
                except (BrowserProtectionDetected, BrowserRateLimited):
                    if lease is not None and lease.persistent:
                        await self._discard_active_session()
                    raise
                except BrowserSourceUnavailable as error:
                    should_reinitialize = (
                        not cold_recovery_used
                        and lease is not None
                        and lease.reused
                        and stage
                        in {
                            "load_page",
                            "choose_origin",
                            "choose_destination",
                            "choose_departure",
                            "pre_submit_identity_check",
                        }
                    )
                    if lease is not None and lease.persistent:
                        await self._discard_active_session()
                    if should_reinitialize:
                        self._event_logger.info(
                            "KORAIL Pydoll event=cold_reinit source=browser "
                            "reason=warm_pre_submit_state stage=%s",
                            stage,
                        )
                        cold_recovery_used = True
                        continue
                    if error.stage == "unspecified":
                        raise BrowserSourceUnavailable(stage) from error
                    raise
                except Exception as error:
                    if lease is not None and lease.persistent:
                        await self._discard_active_session()
                    raise BrowserSourceUnavailable(stage) from error
                finally:
                    if lease is not None and not lease.persistent:
                        await lease.context.__aexit__(*sys.exc_info())

    async def close(self) -> None:
        async with self._search_lock:
            await self._http_replay_manager.discard()
            await self._discard_active_session()

    async def direct_search_url(
        self,
        origin: str,
        destination: str,
        travel_date: date,
        departure_time: clock_time,
    ) -> str | None:
        resolver = self._station_identity_resolver
        if resolver is None:
            return None
        try:
            origin_identity, destination_identity = await resolver.resolve_pair(origin, destination)
        except KorailStationIdentityUnavailable:
            return None
        return build_korail_general_search_url(
            origin=origin_identity,
            destination=destination_identity,
            travel_date=travel_date,
            departure_time=departure_time,
        )

    async def _acquire_session(self) -> _ReadOnlySearchSessionLease:
        if not self._session_reuse_enabled:
            created_at = self._monotonic()
            context = self._session_factory(self._page_url, self._timeout_ms, self._headless)
            session = await context.__aenter__()
            return _ReadOnlySearchSessionLease(
                context=context,
                session=session,
                created_at=created_at,
                searches_started=1,
                persistent=False,
                reused=False,
            )

        now = self._monotonic()
        active = self._active_session
        if active is not None and (
            now - active.last_used_at >= self._session_reuse_ttl_seconds
            or active.searches_started >= self._session_reuse_max_searches
        ):
            await self._discard_active_session()
            active = None
        reused = active is not None
        if active is None:
            context = self._session_factory(self._page_url, self._timeout_ms, self._headless)
            session = await context.__aenter__()
            active = _ActiveReadOnlySearchSession(
                context=context,
                session=session,
                created_at=now,
                last_used_at=now,
            )
            self._active_session = active
        active.searches_started += 1
        active.last_used_at = now
        return _ReadOnlySearchSessionLease(
            context=active.context,
            session=active.session,
            created_at=active.created_at,
            searches_started=active.searches_started,
            persistent=True,
            reused=reused,
        )

    async def _discard_active_session(self) -> None:
        active = self._active_session
        self._active_session = None
        if active is not None:
            await self._cleanup(active.context.__aexit__(*sys.exc_info()))

    @property
    def _session_reuse_enabled(self) -> bool:
        return self._session_reuse_ttl_seconds > 0 and self._session_reuse_max_searches > 1

    async def _assert_identity(
        self,
        session: KorailPydollReadOnlySearchSession,
        request: BrowserSeatSearchRequest,
        stage: str,
    ) -> None:
        origin = normalize_korail_station(await session.current_station("departure"))
        destination = normalize_korail_station(await session.current_station("arrival"))
        selected_date, selected_hour = await session.current_schedule()
        passenger = " ".join((await session.current_passenger()).split())
        origin_matches = origin == request.origin
        destination_matches = destination == request.destination
        departure_date_matches = selected_date == request.travel_date
        departure_hour_matches = selected_hour == request.departure_from.hour
        passenger_matches = passenger == "총 1명"
        if not all(
            (
                origin_matches,
                destination_matches,
                departure_date_matches,
                departure_hour_matches,
                passenger_matches,
            )
        ):
            self._event_logger.warning(
                "KORAIL Pydoll identity mismatch stage=%s origin=%s destination=%s "
                "date=%s hour=%s passenger=%s",
                stage,
                origin_matches,
                destination_matches,
                departure_date_matches,
                departure_hour_matches,
                passenger_matches,
            )
            raise BrowserSourceUnavailable(stage)

    async def _assert_result_identity(
        self,
        session: KorailPydollReadOnlySearchSession,
        request: BrowserSeatSearchRequest,
    ) -> None:
        selected_date, selected_hour = await session.current_schedule()
        passenger = " ".join((await session.current_passenger()).split())
        if (
            selected_date != request.travel_date
            or selected_hour != request.departure_from.hour
            or passenger != "총 1명"
        ):
            self._event_logger.warning(
                "KORAIL Pydoll result identity mismatch date=%s hour=%s passenger=%s",
                selected_date == request.travel_date,
                selected_hour == request.departure_from.hour,
                passenger == "총 1명",
            )
            raise BrowserSourceUnavailable("result_identity_check")

    @staticmethod
    def read_result(
        snapshot: PydollPageSnapshot,
        request: BrowserSeatSearchRequest,
    ) -> BrowserSeatSearchResult:
        trains: list[BrowserTrainSnapshot] = []
        for row in snapshot.rows:
            train_type = parse_official_train_type(row.kind_text)
            if train_type is None:
                continue
            route = KORAIL_ROUTE_HEADING.match(" ".join(row.route_text.split()))
            if route is None:
                raise BrowserSourceUnavailable("read_result")
            if (
                normalize_korail_station(route.group(1)) != request.origin
                or normalize_korail_station(route.group(2)) != request.destination
            ):
                raise BrowserSourceUnavailable("read_result")
            departure_time = clock_time.fromisoformat(route.group(3))
            if not request.departure_from <= departure_time <= request.departure_to:
                continue
            arrival_time = clock_time.fromisoformat(route.group(4))
            if len(row.seats) != 2:
                raise BrowserSourceUnavailable("read_result")
            standard = status_from_seat_box(row.seats[0].text, set(row.seats[0].classes))
            first = status_from_seat_box(row.seats[1].text, set(row.seats[1].classes))
            if standard is None or first is None:
                raise BrowserSourceUnavailable("read_result")
            departure_at, arrival_at = service_datetimes(
                request.travel_date,
                departure_time,
                arrival_time,
            )
            try:
                train_number = normalize_korail_train_number(row.train_number)
            except ValueError as error:
                raise BrowserSourceUnavailable("read_result") from error
            trains.append(
                BrowserTrainSnapshot(
                    train_number=train_number,
                    train_type=train_type,
                    departure_at=departure_at,
                    arrival_at=arrival_at,
                    adult_fare=parse_unambiguous_adult_fare(row.seats[0].text),
                    standard=standard,
                    first=first,
                    expected_delay_minutes=parse_expected_delay_minutes(row.full_text),
                )
            )
        if not trains:
            raise BrowserSourceUnavailable("read_result")
        return BrowserSeatSearchResult(
            origin=request.origin,
            destination=request.destination,
            travel_date=request.travel_date,
            passenger_count=1,
            observed_at=datetime.now(UTC),
            trains=trains,
        )
