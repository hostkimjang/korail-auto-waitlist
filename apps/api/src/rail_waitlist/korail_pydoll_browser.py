from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import time
from collections.abc import AsyncIterable, Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from datetime import time as clock_time
from pathlib import Path
from typing import Any, ClassVar, Protocol, Self, cast
from urllib.parse import urlsplit

from .korail_browser_automation import (
    FULLSTACK_E2E_PAGE_URL,
    OFFICIAL_KORAIL_SEARCH_URL,
    BrowserSeatSearchRequest,
    BrowserSeatSearchResult,
    BrowserSourceUnavailable,
    is_rate_limit_response,
    protection_trigger_from_http_response,
    protection_trigger_from_text,
)
from .korail_http_replay import (
    HttpReplayInvalidCapture,
    KorailHttpReplayClient,
    KorailHttpReplayPlan,
    build_http_replay_plan,
)
from .korail_pydoll_auth_actor import (
    ActivePydollAuthenticationSession as _ActivePydollSession,
)
from .korail_pydoll_auth_actor import (
    KorailCredentialInput as AuthKorailCredentialInput,
)
from .korail_pydoll_auth_actor import (
    KorailLoginMethod as AuthKorailLoginMethod,
)
from .korail_pydoll_auth_actor import (
    KorailSessionActorSnapshot as AuthKorailSessionActorSnapshot,
)
from .korail_pydoll_auth_actor import (
    KorailSessionActorState as AuthKorailSessionActorState,
)
from .korail_pydoll_auth_actor import (
    PydollAuthenticationSessionActor,
    PydollAuthenticationSessionLease,
    credential_fingerprint,
)
from .korail_pydoll_confirmation_reader import (
    _parse_korail_payment_deadline,
    read_korail_same_session_confirmation,
)
from .korail_pydoll_contracts import (
    PydollPageSnapshot,
    PydollSeatBox,
    PydollTrainRow,
    normalize_korail_train_number,
)
from .korail_pydoll_contracts import (
    normalize_korail_station as _normalize_station,
)
from .korail_pydoll_http_replay import DEFAULT_HTTP_REPLAY_ROUTE_CACHE_SIZE
from .korail_pydoll_login_driver import (
    LoginAttemptState,
    PydollLoginDomDriver,
    login_step,
)
from .korail_pydoll_page_safety import (
    GENERIC_PROTECTION_TRIGGERS as _GENERIC_PROTECTION_TRIGGERS,
)
from .korail_pydoll_page_safety import (
    assert_pydoll_response_allowed,
)
from .korail_pydoll_reservation_actor import (
    KorailReservationOutcome as ActorKorailReservationOutcome,
)
from .korail_pydoll_reservation_actor import (
    KorailReservationRequest as ActorKorailReservationRequest,
)
from .korail_pydoll_reservation_actor import (
    KorailReservationResult as ActorKorailReservationResult,
)
from .korail_pydoll_reservation_actor import (
    KorailReservationSeatClass as ActorKorailReservationSeatClass,
)
from .korail_pydoll_reservation_actor import (
    PydollReservationActor,
    has_unique_reservation_target,
)
from .korail_pydoll_reservation_actor import (
    assert_reservation_identity as assert_actor_reservation_identity,
)
from .korail_pydoll_reservation_driver import (
    PydollReservationDomDriver,
    ReservationControlState,
)
from .korail_pydoll_reservation_driver import (
    ReservationAttemptState as _ReservationAttemptState,
)
from .korail_pydoll_search_actor import PydollReadOnlySearchActor
from .korail_reservation_confirmation import KorailSameSessionDetailEvidence
from .korail_search_bootstrap import (
    KorailStationIdentityResolver,
    build_korail_general_search_url,  # noqa: F401 -- compatibility module export.
    validate_korail_general_search_url,
)
from .reservation_confirmation import ReservationConfirmationTarget

_MAX_MORE_RESULT_ACTIONS = 19
# Compatibility seam: focused tests patch this facade value before construction;
# the canonical manager receives that value and owns the actual eviction behavior.
_HTTP_REPLAY_ROUTE_CACHE_SIZE = DEFAULT_HTTP_REPLAY_ROUTE_CACHE_SIZE
_PROTECTION_SURFACE_SELECTOR = (
    '[role="alert"], dialog[open], [aria-modal="true"], .alert, .error, .popup, .modal'
)
_KORAIL_RESERVATION_LIST_URL = "https://www.korail.com/ticket/reservation/list"
logger = logging.getLogger(__name__)

