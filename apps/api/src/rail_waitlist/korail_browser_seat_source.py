from __future__ import annotations

import asyncio as asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass as dataclass
from datetime import UTC, datetime
from datetime import time as clock_time
from datetime import timedelta as timedelta
from typing import Protocol as Protocol
from urllib.parse import urlsplit as urlsplit
from zoneinfo import ZoneInfo

import httpx as httpx
from pydantic import ValidationError

from .domain import Provider as Provider
from .domain import ReservationOutcome as ReservationOutcome
from .domain import SeatClass as SeatClass
from .korail_sidecar import client as _client_owner
from .korail_sidecar.browser_contracts import (
    SOURCE_NAME,
    BrowserSeatSearchRequest,
    BrowserSeatSearchResult,
)
from .korail_sidecar.browser_contracts import (
    BrowserTrainSnapshot as BrowserTrainSnapshot,
)
from .korail_sidecar.browser_page_contracts import (
    OFFICIAL_KORAIL_SEARCH_URL as OFFICIAL_KORAIL_SEARCH_URL,
)
from .korail_sidecar.contracts import KorailCredentialRequest as KorailCredentialRequest
from .korail_sidecar.contracts import KorailLoginVerifyRequest as KorailLoginVerifyRequest
from .korail_sidecar.contracts import (
    KorailLoginVerifyResult as KorailLoginVerifyResult,
)
from .korail_sidecar.contracts import (
    KorailReservationConfirmationRequest as KorailReservationConfirmationRequest,
)
from .korail_sidecar.contracts import (
    KorailReservationConfirmationResult as KorailReservationConfirmationResult,
)
from .korail_sidecar.contracts import (
    KorailReserveOnceRequest as KorailReserveOnceRequest,
)
from .korail_sidecar.contracts import (
    KorailReserveOnceResult as KorailReserveOnceResult,
)
from .korail_sidecar.contracts import (
    KorailSessionStateResult,
)
from .observations.contracts import (
    ObservationErrorCategory,
    SeatObservationRequest,
    SeatObservationResult,
)
from .provider_account_management.contracts import ProviderCredentials
from .provider_account_management.login_verification import ProviderLoginVerification
from .provider_account_management.login_verification import (
    ProviderLoginVerificationOutcome as ProviderLoginVerificationOutcome,
)
from .provider_adapters import korail_browser_auth_policy as _auth_policy_owner
from .provider_adapters import korail_browser_observation_policy as _observation_policy_owner
from .provider_adapters import korail_browser_query_runtime as _query_runtime_owner
from .provider_adapters import korail_browser_reservation_policy as _reservation_policy_owner
from .provider_adapters import korail_browser_window_policy as _window_policy_owner
from .reservations.contracts import ReservationProgressStage as ReservationProgressStage
from .reservations.contracts import ReservationRequest, ReservationResult
from .reservations.provider_confirmation import korail_sidecar_runtime as _confirmation_runtime
from .reservations.provider_confirmation.contracts import (
    ReservationConfirmationOutcome as ReservationConfirmationOutcome,
)
from .reservations.provider_confirmation.contracts import (
    ReservationConfirmationResult,
    ReservationConfirmationTarget,
)
from .seat_status_cooldown import CooldownStore, MemoryCooldownStore
from .timetable_management import korail_browser_projection as _projection_owner
from .timetable_management.schemas import (
    SeatAvailability as SeatAvailability,
)
from .timetable_management.schemas import (
    SeatAvailabilityAction as SeatAvailabilityAction,
)
from .timetable_management.schemas import (
    SeatAvailabilityNotObservedReason,
    TimetableItem,
)
from .timetable_management.schemas import (
    SeatAvailabilityProvenance as SeatAvailabilityProvenance,
)
from .timetable_management.schemas import (
    SeatClassAvailability as SeatClassAvailability,
)

KOREA = ZoneInfo("Asia/Seoul")
SOURCE_FAILURE_COOLDOWN_MAX_SECONDS = _query_runtime_owner.SOURCE_FAILURE_COOLDOWN_MAX_SECONDS


class KorailBrowserTimetableUnavailable(RuntimeError):
    """The live official browser could not provide a trustworthy timetable."""


BrowserAdapterTransport = _client_owner.BrowserAdapterTransport
_AdapterFailure = _client_owner._AdapterFailure
HttpBrowserAdapterTransport = _client_owner.HttpBrowserAdapterTransport
_normalize_train_number = _projection_owner.normalize_train_number
_seat_class = _projection_owner._seat_class
_CacheEntry = _query_runtime_owner._CacheEntry
_QueryCooldown = _query_runtime_owner._QueryCooldown
_ProviderCooldown = _query_runtime_owner._ProviderCooldown


