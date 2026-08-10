from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import time
from collections.abc import AsyncIterable as AsyncIterable
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass as dataclass
from datetime import UTC, date, datetime
from datetime import time as clock_time
from typing import Any, Protocol, Self, cast
from urllib.parse import urlsplit

from .korail_sidecar.browser_contracts import (
    BrowserSeatSearchRequest,
    BrowserSeatSearchResult,
    BrowserSourceUnavailable,
)
from .korail_sidecar.browser_page_contracts import (
    FULLSTACK_E2E_PAGE_URL,
    OFFICIAL_KORAIL_SEARCH_URL,
)
from .korail_sidecar.browser_protection import (
    is_rate_limit_response,
    protection_trigger_from_http_response,
    protection_trigger_from_text,
)
from .korail_sidecar.http_replay import (
    HttpReplayInvalidCapture,
    KorailHttpReplayClient,
    KorailHttpReplayPlan,
    build_http_replay_plan,
)
from .korail_sidecar.pydoll import dom_interaction as _dom_interaction_owner
from .korail_sidecar.pydoll import live_dom as _live_dom_owner
from .korail_sidecar.pydoll import search_hour_carousel_input as _search_hour_carousel_input_owner
from .korail_sidecar.pydoll import (
    search_hour_carousel_observation as _search_hour_carousel_observation_owner,
)
from .korail_sidecar.pydoll import search_hour_policy as _search_hour_policy_owner
from .korail_sidecar.pydoll import search_schedule_commit as _search_schedule_commit_owner
from .korail_sidecar.pydoll.auth_actor import (
    ActivePydollAuthenticationSession as _ActivePydollSession,
)
from .korail_sidecar.pydoll.auth_actor import (
    KorailSessionActorSnapshot as AuthKorailSessionActorSnapshot,
)
from .korail_sidecar.pydoll.auth_actor import (
    KorailSessionActorState as AuthKorailSessionActorState,
)
from .korail_sidecar.pydoll.auth_actor import (
    PydollAuthenticationSessionActor,
    PydollAuthenticationSessionLease,
    credential_fingerprint,
)
from .korail_sidecar.pydoll.auth_contracts import (
    KorailCredentialInput as AuthKorailCredentialInput,
)
from .korail_sidecar.pydoll.auth_contracts import (
    KorailLoginMethod as AuthKorailLoginMethod,
)
from .korail_sidecar.pydoll.chromium_lifecycle import (
    PydollChromiumLifecycle,
    cleanup_pydoll_tab_listener,
)
from .korail_sidecar.pydoll.chromium_lifecycle import (
    configure_chromium_options as _configure_chromium_options,
)
from .korail_sidecar.pydoll.chromium_lifecycle import (
    finish_owned_cleanup as _finish_owned_cleanup,
)
from .korail_sidecar.pydoll.chromium_lifecycle import (
    probe_pydoll_chromium as probe_pydoll_chromium,
)
from .korail_sidecar.pydoll.chromium_lifecycle import (
    set_chromium_binary as _set_chromium_binary,  # noqa: F401 -- compatibility export.
)
from .korail_sidecar.pydoll.confirmation_reader import (
    _parse_korail_payment_deadline,
    read_korail_same_session_confirmation,
)
from .korail_sidecar.pydoll.http_replay import DEFAULT_HTTP_REPLAY_ROUTE_CACHE_SIZE
from .korail_sidecar.pydoll.login_driver import (
    LoginAttemptState,
    PydollLoginDomDriver,
    login_step,
)
from .korail_sidecar.pydoll.page_contracts import (
    PydollPageSnapshot,
    PydollSeatBox,  # noqa: F401 -- compatibility module export.
    PydollTrainRow,  # noqa: F401 -- compatibility module export.
    normalize_korail_train_number,
)
from .korail_sidecar.pydoll.page_contracts import (
    normalize_korail_station as _normalize_station,
)
from .korail_sidecar.pydoll.page_safety import (
    GENERIC_PROTECTION_TRIGGERS as _GENERIC_PROTECTION_TRIGGERS,  # noqa: F401
)
from .korail_sidecar.pydoll.page_safety import (
    assert_pydoll_response_allowed,
)
from .korail_sidecar.pydoll.reservation_actor import (
    PydollReservationActor,
    has_unique_reservation_target,
)
from .korail_sidecar.pydoll.reservation_actor import (
    assert_reservation_identity as assert_actor_reservation_identity,
)
from .korail_sidecar.pydoll.reservation_contracts import (
    KorailReservationOutcome as ActorKorailReservationOutcome,
)
from .korail_sidecar.pydoll.reservation_contracts import (
    KorailReservationProgressCallback as ActorKorailReservationProgressCallback,
)
from .korail_sidecar.pydoll.reservation_contracts import (
    KorailReservationRequest as ActorKorailReservationRequest,
)
from .korail_sidecar.pydoll.reservation_contracts import (
    KorailReservationResult as ActorKorailReservationResult,
)
from .korail_sidecar.pydoll.reservation_contracts import (
    KorailReservationSeatClass as ActorKorailReservationSeatClass,
)
from .korail_sidecar.pydoll.reservation_driver import (
    PydollReservationDomDriver,
)
from .korail_sidecar.pydoll.reservation_driver import (
    ReservationAttemptState as _ReservationAttemptState,
)
from .korail_sidecar.pydoll.reservation_driver import (
    ReservationControlState as ReservationControlState,
)
from .korail_sidecar.pydoll.search_actor import PydollReadOnlySearchActor
from .korail_sidecar.pydoll.search_driver import (
    PydollSearchDomDriver,
)
from .korail_sidecar.pydoll.search_driver import (
    SearchControlState as SearchControlState,
)
from .korail_sidecar.pydoll.search_driver import (
    SearchHourCandidate as _HourCandidate,
)
from .korail_sidecar.pydoll.search_snapshot_policy import (
    deduplicate_search_snapshot as _deduplicate_snapshot,
)
from .korail_sidecar.pydoll.search_snapshot_policy import (
    merge_search_snapshots as _merge_page_snapshots,
)
from .korail_sidecar.pydoll.search_snapshot_policy import (
    snapshot_requires_expansion_stop as _snapshot_requires_expansion_stop,
)
from .korail_sidecar.pydoll.search_snapshot_policy import (
    train_row_identity as _train_row_identity,
)
from .provider_adapters.korail_search_bootstrap import KorailStationIdentityResolver
from .provider_registry.korail_search_url_policy import (
    build_korail_general_search_url as build_korail_general_search_url,
)
from .provider_registry.korail_search_url_policy import validate_korail_general_search_url
from .reservations.provider_confirmation.contracts import ReservationConfirmationTarget
from .reservations.provider_confirmation.korail import KorailSameSessionDetailEvidence

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
KorailReservationProgressCallback = ActorKorailReservationProgressCallback
KorailReservationRequest = ActorKorailReservationRequest
KorailReservationResult = ActorKorailReservationResult
KorailReservationSeatClass = ActorKorailReservationSeatClass
_snapshot_has_unique_reservation_target = has_unique_reservation_target
_has_disabled_class = _search_hour_policy_owner.has_disabled_class
_ControlState = _live_dom_owner.PydollControlState
_sanitized_class_tokens = _live_dom_owner.sanitized_class_tokens


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

    async def probe_authenticated_session(self) -> bool: ...

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
        *,
        on_progress: KorailReservationProgressCallback | None = None,
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
        *,
        on_progress: KorailReservationProgressCallback | None = None,
    ) -> KorailReservationResult:
        """Run one exact booking attempt and stop before every payment action."""
        if on_progress is None:
            return await self._reservation_actor.reserve_once(request)
        return await self._reservation_actor.reserve_once(request, on_progress=on_progress)

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
            first_error: BaseException | None = None
            for cleanup in (
                self._search_actor.close(),
                self._auth_actor.close_locked(),
            ):
                try:
                    await _finish_owned_cleanup(cleanup)
                except BaseException as error:
                    if first_error is None:
                        first_error = error
            if first_error is not None:
                raise first_error

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
    _evaluate_value_interaction = staticmethod(_dom_interaction_owner.evaluate_value)
    _evaluate_text_interaction = staticmethod(_dom_interaction_owner.evaluate_text)
    _wait_for_value_interaction = staticmethod(_dom_interaction_owner.wait_for_value)
    _click_exact_text_interaction = staticmethod(_dom_interaction_owner.click_exact_text)
    _wait_for_exact_text_interaction = staticmethod(_dom_interaction_owner.wait_for_exact_text)
    _wait_for_enabled_exact_text_interaction = staticmethod(
        _dom_interaction_owner.wait_for_enabled_exact_text
    )
    _wait_for_visible_elements_interaction = staticmethod(
        _dom_interaction_owner.wait_for_visible_elements
    )
    _wait_for_dialog_interaction = staticmethod(_dom_interaction_owner.wait_for_dialog)
    _find_exact_visible_interaction = staticmethod(_dom_interaction_owner.find_exact_visible)
    _has_exact_visible_interaction = staticmethod(_dom_interaction_owner.has_exact_visible)
    _collect_visible_elements = staticmethod(_live_dom_owner.visible_elements)
    _read_control_state = staticmethod(_live_dom_owner.read_control_state)
    _swipe_hour_carousel_input = staticmethod(_search_hour_carousel_input_owner.swipe_hour_carousel)
    _navigate_hour_carousel_by_keyboard_input = staticmethod(
        _search_hour_carousel_input_owner.navigate_hour_carousel_by_keyboard
    )
    _dispatch_mouse_event_input = staticmethod(
        _search_hour_carousel_input_owner.dispatch_mouse_event
    )
    _read_hour_candidates_observation = staticmethod(
        _search_hour_carousel_observation_owner.read_hour_candidates
    )
    _wait_for_hour_window_change_observation = staticmethod(
        _search_hour_carousel_observation_owner.wait_for_hour_window_change
    )
    _log_hour_window_navigation_failure_observation = staticmethod(
        _search_hour_carousel_observation_owner.log_hour_window_navigation_failure
    )
    _wait_for_hour_animation_observation = staticmethod(
        _search_hour_carousel_observation_owner.wait_for_hour_animation
    )
    _hour_carousel_control_metadata_observation = staticmethod(
        _search_hour_carousel_observation_owner.hour_carousel_control_metadata
    )
    _find_hour_navigation_control_observation = staticmethod(
        _search_hour_carousel_observation_owner.find_hour_navigation_control
    )
    _wait_for_schedule_commit = staticmethod(_search_schedule_commit_owner.wait_for_schedule)
    _wait_for_schedule_date_commit = staticmethod(
        _search_schedule_commit_owner.wait_for_schedule_date
    )
    _click_hour_and_confirm_commit = staticmethod(
        _search_schedule_commit_owner.click_hour_and_confirm
    )
    _current_hour_window = staticmethod(_search_hour_policy_owner.current_hour_window)
    _hour_window_signature = staticmethod(_search_hour_policy_owner.hour_window_signature)
    _is_soft_aria_hour = staticmethod(_search_hour_policy_owner.is_soft_aria_hour)
    _is_soft_dom_hour = staticmethod(_search_hour_policy_owner.is_soft_dom_hour)
    _is_exact_hour_catalog = staticmethod(_search_hour_policy_owner.is_exact_hour_catalog)
    _is_soft_adjacent_hour = staticmethod(_search_hour_policy_owner.is_soft_adjacent_hour)
    _is_exact_selected_hour = staticmethod(_search_hour_policy_owner.is_exact_selected_hour)
    _control_state_log_value = staticmethod(_search_hour_policy_owner.control_state_log_value)

    def __init__(self, page_url: str, timeout_ms: int, headless: bool) -> None:
        self.page_url = page_url
        self.timeout_ms = timeout_ms
        self.headless = headless
        self._chromium_lifecycle = PydollChromiumLifecycle(
            headless=headless,
            on_response=self._on_response_received,
            options_configurer=_configure_chromium_options,
            event_logger=logger,
        )
        self._submitted = False
        self._network_responses: dict[tuple[int, str], None] = {}
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
        self._search_driver = PydollSearchDomDriver(
            port=self,
            timeout_seconds=self._timeout_seconds,
            query=self._search_query,
            execute_script=self._search_execute_script,
            evaluate_value=lambda selector: self._evaluate_value(selector),
            evaluate_text=lambda selector: self._evaluate_text(selector),
            is_submitted=lambda: self._submitted,
            mark_submitted=self._mark_search_submitted,
            network_responses=lambda: self._network_responses,
            deduplicate_snapshot=_deduplicate_snapshot,
            merge_page_snapshots=_merge_page_snapshots,
            snapshot_requires_expansion_stop=_snapshot_requires_expansion_stop,
            train_row_identity=_train_row_identity,
            monotonic=time.monotonic,
            sleep=asyncio.sleep,
            protection_surface_selector=_PROTECTION_SURFACE_SELECTOR,
        )

    @property
    def _browser(self) -> Any:
        return self._chromium_lifecycle.browser

    @_browser.setter
    def _browser(self, value: Any) -> None:
        self._chromium_lifecycle.browser = value

    @property
    def _tab(self) -> Any:
        return self._chromium_lifecycle.tab

    @_tab.setter
    def _tab(self, value: Any) -> None:
        self._chromium_lifecycle.tab = value

    @property
    def _network_callback_id(self) -> int | None:
        return self._chromium_lifecycle.callback_id

    @_network_callback_id.setter
    def _network_callback_id(self, value: int | None) -> None:
        self._chromium_lifecycle.callback_id = value

    @property
    def _network_events_enabled_by_session(self) -> bool:
        return self._chromium_lifecycle.network_events_enabled_by_owner

    @_network_events_enabled_by_session.setter
    def _network_events_enabled_by_session(self, value: bool) -> None:
        self._chromium_lifecycle.network_events_enabled_by_owner = value

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

    def _search_query(self, selector: str, **options: object) -> Awaitable[Any]:
        return cast(Awaitable[Any], self._tab.query(selector, **options))

    def _search_execute_script(
        self,
        script: str,
        *,
        return_by_value: bool,
    ) -> Awaitable[object]:
        return cast(
            Awaitable[object],
            self._tab.execute_script(script, return_by_value=return_by_value),
        )

    def _mark_search_submitted(self) -> None:
        self._submitted = True

    def _reset_login_search_state(self) -> None:
        self._submitted = False
        self._network_responses.clear()

    async def __aenter__(self) -> Self:
        await self._chromium_lifecycle.start()
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self._chromium_lifecycle.close(raise_on_failure=exc_type is None)

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
        try:
            await self._tab.go_to(url, timeout=max(1, self.timeout_ms // 1000))
        except Exception as error:  # noqa: BLE001 -- Pydoll is an optional sidecar extra.
            if (
                type(error).__module__ != "pydoll.exceptions"
                or type(error).__name__ != "PageLoadTimeout"
            ):
                raise
            # Chromium can reach the result DOM while a non-essential resource keeps
            # Pydoll's LOAD_EVENT_FIRED wait open. The caller still runs the ordinary
            # protection, result and exact-train checks, so preserve that verified
            # path instead of discarding an otherwise usable reservation attempt.
            logging.getLogger(__name__).warning(
                "KORAIL direct navigation load signal timed out; validating current DOM"
            )
        return await self._snapshot()

    async def navigate_fresh(self, url: str) -> PydollPageSnapshot:
        """Navigate a fresh tab in the current browser context to a validated result URL."""

        try:
            validate_korail_general_search_url(url)
        except ValueError as error:
            raise BrowserSourceUnavailable("direct_navigation") from error
        capture_started = getattr(self, "_http_capture_start", None) is not None
        if self._opened_once:
            await self._replace_tab()
            if capture_started:
                self._http_capture_start = len(await self._tab.get_network_logs())
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
        await self._chromium_lifecycle.replace_tab()

    async def _attach_network_listener(self, tab: Any) -> tuple[int, bool]:
        return await self._chromium_lifecycle.attach_network_listener(tab)

    @staticmethod
    async def _cleanup_tab_listener(
        tab: Any,
        callback_id: int | None,
        network_events_enabled_by_session: bool,
    ) -> None:
        await cleanup_pydoll_tab_listener(
            tab,
            callback_id,
            network_events_enabled_by_session,
            event_logger=logger,
        )

    async def choose_station(self, kind: str, station: str) -> None:
        await self._search_driver.choose_station(kind, station)

    async def choose_schedule(self, travel_date: date, departure_hour: int) -> None:
        await self._search_driver.choose_schedule(travel_date, departure_hour)

    async def current_station(self, kind: str) -> str:
        return await self._search_driver.current_station(kind)

    async def current_schedule(self) -> tuple[date, int]:
        return await self._search_driver.current_schedule()

    async def current_passenger(self) -> str:
        return await self._search_driver.current_passenger()

    async def ensure_authenticated(self, credential: KorailCredentialInput) -> bool:
        """Use one explicit official login method and verify an authenticated header."""
        return await self._login_driver.ensure_authenticated(credential)

    async def probe_authenticated_session(self) -> bool:
        """Validate and refresh the current official session without exposing its payload."""
        return await self._login_driver.probe_official_authenticated_session()

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
        await self._search_driver.submit_once()

    async def wait_for_result(self) -> PydollPageSnapshot:
        return await self._search_driver.wait_for_result()

    async def expand_results(
        self,
        snapshot: PydollPageSnapshot,
        max_actions: int,
    ) -> PydollPageSnapshot:
        return await self._search_driver.expand_results(snapshot, max_actions)

    async def reserve_once(
        self,
        request: KorailReservationRequest,
        *,
        on_progress: KorailReservationProgressCallback | None = None,
    ) -> KorailReservationResult:
        if on_progress is None:
            return await self._reservation_driver.reserve_once(request)
        return await self._reservation_driver.reserve_once(request, on_progress=on_progress)

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
        return await self._search_driver.wait_for_result_growth(previous_rows)

    async def _snapshot(self) -> PydollPageSnapshot:
        return await self._search_driver.snapshot()

    async def _evaluate_value(self, selector: str) -> object:
        return await self._evaluate_value_interaction(self._tab, selector)

    async def _evaluate_text(self, selector: str) -> str:
        return await self._evaluate_text_interaction(self._tab, selector)

    async def _wait_for_value(
        self, selector: str, expected: str, *, contains: bool = False
    ) -> None:
        await self._wait_for_value_interaction(
            self,
            selector,
            expected,
            contains=contains,
            timeout_seconds=lambda: self._timeout_seconds,
            monotonic=time.monotonic,
            sleep=asyncio.sleep,
            source_unavailable_type=BrowserSourceUnavailable,
        )

    async def _click_exact_text(self, selector: str, text: str) -> None:
        await self._click_exact_text_interaction(self, selector, text)

    async def _wait_for_exact_text(self, selector: str, text: str, *, scope: Any = None) -> Any:
        return await self._wait_for_exact_text_interaction(
            self,
            selector,
            text,
            scope=scope,
            timeout_seconds=lambda: self._timeout_seconds,
            monotonic=time.monotonic,
            sleep=asyncio.sleep,
            source_unavailable_type=BrowserSourceUnavailable,
        )

    async def _wait_for_enabled_exact_text(
        self,
        selector: str,
        text: str,
        *,
        scope: Any = None,
        failure_stage: str = "disabled_control",
        accepted_labels: tuple[str, ...] = (),
    ) -> Any:
        return await self._wait_for_enabled_exact_text_interaction(
            self,
            selector,
            text,
            scope=scope,
            failure_stage=failure_stage,
            accepted_labels=accepted_labels,
            timeout_seconds=lambda: self._timeout_seconds,
            monotonic=time.monotonic,
            sleep=asyncio.sleep,
            event_logger=logger,
            source_unavailable_type=BrowserSourceUnavailable,
        )

    async def _read_hour_candidates(
        self,
        selector: str,
        *,
        scope: Any,
        visible_only: bool = True,
    ) -> list[_HourCandidate]:
        return await self._read_hour_candidates_observation(
            self,
            selector,
            scope=scope,
            visible_only=visible_only,
        )

    async def _wait_for_hour_window_change(
        self,
        dialog: Any,
        before: tuple[int, ...],
        direction: str,
        *,
        timeout_seconds: float | None = None,
    ) -> bool:
        return await self._wait_for_hour_window_change_observation(
            self,
            dialog,
            before,
            direction,
            timeout_seconds=timeout_seconds,
            default_timeout_seconds=self._timeout_seconds,
            monotonic=time.monotonic,
            sleep=asyncio.sleep,
        )

    async def _log_hour_window_navigation_failure(
        self,
        dialog: Any,
        before: tuple[int, ...],
    ) -> None:
        await self._log_hour_window_navigation_failure_observation(
            self,
            dialog,
            before,
            event_logger=logger,
        )

    async def _wait_for_hour_animation(
        self,
        dialog: Any,
        expected_hours: tuple[int, ...],
    ) -> None:
        await self._wait_for_hour_animation_observation(
            self,
            dialog,
            expected_hours,
            sleep=asyncio.sleep,
        )

    @staticmethod
    async def _hour_carousel_control_metadata(dialog: Any) -> tuple[object, ...]:
        return await _PydollSession._hour_carousel_control_metadata_observation(
            dialog,
            sanitize_class_tokens=_sanitized_class_tokens,
        )

    async def _find_hour_navigation_control(
        self,
        direction: str,
        *,
        scope: Any,
    ) -> Any | None:
        return await self._find_hour_navigation_control_observation(
            self,
            direction,
            scope=scope,
        )

    async def _swipe_hour_carousel(self, dialog: Any, direction: str) -> None:
        await self._swipe_hour_carousel_input(self, dialog, direction)

    async def _navigate_hour_carousel_by_keyboard(
        self,
        dialog: Any,
        direction: str,
    ) -> bool:
        return await self._navigate_hour_carousel_by_keyboard_input(self, dialog, direction)

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
        await self._dispatch_mouse_event_input(
            self,
            event_type,
            x,
            y,
            buttons=buttons,
            button=button,
            click_count=click_count,
        )

    async def _wait_for_schedule(self, travel_date: date, departure_hour: int) -> None:
        await self._wait_for_schedule_commit(
            self,
            travel_date,
            departure_hour,
            timeout_seconds=lambda: self._timeout_seconds,
            monotonic=time.monotonic,
            sleep=asyncio.sleep,
            source_unavailable_type=BrowserSourceUnavailable,
        )

    async def _wait_for_schedule_date(self, travel_date: date) -> None:
        await self._wait_for_schedule_date_commit(
            self,
            travel_date,
            timeout_seconds=lambda: self._timeout_seconds,
            monotonic=time.monotonic,
            sleep=asyncio.sleep,
            source_unavailable_type=BrowserSourceUnavailable,
        )

    async def _click_hour_and_confirm(self, candidate: _HourCandidate) -> bool:
        return await self._click_hour_and_confirm_commit(
            self,
            candidate,
            timeout_seconds=lambda: self._timeout_seconds,
            monotonic=time.monotonic,
            sleep=asyncio.sleep,
        )

    async def _wait_for_visible_elements(
        self,
        selector: str,
        *,
        scope: Any = None,
        failure_stage: str,
    ) -> list[Any]:
        return await self._wait_for_visible_elements_interaction(
            self,
            selector,
            scope=scope,
            failure_stage=failure_stage,
            timeout_seconds=lambda: self._timeout_seconds,
            monotonic=time.monotonic,
            sleep=asyncio.sleep,
            event_logger=logger,
            source_unavailable_type=BrowserSourceUnavailable,
        )

    async def _wait_for_dialog(self, marker: str) -> Any:
        return await self._wait_for_dialog_interaction(
            self,
            marker,
            timeout_seconds=lambda: self._timeout_seconds,
            monotonic=time.monotonic,
            sleep=asyncio.sleep,
            source_unavailable_type=BrowserSourceUnavailable,
        )

    async def _find_exact_visible(self, selector: str, text: str, *, scope: Any = None) -> Any:
        return await self._find_exact_visible_interaction(
            self,
            selector,
            text,
            scope=scope,
        )

    async def _has_exact_visible(self, selector: str, text: str, *, scope: Any = None) -> bool:
        return await self._has_exact_visible_interaction(
            self,
            selector,
            text,
            scope=scope,
        )

    async def _visible_elements(self, selector: str, *, scope: Any = None) -> list[Any]:
        root = scope or self._tab
        return await self._collect_visible_elements(root, selector)

    @property
    def _timeout_seconds(self) -> float:
        return max(1, self.timeout_ms / 1000)

    async def _close(self) -> None:
        await self._chromium_lifecycle.close()

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
            self._network_responses.setdefault((status, resource_type), None)


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


del _dom_interaction_owner
del _live_dom_owner
del _search_hour_carousel_input_owner
del _search_hour_carousel_observation_owner
del _search_hour_policy_owner
del _search_schedule_commit_owner
