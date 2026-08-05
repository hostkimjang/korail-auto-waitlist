from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import os
import re
import time
from collections.abc import AsyncIterable, Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from datetime import time as clock_time
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, Protocol, Self
from urllib.parse import urlsplit

from .korail_browser_automation import (
    FULLSTACK_E2E_PAGE_URL,
    OFFICIAL_KORAIL_SEARCH_URL,
    BrowserProtectionDetected,
    BrowserRateLimited,
    BrowserSeatSearchRequest,
    BrowserSeatSearchResult,
    BrowserSourceUnavailable,
    BrowserTrainSnapshot,
    is_rate_limit_response,
    parse_expected_delay_minutes,
    parse_official_train_type,
    parse_unambiguous_adult_fare,
    protection_trigger_from_http_response,
    protection_trigger_from_text,
    service_datetimes,
    status_from_seat_box,
)
from .korail_http_replay import (
    HttpReplayInvalidCapture,
    KorailHttpReplayClient,
    KorailHttpReplayPlan,
    build_http_replay_plan,
)
from .korail_pydoll_confirmation_reader import (
    _parse_korail_payment_deadline,
    read_korail_same_session_confirmation,
)
from .korail_pydoll_http_replay import (
    DEFAULT_HTTP_REPLAY_ROUTE_CACHE_SIZE,
    PydollHttpReplayManager,
)
from .korail_reservation_confirmation import KorailSameSessionDetailEvidence
from .korail_reservation_controls import booking_seat_control_key
from .korail_search_bootstrap import (
    KorailStationIdentityResolver,
    KorailStationIdentityUnavailable,
    build_korail_general_search_url,
    validate_korail_general_search_url,
)
from .reservation_confirmation import ReservationConfirmationTarget

_ROUTE_HEADING = re.compile(
    r"^(.+?)\s*→\s*(.+?)\s*\(\s*(\d{2}:\d{2})\s*~\s*(\d{2}:\d{2})\s*\)"
    r"(?:\s*소요시간\s*:\s*.+)?$"
)
_GENERIC_PROTECTION_TRIGGERS = frozenset({"marker_abnormal_access", "marker_unauthorized_tool"})
_MAX_MORE_RESULT_ACTIONS = 19
# Compatibility seam: focused tests patch this facade value before construction;
# the canonical manager receives that value and owns the actual eviction behavior.
_HTTP_REPLAY_ROUTE_CACHE_SIZE = DEFAULT_HTTP_REPLAY_ROUTE_CACHE_SIZE
_PROTECTION_SURFACE_SELECTOR = (
    '[role="alert"], dialog[open], [aria-modal="true"], .alert, .error, .popup, .modal'
)
_KORAIL_RESERVATION_LIST_URL = "https://www.korail.com/ticket/reservation/list"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PydollSeatBox:
    text: str
    classes: frozenset[str]


@dataclass(frozen=True)
class PydollTrainRow:
    kind_text: str
    train_number: str
    route_text: str
    seats: tuple[PydollSeatBox, ...]
    full_text: str = ""


@dataclass(frozen=True)
class PydollPageSnapshot:
    body_text: str
    rows: tuple[PydollTrainRow, ...]
    protection_texts: tuple[str, ...] = ()
    network_responses: tuple[tuple[int, str], ...] = ()
    url: str = ""
    title: str = ""
    reservation_rows: tuple[str, ...] = ()


class KorailReservationSeatClass(StrEnum):
    GENERAL = "general"
    SPECIAL = "special"

    @property
    def label(self) -> str:
        return "일반실" if self is self.GENERAL else "특실"


class KorailLoginMethod(StrEnum):
    MEMBERSHIP_NUMBER = "membership_number"
    EMAIL = "email"
    PHONE = "phone"

    @property
    def tab_selector(self) -> str:
        return {
            self.MEMBERSHIP_NUMBER: "button#memberNo[type='button']",
            self.EMAIL: "button#email[type='button']",
            self.PHONE: "button#phone[type='button']",
        }[self]

    @property
    def identity_selector(self) -> str:
        return {
            self.MEMBERSHIP_NUMBER: (
                "input#id[name='id'][type='text'][title='회원번호'][maxlength='10']"
            ),
            self.EMAIL: "input#id[name='id'][type='email'][title='이메일 주소']",
            self.PHONE: ("input#id[name='id'][type='text'][title='휴대폰 번호'][maxlength='11']"),
        }[self]


class KorailReservationOutcome(StrEnum):
    PAYMENT_REQUIRED = "payment_required"
    AUTH_REQUIRED = "auth_required"
    CONSENT_REQUIRED = "consent_required"
    ACTION_REQUIRED = "action_required"
    PROVIDER_BLOCKED = "provider_blocked"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class KorailSessionActorState(StrEnum):
    COLD = "cold"
    AUTHENTICATING = "authenticating"
    READY = "ready"
    STALE = "stale"
    AUTH_REQUIRED = "auth_required"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class KorailSessionActorSnapshot:
    state: KorailSessionActorState
    credential_generation: str | None
    created_at_monotonic: float | None
    last_verified_at_monotonic: float | None
    last_used_at_monotonic: float | None
    local_reuse_until_monotonic: float | None
    locally_reusable: bool


@dataclass(frozen=True, repr=False)
class KorailCredentialInput:
    login_id: str = field(repr=False)
    password: str = field(repr=False)
    version: str
    login_method: KorailLoginMethod = KorailLoginMethod.MEMBERSHIP_NUMBER


def _credential_fingerprint(credential: KorailCredentialInput) -> bytes:
    """Return a domain-separated, unambiguous digest without retaining credential text."""

    digest = hashlib.sha256(b"rail-waitlist:korail-pydoll-credential:v1\0")
    for value in (
        credential.login_method.value,
        credential.login_id,
        credential.password,
    ):
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.digest()


@dataclass(frozen=True)
class KorailReservationRequest:
    origin: str
    destination: str
    travel_date: date
    train_number: str
    train_type: str | None
    departure_time: clock_time
    arrival_time: clock_time
    seat_class: KorailReservationSeatClass
    credential: KorailCredentialInput = field(repr=False)


@dataclass(frozen=True)
class KorailReservationResult:
    outcome: KorailReservationOutcome
    reason: str
    seat_clicked: bool = False
    reservation_clicked: bool = False
    session_ready_at: datetime | None = None
    target_rechecked_at: datetime | None = None
    seat_selected_at: datetime | None = None
    reservation_requested_at: datetime | None = None


@dataclass(frozen=True)
class _ControlState:
    enabled: bool
    aria_disabled: str
    disabled_attribute: bool
    classes: tuple[str, ...]
    container_classes: tuple[str, ...]
    slide_classes: tuple[str, ...]
    read_error: bool = False


@dataclass
class _ReservationAttemptState:
    """One-shot latches whose loss could repeat an official booking action."""

    login_attempted: bool = False
    pre_login_route_check_attempted: bool = False
    pre_login_route_authenticated: bool = False
    post_submit_check_attempted: bool = False
    post_submit_authenticated: bool = False
    preserved_selection_checked: bool = False
    preserved_selection_matches: bool = False
    reservation_clicked: bool = False


@dataclass(frozen=True)
class _HourCandidate:
    element: Any
    hour: int
    state: _ControlState


class PydollBrowserSession(Protocol):
    async def open(self) -> PydollPageSnapshot: ...

    async def navigate(self, url: str) -> PydollPageSnapshot: ...

    async def navigate_fresh(self, url: str) -> PydollPageSnapshot: ...

    async def choose_station(self, kind: str, station: str) -> None: ...

    async def choose_schedule(self, travel_date: date, departure_hour: int) -> None: ...

    async def current_station(self, kind: str) -> str: ...

    async def current_schedule(self) -> tuple[date, int]: ...

    async def current_passenger(self) -> str: ...

    async def ensure_authenticated(self, credential: KorailCredentialInput) -> bool: ...

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

    async def reserve_once(
        self,
        request: KorailReservationRequest,
    ) -> KorailReservationResult: ...

    async def read_reservation_list(self) -> PydollPageSnapshot: ...

    async def _snapshot(self) -> PydollPageSnapshot: ...

    async def _probe_official_authenticated_session(self) -> bool: ...

    async def _has_authenticated_header(self) -> bool: ...


class PydollSessionContext(Protocol):
    async def __aenter__(self) -> PydollBrowserSession: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> bool | None: ...


PydollSessionFactory = Callable[[str, int, bool], PydollSessionContext]


def _default_pydoll_session_factory(
    page_url: str,
    timeout_ms: int,
    headless: bool,
) -> PydollSessionContext:
    return _PydollSessionContext(_PydollSession(page_url, timeout_ms, headless))


@dataclass
class _ActivePydollSession:
    context: PydollSessionContext
    session: PydollBrowserSession
    created_at: float
    last_used_at: float
    searches_started: int = 0
    credential_version: str | None = None
    authenticated_credential_version: str | None = None
    authenticated_credential_fingerprint: bytes | None = field(default=None, repr=False)


@dataclass(frozen=True)
class _PydollSessionLease:
    context: PydollSessionContext
    session: PydollBrowserSession
    created_at: float
    searches_started: int
    persistent: bool
    reused: bool
    authenticated: bool


async def _finish_owned_cleanup(cleanup: Awaitable[object]) -> None:
    """Finish Chromium cleanup even when the owning task is cancelled repeatedly."""
    pending_cancellation: asyncio.CancelledError | None = None
    cleanup_task: asyncio.Future[object] = asyncio.ensure_future(cleanup)
    while not cleanup_task.done():
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError as error:
            if pending_cancellation is None:
                pending_cancellation = error
    cleanup_task.result()
    if pending_cancellation is not None:
        raise pending_cancellation


async def probe_pydoll_chromium() -> None:
    """Start and close Pydoll Chromium without making an external request."""
    try:
        from pydoll.browser import Chrome
        from pydoll.browser.options import ChromiumOptions
    except ImportError as error:
        raise BrowserSourceUnavailable("browser_import") from error
    browser: Any = None
    try:
        chromium_options_factory: Any = ChromiumOptions
        options = chromium_options_factory()
        options.headless = True
        _set_chromium_binary(options)
        browser = Chrome(options=options)
        await browser.__aenter__()
        await browser.start()
    except BrowserSourceUnavailable:
        raise
    except Exception as error:
        raise BrowserSourceUnavailable("browser_launch") from error
    finally:
        if browser is not None:
            try:
                await browser.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001 -- optional backend exceptions are not stable.
                try:
                    await browser.stop()
                except Exception:  # noqa: BLE001 -- optional backend exceptions are not stable.
                    await browser.close()