class KorailBrowserSeatSource:
    """Exact-matches sanitized sidecar snapshots onto official timetable rows."""

    _overlay_item = staticmethod(_projection_owner.overlay_item)
    _mark_not_observed = staticmethod(_projection_owner.mark_not_observed)
    _project_overlay_items = staticmethod(_projection_owner.project_overlay_items)
    _build_login_verify_request = staticmethod(_auth_policy_owner.build_login_verify_request)
    _project_login_verification_failure = staticmethod(
        _auth_policy_owner.project_login_verification_failure
    )
    _project_login_verification_result = staticmethod(
        _auth_policy_owner.project_login_verification_result
    )
    _build_observation_search_request = staticmethod(
        _observation_policy_owner.build_observation_search_request
    )
    _project_observation_result = staticmethod(_observation_policy_owner.project_observation_result)
    _build_reservation_request = staticmethod(_reservation_policy_owner.build_reservation_request)
    _project_reservation_failure = staticmethod(
        _reservation_policy_owner.project_reservation_failure
    )
    _project_reservation_result = staticmethod(_reservation_policy_owner.project_reservation_result)
    _select_browser_departure_from = staticmethod(
        _window_policy_owner.select_browser_departure_from
    )

    def __init__(
        self,
        *,
        enabled: bool,
        adapter_url: str,
        cache_ttl_seconds: int,
        timeout_seconds: float,
        token: str | None = None,
        rate_limit_cooldown_seconds: int,
        protection_cooldown_seconds: int,
        transport: BrowserAdapterTransport | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] | None = None,
        cooldown_store: CooldownStore | None = None,
        allow_fullstack_test_url: bool = False,
    ) -> None:
        self.enabled = enabled
        self.cache_ttl_seconds = cache_ttl_seconds
        self.rate_limit_cooldown_seconds = rate_limit_cooldown_seconds
        self.protection_cooldown_seconds = protection_cooldown_seconds
        self._transport = transport or HttpBrowserAdapterTransport(
            adapter_url,
            timeout_seconds,
            token,
            allow_fullstack_test_url=allow_fullstack_test_url,
        )
        self._monotonic = monotonic
        self._now = now or (lambda: datetime.now(KOREA))
        self._cooldown_store = cooldown_store or MemoryCooldownStore(monotonic)
        self._query_runtime = _query_runtime_owner.KorailBrowserQueryRuntime()

    @property
    def _query_cooldowns(
        self,
    ) -> dict[_query_runtime_owner.QueryKey, _QueryCooldown]:
        """Keep the legacy read seam while the runtime owns mutable query state."""

        return self._query_runtime.query_cooldowns

    async def close(self) -> None:
        await self.drain_pending_calls()
        await self._transport.close()

    async def verify_login(
        self,
        credentials: ProviderCredentials,
    ) -> ProviderLoginVerification:
        if not self.enabled:
            return self._project_login_verification_failure("failed")
        try:
            request = self._build_login_verify_request(credentials)
            result = await self._transport.verify_login(request)
        except (ValueError, ValidationError):
            return self._project_login_verification_failure("invalid_identifier")
        except _AdapterFailure as error:
            return self._project_login_verification_failure(
                "provider_blocked" if error.protection or error.rate_limited else "failed"
            )
        return self._project_login_verification_result(result)

    async def prewarm_login(
        self,
        credentials: ProviderCredentials,
    ) -> ProviderLoginVerification:
        if not self.enabled:
            return self._project_login_verification_failure("failed")
        try:
            request = self._build_login_verify_request(credentials)
            result = await self._transport.prewarm_login(request)
        except (ValueError, ValidationError):
            return self._project_login_verification_failure("invalid_identifier")
        except _AdapterFailure as error:
            return self._project_login_verification_failure(
                "provider_blocked" if error.protection or error.rate_limited else "failed"
            )
        return self._project_login_verification_result(result)

    async def session_state(self) -> KorailSessionStateResult:
        if not self.enabled:
            return KorailSessionStateResult(state="cold", locally_reusable=False)
        try:
            return await self._transport.session_state()
        except _AdapterFailure:
            return KorailSessionStateResult(state="stale", locally_reusable=False)

    async def confirm_reservation(
        self,
        target: ReservationConfirmationTarget,
    ) -> ReservationConfirmationResult:
        return await _confirmation_runtime.confirm_korail_sidecar_reservation(
            enabled=self.enabled,
            target=target,
            confirm=lambda request: self._transport.confirm_reservation(request),
            normalize_train_number=_normalize_train_number,
            now=lambda: datetime.now(UTC),
            adapter_failure_type=_AdapterFailure,
        )

    async def drain_pending_calls(self) -> None:
        await self._query_runtime.drain_pending_calls()

    async def observation_deferred_until(self) -> datetime | None:
        """Expose the shared Redis hold without issuing a sidecar request."""
        if not self.enabled:
            return None
        return await self._query_runtime.observation_deferred_until(
            cooldown_store=lambda: self._cooldown_store,
        )

    async def observe(
        self,
        request: SeatObservationRequest,
        *,
        origin: str,
        destination: str,
    ) -> list[SeatObservationResult]:
        """Observe one exact KORAIL candidate from a shared service-day browser query."""
        browser_request = self._build_observation_search_request(
            request,
            enabled=self.enabled,
            origin=origin,
            destination=destination,
            select_departure_from=self._browser_departure_from,
        )
        if browser_request is None:
            return self._observation_error(request, "provider_unavailable")
        try:
            result = await self._search(browser_request)
        except _ProviderCooldown:
            return self._observation_error(request, "provider_unavailable")
        except _AdapterFailure as error:
            await self._open_cooldown(error, browser_request.cache_key())
            return self._observation_error(request, "provider_unavailable")
        except (RuntimeError, TypeError, ValueError) as error:
            await self._open_cooldown(
                _AdapterFailure("source_unavailable"), browser_request.cache_key()
            )
            return self._observation_error(
                request,
                (
                    "schema_mismatch"
                    if isinstance(error, (TypeError, ValueError))
                    else "provider_unavailable"
                ),
            )

        projected = self._project_observation_result(
            request,
            result,
            normalize_train_number=_normalize_train_number,
            cache_ttl_seconds=self.cache_ttl_seconds,
        )
        if projected is None:
            return self._observation_error(request, "provider_unavailable")
        return projected

    async def reserve_once(
        self,
        request: ReservationRequest,
        credentials: ProviderCredentials,
    ) -> ReservationResult:
        """Send one exact reservation command to the same managed browser sidecar."""
        observed_at = datetime.now(UTC)
        internal_request = self._build_reservation_request(
            request,
            credentials,
            enabled=self.enabled,
            normalize_train_number=_normalize_train_number,
        )
        if internal_request is None:
            return self._project_reservation_failure(observed_at)
        try:
            result = await self._transport.reserve(internal_request)
        except _AdapterFailure as error:
            return self._project_reservation_failure(
                observed_at,
                provider_blocked=error.protection or error.rate_limited,
            )

        return self._project_reservation_result(
            result,
            observed_at=datetime.now(UTC),
        )

    async def reserve_once_with_progress(
        self,
        request: ReservationRequest,
        credentials: ProviderCredentials,
        on_progress: _client_owner.ReservationProgressCallback,
    ) -> ReservationResult:
        """Stream verified sidecar progress while issuing exactly one reservation command."""

        observed_at = datetime.now(UTC)
        internal_request = self._build_reservation_request(
            request,
            credentials,
            enabled=self.enabled,
            normalize_train_number=_normalize_train_number,
        )
        if internal_request is None:
            return self._project_reservation_failure(observed_at)
        try:
            result = await self._transport.reserve_with_progress(internal_request, on_progress)
        except _AdapterFailure as error:
            if error.reservation_command_uncertain:
                return ReservationResult(
                    outcome=ReservationOutcome.UNKNOWN,
                    source="korail-pydoll-reservation",
                    observed_at=datetime.now(UTC),
                    progress_stages=error.progress_stages,
                )
            return self._project_reservation_failure(
                observed_at,
                provider_blocked=error.protection or error.rate_limited,
            )
        return self._project_reservation_result(result, observed_at=datetime.now(UTC))

    @staticmethod
    def _observation_error(
        request: SeatObservationRequest,
        error_category: ObservationErrorCategory,
    ) -> list[SeatObservationResult]:
        from .domain import SeatObservationStatus

        observed_at = datetime.now(UTC)
        return [
            SeatObservationResult(
                seat_class=request.seat_class,
                status=SeatObservationStatus.ERROR,
                source=SOURCE_NAME,
                observed_at=observed_at,
                fresh_until=observed_at,
                error_category=error_category,
            )
        ]

    async def overlay(
        self,
        items: list[TimetableItem],
        *,
        origin: str,
        destination: str,
        departure_from: datetime,
        departure_to: datetime,
        passenger_count: int,
    ) -> list[TimetableItem]:
        if not items:
            return items
        if not self.enabled:
            return items
        if passenger_count != 1:
            return self._mark_not_observed(items, "passenger_count_not_supported")
        local_from = departure_from.astimezone(KOREA)
        local_to = departure_to.astimezone(KOREA)
        if local_from.date() != local_to.date():
            return self._mark_not_observed(items, "source_unavailable")
        browser_departure_from = self._browser_departure_from(local_from, local_to)
        if browser_departure_from is None:
            reason: SeatAvailabilityNotObservedReason = (
                "departure_window_elapsed"
                if local_to < self._now().astimezone(KOREA)
                else "source_unavailable"
            )
            return self._mark_not_observed(items, reason)
        request = BrowserSeatSearchRequest(
            origin=origin,
            destination=destination,
            travel_date=local_from.date(),
            # Preserve coupled train identities such as 032/9032 from midnight on
            # future dates; today's elapsed KST hours are not selectable.
            departure_from=browser_departure_from,
            departure_to=local_to.time().replace(tzinfo=None),
            passenger_count=passenger_count,
        )
        try:
            result = await self._search(request)
        except _ProviderCooldown as error:
            return self._mark_not_observed(items, error.reason)
        except _AdapterFailure as error:
            await self._open_cooldown(error, request.cache_key())
            return self._mark_not_observed(items, error.reason)
        except (RuntimeError, TypeError, ValueError):
            await self._open_cooldown(_AdapterFailure("source_unavailable"), request.cache_key())
            return self._mark_not_observed(items, "source_unavailable")

        return self._project_overlay_items(
            items,
            result,
            train_number_normalizer=_normalize_train_number,
            seat_class_projector=_seat_class,
            item_projector=self._overlay_item,
            not_observed_marker=self._mark_not_observed,
            timezone=KOREA,
        )

    async def search_timetable(
        self,
        *,
        origin: str,
        destination: str,
        departure_from: datetime,
        departure_to: datetime,
        passenger_count: int,
    ) -> list[TimetableItem]:
        """Build the primary KORAIL timetable from one official browser result.

        TAGO is deliberately not involved in this path. The sidecar response contains
        the exact official train identity, schedule and seat state; optional fare data
        remains ``None`` when the official row does not expose it unambiguously.
        """

        if not self.enabled or passenger_count != 1:
            raise KorailBrowserTimetableUnavailable("KORAIL live timetable is unavailable")
        local_from = departure_from.astimezone(KOREA)
        local_to = departure_to.astimezone(KOREA)
        if local_from.date() != local_to.date() or local_to <= local_from:
            raise KorailBrowserTimetableUnavailable("KORAIL live timetable is unavailable")
        browser_departure_from = self._browser_departure_from(local_from, local_to)
        if browser_departure_from is None:
            return []
        request = BrowserSeatSearchRequest(
            origin=origin,
            destination=destination,
            travel_date=local_from.date(),
            departure_from=browser_departure_from,
            departure_to=local_to.time().replace(tzinfo=None),
            passenger_count=passenger_count,
        )
        try:
            result = await self._search(request)
        except _ProviderCooldown as error:
            raise KorailBrowserTimetableUnavailable(error.reason) from None
        except _AdapterFailure as error:
            await self._open_cooldown(error, request.cache_key())
            raise KorailBrowserTimetableUnavailable(error.reason) from None
        except (RuntimeError, TypeError, ValueError):
            await self._open_cooldown(_AdapterFailure("source_unavailable"), request.cache_key())
            raise KorailBrowserTimetableUnavailable("source_unavailable") from None

        return _projection_owner.project_primary_timetable(
            result,
            departure_from=local_from,
            departure_to=local_to,
            train_number_normalizer=_normalize_train_number,
            seat_class_projector=_seat_class,
        )

    def _browser_departure_from(
        self,
        local_from: datetime,
        local_to: datetime,
    ) -> clock_time | None:
        """Choose the earliest hour that KORAIL's current KST picker can select."""
        return self._select_browser_departure_from(
            local_from,
            local_to,
            now=self._now(),
            timezone=KOREA,
        )

    async def _search(self, request: BrowserSeatSearchRequest) -> BrowserSeatSearchResult:
        return await self._query_runtime.search(
            request,
            load=lambda key, value: self._load(key, value),
            monotonic=lambda: self._monotonic(),
            cooldown_store=lambda: self._cooldown_store,
        )

    async def _load(
        self,
        key: tuple[str, str, str, str, str, int],
        request: BrowserSeatSearchRequest,
    ) -> BrowserSeatSearchResult:
        return await self._query_runtime.load(
            key,
            request,
            provider_search=lambda: self._transport.search(request),
            monotonic=lambda: self._monotonic(),
            cache_ttl_seconds=lambda: self.cache_ttl_seconds,
        )

    async def _open_cooldown(
        self,
        error: _AdapterFailure,
        key: tuple[str, str, str, str, str, int],
    ) -> None:
        await self._query_runtime.open_cooldown(
            error,
            key,
            monotonic=lambda: self._monotonic(),
            cooldown_store=lambda: self._cooldown_store,
            rate_limit_seconds=lambda: self.rate_limit_cooldown_seconds,
            protection_seconds=lambda: self.protection_cooldown_seconds,
        )


del _auth_policy_owner
del _observation_policy_owner
del _reservation_policy_owner
del _window_policy_owner