_PydollSessionLease = PydollAuthenticationSessionLease
_credential_fingerprint = credential_fingerprint
KorailCredentialInput = AuthKorailCredentialInput
KorailLoginMethod = AuthKorailLoginMethod
KorailSessionActorSnapshot = AuthKorailSessionActorSnapshot
KorailSessionActorState = AuthKorailSessionActorState
KorailReservationOutcome = ActorKorailReservationOutcome
KorailReservationRequest = ActorKorailReservationRequest
KorailReservationResult = ActorKorailReservationResult
KorailReservationSeatClass = ActorKorailReservationSeatClass
_snapshot_has_unique_reservation_target = has_unique_reservation_target


@dataclass(frozen=True)
class _ControlState(ReservationControlState):
    enabled: bool
    aria_disabled: str
    disabled_attribute: bool
    classes: tuple[str, ...]
    container_classes: tuple[str, ...]
    slide_classes: tuple[str, ...]
    read_error: bool = False


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
        self._monotonic = monotonic
        self._auth_actor = PydollAuthenticationSessionActor[PydollBrowserSession](
            page_url=self.page_url,
            timeout_ms=self.timeout_ms,
            headless=self.headless,
            session_factory=self._session_factory,
            session_reuse_ttl_seconds=self._session_reuse_ttl_seconds,
            session_reuse_max_searches=self._session_reuse_max_searches,
            monotonic=self._monotonic,
            cleanup=_finish_owned_cleanup,
            response_safety_guard=self._assert_response_allowed,
            fingerprint=_credential_fingerprint,
        )
        # Capture module-level compatibility seams at construction time before handing
        # read-only lifecycle ownership to the search actor.
        self._search_actor = PydollReadOnlySearchActor(
            page_url=self.page_url,
            timeout_ms=self.timeout_ms,
            headless=self.headless,
            session_factory=self._session_factory,
            session_reuse_ttl_seconds=self._session_reuse_ttl_seconds,
            session_reuse_max_searches=self._session_reuse_max_searches,
            station_identity_resolver=station_identity_resolver,
            monotonic=self._monotonic,
            cleanup=_finish_owned_cleanup,
            response_safety_guard=self._assert_response_allowed,
            http_replay_client_factory=KorailHttpReplayClient,
            http_replay_route_cache_size=_HTTP_REPLAY_ROUTE_CACHE_SIZE,
            event_logger=logger,
        )
        self._reservation_actor = PydollReservationActor[PydollBrowserSession](
            auth_lock=self._session_lock,
            direct_search_url=self._direct_search_url,
            discard_if_credential_changed=self._auth_actor.discard_if_credential_changed,
            acquire_session=self._acquire_session,
            ensure_authenticated_session=self._ensure_authenticated_session,
            discard_with_state=self._auth_actor.discard_with_state,
            response_safety_guard=self._assert_response_allowed,
            reservation_identity_guard=self._assert_reservation_identity,
            has_unique_reservation_target=_snapshot_has_unique_reservation_target,
            max_more_result_actions=_MAX_MORE_RESULT_ACTIONS,
            utc_now=lambda: datetime.now(UTC),
        )

    @property
    def _session_lock(self) -> asyncio.Lock:
        """Compatibility view of the authentication actor serialization boundary."""

        return self._auth_actor.lock

    @property
    def _active_session(self) -> _ActivePydollSession[PydollBrowserSession] | None:
        return self._auth_actor.active_session

    @_active_session.setter
    def _active_session(
        self,
        value: _ActivePydollSession[PydollBrowserSession] | None,
    ) -> None:
        self._auth_actor.active_session = value

    @property
    def _session_actor_state(self) -> KorailSessionActorState:
        return self._auth_actor.state

    @_session_actor_state.setter
    def _session_actor_state(self, value: KorailSessionActorState) -> None:
        self._auth_actor.state = value

    @property
    def _session_actor_generation(self) -> str | None:
        return self._auth_actor.generation

    @_session_actor_generation.setter
    def _session_actor_generation(self, value: str | None) -> None:
        self._auth_actor.generation = value

    @property
    def _session_actor_created_at(self) -> float | None:
        return self._auth_actor.created_at

    @_session_actor_created_at.setter
    def _session_actor_created_at(self, value: float | None) -> None:
        self._auth_actor.created_at = value

    @property
    def _session_actor_last_verified_at(self) -> float | None:
        return self._auth_actor.last_verified_at

    @_session_actor_last_verified_at.setter
    def _session_actor_last_verified_at(self, value: float | None) -> None:
        self._auth_actor.last_verified_at = value

    @property
    def _session_actor_last_used_at(self) -> float | None:
        return self._auth_actor.last_used_at

    @_session_actor_last_used_at.setter
    def _session_actor_last_used_at(self, value: float | None) -> None:
        self._auth_actor.last_used_at = value

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
        return await self._search_actor.search(request)

    async def reserve_once(
        self,
        request: KorailReservationRequest,
    ) -> KorailReservationResult:
        """Run one exact booking attempt and stop before every payment action."""
        return await self._reservation_actor.reserve_once(request)

    async def _direct_search_url(
        self,
        origin: str,
        destination: str,
        travel_date: date,
        departure_time: clock_time,
    ) -> str | None:
        return await self._search_actor.direct_search_url(
            origin,
            destination,
            travel_date,
            departure_time,
        )

    async def verify_credentials(self, credential: KorailCredentialInput) -> bool:
        """Authenticate once without submitting a timetable search or reservation."""
        return await self._auth_actor.verify_credentials(credential)

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
        return await self._auth_actor.prewarm_credentials(credential)

    def session_snapshot(self) -> KorailSessionActorSnapshot:
        """Return non-secret process-local authentication actor telemetry."""
        return self._auth_actor.snapshot()

    async def close(self) -> None:
        """Close the bounded in-memory browser sessions owned by this client."""

        # The only operation that needs both actor locks always takes authenticated
        # session -> read-only search. No search or authentication path takes the
        # other actor's lock, so shutdown cannot form a lock-order cycle.
        async with self._session_lock:
            await self._search_actor.close()
            await self._auth_actor.close_locked()

    @property
    def _active_http_replays(self) -> Mapping[tuple[str, str], object]:
        """Read-only compatibility inspection for focused replay lifecycle tests."""

        return self._search_actor.active_http_replays

    @property
    def _active_search_session(self) -> object | None:
        """Read-only compatibility inspection for search-session cleanup tests."""

        return self._search_actor.active_session

    async def _acquire_session(
        self,
        *,
        credential_version: str | None = None,
    ) -> _PydollSessionLease[PydollBrowserSession]:
        return await self._auth_actor.acquire_session(credential_version=credential_version)

    async def _ensure_authenticated_session(
        self,
        session: PydollBrowserSession,
        credential: KorailCredentialInput,
    ) -> bool:
        return await self._auth_actor.ensure_authenticated_session(session, credential)

    async def _discard_active_session(self) -> None:
        await self._auth_actor.discard_active_session()

    @property
    def _session_reuse_enabled(self) -> bool:
        return self._auth_actor.reuse_enabled

    @staticmethod
    async def _assert_reservation_identity(
        session: PydollBrowserSession,
        request: KorailReservationRequest,
        stage: str,
    ) -> None:
        await assert_actor_reservation_identity(session, request, stage)

    @staticmethod
    def _assert_response_allowed(snapshot: PydollPageSnapshot, stage: str) -> None:
        assert_pydoll_response_allowed(snapshot, stage, event_logger=logger)

    @staticmethod
    def _read_result(
        snapshot: PydollPageSnapshot,
        request: BrowserSeatSearchRequest,
    ) -> BrowserSeatSearchResult:
        return PydollReadOnlySearchActor.read_result(snapshot, request)


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
        self._login_driver = PydollLoginDomDriver(
            port=self,
            page_url=self.page_url,
            timeout_ms=self.timeout_ms,
            timeout_seconds=self._timeout_seconds,
            go_to=self._login_go_to,
            execute_script=self._login_execute_script,
            snapshot=lambda: self._snapshot(),
            visible_elements=lambda selector, **options: self._visible_elements(
                selector,
                **options,
            ),
            has_exact_visible=lambda selector, text: self._has_exact_visible(selector, text),
            wait_for_exact_text=lambda selector, text: self._wait_for_exact_text(selector, text),
            reset_search_state=self._reset_login_search_state,
            response_safety_guard=lambda snapshot, stage: assert_pydoll_response_allowed(
                snapshot,
                stage,
                event_logger=logger,
            ),
            monotonic=time.monotonic,
            sleep=asyncio.sleep,
            event_logger=logger,
        )
        self._reservation_driver = PydollReservationDomDriver(
            port=self,
            timeout_ms=self.timeout_ms,
            timeout_seconds=self._timeout_seconds,
            execute_script=self._login_execute_script,
            visible_elements=lambda selector, **options: self._visible_elements(
                selector,
                **options,
            ),
            current_schedule=lambda: self.current_schedule(),
            read_control_state=lambda element: self._read_control_state(element),
            monotonic=time.monotonic,
            sleep=asyncio.sleep,
            utc_now=lambda: datetime.now(UTC),
            event_logger=logger,
        )

    def _login_go_to(self, url: str, timeout: int) -> Awaitable[object]:
        return cast(Awaitable[object], self._tab.go_to(url, timeout=timeout))

    def _login_execute_script(
        self,
        script: str,
        *,
        return_by_value: bool,
        await_promise: bool,
        timeout: int,
    ) -> Awaitable[object]:
        return cast(
            Awaitable[object],
            self._tab.execute_script(
                script,
                return_by_value=return_by_value,
                await_promise=await_promise,
                timeout=timeout,
            ),
        )

    def _reset_login_search_state(self) -> None:
        self._submitted = False
        self._network_responses.clear()

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
        return await self._login_driver.ensure_authenticated(credential)

    async def _authenticate_in_place(
        self,
        credential: KorailCredentialInput,
        attempt: LoginAttemptState | None = None,
    ) -> bool:
        """Submit the current login page once without replacing its booking history state."""
        return await self._login_driver.authenticate_in_place(credential, attempt)

    async def _submit_login_form(self, credential: KorailCredentialInput) -> bool:
        """Fill and submit one uniquely scoped official login form."""
        return await self._login_driver.submit_login_form(credential)

    async def _wait_for_login_authentication(
        self,
        attempt: LoginAttemptState | None = None,
    ) -> bool:
        """Observe one submitted login without navigating away from the current route."""
        return await self._login_driver.wait_for_login_authentication(attempt)

    async def _confirm_authenticated_search(
        self,
        attempt: LoginAttemptState,
    ) -> bool:
        """Require the authenticated header to persist on the official search page."""
        return await self._login_driver.confirm_authenticated_search(attempt)

    async def _probe_official_authenticated_session(self) -> bool:
        """Return only the official loginCheck boolean; never expose its payload."""
        return await self._login_driver.probe_official_authenticated_session()

    async def _has_authenticated_header(self) -> bool:
        """Read the official desktop/mobile authenticated header controls."""
        return await self._login_driver.has_authenticated_header()

    async def _wait_for_authenticated_header(self) -> bool:
        """Wait for the asynchronous official loginCheck hydration to finish."""
        return await self._login_driver.wait_for_authenticated_header()

    @staticmethod
    async def _login_step(stage: str, awaitable: Awaitable[Any]) -> Any:
        """Map browser-library failures to a secret-free, code-owned login stage."""
        return await login_step(stage, awaitable)

    async def _wait_for_unique_login_method_tab(
        self,
        login_method: KorailLoginMethod,
    ) -> Any | None:
        """Wait for one SPA-rendered method tab without repeating navigation or clicks."""
        return await self._login_driver.wait_for_unique_login_method_tab(login_method)

    async def _wait_for_login_controls(
        self,
        login_method: KorailLoginMethod,
    ) -> tuple[Any, Any, Any] | None:
        """Resolve one active official login panel without assuming one HTML form.

        The official page renders the identifier and password in separate ``form``
        elements and places the submit button outside both.  The active tab panel is
        the stable accessibility boundary shared by all three supported login methods.
        """

        return await self._login_driver.wait_for_login_controls(login_method)

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
        return await self._reservation_driver.reserve_once(request)

    async def _has_exact_preserved_booking_state(
        self,
        request: KorailReservationRequest,
    ) -> bool:
        return await self._reservation_driver.has_exact_preserved_booking_state(request)

    async def _actionable_seat_controls(
        self,
        row: Any,
        seat_class_label: str,
    ) -> list[Any]:
        return await self._reservation_driver.actionable_seat_controls(
            row,
            seat_class_label,
        )

    async def _seat_price_box_metadata(self, element: Any) -> tuple[str, tuple[str, ...]]:
        return await self._reservation_driver.seat_price_box_metadata(element)

    async def _row_matches_reservation(
        self,
        row: Any,
        request: KorailReservationRequest,
    ) -> bool:
        return await self._reservation_driver.row_matches_reservation(row, request)

    async def _probe_reservation_terminal(
        self,
        request: KorailReservationRequest,
        attempt: _ReservationAttemptState | None = None,
    ) -> KorailReservationResult | None:
        return await self._reservation_driver.probe_reservation_terminal(request, attempt)

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
    try:
        return normalize_korail_train_number(value)
    except ValueError as error:
        raise BrowserSourceUnavailable("read_result") from error


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