class PydollKorailBrowserClient:
    """Runs official KORAIL searches through Pydoll's raw-CDP browser API."""

    def __init__(
        self,
        *,
        page_url: str = OFFICIAL_KORAIL_SEARCH_URL,
        timeout_seconds: float = 25,
        headless: bool = True,
        allow_test_loopback: bool = False,
        allow_fullstack_fixture: bool = False,
        session_factory: PydollSessionFactory | None = None,
        session_reuse_ttl_seconds: float = 0,
        session_reuse_max_searches: int = 1,
        station_identity_resolver: KorailStationIdentityResolver | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if session_reuse_ttl_seconds < 0:
            raise ValueError("session_reuse_ttl_seconds must be non-negative")
        if session_reuse_max_searches < 1:
            raise ValueError("session_reuse_max_searches must be at least 1")
        self.page_url = page_url
        self.timeout_ms = int(timeout_seconds * 1000)
        self.headless = headless
        self._validate_page_url(allow_test_loopback, allow_fullstack_fixture)
        self._session_factory: PydollSessionFactory = (
            session_factory or _default_pydoll_session_factory
        )
        self._session_reuse_ttl_seconds = session_reuse_ttl_seconds
        self._session_reuse_max_searches = session_reuse_max_searches
        self._station_identity_resolver = station_identity_resolver
        self._monotonic = monotonic
        self._session_lock = asyncio.Lock()
        self._search_lock = asyncio.Lock()
        self._active_session: _ActivePydollSession | None = None
        self._active_search_session: _ActivePydollSession | None = None
        self._session_actor_state = KorailSessionActorState.COLD
        self._session_actor_generation: str | None = None
        self._session_actor_created_at: float | None = None
        self._session_actor_last_verified_at: float | None = None
        self._session_actor_last_used_at: float | None = None
        # Capture the module-level factory at construction time. Existing focused tests
        # replace this name before creating the facade, and production never exposes the
        # replay client's captured cookies or request material.
        self._http_replay_manager = PydollHttpReplayManager(
            timeout_seconds=max(1, self.timeout_ms / 1000),
            reuse_ttl_seconds=self._session_reuse_ttl_seconds,
            reuse_max_searches=self._session_reuse_max_searches,
            route_cache_size=_HTTP_REPLAY_ROUTE_CACHE_SIZE,
            monotonic=self._monotonic,
            client_factory=KorailHttpReplayClient,
            cleanup=_finish_owned_cleanup,
            event_logger=logger,
        )

    def _validate_page_url(
        self,
        allow_test_loopback: bool,
        allow_fullstack_fixture: bool,
    ) -> None:
        parsed = urlsplit(self.page_url)
        official = (
            parsed.scheme == "https"
            and parsed.hostname == "www.korail.com"
            and parsed.port in {None, 443}
            and parsed.username is None
            and parsed.password is None
            and parsed.path == "/ticket/search/general"
            and not parsed.query
            and not parsed.fragment
        )
        if official:
            return
        if allow_fullstack_fixture and self.page_url == FULLSTACK_E2E_PAGE_URL:
            return
        if allow_test_loopback and parsed.scheme == "http":
            try:
                if ipaddress.ip_address(parsed.hostname or "").is_loopback:
                    return
            except ValueError:
                if parsed.hostname == "localhost":
                    return
        raise ValueError("Pydoll browser page URL must be the official KORAIL HTTPS host")

    async def search(self, request: BrowserSeatSearchRequest) -> BrowserSeatSearchResult:
        if request.passenger_count != 1:
            raise BrowserSourceUnavailable("passenger_count_not_supported")
        async with self._search_lock:
            replayed = await self._http_replay_manager.try_search(request)
            if replayed is not None:
                return replayed
            direct_url = await self._direct_search_url(
                request.origin,
                request.destination,
                request.travel_date,
                request.departure_from,
            )
            cold_recovery_used = False
            while True:
                stage = "browser_launch"
                lease: _PydollSessionLease | None = None
                try:
                    lease = await self._acquire_search_session()
                    session = lease.session
                    if direct_url is None:
                        stage = "load_page"
                        self._assert_response_allowed(await session.open(), stage)
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
                        capture_started = (
                            False
                            if lease.authenticated
                            else await self._http_replay_manager.begin_capture(session)
                        )
                        stage = "submit_search"
                        await session.submit_once()
                    else:
                        # Navigation itself starts the one official business lookup.
                        # Capture first, and never retry through the UI after this point.
                        capture_started = await self._http_replay_manager.begin_capture(session)
                        stage = "direct_navigation"
                        self._assert_response_allowed(await session.navigate(direct_url), stage)
                    stage = "wait_result"
                    snapshot = await session.wait_for_result()
                    self._assert_response_allowed(snapshot, stage)
                    stage = "expand_results"
                    snapshot = await session.expand_results(snapshot, _MAX_MORE_RESULT_ACTIONS)
                    self._assert_response_allowed(snapshot, stage)
                    stage = "result_identity_check"
                    await self._assert_result_identity(session, request)
                    stage = "read_result"
                    result = self._read_result(snapshot, request).model_copy(
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
                                await self._discard_active_search_session()
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
                        await self._discard_active_search_session()
                    raise
                except (BrowserProtectionDetected, BrowserRateLimited):
                    if lease is not None and lease.persistent:
                        await self._discard_active_search_session()
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
                        await self._discard_active_search_session()
                    if should_reinitialize:
                        # A reused browser context can outlive the official page's
                        # in-memory search/session state. The failure happened before
                        # submit, so a single cold initialization does not duplicate an
                        # upstream train-search request.
                        logger.info(
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
                        await self._discard_active_search_session()
                    raise BrowserSourceUnavailable(stage) from error
                finally:
                    if lease is not None and not lease.persistent:
                        await lease.context.__aexit__(None, None, None)

    async def reserve_once(
        self,
        request: KorailReservationRequest,
    ) -> KorailReservationResult:
        """Run one exact booking attempt and stop before every payment action."""

        direct_url = await self._direct_search_url(
            request.origin,
            request.destination,
            request.travel_date,
            request.departure_time,
        )
        async with self._session_lock:
            # Detached timetable replay belongs to the read-only search actor.
            # Reservation never consumes or retires it and therefore cannot wait for
            # an in-flight timetable search before acting on the authenticated session.
            active = self._active_session
            credential_fingerprint = _credential_fingerprint(request.credential)
            if active is not None and (
                (
                    active.credential_version is not None
                    and active.credential_version != request.credential.version
                )
                or (
                    active.authenticated_credential_version is not None
                    and active.authenticated_credential_fingerprint != credential_fingerprint
                )
            ):
                self._session_actor_state = KorailSessionActorState.STALE
                await self._discard_active_session()

            lease: _PydollSessionLease | None = None
            stage = "browser_launch"
            seat_clicked = False
            reservation_clicked = False
            session_ready_at: datetime | None = None
            try:
                lease = await self._acquire_session(
                    credential_version=request.credential.version,
                )
                session = lease.session
                warm_direct_navigation = direct_url is not None and lease.authenticated
                if not warm_direct_navigation:
                    stage = "load_page"
                    self._assert_response_allowed(await session.open(), stage)
                stage = "authenticate"
                if not await self._ensure_authenticated_session(session, request.credential):
                    return KorailReservationResult(
                        outcome=KorailReservationOutcome.AUTH_REQUIRED,
                        reason="authentication_required",
                    )
                session_ready_at = datetime.now(UTC)
                if direct_url is not None and lease.authenticated:
                    # Preserve a fresh document/history boundary for every attempt while
                    # avoiding the redundant public search-page navigation. The new tab
                    # stays in the authenticated Chromium context; no cookie, captured
                    # template, or reservation payload crosses into application code.
                    stage = "direct_navigation"
                    self._assert_response_allowed(await session.navigate_fresh(direct_url), stage)
                elif direct_url is None:
                    stage = "choose_origin"
                    await session.choose_station("departure", request.origin)
                    stage = "choose_destination"
                    await session.choose_station("arrival", request.destination)
                    stage = "choose_departure"
                    await session.choose_schedule(request.travel_date, request.departure_time.hour)
                    stage = "pre_submit_identity_check"
                    await self._assert_reservation_identity(session, request, stage)
                    stage = "submit_search"
                    await session.submit_once()
                else:
                    stage = "direct_navigation"
                    self._assert_response_allowed(await session.navigate(direct_url), stage)
                stage = "wait_result"
                snapshot = await session.wait_for_result()
                self._assert_response_allowed(snapshot, stage)
                if not _snapshot_has_unique_reservation_target(snapshot, request):
                    stage = "expand_results"
                    snapshot = await session.expand_results(snapshot, _MAX_MORE_RESULT_ACTIONS)
                    self._assert_response_allowed(snapshot, stage)
                stage = "reserve_once"
                result = await session.reserve_once(request)
                seat_clicked = result.seat_clicked
                reservation_clicked = result.reservation_clicked
                if result.outcome in {
                    KorailReservationOutcome.AUTH_REQUIRED,
                    KorailReservationOutcome.PROVIDER_BLOCKED,
                }:
                    await self._discard_active_session()
                    self._session_actor_state = (
                        KorailSessionActorState.AUTH_REQUIRED
                        if result.outcome is KorailReservationOutcome.AUTH_REQUIRED
                        else KorailSessionActorState.BLOCKED
                    )
                return replace(result, session_ready_at=session_ready_at)
            except asyncio.CancelledError:
                await self._discard_active_session()
                self._session_actor_state = KorailSessionActorState.STALE
                raise
            except (BrowserProtectionDetected, BrowserRateLimited):
                await self._discard_active_session()
                self._session_actor_state = KorailSessionActorState.BLOCKED
                return KorailReservationResult(
                    outcome=KorailReservationOutcome.PROVIDER_BLOCKED,
                    reason="provider_access_restricted",
                    seat_clicked=seat_clicked,
                    reservation_clicked=reservation_clicked,
                    session_ready_at=session_ready_at,
                )
            except BrowserSourceUnavailable:
                # An uncertain result after the reservation button is never retried.
                return KorailReservationResult(
                    outcome=KorailReservationOutcome.FAILED,
                    reason=f"source_unavailable:{stage}",
                    seat_clicked=seat_clicked,
                    reservation_clicked=reservation_clicked,
                    session_ready_at=session_ready_at,
                )
            except Exception:  # noqa: BLE001 -- browser backend errors are intentionally opaque.
                return KorailReservationResult(
                    outcome=KorailReservationOutcome.FAILED,
                    reason=f"browser_error:{stage}",
                    seat_clicked=seat_clicked,
                    reservation_clicked=reservation_clicked,
                    session_ready_at=session_ready_at,
                )
            finally:
                if lease is not None and not lease.persistent:
                    await lease.context.__aexit__(None, None, None)

    async def _direct_search_url(
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

    async def verify_credentials(self, credential: KorailCredentialInput) -> bool:
        """Authenticate once without submitting a timetable search or reservation."""

        async with self._session_lock:
            # Account editing must prove the newly supplied credential. A fresh
            # context prevents an already-authenticated reusable session from
            # accepting a different ID/password merely because logout is visible.
            if self._active_session is not None:
                self._session_actor_state = KorailSessionActorState.STALE
            await self._discard_active_session()

            lease: _PydollSessionLease | None = None
            stage = "browser_launch"
            try:
                lease = await self._acquire_session(credential_version=credential.version)
                session = lease.session
                stage = "load_page"
                self._assert_response_allowed(await session.open(), stage)
                stage = "authenticate"
                return await self._ensure_authenticated_session(session, credential)
            except asyncio.CancelledError:
                await self._discard_active_session()
                self._session_actor_state = KorailSessionActorState.STALE
                raise
            except (BrowserProtectionDetected, BrowserRateLimited):
                await self._discard_active_session()
                self._session_actor_state = KorailSessionActorState.BLOCKED
                raise
            except BrowserSourceUnavailable as error:
                await self._discard_active_session()
                self._session_actor_state = KorailSessionActorState.STALE
                if error.stage == "unspecified":
                    raise BrowserSourceUnavailable(stage) from error
                raise
            except Exception as error:
                await self._discard_active_session()
                self._session_actor_state = KorailSessionActorState.STALE
                raise BrowserSourceUnavailable(stage) from error
            finally:
                if lease is not None and not lease.persistent:
                    await lease.context.__aexit__(None, None, None)

    async def read_reservation_detail(
        self,
        target: ReservationConfirmationTarget,
    ) -> KorailSameSessionDetailEvidence:
        """Read exact hold evidence while preserving the active auth-session generation."""

        async with self._session_lock:
            active = self._active_session
            if active is None or active.authenticated_credential_version is None:
                return KorailSameSessionDetailEvidence(
                    observed_at=datetime.now(UTC),
                    credential_version=None,
                    exact_identity_matched=False,
                    payment_pending_markers_present=False,
                )
            credential_version = (
                int(active.authenticated_credential_version)
                if active.authenticated_credential_version.isdigit()
                else None
            )
            return await read_korail_same_session_confirmation(
                session=active.session,
                target=target,
                credential_version=credential_version,
                payment_deadline_parser=_parse_korail_payment_deadline,
            )

    async def prewarm_credentials(self, credential: KorailCredentialInput) -> bool:
        """Create or reuse a locally valid authenticated session without booking work."""

        async with self._session_lock:
            active = self._active_session
            fingerprint = _credential_fingerprint(credential)
            now = self._monotonic()
            if (
                active is not None
                and active.authenticated_credential_version == credential.version
                and active.authenticated_credential_fingerprint == fingerprint
                and now - active.last_used_at < self._session_reuse_ttl_seconds
                and active.searches_started < self._session_reuse_max_searches
                and self._session_actor_state is KorailSessionActorState.READY
            ):
                active.last_used_at = now
                self._session_actor_last_used_at = now
                return True

        return await self.verify_credentials(credential)

    def session_snapshot(self) -> KorailSessionActorSnapshot:
        """Return non-secret process-local authentication actor telemetry."""

        now = self._monotonic()
        active = self._active_session
        state = self._session_actor_state
        local_reuse_until = None
        locally_reusable = False
        if active is not None and active.authenticated_credential_version is not None:
            local_reuse_until = active.last_used_at + self._session_reuse_ttl_seconds
            locally_reusable = (
                self._session_reuse_enabled
                and now < local_reuse_until
                and active.searches_started < self._session_reuse_max_searches
                and state is KorailSessionActorState.READY
            )
            if state is KorailSessionActorState.READY and not locally_reusable:
                state = KorailSessionActorState.STALE
        return KorailSessionActorSnapshot(
            state=state,
            credential_generation=self._session_actor_generation,
            created_at_monotonic=self._session_actor_created_at,
            last_verified_at_monotonic=self._session_actor_last_verified_at,
            last_used_at_monotonic=self._session_actor_last_used_at,
            local_reuse_until_monotonic=local_reuse_until,
            locally_reusable=locally_reusable,
        )

    async def close(self) -> None:
        """Close the bounded in-memory browser sessions owned by this client."""

        # The only operation that needs both actor locks always takes authenticated
        # session -> read-only search. No search or authentication path takes the
        # other actor's lock, so shutdown cannot form a lock-order cycle.
        async with self._session_lock, self._search_lock:
            await self._http_replay_manager.discard()
            await self._discard_active_search_session()
            await self._discard_active_session()
            self._session_actor_state = KorailSessionActorState.COLD

    @property
    def _active_http_replays(self) -> Mapping[tuple[str, str], object]:
        """Read-only compatibility inspection for focused replay lifecycle tests."""

        return self._http_replay_manager.active_leases

    async def _acquire_session(
        self,
        *,
        credential_version: str | None = None,
    ) -> _PydollSessionLease:
        if not self._session_reuse_enabled:
            created_at = self._monotonic()
            context = self._session_factory(self.page_url, self.timeout_ms, self.headless)
            session = await context.__aenter__()
            return _PydollSessionLease(
                context=context,
                session=session,
                created_at=created_at,
                searches_started=1,
                persistent=False,
                reused=False,
                authenticated=False,
            )

        now = self._monotonic()
        active = self._active_session
        if active is not None and (
            now - active.last_used_at >= self._session_reuse_ttl_seconds
            or active.searches_started >= self._session_reuse_max_searches
        ):
            if active.authenticated_credential_version is not None:
                self._session_actor_state = KorailSessionActorState.STALE
            await self._discard_active_session()
            active = None
        reused = active is not None
        if active is None:
            context = self._session_factory(self.page_url, self.timeout_ms, self.headless)
            session = await context.__aenter__()
            active = _ActivePydollSession(
                context=context,
                session=session,
                created_at=now,
                last_used_at=now,
                credential_version=credential_version,
            )
            self._active_session = active
            if credential_version is not None:
                self._session_actor_generation = credential_version
                self._session_actor_created_at = now
                self._session_actor_last_used_at = now
        elif credential_version is not None and active.credential_version is None:
            active.credential_version = credential_version
            self._session_actor_generation = credential_version
            self._session_actor_created_at = active.created_at
        active.searches_started += 1
        active.last_used_at = now
        if credential_version is not None:
            self._session_actor_last_used_at = now
        return _PydollSessionLease(
            context=active.context,
            session=active.session,
            created_at=active.created_at,
            searches_started=active.searches_started,
            persistent=True,
            reused=reused,
            authenticated=active.authenticated_credential_version is not None,
        )

    async def _acquire_search_session(self) -> _PydollSessionLease:
        """Lease a browser owned exclusively by the read-only search actor."""

        if not self._session_reuse_enabled:
            created_at = self._monotonic()
            context = self._session_factory(self.page_url, self.timeout_ms, self.headless)
            session = await context.__aenter__()
            return _PydollSessionLease(
                context=context,
                session=session,
                created_at=created_at,
                searches_started=1,
                persistent=False,
                reused=False,
                authenticated=False,
            )

        now = self._monotonic()
        active = self._active_search_session
        if active is not None and (
            now - active.last_used_at >= self._session_reuse_ttl_seconds
            or active.searches_started >= self._session_reuse_max_searches
        ):
            await self._discard_active_search_session()
            active = None
        reused = active is not None
        if active is None:
            context = self._session_factory(self.page_url, self.timeout_ms, self.headless)
            session = await context.__aenter__()
            active = _ActivePydollSession(
                context=context,
                session=session,
                created_at=now,
                last_used_at=now,
            )
            self._active_search_session = active
        active.searches_started += 1
        active.last_used_at = now
        return _PydollSessionLease(
            context=active.context,
            session=active.session,
            created_at=active.created_at,
            searches_started=active.searches_started,
            persistent=True,
            reused=reused,
            authenticated=False,
        )

    async def _ensure_authenticated_session(
        self,
        session: PydollBrowserSession,
        credential: KorailCredentialInput,
    ) -> bool:
        """Authenticate a persistent browser once per credential version and fingerprint.

        The caller holds ``_session_lock``. Authentication material stays in the browser
        context only; this state records a version, a one-way in-memory credential digest,
        and monotonic timestamps, not an ID, password, cookie, or browser storage value.
        """

        active = self._active_session
        if active is None or active.session is not session:
            now = self._monotonic()
            self._session_actor_state = KorailSessionActorState.AUTHENTICATING
            self._session_actor_generation = credential.version
            self._session_actor_created_at = now
            self._session_actor_last_used_at = now
            authenticated = await session.ensure_authenticated(credential)
            if authenticated:
                verified_at = self._monotonic()
                self._session_actor_last_verified_at = verified_at
                self._session_actor_last_used_at = verified_at
            self._session_actor_state = (
                KorailSessionActorState.STALE
                if authenticated
                else KorailSessionActorState.AUTH_REQUIRED
            )
            return authenticated
        credential_fingerprint = _credential_fingerprint(credential)
        if (
            active.authenticated_credential_version == credential.version
            and active.authenticated_credential_fingerprint == credential_fingerprint
        ):
            active.last_used_at = self._monotonic()
            self._session_actor_last_used_at = active.last_used_at
            self._session_actor_state = KorailSessionActorState.READY
            return True
        self._session_actor_state = KorailSessionActorState.AUTHENTICATING
        self._session_actor_generation = credential.version
        authenticated = await session.ensure_authenticated(credential)
        if not authenticated:
            await self._discard_active_session()
            self._session_actor_state = KorailSessionActorState.AUTH_REQUIRED
            return False
        active.authenticated_credential_version = credential.version
        active.authenticated_credential_fingerprint = credential_fingerprint
        active.last_used_at = self._monotonic()
        self._session_actor_last_verified_at = active.last_used_at
        self._session_actor_last_used_at = active.last_used_at
        self._session_actor_state = KorailSessionActorState.READY
        return True

    async def _discard_active_session(self) -> None:
        active = self._active_session
        self._active_session = None
        if active is not None:
            await _finish_owned_cleanup(active.context.__aexit__(None, None, None))

    async def _discard_active_search_session(self) -> None:
        active = self._active_search_session
        self._active_search_session = None
        if active is not None:
            await _finish_owned_cleanup(active.context.__aexit__(None, None, None))

    @property
    def _session_reuse_enabled(self) -> bool:
        return self._session_reuse_ttl_seconds > 0 and self._session_reuse_max_searches > 1

    @staticmethod
    async def _assert_identity(
        session: PydollBrowserSession,
        request: BrowserSeatSearchRequest,
        stage: str,
    ) -> None:
        origin = _normalize_station(await session.current_station("departure"))
        destination = _normalize_station(await session.current_station("arrival"))
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
            logger.warning(
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

    @staticmethod
    async def _assert_reservation_identity(
        session: PydollBrowserSession,
        request: KorailReservationRequest,
        stage: str,
    ) -> None:
        origin = _normalize_station(await session.current_station("departure"))
        destination = _normalize_station(await session.current_station("arrival"))
        selected_date, selected_hour = await session.current_schedule()
        passenger = " ".join((await session.current_passenger()).split())
        if not all(
            (
                origin == request.origin,
                destination == request.destination,
                selected_date == request.travel_date,
                selected_hour == request.departure_time.hour,
                passenger == "총 1명",
            )
        ):
            raise BrowserSourceUnavailable(stage)

    @staticmethod
    async def _assert_result_identity(
        session: PydollBrowserSession,
        request: BrowserSeatSearchRequest,
    ) -> None:
        selected_date, selected_hour = await session.current_schedule()
        passenger = " ".join((await session.current_passenger()).split())
        if (
            selected_date != request.travel_date
            or selected_hour != request.departure_from.hour
            or passenger != "총 1명"
        ):
            logger.warning(
                "KORAIL Pydoll result identity mismatch date=%s hour=%s passenger=%s",
                selected_date == request.travel_date,
                selected_hour == request.departure_from.hour,
                passenger == "총 1명",
            )
            raise BrowserSourceUnavailable("result_identity_check")

    @staticmethod
    def _assert_response_allowed(snapshot: PydollPageSnapshot, stage: str) -> None:
        for status, resource_type in snapshot.network_responses:
            if is_rate_limit_response(status, resource_type):
                raise BrowserRateLimited()
            trigger = protection_trigger_from_http_response(status, resource_type)
            if trigger == "http_403_main":
                _log_protection_snapshot(snapshot, stage, trigger)
                raise BrowserProtectionDetected(trigger, stage)
        trigger = protection_trigger_from_text(snapshot.body_text)
        if trigger is None:
            return
        if trigger not in _GENERIC_PROTECTION_TRIGGERS:
            _log_protection_snapshot(snapshot, stage, trigger)
            raise BrowserProtectionDetected(trigger, stage)
        if (
            any(
                protection_trigger_from_text(text) in _GENERIC_PROTECTION_TRIGGERS
                for text in snapshot.protection_texts
            )
            or not snapshot.rows
        ):
            _log_protection_snapshot(snapshot, stage, trigger)
            raise BrowserProtectionDetected(trigger, stage)

    @staticmethod
    def _read_result(
        snapshot: PydollPageSnapshot,
        request: BrowserSeatSearchRequest,
    ) -> BrowserSeatSearchResult:
        trains: list[BrowserTrainSnapshot] = []
        for row in snapshot.rows:
            train_type = parse_official_train_type(row.kind_text)
            if train_type is None:
                continue
            route = _ROUTE_HEADING.match(" ".join(row.route_text.split()))
            if route is None:
                raise BrowserSourceUnavailable("read_result")
            if (
                _normalize_station(route.group(1)) != request.origin
                or _normalize_station(route.group(2)) != request.destination
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
            trains.append(
                BrowserTrainSnapshot(
                    train_number=_normalize_train_number(row.train_number),
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


class _PydollSession:
    _STATION_NAMES: ClassVar[dict[str, str]] = {
        "departure": "txtGoStart",
        "arrival": "txtGoEnd",
    }
    _STATION_TRIGGERS: ClassVar[dict[str, str]] = {
        "departure": "출발역 선택",
        "arrival": "도착역 선택",
    }

    def __init__(self, page_url: str, timeout_ms: int, headless: bool) -> None:
        self.page_url = page_url
        self.timeout_ms = timeout_ms
        self.headless = headless
        self._browser: Any = None
        self._tab: Any = None
        self._submitted = False
        self._network_callback_id: int | None = None
        self._network_events_enabled_by_session = False
        self._network_responses: set[tuple[int, str]] = set()
        self._opened_once = False
        self._http_capture_start: int | None = None

    async def __aenter__(self) -> Self:
        try:
            from pydoll.browser import Chrome
            from pydoll.browser.options import ChromiumOptions
        except ImportError as error:
            raise BrowserSourceUnavailable("browser_import") from error
        try:
            chromium_options_factory: Any = ChromiumOptions
            options = chromium_options_factory()
            options.headless = self.headless
            _set_chromium_binary(options)
            self._browser = Chrome(options=options)
            await self._browser.__aenter__()
            self._tab = await self._browser.start()
            (
                self._network_callback_id,
                self._network_events_enabled_by_session,
            ) = await self._attach_network_listener(self._tab)
            return self
        except BrowserSourceUnavailable:
            await self._close()
            raise
        except Exception as error:
            await self._close()
            raise BrowserSourceUnavailable("browser_launch") from error

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self._close()

    async def open(self) -> PydollPageSnapshot:
        # A warm browser keeps only the ordinary in-memory Chromium session.  Every
        # lookup gets a fresh tab, starts from the public search page, and performs
        # one fresh submit. Tabs share the normal browser context without exporting
        # cookie or storage values to application code.
        if self._opened_once:
            await self._replace_tab()
        self._opened_once = True
        self._submitted = False
        self._network_responses.clear()
        await self._tab.go_to(self.page_url, timeout=max(1, self.timeout_ms // 1000))
        await self._wait_for_exact_text("button", "열차 조회")
        return await self._snapshot()

    async def navigate(self, url: str) -> PydollPageSnapshot:
        try:
            validate_korail_general_search_url(url)
        except ValueError as error:
            raise BrowserSourceUnavailable("direct_navigation") from error
        self._submitted = True
        self._network_responses.clear()
        await self._tab.go_to(url, timeout=max(1, self.timeout_ms // 1000))
        return await self._snapshot()

    async def navigate_fresh(self, url: str) -> PydollPageSnapshot:
        """Navigate a fresh tab in the current browser context to a validated result URL."""

        try:
            validate_korail_general_search_url(url)
        except ValueError as error:
            raise BrowserSourceUnavailable("direct_navigation") from error
        if self._opened_once:
            await self._replace_tab()
        self._opened_once = True
        return await self.navigate(url)

    async def read_reservation_list(self) -> PydollPageSnapshot:
        """Open the official reservation list and return a read-only snapshot.

        This method deliberately performs navigation only.  It never selects a
        reservation row or clicks payment/cancellation controls.
        """

        self._network_responses.clear()
        await self._tab.go_to(
            _KORAIL_RESERVATION_LIST_URL,
            timeout=max(1, self.timeout_ms // 1000),
        )
        deadline = time.monotonic() + min(self._timeout_seconds, 10)
        last = await self._snapshot()
        while time.monotonic() < deadline:
            path = urlsplit(last.url).path.rstrip("/")
            if path in {"/ticket/login", "/ticket/reservation/list"}:
                return last
            if protection_trigger_from_text(last.body_text) is not None:
                return last
            await asyncio.sleep(0.2)
            last = await self._snapshot()
        return last

    async def _replace_tab(self) -> None:
        old_tab = self._tab
        old_callback_id = self._network_callback_id
        old_enabled_by_session = self._network_events_enabled_by_session
        new_tab = await self._browser.new_tab()
        callback_id, enabled_by_session = await self._attach_network_listener(new_tab)
        self._tab = new_tab
        self._network_callback_id = callback_id
        self._network_events_enabled_by_session = enabled_by_session
        await self._cleanup_tab_listener(
            old_tab,
            old_callback_id,
            old_enabled_by_session,
        )
        await old_tab.close()

    async def _attach_network_listener(self, tab: Any) -> tuple[int, bool]:
        enabled_by_session = False
        if not tab.network_events_enabled:
            await tab.enable_network_events()
            enabled_by_session = True
        from pydoll.protocol.network.events import NetworkEvent

        callback_id = await tab.on(
            NetworkEvent.RESPONSE_RECEIVED,
            self._on_response_received,
        )
        return callback_id, enabled_by_session

    @staticmethod
    async def _cleanup_tab_listener(
        tab: Any,
        callback_id: int | None,
        network_events_enabled_by_session: bool,
    ) -> None:
        if tab is not None and callback_id is not None:
            try:
                await tab.remove_callback(callback_id)
            except Exception:  # noqa: BLE001 -- optional backend exceptions are not stable.
                logger.warning("KORAIL Pydoll network callback cleanup failed")
        if tab is not None and network_events_enabled_by_session:
            try:
                await tab.disable_network_events()
            except Exception:  # noqa: BLE001 -- optional backend exceptions are not stable.
                logger.warning("KORAIL Pydoll network event cleanup failed")

    async def choose_station(self, kind: str, station: str) -> None:
        trigger = await self._find_exact_visible("a", self._STATION_TRIGGERS[kind])
        await trigger.click()
        dialog = await self._wait_for_dialog("기차역 조회")
        try:
            target = await self._find_exact_visible("a", station, scope=dialog)
        except LookupError:
            inputs = await self._visible_elements(
                "input[title='역명을 입력해주세요']", scope=dialog
            )
            if len(inputs) != 1:
                raise BrowserSourceUnavailable("station_search_input") from None
            await inputs[0].clear()
            await inputs[0].type_text(station)
            search = await self._find_exact_visible("button", "검색", scope=dialog)
            await search.click()
            target = await self._wait_for_exact_text("a", station, scope=dialog)
        await target.click()
        await self._wait_for_value(f"input[name='{self._STATION_NAMES[kind]}']", station)

    async def choose_schedule(self, travel_date: date, departure_hour: int) -> None:
        applied_date, applied_hour = await self.current_schedule()
        target_date_was_selected = applied_date == travel_date
        pre_picker_hour_matches = applied_hour == departure_hour
        trigger = await self._tab.query("a[title='출발일']", timeout=self._timeout_seconds)
        await trigger.click()
        dialog = await self._wait_for_dialog("날짜 선택")
        target_month = f"{travel_date.year}. {travel_date.month:02d}."
        target_slide = None
        await self._wait_for_visible_elements(
            ".datepk_wrap .slick-slide.slick-active",
            scope=dialog,
            failure_stage="departure_date_controls",
        )
        for _ in range(25):
            active_slides = await self._visible_elements(
                ".datepk_wrap .slick-slide.slick-active", scope=dialog
            )
            for slide in active_slides:
                label = await slide.query("p.date", raise_exc=False)
                if label is not None and (await label.text).strip() == target_month:
                    target_slide = slide
                    break
            if target_slide is not None:
                break
            current = await dialog.query(".datepk_wrap .slick-current p.date")
            current_match = re.fullmatch(r"(\d{4})\.\s*(\d{2})\.", (await current.text).strip())
            if current_match is None:
                raise BrowserSourceUnavailable("departure_month_navigate")
            current_month = (int(current_match.group(1)), int(current_match.group(2)))
            direction = (
                ".slick-next"
                if current_month < (travel_date.year, travel_date.month)
                else ".slick-prev"
            )
            arrow = await dialog.query(
                f".datepk_wrap button{direction}:not(.slick-disabled)", raise_exc=False
            )
            if arrow is None:
                raise BrowserSourceUnavailable("departure_month_navigate")
            await arrow.click()
            await asyncio.sleep(0.3)
        if target_slide is None:
            raise BrowserSourceUnavailable("departure_month_find")
        if not target_date_was_selected:
            day = await self._wait_for_enabled_exact_text(
                ".datepicker a",
                str(travel_date.day),
                scope=target_slide,
                failure_stage="departure_date_disabled",
                accepted_labels=(f"{travel_date.day}출발일", f"{travel_date.day} 출발일"),
            )
            await day.click()
            # The official picker can leave hour controls in the previous
            # service-date state until the changed date is applied. Commit only
            # the date first, verify the public input, then reopen the picker so
            # hour enabled/disabled state belongs to the requested date.
            apply_button = await self._find_exact_visible("button", "적용", scope=dialog)
            await apply_button.click()
            await self._wait_for_schedule_date(travel_date)
            _, applied_hour = await self.current_schedule()
            pre_picker_hour_matches = applied_hour == departure_hour
            trigger = await self._tab.query(
                "a[title='출발일']",
                timeout=self._timeout_seconds,
            )
            await trigger.click()
            dialog = await self._wait_for_dialog("날짜 선택")
            target_date_was_selected = True

        seen_signatures: set[tuple[object, ...]] = set()
        await self._wait_for_visible_elements(
            ".slideWrap .slick-slide.slick-active a",
            scope=dialog,
            failure_stage="departure_hour_controls",
        )
        for _ in range(24):
            candidates = await self._read_hour_candidates(
                ".slideWrap .slick-slide.slick-active a", scope=dialog
            )
            all_candidates = await self._read_hour_candidates(
                ".slideWrap .slick-slide a",
                scope=dialog,
                visible_only=False,
            )
            current_window = self._current_hour_window(candidates)
            signature = self._hour_window_signature(current_window)
            if not current_window or signature in seen_signatures:
                raise BrowserSourceUnavailable("departure_hour_navigate")
            seen_signatures.add(signature)

            active_targets = [
                candidate for candidate in candidates if candidate.hour == departure_hour
            ]
            all_targets = [
                candidate for candidate in all_candidates if candidate.hour == departure_hour
            ]
            current_targets = [
                candidate for candidate in current_window if candidate.hour == departure_hour
            ]
            # React Slick keeps every exact hour in the official DOM even when
            # only the first ten anchors are inside the clipped visual window.
            # Validate the hidden catalog, but move the visible carousel below
            # and click the target only after it becomes active.
            if (
                not active_targets
                and self._is_exact_hour_catalog(all_candidates)
                and (len(all_targets) != 1 or not self._is_soft_dom_hour(all_targets[0]))
            ):
                raise BrowserSourceUnavailable("departure_hour_disabled")
            if (
                len(active_targets) == 1
                and self._is_soft_adjacent_hour(candidates, current_window, active_targets[0])
                and await self._click_hour_and_confirm(active_targets[0])
            ):
                # KORAIL renders the next visible five-hour group inside the
                # active Slick viewport but sets only aria-disabled/tabindex on
                # those anchors. A normal pointer click still selects the hour.
                # Live ``current`` confirmation and exact #startDate readback
                # both reject an ignored click.
                break
            for candidate in current_targets:
                if candidate.state.enabled:
                    if not await self._click_hour_and_confirm(candidate):
                        raise BrowserSourceUnavailable("departure_hour_navigate")
                    break
            else:
                already_selected = self._is_exact_selected_hour(
                    current_window,
                    current_targets,
                    target_date_is_selected=target_date_was_selected,
                    pre_picker_hour_matches=pre_picker_hour_matches,
                )
                if already_selected:
                    break
                if current_targets:
                    raise BrowserSourceUnavailable("departure_hour_disabled")
                current_hours = tuple(candidate.hour for candidate in current_window)
                if departure_hour < min(current_hours):
                    direction = ".slick-prev"
                elif departure_hour > max(current_hours):
                    direction = ".slick-next"
                else:
                    raise BrowserSourceUnavailable("departure_hour_navigate")
                arrow = await self._find_hour_navigation_control(direction, scope=dialog)
                if arrow is not None:
                    await arrow.click()
                    if await self._wait_for_hour_window_change(
                        dialog,
                        current_hours,
                        direction,
                        timeout_seconds=1,
                    ):
                        continue

                # The production time picker can have no time-owned arrow at all,
                # or expose a disabled-looking ``a.slick-prev`` whose click is
                # ignored while Slick is settling. Keep the date carousel out of
                # this path and reproduce the user's horizontal drag inside the
                # unique time viewport.
                await self._swipe_hour_carousel(dialog, direction)
                if await self._wait_for_hour_window_change(dialog, current_hours, direction):
                    continue
                # Some official picker renders expose neither a usable Slick arrow nor
                # mouse-drag handling, but retain the carousel's normal keyboard
                # navigation. Focus the visible viewport and send one standard arrow
                # key; do not mutate the picker DOM or accept the requested time
                # unless the live window and final #startDate readback both confirm it.
                if await self._navigate_hour_carousel_by_keyboard(
                    dialog, direction
                ) and await self._wait_for_hour_window_change(
                    dialog,
                    current_hours,
                    direction,
                ):
                    continue
                await self._log_hour_window_navigation_failure(
                    dialog,
                    current_hours,
                )
                raise BrowserSourceUnavailable("departure_hour_navigate")
            break
        else:
            raise BrowserSourceUnavailable("departure_hour_find")
        apply_button = await self._find_exact_visible("button", "적용", scope=dialog)
        await apply_button.click()
        await self._wait_for_schedule(travel_date, departure_hour)

    async def current_station(self, kind: str) -> str:
        return str(await self._evaluate_value(f"input[name='{self._STATION_NAMES[kind]}']")).strip()

    async def current_schedule(self) -> tuple[date, int]:
        value = str(await self._evaluate_value("#startDate")).strip()
        match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\([^)]*\)\s+(\d{2}):00", value)
        if match is None:
            raise BrowserSourceUnavailable("departure_current_date")
        return date.fromisoformat(match.group(1)), int(match.group(2))

    async def current_passenger(self) -> str:
        value = await self._evaluate_text("a.data.btn_pop")
        if not value:
            value = str(await self._evaluate_value("#passenger, #labelple"))
        return " ".join(value.split())

    async def ensure_authenticated(self, credential: KorailCredentialInput) -> bool:
        """Use one explicit official login method and verify an authenticated header."""

        attempt = _ReservationAttemptState()
        if await self._login_step(
            "login_session_probe",
            self._has_authenticated_header(),
        ):
            return await self._confirm_authenticated_search(attempt)
        await self._login_step(
            "login_page_navigate",
            self._tab.go_to(
                "https://www.korail.com/ticket/login",
                timeout=max(1, self.timeout_ms // 1000),
            ),
        )
        if not await self._submit_login_form(credential):
            return False
        if not await self._wait_for_login_authentication(attempt):
            return False
        return await self._confirm_authenticated_search(attempt)

    async def _authenticate_in_place(
        self,
        credential: KorailCredentialInput,
        attempt: _ReservationAttemptState | None = None,
    ) -> bool:
        """Submit the current login page once without replacing its booking history state."""

        if await self._login_step(
            "reservation_login_session_probe",
            self._has_authenticated_header(),
        ):
            return True
        if not await self._submit_login_form(credential):
            return False
        return await self._wait_for_login_authentication(attempt)

    async def _submit_login_form(self, credential: KorailCredentialInput) -> bool:
        """Fill and submit one uniquely scoped official login form."""

        tab = await self._login_step(
            "login_method_tab",
            self._wait_for_unique_login_method_tab(credential.login_method),
        )
        if tab is None:
            return False
        await self._login_step("login_method_select", tab.click())
        controls = await self._login_step(
            "login_controls",
            self._wait_for_login_controls(credential.login_method),
        )
        if controls is None:
            return False
        login_id, password, submit = controls

        await self._login_step("login_identity_clear", login_id.clear())
        await self._login_step(
            "login_identity_input",
            login_id.type_text(credential.login_id),
        )
        await self._login_step("login_password_clear", password.clear())
        await self._login_step("login_password_input", password.type_text(credential.password))
        await self._login_step("login_submit", submit.click())
        return True

    async def _wait_for_login_authentication(
        self,
        attempt: _ReservationAttemptState | None = None,
    ) -> bool:
        """Observe one submitted login without navigating away from the current route."""

        submitted_at = time.monotonic()
        deadline = submitted_at + self._timeout_seconds
        # The official login request can establish the server session before its
        # React header changes from ``로그인`` to ``로그아웃``.  Verify that server
        # session independently, but keep the checks bounded so one explicit
        # credential verification cannot become a polling loop against KORAIL.
        attempt = attempt or _ReservationAttemptState()
        session_probe_delay = min(0.25, self._timeout_seconds / 4)
        while time.monotonic() < deadline:
            snapshot = await self._login_step("login_result_snapshot", self._snapshot())
            PydollKorailBrowserClient._assert_response_allowed(snapshot, "authenticate")
            authenticated_header = await self._login_step(
                "login_result_header",
                self._has_authenticated_header(),
            )
            # The account-verification caller separately confirms this session on
            # the search page. Reservation callers return here so the current tab's
            # booking history state is not replaced by explicit navigation.
            if authenticated_header:
                logger.info("KORAIL login session marker stage=login_page present=true")
                return True
            elapsed = time.monotonic() - submitted_at
            if not attempt.post_submit_check_attempted and elapsed >= session_probe_delay:
                # Latch before awaiting. A failed/uncertain fetch must not trigger
                # another official loginCheck request during this reservation.
                attempt.post_submit_check_attempted = True
                attempt.post_submit_authenticated = bool(
                    await self._login_step(
                        "login_page_session_check",
                        self._probe_official_authenticated_session(),
                    )
                )
                logger.info(
                    "KORAIL login session marker stage=login_page_official_session "
                    "attempt=1 present=%s",
                    str(attempt.post_submit_authenticated).lower(),
                )
                if attempt.post_submit_authenticated:
                    return True
            await asyncio.sleep(0.1)
        logger.info("KORAIL login session marker stage=login_page present=false")
        return False

    async def _confirm_authenticated_search(
        self,
        attempt: _ReservationAttemptState,
    ) -> bool:
        """Require the authenticated header to persist on the official search page."""

        await self._login_step(
            "login_return_search",
            self._tab.go_to(
                self.page_url,
                timeout=max(1, self.timeout_ms // 1000),
            ),
        )
        self._submitted = False
        self._network_responses.clear()
        await self._login_step(
            "login_return_search",
            self._wait_for_exact_text("button", "열차 조회"),
        )
        if not attempt.post_submit_check_attempted:
            attempt.post_submit_check_attempted = True
            attempt.post_submit_authenticated = bool(
                await self._login_step(
                    "login_search_session_check",
                    self._probe_official_authenticated_session(),
                )
            )
        if attempt.post_submit_authenticated:
            logger.info("KORAIL login session marker stage=official_session present=true")
            return True
        authenticated = bool(
            await self._login_step(
                "login_search_session_probe",
                self._wait_for_authenticated_header(),
            )
        )
        logger.info(
            "KORAIL login session marker stage=search_page present=%s",
            str(authenticated).lower(),
        )
        return authenticated

    async def _probe_official_authenticated_session(self) -> bool:
        """Return only the official loginCheck boolean; never expose its payload."""

        script = """
            (async () => {
              try {
                const response = await fetch(
                  '/ebizweb/common/loginCheck?Device=BH&Version=999999999',
                  {
                    method: 'GET',
                    credentials: 'same-origin',
                    cache: 'no-store',
                    headers: { Accept: 'application/json' },
                  },
                );
                if (!response.ok) return false;
                const payload = await response.json();
                return payload?.strResult === 'SUCC' && !payload?.h_msg_cd;
              } catch (_) {
                return false;
              }
            })()
        """
        response = await self._tab.execute_script(
            script,
            return_by_value=True,
            await_promise=True,
            timeout=self.timeout_ms,
        )
        try:
            return response["result"]["result"].get("value") is True
        except (AttributeError, KeyError, TypeError):
            return False

    async def _has_authenticated_header(self) -> bool:
        """Read the official desktop/mobile authenticated header controls."""

        return await self._has_exact_visible(
            "a.btnGoLogout,button.logoutBtn",
            "로그아웃",
        )

    async def _wait_for_authenticated_header(self) -> bool:
        """Wait for the asynchronous official loginCheck hydration to finish."""

        deadline = time.monotonic() + self._timeout_seconds
        while time.monotonic() < deadline:
            if await self._has_authenticated_header():
                return True
            await asyncio.sleep(0.1)
        return False

    @staticmethod
    async def _login_step(stage: str, awaitable: Awaitable[Any]) -> Any:
        """Map browser-library failures to a secret-free, code-owned login stage."""

        try:
            return await awaitable
        except (
            BrowserProtectionDetected,
            BrowserRateLimited,
            BrowserSourceUnavailable,
        ):
            raise
        except Exception as error:
            raise BrowserSourceUnavailable(stage) from error

    async def _wait_for_unique_login_method_tab(
        self,
        login_method: KorailLoginMethod,
    ) -> Any | None:
        """Wait for one SPA-rendered method tab without repeating navigation or clicks."""

        deadline = time.monotonic() + self._timeout_seconds
        while time.monotonic() < deadline:
            tabs = await self._visible_elements(login_method.tab_selector)
            if len(tabs) == 1:
                logger.info("KORAIL login control marker stage=login_method_tab outcome=ready")
                return tabs[0]
            if len(tabs) > 1:
                logger.info("KORAIL login control marker stage=login_method_tab outcome=ambiguous")
                return None
            await asyncio.sleep(0.1)
        logger.info("KORAIL login control marker stage=login_method_tab outcome=timeout")
        return None

    async def _wait_for_login_controls(
        self,
        login_method: KorailLoginMethod,
    ) -> tuple[Any, Any, Any] | None:
        """Resolve one active official login panel without assuming one HTML form.

        The official page renders the identifier and password in separate ``form``
        elements and places the submit button outside both.  The active tab panel is
        the stable accessibility boundary shared by all three supported login methods.
        """

        deadline = time.monotonic() + self._timeout_seconds
        password_selector = "input#password[name='password'][type='password']"
        while time.monotonic() < deadline:
            panels = await self._visible_elements(".tabPage.active[role='tabpanel']")
            if len(panels) == 1:
                panel = panels[0]
                identities = await self._visible_elements(
                    login_method.identity_selector,
                    scope=panel,
                )
                passwords = await self._visible_elements(password_selector, scope=panel)
                submits = [
                    control
                    for control in await self._visible_elements(
                        "button,[role='button']",
                        scope=panel,
                    )
                    if " ".join(str(await control.text).split()) == "로그인"
                ]
                if len(identities) == len(passwords) == len(submits) == 1:
                    return identities[0], passwords[0], submits[0]
            await asyncio.sleep(0.1)
        return None

    async def begin_http_replay_capture(self) -> None:
        if self._submitted or self._http_capture_start is not None:
            raise HttpReplayInvalidCapture()
        self._http_capture_start = len(await self._tab.get_network_logs())

    async def export_http_replay_plan(
        self,
        *,
        origin: str,
        destination: str,
        captured_date: date,
    ) -> KorailHttpReplayPlan:
        if self._http_capture_start is None:
            raise HttpReplayInvalidCapture()
        try:
            network_events = (await self._tab.get_network_logs())[self._http_capture_start :]
            cookies = await self._tab.get_cookies()
            return build_http_replay_plan(
                network_events,
                cookies,
                origin,
                destination,
                captured_date,
            )
        finally:
            self._http_capture_start = None

    async def submit_once(self) -> None:
        if self._submitted:
            raise BrowserSourceUnavailable("submit_button")
        self._submitted = True
        await self._click_exact_text("button", "열차 조회")

    async def wait_for_result(self) -> PydollPageSnapshot:
        deadline = time.monotonic() + self._timeout_seconds
        last = await self._snapshot()
        while time.monotonic() < deadline:
            trigger = protection_trigger_from_text(last.body_text)
            if trigger is not None or last.rows or last.network_responses:
                return last
            if re.search(r"조회\s*결과(?:가)?\s*(?:없|0건)", last.body_text):
                raise BrowserSourceUnavailable("wait_result")
            await asyncio.sleep(0.25)
            last = await self._snapshot()
        raise BrowserSourceUnavailable("wait_result")

    async def expand_results(
        self,
        snapshot: PydollPageSnapshot,
        max_actions: int,
    ) -> PydollPageSnapshot:
        """Expand only the current result list without submitting or reloading the search."""
        current = _deduplicate_snapshot(snapshot)
        accumulated = current
        for _ in range(max(0, max_actions)):
            if _snapshot_requires_expansion_stop(current):
                break
            try:
                more = await self._find_exact_visible("a", "더보기")
            except LookupError:
                break
            previous_rows = {_train_row_identity(row) for row in current.rows}
            await more.click()
            candidate, progressed = await self._wait_for_result_growth(previous_rows)
            accumulated = _merge_page_snapshots(accumulated, candidate)
            current = candidate
            if _snapshot_requires_expansion_stop(candidate) or not progressed:
                break
        return accumulated

    async def reserve_once(
        self,
        request: KorailReservationRequest,
    ) -> KorailReservationResult:
        """Click only the exact seat and reservation controls, each at most once."""

        target_rechecked_at: datetime | None = None
        seat_selected_at: datetime | None = None
        reservation_requested_at: datetime | None = None

        def result(
            outcome: KorailReservationOutcome,
            reason: str,
            *,
            seat_clicked: bool = False,
            reservation_clicked: bool = False,
        ) -> KorailReservationResult:
            return KorailReservationResult(
                outcome=outcome,
                reason=reason,
                seat_clicked=seat_clicked,
                reservation_clicked=reservation_clicked,
                target_rechecked_at=target_rechecked_at,
                seat_selected_at=seat_selected_at,
                reservation_requested_at=reservation_requested_at,
            )

        rows = await self._visible_elements("li.tckList")
        matches = [row for row in rows if await self._row_matches_reservation(row, request)]
        if len(matches) != 1:
            target_rechecked_at = datetime.now(UTC)
            return result(
                KorailReservationOutcome.UNAVAILABLE,
                "target_not_unique",
            )

        row = matches[0]
        seat_controls = await self._actionable_seat_controls(
            row,
            request.seat_class.label,
        )
        target_rechecked_at = datetime.now(UTC)
        if len(seat_controls) > 1:
            return result(
                KorailReservationOutcome.UNAVAILABLE,
                "seat_control_not_unique",
            )
        if not seat_controls:
            return result(
                KorailReservationOutcome.UNAVAILABLE,
                "seat_not_available",
            )

        seat = seat_controls[0]
        await seat.click()
        seat_selected_at = datetime.now(UTC)
        attempt = _ReservationAttemptState()
        deadline = time.monotonic() + self._timeout_seconds
        while time.monotonic() < deadline:
            terminal = await self._probe_reservation_terminal(request, attempt)
            if terminal is not None:
                if terminal.outcome is KorailReservationOutcome.AUTH_REQUIRED:
                    if attempt.login_attempted:
                        return result(
                            KorailReservationOutcome.AUTH_REQUIRED,
                            "authentication_required",
                            seat_clicked=True,
                            reservation_clicked=attempt.reservation_clicked,
                        )
                    attempt.login_attempted = True
                    if not await self._authenticate_in_place(request.credential, attempt):
                        return result(
                            KorailReservationOutcome.AUTH_REQUIRED,
                            "authentication_required",
                            seat_clicked=True,
                            reservation_clicked=attempt.reservation_clicked,
                        )
                    deadline = time.monotonic() + self._timeout_seconds
                    continue
                return result(
                    terminal.outcome,
                    terminal.reason,
                    seat_clicked=True,
                    reservation_clicked=attempt.reservation_clicked,
                )
            if not attempt.reservation_clicked:
                candidates = []
                for control in await self._visible_elements("button.reservbtn"):
                    if " ".join(str(await control.text).split()) == "예매":
                        candidates.append(control)
                if len(candidates) > 1:
                    return result(
                        KorailReservationOutcome.UNAVAILABLE,
                        "reservation_control_ambiguous",
                        seat_clicked=True,
                    )
                if len(candidates) == 1:
                    if attempt.login_attempted:
                        if not attempt.preserved_selection_checked:
                            attempt.preserved_selection_checked = True
                            attempt.preserved_selection_matches = (
                                await self._has_exact_preserved_booking_state(request)
                            )
                        if not attempt.preserved_selection_matches:
                            return result(
                                KorailReservationOutcome.FAILED,
                                "reservation_selection_not_preserved",
                                seat_clicked=True,
                            )
                    state = await self._read_control_state(candidates[0])
                    if (
                        state.read_error
                        or not state.enabled
                        or state.disabled_attribute
                        or state.aria_disabled.casefold() == "true"
                    ):
                        return result(
                            KorailReservationOutcome.UNAVAILABLE,
                            "reservation_control_disabled",
                            seat_clicked=True,
                        )
                    # Set the latch before awaiting the click. An uncertain click
                    # result must never permit a second reservation submission.
                    attempt.reservation_clicked = True
                    reservation_requested_at = datetime.now(UTC)
                    try:
                        await candidates[0].click()
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 -- click outcome is intentionally uncertain.
                        return result(
                            KorailReservationOutcome.FAILED,
                            "reservation_result_unknown:reservation_click_error",
                            seat_clicked=True,
                            reservation_clicked=True,
                        )
                    deadline = time.monotonic() + self._timeout_seconds
                    continue
            await asyncio.sleep(0.1)
        if not attempt.reservation_clicked:
            return result(
                KorailReservationOutcome.FAILED,
                "reservation_control_timeout",
                seat_clicked=True,
            )
        return result(
            KorailReservationOutcome.FAILED,
            "reservation_result_unknown",
            seat_clicked=True,
            reservation_clicked=True,
        )

    async def _has_exact_preserved_booking_state(
        self,
        request: KorailReservationRequest,
    ) -> bool:
        """Re-identify the official same-tab selection without exporting its payload."""

        script = """
            (() => {
              const state = history.state?.state;
              if (!state || typeof state !== 'object') return false;
              if (typeof state.redirectUrl !== 'string' ||
                  !state.redirectUrl.startsWith('/ticket/')) return false;
              if (!Array.isArray(state.reservedTrainList) ||
                  state.reservedTrainList.length !== 1) return false;
              return Boolean(state.reserveParams &&
                typeof state.reserveParams === 'object' &&
                !Array.isArray(state.reserveParams));
            })()
        """
        response = await self._tab.execute_script(
            script,
            return_by_value=True,
            await_promise=False,
            timeout=self.timeout_ms,
        )
        try:
            state_shape_matches = response["result"]["result"].get("value") is True
        except (AttributeError, KeyError, TypeError):
            return False
        if not state_shape_matches:
            return False
        try:
            selected_date, _ = await self.current_schedule()
            rows = [
                row
                for row in await self._visible_elements("li.tckList")
                if await self._row_matches_reservation(row, request)
            ]
            if selected_date != request.travel_date or len(rows) != 1:
                return False
            seat_controls = await self._actionable_seat_controls(
                rows[0],
                request.seat_class.label,
            )
            return len(seat_controls) == 1
        except Exception:  # noqa: BLE001 -- missing/changed official state fails closed.
            return False

    async def _actionable_seat_controls(
        self,
        row: Any,
        seat_class_label: str,
    ) -> list[Any]:
        """Return exact visible price controls that are currently actionable.

        The official row can retain an unavailable anchor for the same seat class
        beside its live price anchor.  Counting both labels before checking their
        state incorrectly turns a unique booking action into an ambiguity.  Keep
        the exact-row boundary established by the caller, then admit only controls
        with the requested class, a price, and a live enabled state.
        """

        actionable_by_label: dict[str, Any] = {}
        for control in await self._visible_elements("a", scope=row):
            raw_text = str(await control.text)
            price_box_text, price_box_classes = await self._seat_price_box_metadata(control)
            key = booking_seat_control_key(
                seat_class_label=seat_class_label,
                control_text=raw_text,
                price_box_text=price_box_text,
                price_box_classes=price_box_classes,
            )
            if key is None:
                continue
            state = await self._read_control_state(control)
            if (
                state.read_error
                or not state.enabled
                or state.disabled_attribute
                or state.aria_disabled.casefold() == "true"
            ):
                continue
            # The responsive official row can render the same seat action more than
            # once.  Once the caller has fixed the exact train row and seat class,
            # controls with the same normalized label and price are equivalent.  Keep
            # one of those duplicates, but preserve differently labelled/priced
            # controls as ambiguous so the caller still fails closed.
            actionable_by_label.setdefault(key, control)
        return list(actionable_by_label.values())

    @staticmethod
    async def _seat_price_box_metadata(element: Any) -> tuple[str, tuple[str, ...]]:
        """Read only the bounded label and CSS tokens of the owning seat box."""

        try:
            response = await element.execute_script(
                """
                function() {
                  const box = this.closest('.price_box');
                  return {
                    text: box ? (box.innerText || '').slice(0, 200) : '',
                    classes: box ? Array.from(box.classList).slice(0, 8) : [],
                  };
                }
                """,
                return_by_value=True,
            )
            value = response.get("result", {}).get("result", {}).get("value", {})
            if not isinstance(value, dict):
                return "", ()
            classes = value.get("classes", [])
            return (
                str(value.get("text", ""))[:200],
                _sanitized_class_tokens(
                    " ".join(str(item) for item in classes) if isinstance(classes, list) else ""
                ),
            )
        except Exception:  # noqa: BLE001 -- missing owner metadata falls back to anchor text.
            return "", ()

    async def _row_matches_reservation(
        self,
        row: Any,
        request: KorailReservationRequest,
    ) -> bool:
        kind = await row.query(".tck_inner .tit_box", raise_exc=False)
        number = await row.query(".tck_inner .tit_box .num", raise_exc=False)
        route = await row.query(".tck_inner .data_box.right", raise_exc=False)
        if kind is None or route is None:
            return False
        kind_text = " ".join(str(await kind.text).split())
        number_text = " ".join(str(await number.text).split()) if number is not None else kind_text
        try:
            normalized_number = _normalize_train_number(number_text)
        except BrowserSourceUnavailable:
            return False
        if normalized_number != _normalize_train_number(request.train_number):
            return False
        type_text = re.sub(rf"(?<!\d)0*{re.escape(normalized_number)}(?!\d)", "", kind_text)
        if request.train_type is not None:
            normalized_type = re.sub(r"\s+", "", type_text).casefold()
            if normalized_type != re.sub(r"\s+", "", request.train_type).casefold():
                return False
        route_text = " ".join(str(await route.text).split())
        route_match = _ROUTE_HEADING.fullmatch(route_text)
        if route_match is None:
            return False
        origin, destination, departure, arrival = route_match.groups()
        return (
            _normalize_station(origin) == request.origin
            and _normalize_station(destination) == request.destination
            and departure == request.departure_time.strftime("%H:%M")
            and arrival == request.arrival_time.strftime("%H:%M")
        )

    async def _probe_reservation_terminal(
        self,
        request: KorailReservationRequest,
        attempt: _ReservationAttemptState | None = None,
    ) -> KorailReservationResult | None:
        attempt = attempt or _ReservationAttemptState()
        snapshot = await self._snapshot()
        for status, resource_type in snapshot.network_responses:
            if is_rate_limit_response(status, resource_type):
                return KorailReservationResult(
                    KorailReservationOutcome.PROVIDER_BLOCKED,
                    "rate_limited",
                )
            if protection_trigger_from_http_response(status, resource_type) is not None:
                return KorailReservationResult(
                    KorailReservationOutcome.PROVIDER_BLOCKED,
                    "provider_access_restricted",
                )
        if protection_trigger_from_text(snapshot.body_text) is not None or any(
            protection_trigger_from_text(text) is not None for text in snapshot.protection_texts
        ):
            return KorailReservationResult(
                KorailReservationOutcome.PROVIDER_BLOCKED,
                "provider_access_restricted",
            )

        path = urlsplit(snapshot.url).path
        authenticated_login_route = False
        if path.rstrip("/") == "/ticket/login":
            # The React router can briefly retain the login URL after the official
            # session has already been established.  Confirm the same server-side
            # session used by login verification before treating the route as an
            # authentication terminal; continuing only observes the current attempt
            # and never repeats either booking click.
            # A successful post-submit loginCheck is authoritative for this same
            # attempt even while the SPA temporarily retains the login URL. Reuse
            # the latch without another official request.
            authenticated = attempt.post_submit_authenticated
            if not authenticated:
                if not attempt.pre_login_route_check_attempted:
                    attempt.pre_login_route_check_attempted = True
                    attempt.pre_login_route_authenticated = (
                        await self._probe_official_authenticated_session()
                    )
                authenticated = attempt.pre_login_route_authenticated
            if not authenticated:
                authenticated = await self._has_authenticated_header()
            if not authenticated:
                return KorailReservationResult(
                    KorailReservationOutcome.AUTH_REQUIRED,
                    "authentication_required",
                )
            authenticated_login_route = True
            logger.info(
                "KORAIL reservation marker stage=terminal_probe login_route_authenticated=true"
            )

        dialogs = await self._visible_elements("[role='dialog'], dialog[open], [aria-modal='true']")
        delay_dialogs = []
        for dialog in dialogs:
            text = " ".join(str(await dialog.text).split())
            labels = {
                " ".join(str(await control.text).split())
                for control in await self._visible_elements("button,a", scope=dialog)
            }
            if "지연승낙 안내" in text and {"아니오", "네"}.issubset(labels):
                delay_dialogs.append(dialog)
        if len(delay_dialogs) == 1:
            return KorailReservationResult(
                KorailReservationOutcome.CONSENT_REQUIRED,
                "delay_consent_required",
            )
        if len(delay_dialogs) > 1:
            return KorailReservationResult(
                KorailReservationOutcome.FAILED,
                "delay_consent_ambiguous",
            )
        if dialogs and not authenticated_login_route:
            return KorailReservationResult(
                KorailReservationOutcome.ACTION_REQUIRED,
                "official_action_required",
            )
        if dialogs:
            # The official SPA can retain its login-route dialog shell after
            # loginCheck has already authenticated the same tab. It is not a
            # booking consent gate, and treating it as one prevents the preserved
            # exact reservation control from being used. Keep ignoring only while
            # the authenticated tab still reports the login route. Once another
            # route renders, any remaining dialog is a manual-action terminal.
            logger.info(
                "KORAIL reservation marker stage=terminal_probe "
                "authenticated_login_shell_ignored=true"
            )

        body = " ".join(snapshot.body_text.split())
        target_markers = (
            _has_exact_train_number_marker(body, request.train_number)
            and request.departure_time.strftime("%H:%M") in body
            and request.arrival_time.strftime("%H:%M") in body
            and request.seat_class.label in body
            and any(marker in body for marker in _reservation_date_markers(request.travel_date))
        )
        pending_markers = all(marker in body for marker in ("예약취소", "장바구니", "결제하기"))
        if path.rstrip("/") == "/ticket/reservation/detail" and target_markers and pending_markers:
            return KorailReservationResult(
                KorailReservationOutcome.PAYMENT_REQUIRED,
                "reservation_pending_payment",
            )
        return None

    async def _wait_for_result_growth(
        self,
        previous_rows: set[tuple[str, str, str]],
    ) -> tuple[PydollPageSnapshot, bool]:
        deadline = time.monotonic() + min(self._timeout_seconds, 10)
        last = await self._snapshot()
        while time.monotonic() < deadline:
            if _snapshot_requires_expansion_stop(last):
                return last, False
            current_rows = {_train_row_identity(row) for row in last.rows}
            if current_rows - previous_rows:
                return last, True
            await asyncio.sleep(0.25)
            last = await self._snapshot()
        return last, False

    async def _snapshot(self) -> PydollPageSnapshot:
        script = """
            (() => {
              const visible = (element) => {
                const style = getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.display !== 'none' && style.visibility !== 'hidden'
                  && rect.width > 0 && rect.height > 0;
              };
              return {
                body: document.body?.innerText || '',
                url: window.location.href,
                title: document.title || '',
                reservationRows: (() => {
                  const normalized = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                  const action = Array.from(document.querySelectorAll('button,a'))
                    .filter(visible)
                    .filter((item) => normalized(item.innerText) === '결제/발권');
                  const rows = [];
                  for (const control of action) {
                    let current = control.parentElement;
                    let best = null;
                    for (let depth = 0; current && depth < 9; depth += 1) {
                      const text = current.innerText || '';
                      if (text.includes('예약취소') && text.includes('예약변경') &&
                          text.includes('결제/발권') && text.includes('→') &&
                          (text.match(/\\b\\d{2}:\\d{2}\\b/g) || []).length >= 2) {
                        best = current;
                        break;
                      }
                      current = current.parentElement;
                    }
                    if (best && !rows.includes(best)) rows.push(best);
                  }
                  return rows.map((row) => row.innerText || '');
                })(),
                protectionTexts: Array.from(document.querySelectorAll(
                  __PROTECTION_SURFACE_SELECTOR__
                )).filter(visible).map((item) => item.innerText),
                rows: Array.from(document.querySelectorAll('li.tckList')).filter(visible)
                  .map((row) => ({
                    kind: row.querySelector('.tck_inner .tit_box')?.innerText || '',
                    number: row.querySelector('.tck_inner .tit_box .num')?.innerText || '',
                    route: row.querySelector('.tck_inner .data_box.right')?.innerText || '',
                    fullText: row.innerText || '',
                    seats: Array.from(row.querySelectorAll('.tck_inner .price_box'))
                      .filter(visible).map((box) => ({
                        text: box.innerText,
                        classes: Array.from(new Set([
                          ...box.classList,
                          ...Array.from(box.querySelectorAll('.sold_out,.sold_out_soon'))
                            .flatMap((item) => Array.from(item.classList)),
                        ])),
                      })),
                  })),
              };
            })()
            """.replace("__PROTECTION_SURFACE_SELECTOR__", repr(_PROTECTION_SURFACE_SELECTOR))
        response = await self._tab.execute_script(
            script,
            return_by_value=True,
        )
        value = response["result"]["result"]["value"]
        return PydollPageSnapshot(
            body_text=str(value["body"]),
            url=str(value["url"]),
            title=str(value["title"]),
            reservation_rows=tuple(str(item) for item in value["reservationRows"]),
            protection_texts=tuple(str(item) for item in value["protectionTexts"]),
            network_responses=tuple(sorted(self._network_responses)),
            rows=tuple(
                PydollTrainRow(
                    kind_text=str(row["kind"]),
                    train_number=str(row["number"]),
                    route_text=str(row["route"]),
                    seats=tuple(
                        PydollSeatBox(
                            text=str(box["text"]),
                            classes=frozenset(str(item) for item in box["classes"]),
                        )
                        for box in row["seats"]
                    ),
                    full_text=str(row["fullText"]),
                )
                for row in value["rows"]
            ),
        )

    async def _evaluate_value(self, selector: str) -> object:
        response = await self._tab.execute_script(
            f"document.querySelector({selector!r})?.value ?? ''", return_by_value=True
        )
        return response["result"]["result"].get("value", "")

    async def _evaluate_text(self, selector: str) -> str:
        response = await self._tab.execute_script(
            f"document.querySelector({selector!r})?.innerText ?? ''", return_by_value=True
        )
        return str(response["result"]["result"].get("value", ""))

    async def _wait_for_value(
        self, selector: str, expected: str, *, contains: bool = False
    ) -> None:
        deadline = time.monotonic() + self._timeout_seconds
        while time.monotonic() < deadline:
            actual = str(await self._evaluate_value(selector)).strip()
            if (contains and expected in actual) or (not contains and actual == expected):
                return
            await asyncio.sleep(0.1)
        raise BrowserSourceUnavailable("input_readback")

    async def _click_exact_text(self, selector: str, text: str) -> None:
        await (await self._find_exact_visible(selector, text)).click()

    async def _wait_for_exact_text(self, selector: str, text: str, *, scope: Any = None) -> Any:
        deadline = time.monotonic() + self._timeout_seconds
        while time.monotonic() < deadline:
            try:
                return await self._find_exact_visible(selector, text, scope=scope)
            except LookupError:
                await asyncio.sleep(0.1)
        raise BrowserSourceUnavailable("visible_control")

    async def _wait_for_enabled_exact_text(
        self,
        selector: str,
        text: str,
        *,
        scope: Any = None,
        failure_stage: str = "disabled_control",
        accepted_labels: tuple[str, ...] = (),
    ) -> Any:
        deadline = time.monotonic() + self._timeout_seconds
        last_visible_count = 0
        last_states: list[_ControlState] = []
        normalized_labels = {" ".join(label.split()) for label in (text, *accepted_labels)}
        while time.monotonic() < deadline:
            # Slick keeps cloned slides in the rendered tree while moving between
            # ranges.  A disabled clone can therefore precede the enabled control
            # with the same visible label.  Select from every exact visible match
            # instead of repeatedly waiting on the first clone.
            visible = await self._visible_elements(selector, scope=scope)
            last_visible_count = len(visible)
            last_states = []
            for element in visible:
                label = " ".join(str(await element.text).split())
                if label not in normalized_labels:
                    continue
                state = await self._read_control_state(element)
                last_states.append(state)
                if state.enabled:
                    return element
            await asyncio.sleep(0.1)
        logger.warning(
            "KORAIL Pydoll control unavailable stage=%s visible=%d exact=%d states=%s",
            failure_stage,
            last_visible_count,
            len(last_states),
            tuple(self._control_state_log_value(state) for state in last_states),
        )
        raise BrowserSourceUnavailable(failure_stage)

    async def _read_hour_candidates(
        self,
        selector: str,
        *,
        scope: Any,
        visible_only: bool = True,
    ) -> list[_HourCandidate]:
        candidates: list[_HourCandidate] = []
        if visible_only:
            elements = await self._visible_elements(selector, scope=scope)
        else:
            elements = await scope.query(selector, find_all=True, raise_exc=False) or []
        for element in elements:
            label = (await element.text).strip()
            if re.fullmatch(r"\d{2}시", label) is None:
                continue
            candidates.append(
                _HourCandidate(
                    element=element,
                    hour=int(label.removesuffix("시")),
                    state=await self._read_control_state(element),
                )
            )
        return candidates

    @staticmethod
    def _current_hour_window(candidates: list[_HourCandidate]) -> list[_HourCandidate]:
        current_indexes = [
            index
            for index, candidate in enumerate(candidates)
            if "slick-current" in candidate.state.slide_classes
        ]
        if not current_indexes or current_indexes != list(
            range(current_indexes[0], current_indexes[-1] + 1)
        ):
            return []
        current_window: list[_HourCandidate] = []
        for candidate in candidates[current_indexes[0] :]:
            if "slick-current" not in candidate.state.slide_classes and not candidate.state.enabled:
                break
            current_window.append(candidate)
        return current_window

    @classmethod
    def _hour_window_signature(cls, candidates: list[_HourCandidate]) -> tuple[object, ...]:
        return tuple(
            (
                candidate.hour,
                cls._control_state_log_value(candidate.state),
            )
            for candidate in candidates
        )

    async def _wait_for_hour_window_change(
        self,
        dialog: Any,
        before: tuple[int, ...],
        direction: str,
        *,
        timeout_seconds: float | None = None,
    ) -> bool:
        timeout = min(self._timeout_seconds, 3) if timeout_seconds is None else timeout_seconds
        deadline = time.monotonic() + max(0.05, timeout)
        stable_progress: tuple[int, ...] = ()
        stable_reads = 0
        while time.monotonic() < deadline:
            candidates = await self._read_hour_candidates(
                ".slideWrap .slick-slide.slick-active a", scope=dialog
            )
            after = tuple(candidate.hour for candidate in self._current_hour_window(candidates))
            progressed = bool(after) and (
                (direction == ".slick-next" and after[0] > before[0])
                or (direction == ".slick-prev" and after[0] < before[0])
            )
            if progressed:
                if after == stable_progress:
                    stable_reads += 1
                else:
                    stable_progress = after
                    stable_reads = 1
                if stable_reads >= 2:
                    await self._wait_for_hour_animation(dialog, after)
                    return True
            else:
                stable_progress = ()
                stable_reads = 0
            await asyncio.sleep(0.05)
        return False

    async def _log_hour_window_navigation_failure(
        self,
        dialog: Any,
        before: tuple[int, ...],
    ) -> None:
        candidates = await self._read_hour_candidates(
            ".slideWrap .slick-slide.slick-active a", scope=dialog
        )
        after = tuple(candidate.hour for candidate in self._current_hour_window(candidates))
        logger.warning(
            "KORAIL Pydoll hour window did not change stage=departure_hour_navigate "
            "before=%s after=%s controls=%s",
            before,
            after,
            await self._hour_carousel_control_metadata(dialog),
        )

    async def _wait_for_hour_animation(
        self,
        dialog: Any,
        expected_hours: tuple[int, ...],
    ) -> None:
        try:
            response = await dialog.execute_script(
                """
                function() {
                  const track = this.querySelector('.slideWrap .slick-track');
                  if (!track) return null;
                  const style = getComputedStyle(track);
                  const milliseconds = (value) => value.split(',').map((part) => {
                    const token = part.trim();
                    if (token.endsWith('ms')) return Number.parseFloat(token);
                    if (token.endsWith('s')) return Number.parseFloat(token) * 1000;
                    return 0;
                  });
                  const duration = Math.max(0, ...milliseconds(style.transitionDuration));
                  const delay = Math.max(0, ...milliseconds(style.transitionDelay));
                  return Math.min(1500, duration + delay);
                }
                """,
                return_by_value=True,
            )
            value = response.get("result", {}).get("result", {}).get("value")
            if not isinstance(value, (int, float)) or not 0 <= value <= 1500:
                raise ValueError("invalid hour transition duration")
            await asyncio.sleep((value / 1000) + 0.05)
            candidates = await self._read_hour_candidates(
                ".slideWrap .slick-slide.slick-active a",
                scope=dialog,
            )
            settled_hours = tuple(
                candidate.hour for candidate in self._current_hour_window(candidates)
            )
            if settled_hours != expected_hours:
                raise ValueError("hour transition did not settle")
        except BrowserSourceUnavailable:
            raise
        except Exception as error:
            raise BrowserSourceUnavailable("departure_hour_navigate") from error

    @staticmethod
    async def _hour_carousel_control_metadata(dialog: Any) -> tuple[object, ...]:
        """Return bounded structural metadata only; never page text, URLs, or request values."""
        try:
            response = await dialog.execute_script(
                """
                function() {
                  const visible = (element) => {
                    const style = getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden'
                      && rect.width > 0 && rect.height > 0;
                  };
                  const wrap = this.querySelector('.slideWrap');
                  if (!wrap) return [];
                  const root = wrap.parentElement?.parentElement || wrap.parentElement || wrap;
                  return Array.from(root.querySelectorAll('button, a'))
                    .filter(visible)
                    .slice(0, 24)
                    .map((element) => {
                      const relation = wrap.contains(element)
                        ? 'inside'
                        : (wrap.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING)
                          ? 'after'
                          : 'before';
                      return {
                        tag: element.tagName.toLowerCase(),
                        classes: Array.from(element.classList).slice(0, 8),
                        relation,
                        parentClasses: Array.from(
                          element.parentElement?.classList || []
                        ).slice(0, 8),
                      };
                    });
                }
                """,
                return_by_value=True,
            )
            value = response.get("result", {}).get("result", {}).get("value", [])
            if not isinstance(value, list):
                return ()
            return tuple(
                (
                    str(item.get("tag", ""))[:16],
                    _sanitized_class_tokens(" ".join(item.get("classes", []))),
                    str(item.get("relation", ""))[:8],
                    _sanitized_class_tokens(" ".join(item.get("parentClasses", []))),
                )
                for item in value
                if isinstance(item, dict)
            )
        except Exception:  # noqa: BLE001 -- diagnostic metadata must not mask the failure.
            return ()

    async def _find_hour_navigation_control(
        self,
        direction: str,
        *,
        scope: Any,
    ) -> Any | None:
        """Resolve one enabled time-carousel arrow owned by ``.slideWrap`` only."""
        visible = await self._visible_elements(
            f".slideWrap :is(button, a){direction}:not(.slick-disabled)", scope=scope
        )
        states = [(element, await self._read_control_state(element)) for element in visible]
        enabled = [element for element, state in states if state.enabled]
        if len(enabled) == 1:
            return enabled[0]
        return None

    async def _swipe_hour_carousel(self, dialog: Any, direction: str) -> None:
        viewports = await self._visible_elements(".slideWrap .slick-list", scope=dialog)
        if len(viewports) != 1:
            raise BrowserSourceUnavailable("departure_hour_navigate")
        try:
            await viewports[0].scroll_into_view()
            bounds = await viewports[0].get_bounds_using_js()
            x = float(bounds["x"])
            y = float(bounds["y"])
            width = float(bounds["width"])
            height = float(bounds["height"])
            if width < 40 or height <= 0:
                raise ValueError("invalid hour carousel bounds")
            leading_x = x + width * 0.75
            trailing_x = x + width * 0.25
            if direction == ".slick-prev":
                leading_x, trailing_x = trailing_x, leading_x
            pointer_y = y + height * 0.5
            await self._dispatch_mouse_event(
                "mouseMoved",
                leading_x,
                pointer_y,
                buttons=0,
            )
            pressed = False
            try:
                await self._dispatch_mouse_event(
                    "mousePressed",
                    leading_x,
                    pointer_y,
                    button="left",
                    buttons=1,
                    click_count=1,
                )
                pressed = True
                for step in range(1, 11):
                    progress = step / 10
                    await self._dispatch_mouse_event(
                        "mouseMoved",
                        leading_x + (trailing_x - leading_x) * progress,
                        pointer_y,
                        button="left",
                        buttons=1,
                    )
                    await asyncio.sleep(0.025)
            finally:
                if pressed:
                    await asyncio.shield(
                        self._dispatch_mouse_event(
                            "mouseReleased",
                            trailing_x,
                            pointer_y,
                            button="left",
                            buttons=0,
                            click_count=1,
                        )
                    )
        except BrowserSourceUnavailable:
            raise
        except Exception as error:
            raise BrowserSourceUnavailable("departure_hour_navigate") from error

    async def _navigate_hour_carousel_by_keyboard(
        self,
        dialog: Any,
        direction: str,
    ) -> bool:
        """Use the picker viewport's documented keyboard-style navigation when focusable."""
        try:
            response = await dialog.execute_script(
                """
                function() {
                  const viewports = this.querySelectorAll('.slideWrap .slick-list');
                  if (viewports.length !== 1) return false;
                  const viewport = viewports[0];
                  viewport.focus({preventScroll: true});
                  return document.activeElement === viewport
                    || viewport.contains(document.activeElement);
                }
                """,
                return_by_value=True,
            )
            focused = response.get("result", {}).get("result", {}).get("value")
            if focused is not True:
                return False
            key, code, virtual_key_code = (
                ("ArrowLeft", "ArrowLeft", 37)
                if direction == ".slick-prev"
                else ("ArrowRight", "ArrowRight", 39)
            )
            await self._tab._execute_command(
                {
                    "method": "Input.dispatchKeyEvent",
                    "params": {
                        "type": "rawKeyDown",
                        "key": key,
                        "code": code,
                        "windowsVirtualKeyCode": virtual_key_code,
                        "nativeVirtualKeyCode": virtual_key_code,
                    },
                }
            )
            await self._tab._execute_command(
                {
                    "method": "Input.dispatchKeyEvent",
                    "params": {
                        "type": "keyUp",
                        "key": key,
                        "code": code,
                        "windowsVirtualKeyCode": virtual_key_code,
                        "nativeVirtualKeyCode": virtual_key_code,
                    },
                }
            )
            return True
        except Exception:  # noqa: BLE001 -- unsupported browser input remains fail-closed.
            return False

    async def _dispatch_mouse_event(
        self,
        event_type: str,
        x: float,
        y: float,
        *,
        buttons: int,
        button: str | None = None,
        click_count: int | None = None,
    ) -> None:
        params: dict[str, object] = {
            "type": event_type,
            "x": round(x),
            "y": round(y),
            "buttons": buttons,
        }
        if button is not None:
            params["button"] = button
        if click_count is not None:
            params["clickCount"] = click_count
        # Pydoll's public mouse helper omits the CDP ``buttons`` bitmask on move.
        await self._tab._execute_command({"method": "Input.dispatchMouseEvent", "params": params})

    async def _wait_for_schedule(self, travel_date: date, departure_hour: int) -> None:
        deadline = time.monotonic() + self._timeout_seconds
        while time.monotonic() < deadline:
            try:
                if await self.current_schedule() == (travel_date, departure_hour):
                    return
            except BrowserSourceUnavailable:
                pass
            await asyncio.sleep(0.1)
        raise BrowserSourceUnavailable("departure_schedule_readback")

    async def _wait_for_schedule_date(self, travel_date: date) -> None:
        deadline = time.monotonic() + self._timeout_seconds
        while time.monotonic() < deadline:
            try:
                selected_date, _ = await self.current_schedule()
                if selected_date == travel_date:
                    return
            except BrowserSourceUnavailable:
                pass
            await asyncio.sleep(0.1)
        raise BrowserSourceUnavailable("departure_schedule_readback")

    @staticmethod
    def _is_soft_aria_hour(candidate: _HourCandidate) -> bool:
        state = candidate.state
        return (
            state.aria_disabled == "true"
            and not state.disabled_attribute
            and not _has_disabled_class(state.classes)
            and not _has_disabled_class(state.container_classes)
            and not _has_disabled_class(state.slide_classes)
            and "slick-active" in state.slide_classes
            and not state.read_error
        )

    @staticmethod
    def _is_soft_dom_hour(candidate: _HourCandidate) -> bool:
        state = candidate.state
        return (
            state.aria_disabled == "true"
            and not state.disabled_attribute
            and not _has_disabled_class(state.classes)
            and not _has_disabled_class(state.container_classes)
            and not _has_disabled_class(state.slide_classes)
            and "slick-slide" in state.slide_classes
            and "slick-cloned" not in state.slide_classes
            and not state.read_error
        )

    @staticmethod
    def _is_exact_hour_catalog(candidates: list[_HourCandidate]) -> bool:
        return len(candidates) == 24 and sorted(candidate.hour for candidate in candidates) == list(
            range(24)
        )

    @classmethod
    def _is_soft_adjacent_hour(
        cls,
        candidates: list[_HourCandidate],
        current_window: list[_HourCandidate],
        target: _HourCandidate,
    ) -> bool:
        if len(candidates) != 10 or len(current_window) != 5 or target in current_window:
            return False
        adjacent = candidates[len(current_window) :]
        return (
            len(adjacent) == 5
            and all(candidate.state.enabled for candidate in current_window)
            and all(cls._is_soft_aria_hour(candidate) for candidate in adjacent)
            and target in adjacent
        )

    async def _click_hour_and_confirm(self, candidate: _HourCandidate) -> bool:
        await candidate.element.click()
        deadline = time.monotonic() + min(self._timeout_seconds, 1)
        while time.monotonic() < deadline:
            state = await self._read_control_state(candidate.element)
            if "current" in state.container_classes:
                return True
            await asyncio.sleep(0.05)
        return False

    @staticmethod
    def _is_exact_selected_hour(
        candidates: list[_HourCandidate],
        target_elements: list[_HourCandidate],
        *,
        target_date_is_selected: bool,
        pre_picker_hour_matches: bool,
    ) -> bool:
        if not target_date_is_selected or not pre_picker_hour_matches:
            return False
        if len(target_elements) != 1 or len(candidates) < 2:
            return False
        target_state = target_elements[0].state
        if (
            target_state.aria_disabled != "true"
            or target_state.disabled_attribute
            or target_state.classes
            or _has_disabled_class(target_state.container_classes)
            or _has_disabled_class(target_state.slide_classes)
            or target_state.read_error
        ):
            return False
        target_hour = target_elements[0].hour
        return all(
            candidate.state.enabled for candidate in candidates if candidate.hour != target_hour
        )

    async def _wait_for_visible_elements(
        self,
        selector: str,
        *,
        scope: Any = None,
        failure_stage: str,
    ) -> list[Any]:
        deadline = time.monotonic() + self._timeout_seconds
        while time.monotonic() < deadline:
            elements = await self._visible_elements(selector, scope=scope)
            if elements:
                return elements
            await asyncio.sleep(0.1)
        logger.warning(
            "KORAIL Pydoll controls unavailable stage=%s visible=0",
            failure_stage,
        )
        raise BrowserSourceUnavailable(failure_stage)

    async def _wait_for_dialog(self, marker: str) -> Any:
        deadline = time.monotonic() + self._timeout_seconds
        while time.monotonic() < deadline:
            for dialog in await self._visible_elements("[role='dialog']"):
                if marker in (await dialog.text):
                    return dialog
            await asyncio.sleep(0.1)
        raise BrowserSourceUnavailable("dialog")

    async def _find_exact_visible(self, selector: str, text: str, *, scope: Any = None) -> Any:
        for element in await self._visible_elements(selector, scope=scope):
            if (await element.text).strip() == text:
                return element
        raise LookupError(text)

    async def _has_exact_visible(self, selector: str, text: str, *, scope: Any = None) -> bool:
        # ``await`` inside a generator expression creates an async generator,
        # which built-in ``any`` cannot consume.  Use an explicit loop so live
        # Pydoll elements are awaited one at a time.
        for element in await self._visible_elements(selector, scope=scope):
            try:
                if " ".join(str(await element.text).split()) == text:
                    return True
            except Exception:  # noqa: BLE001, S112 -- skip detached React nodes.
                continue
        return False

    async def _visible_elements(self, selector: str, *, scope: Any = None) -> list[Any]:
        root = scope or self._tab
        elements = await root.query(selector, find_all=True, raise_exc=False)
        if elements is None:
            return []
        if isinstance(elements, AsyncIterable):
            candidates = [element async for element in elements]
        else:
            candidates = list(elements)
        visible: list[Any] = []
        for element in candidates:
            try:
                if await element.is_visible():
                    visible.append(element)
            except Exception:  # noqa: BLE001, S112 -- detached React nodes are skipped.
                continue
        return visible

    @staticmethod
    async def _read_control_state(element: Any) -> _ControlState:
        """Read dynamic control attributes from the live DOM instead of Pydoll's cache."""
        try:
            response = await element.execute_script(
                """
                function() {
                  const container = this.closest('td, li');
                  const slide = this.closest('.slick-slide');
                  return {
                    ariaDisabled: (this.getAttribute('aria-disabled') || '').toLowerCase(),
                    disabledAttribute: this.hasAttribute('disabled') || Boolean(this.disabled),
                    className: typeof this.className === 'string' ? this.className : '',
                    containerClassName: container && typeof container.className === 'string'
                      ? container.className : '',
                    slideClassName: slide && typeof slide.className === 'string'
                      ? slide.className : '',
                  };
                }
                """,
                return_by_value=True,
            )
            value = response.get("result", {}).get("result", {}).get("value", {})
            if not isinstance(value, dict):
                raise TypeError("control state is not an object")
            aria_disabled = str(value.get("ariaDisabled", "")).lower()
            disabled_attribute = bool(value.get("disabledAttribute", False))
            classes = _sanitized_class_tokens(value.get("className", ""))
            container_classes = _sanitized_class_tokens(value.get("containerClassName", ""))
            slide_classes = _sanitized_class_tokens(value.get("slideClassName", ""))
            class_disabled = (
                _has_disabled_class(classes)
                or _has_disabled_class(container_classes)
                or _has_disabled_class(slide_classes)
            )
            return _ControlState(
                enabled=not disabled_attribute and aria_disabled != "true" and not class_disabled,
                aria_disabled=aria_disabled if aria_disabled in {"", "true", "false"} else "other",
                disabled_attribute=disabled_attribute,
                classes=classes,
                container_classes=container_classes,
                slide_classes=slide_classes,
            )
        except Exception:  # noqa: BLE001 -- optional backend response shapes are not stable.
            return _ControlState(
                enabled=False,
                aria_disabled="read_error",
                disabled_attribute=False,
                classes=(),
                container_classes=(),
                slide_classes=(),
                read_error=True,
            )

    @staticmethod
    def _control_state_log_value(state: _ControlState) -> tuple[object, ...]:
        return (
            state.enabled,
            state.aria_disabled,
            state.disabled_attribute,
            state.classes,
            state.container_classes,
            state.slide_classes,
            state.read_error,
        )

    @property
    def _timeout_seconds(self) -> float:
        return max(1, self.timeout_ms / 1000)

    async def _close(self) -> None:
        browser = self._browser
        tab = self._tab
        self._browser = None
        self._tab = None
        callback_id = self._network_callback_id
        self._network_callback_id = None
        await self._cleanup_tab_listener(
            tab,
            callback_id,
            self._network_events_enabled_by_session,
        )
        self._network_events_enabled_by_session = False
        if browser is None:
            return
        try:
            await browser.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001 -- optional backend exceptions are not stable.
            try:
                await browser.stop()
            except Exception:  # noqa: BLE001 -- optional backend exceptions are not stable.
                await browser.close()

    def _on_response_received(self, event: dict[str, Any]) -> None:
        """Retain only sanitized status/resource evidence from the current browser search."""
        params = event.get("params")
        if not isinstance(params, dict):
            return
        response = params.get("response")
        if not isinstance(response, dict):
            return
        status_value = response.get("status")
        resource_type = str(params.get("type", "")).strip().lower()
        if not isinstance(status_value, (bytes, float, int, str)):
            return
        try:
            status = int(status_value)
        except (TypeError, ValueError):
            return
        if is_rate_limit_response(status, resource_type) or (
            protection_trigger_from_http_response(status, resource_type) == "http_403_main"
        ):
            self._network_responses.add((status, resource_type))


class _PydollSessionContext:
    """Adapt Pydoll's concrete async context to the stable semantic protocol."""

    def __init__(self, session: _PydollSession) -> None:
        self._session = session

    async def __aenter__(self) -> PydollBrowserSession:
        return await self._session.__aenter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> bool | None:
        await self._session.__aexit__(exc_type, exc_value, traceback)
        return None


def _normalize_station(value: str) -> str:
    return " ".join(value.split()).removesuffix("역")


def _log_protection_snapshot(
    snapshot: PydollPageSnapshot,
    stage: str,
    trigger: str,
) -> None:
    marker_surface_count = sum(
        protection_trigger_from_text(text) == trigger for text in snapshot.protection_texts
    )
    logger.warning(
        "KORAIL Pydoll protection evidence stage=%s trigger=%s rows=%d "
        "visible_surfaces=%d marker_surfaces=%d network=%s",
        stage,
        trigger,
        len(snapshot.rows),
        len(snapshot.protection_texts),
        marker_surface_count,
        snapshot.network_responses,
    )


def _sanitized_class_tokens(value: object) -> tuple[str, ...]:
    """Keep bounded CSS token metadata without persisting page text or request values."""
    return tuple(
        token for token in str(value).split()[:8] if re.fullmatch(r"[A-Za-z0-9_-]{1,40}", token)
    )


def _has_disabled_class(tokens: tuple[str, ...]) -> bool:
    return bool({"disabled", "off", "slick-disabled"} & set(tokens))


def _train_row_identity(row: PydollTrainRow) -> tuple[str, str, str]:
    return (
        " ".join(row.kind_text.split()),
        " ".join(row.train_number.split()),
        " ".join(row.route_text.split()),
    )


def _deduplicate_snapshot(snapshot: PydollPageSnapshot) -> PydollPageSnapshot:
    return _merge_page_snapshots(
        PydollPageSnapshot(body_text=snapshot.body_text, rows=()),
        snapshot,
    )


def _merge_page_snapshots(
    accumulated: PydollPageSnapshot,
    candidate: PydollPageSnapshot,
) -> PydollPageSnapshot:
    rows = list(accumulated.rows)
    positions = {_train_row_identity(row): index for index, row in enumerate(rows)}
    for row in candidate.rows:
        identity = _train_row_identity(row)
        existing = positions.get(identity)
        if existing is None:
            positions[identity] = len(rows)
            rows.append(row)
        else:
            rows[existing] = row
    return PydollPageSnapshot(
        body_text=candidate.body_text,
        rows=tuple(rows),
        protection_texts=tuple(
            dict.fromkeys((*accumulated.protection_texts, *candidate.protection_texts))
        ),
        network_responses=tuple(
            sorted(set(accumulated.network_responses) | set(candidate.network_responses))
        ),
    )


def _snapshot_requires_expansion_stop(snapshot: PydollPageSnapshot) -> bool:
    if snapshot.network_responses:
        return True
    trigger = protection_trigger_from_text(snapshot.body_text)
    if trigger is None:
        return False
    if trigger not in _GENERIC_PROTECTION_TRIGGERS:
        return True
    return not snapshot.rows or any(
        protection_trigger_from_text(text) in _GENERIC_PROTECTION_TRIGGERS
        for text in snapshot.protection_texts
    )


def _normalize_train_number(value: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z-]", "", " ".join(value.split()))
    if not normalized or len(normalized) > 40:
        raise BrowserSourceUnavailable("read_result")
    digits = "".join(character for character in normalized if character.isdigit())
    return digits.lstrip("0") or "0"


def _snapshot_has_unique_reservation_target(
    snapshot: PydollPageSnapshot,
    request: KorailReservationRequest,
) -> bool:
    """Skip expansion only when the initial DOM proves one exact target train."""

    matches = 0
    requested_number = _normalize_train_number(request.train_number)
    for row in snapshot.rows:
        try:
            row_number = _normalize_train_number(row.train_number)
        except BrowserSourceUnavailable:
            continue
        if row_number != requested_number:
            continue
        type_text = re.sub(
            rf"(?<!\d)0*{re.escape(requested_number)}(?!\d)",
            "",
            " ".join(row.kind_text.split()),
        )
        if request.train_type is not None and (
            re.sub(r"\s+", "", type_text).casefold()
            != re.sub(r"\s+", "", request.train_type).casefold()
        ):
            continue
        route_match = _ROUTE_HEADING.fullmatch(" ".join(row.route_text.split()))
        if route_match is None:
            continue
        origin, destination, departure, arrival = route_match.groups()
        if not (
            _normalize_station(origin) == request.origin
            and _normalize_station(destination) == request.destination
            and departure == request.departure_time.strftime("%H:%M")
            and arrival == request.arrival_time.strftime("%H:%M")
        ):
            continue
        matches += 1
        if matches > 1:
            return False
    return matches == 1


def _has_exact_train_number_marker(body: str, train_number: str) -> bool:
    """Match one official train number while tolerating display-only leading zeroes."""

    normalized = _normalize_train_number(train_number)
    return re.search(rf"(?<!\d)0*{re.escape(normalized)}(?!\d)", body) is not None


def _has_exact_text_marker(body: str, marker: str) -> bool:
    token = rf"(?<![0-9A-Za-z가-힣]){re.escape(marker)}(?![0-9A-Za-z가-힣])"
    return re.search(token, body) is not None


def _has_exact_route_markers(body: str, origin: str, destination: str) -> bool:
    origin_match = re.search(
        rf"(?<![0-9A-Za-z가-힣]){re.escape(_normalize_station(origin))}(?:역)?"
        r"(?![0-9A-Za-z가-힣])",
        body,
    )
    if origin_match is None:
        return False
    return (
        re.search(
            rf"(?<![0-9A-Za-z가-힣]){re.escape(_normalize_station(destination))}(?:역)?"
            r"(?![0-9A-Za-z가-힣])",
            body[origin_match.end() :],
        )
        is not None
    )


def _reservation_date_markers(value: date) -> tuple[str, ...]:
    return (
        value.isoformat(),
        value.strftime("%Y.%m.%d"),
        value.strftime("%Y. %m. %d"),
        f"{value.year}년{value.month:02d}월{value.day:02d}일",
        f"{value.year}년 {value.month}월 {value.day}일",
        f"{value.month}월 {value.day}일",
    )


def _set_chromium_binary(options: Any) -> None:
    configured = os.environ.get("KORAIL_BROWSER_CHROMIUM_EXECUTABLE_PATH", "").strip()
    if configured:
        path = Path(configured)
        if not path.is_file():
            raise BrowserSourceUnavailable("browser_binary")
        options.binary_location = str(path)
        return
    playwright_root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/ms-playwright"))
    candidates = sorted(playwright_root.glob("chromium-*/chrome-linux/chrome"), reverse=True)
    if candidates:
        options.binary_location = str(candidates[0])
