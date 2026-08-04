from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from datetime import time as clock_time
from typing import Protocol
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
from pydantic import ValidationError

from .domain import Provider, ReservationOutcome, SeatClass
from .korail_browser_automation import (
    OFFICIAL_KORAIL_SEARCH_URL,
    SOURCE_NAME,
    BrowserSeatSearchRequest,
    BrowserSeatSearchResult,
    BrowserTrainSnapshot,
)
from .korail_reservation_contract import (
    KorailCredentialRequest,
    KorailLoginVerifyRequest,
    KorailLoginVerifyResult,
    KorailReservationConfirmationRequest,
    KorailReservationConfirmationResult,
    KorailReserveOnceRequest,
    KorailReserveOnceResult,
    KorailSessionStateResult,
)
from .provider_accounts import ProviderCredentials
from .provider_login_verification import (
    ProviderLoginVerification,
    ProviderLoginVerificationOutcome,
)
from .reservation_confirmation import (
    ReservationConfirmationOutcome,
    ReservationConfirmationResult,
    ReservationConfirmationTarget,
)
from .schemas import (
    ObservationErrorCategory,
    ReservationProgressStage,
    ReservationRequest,
    ReservationResult,
    SeatAvailability,
    SeatAvailabilityAction,
    SeatAvailabilityNotObservedReason,
    SeatAvailabilityProvenance,
    SeatClassAvailability,
    SeatObservationRequest,
    SeatObservationResult,
    TimetableItem,
)
from .seat_status_cooldown import CooldownStore, MemoryCooldownStore

KOREA = ZoneInfo("Asia/Seoul")
SOURCE_FAILURE_COOLDOWN_MAX_SECONDS = 300


class KorailBrowserTimetableUnavailable(RuntimeError):
    """The live official browser could not provide a trustworthy timetable."""


class BrowserAdapterTransport(Protocol):
    async def search(self, request: BrowserSeatSearchRequest) -> BrowserSeatSearchResult: ...

    async def reserve(self, request: KorailReserveOnceRequest) -> KorailReserveOnceResult: ...

    async def verify_login(self, request: KorailLoginVerifyRequest) -> KorailLoginVerifyResult: ...

    async def prewarm_login(self, request: KorailLoginVerifyRequest) -> KorailLoginVerifyResult: ...

    async def session_state(self) -> KorailSessionStateResult: ...

    async def confirm_reservation(
        self,
        request: KorailReservationConfirmationRequest,
    ) -> KorailReservationConfirmationResult: ...

    async def close(self) -> None: ...


class _AdapterFailure(RuntimeError):
    def __init__(
        self,
        reason: SeatAvailabilityNotObservedReason,
        *,
        rate_limited: bool = False,
        protection: bool = False,
    ) -> None:
        self.reason = reason
        self.rate_limited = rate_limited
        self.protection = protection
        super().__init__(reason)


class HttpBrowserAdapterTransport:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        token: str | None,
        *,
        allow_fullstack_test_url: bool = False,
    ) -> None:
        parsed = urlsplit(base_url)
        is_production_sidecar = (
            parsed.scheme == "http"
            and parsed.hostname == "korail-browser-adapter"
            and parsed.port == 8001
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )
        is_fullstack_test_fixture = (
            allow_fullstack_test_url
            and parsed.scheme == "http"
            and parsed.hostname == "e2e-fake-upstream"
            and parsed.port == 8001
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )
        if not (is_production_sidecar or is_fullstack_test_fixture):
            raise ValueError("browser adapter URL must be the exact internal sidecar origin")
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            headers=headers,
        )

    async def search(self, request: BrowserSeatSearchRequest) -> BrowserSeatSearchResult:
        try:
            response = await self._client.post(
                "/v1/seat-snapshot",
                json=request.model_dump(mode="json"),
            )
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise _AdapterFailure("source_unavailable") from error
        if response.status_code == 429:
            raise _AdapterFailure("provider_access_restricted", rate_limited=True)
        if response.status_code in {403, 423}:
            raise _AdapterFailure("provider_access_restricted", protection=True)
        if response.status_code != 200:
            raise _AdapterFailure("source_unavailable")
        try:
            return BrowserSeatSearchResult.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise _AdapterFailure("source_unavailable") from error

    async def reserve(self, request: KorailReserveOnceRequest) -> KorailReserveOnceResult:
        payload = request.model_dump(mode="json", exclude={"credential"})
        payload["credential"] = {
            "login_method": request.credential.login_method,
            "login_id": request.credential.login_id.get_secret_value(),
            "password": request.credential.password.get_secret_value(),
            "version": request.credential.version,
        }
        try:
            response = await self._client.post("/v1/reserve-once", json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise _AdapterFailure("source_unavailable") from error
        if response.status_code == 429:
            raise _AdapterFailure("provider_access_restricted", rate_limited=True)
        if response.status_code in {403, 423}:
            raise _AdapterFailure("provider_access_restricted", protection=True)
        if response.status_code != 200:
            raise _AdapterFailure("source_unavailable")
        try:
            return KorailReserveOnceResult.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise _AdapterFailure("source_unavailable") from error

    async def verify_login(self, request: KorailLoginVerifyRequest) -> KorailLoginVerifyResult:
        return await self._login_request("/v1/verify-login", request)

    async def prewarm_login(self, request: KorailLoginVerifyRequest) -> KorailLoginVerifyResult:
        return await self._login_request("/v1/prewarm-login", request)

    async def _login_request(
        self,
        path: str,
        request: KorailLoginVerifyRequest,
    ) -> KorailLoginVerifyResult:
        payload = {
            "credential": {
                "login_method": request.credential.login_method,
                "login_id": request.credential.login_id.get_secret_value(),
                "password": request.credential.password.get_secret_value(),
                "version": request.credential.version,
            }
        }
        try:
            response = await self._client.post(path, json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise _AdapterFailure("source_unavailable") from error
        if response.status_code == 429:
            raise _AdapterFailure("provider_access_restricted", rate_limited=True)
        if response.status_code in {403, 423}:
            raise _AdapterFailure("provider_access_restricted", protection=True)
        if response.status_code != 200:
            raise _AdapterFailure("source_unavailable")
        try:
            return KorailLoginVerifyResult.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise _AdapterFailure("source_unavailable") from error

    async def session_state(self) -> KorailSessionStateResult:
        try:
            response = await self._client.get("/v1/session-state")
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise _AdapterFailure("source_unavailable") from error
        if response.status_code != 200:
            raise _AdapterFailure("source_unavailable")
        try:
            return KorailSessionStateResult.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise _AdapterFailure("source_unavailable") from error

    async def confirm_reservation(
        self,
        request: KorailReservationConfirmationRequest,
    ) -> KorailReservationConfirmationResult:
        try:
            response = await self._client.post(
                "/v1/confirm-reservation",
                json=request.model_dump(mode="json"),
            )
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise _AdapterFailure("source_unavailable") from error
        if response.status_code == 429:
            raise _AdapterFailure("provider_access_restricted", rate_limited=True)
        if response.status_code in {403, 423}:
            raise _AdapterFailure("provider_access_restricted", protection=True)
        if response.status_code != 200:
            raise _AdapterFailure("source_unavailable")
        try:
            return KorailReservationConfirmationResult.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise _AdapterFailure("source_unavailable") from error

    async def close(self) -> None:
        await self._client.aclose()


@dataclass(frozen=True)
class _CacheEntry:
    expires_at: float
    result: BrowserSeatSearchResult


@dataclass(frozen=True)
class _QueryCooldown:
    expires_at: float
    reason: SeatAvailabilityNotObservedReason


class _ProviderCooldown(RuntimeError):
    def __init__(self, reason: SeatAvailabilityNotObservedReason) -> None:
        self.reason = reason
        super().__init__(reason)


class KorailBrowserSeatSource:
    """Exact-matches sanitized sidecar snapshots onto official timetable rows."""

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
        self._cache: dict[tuple[str, str, str, str, str, int], _CacheEntry] = {}
        self._inflight: dict[
            tuple[str, str, str, str, str, int], asyncio.Task[BrowserSeatSearchResult]
        ] = {}
        self._state_lock = asyncio.Lock()
        self._provider_gate = asyncio.Semaphore(1)
        self._failure_count = 0
        self._query_failure_counts: dict[tuple[str, str, str, str, str, int], int] = {}
        self._query_cooldowns: dict[
            tuple[str, str, str, str, str, int], _QueryCooldown
        ] = {}

    async def close(self) -> None:
        await self.drain_pending_calls()
        await self._transport.close()

    async def verify_login(
        self,
        credentials: ProviderCredentials,
    ) -> ProviderLoginVerification:
        if not self.enabled:
            return ProviderLoginVerification(ProviderLoginVerificationOutcome.FAILED)
        try:
            request = KorailLoginVerifyRequest(
                credential=KorailCredentialRequest(
                    login_method=credentials.login_method,
                    login_id=credentials.login_id,
                    password=credentials.password,
                    version=str(credentials.credential_version),
                )
            )
            result = await self._transport.verify_login(request)
        except (ValueError, ValidationError):
            return ProviderLoginVerification(
                ProviderLoginVerificationOutcome.INVALID_IDENTIFIER
            )
        except _AdapterFailure as error:
            return ProviderLoginVerification(
                ProviderLoginVerificationOutcome.PROVIDER_BLOCKED
                if error.protection or error.rate_limited
                else ProviderLoginVerificationOutcome.FAILED
            )
        return ProviderLoginVerification(ProviderLoginVerificationOutcome(result.outcome))

    async def prewarm_login(
        self,
        credentials: ProviderCredentials,
    ) -> ProviderLoginVerification:
        if not self.enabled:
            return ProviderLoginVerification(ProviderLoginVerificationOutcome.FAILED)
        try:
            request = KorailLoginVerifyRequest(
                credential=KorailCredentialRequest(
                    login_method=credentials.login_method,
                    login_id=credentials.login_id,
                    password=credentials.password,
                    version=str(credentials.credential_version),
                )
            )
            result = await self._transport.prewarm_login(request)
        except (ValueError, ValidationError):
            return ProviderLoginVerification(
                ProviderLoginVerificationOutcome.INVALID_IDENTIFIER
            )
        except _AdapterFailure as error:
            return ProviderLoginVerification(
                ProviderLoginVerificationOutcome.PROVIDER_BLOCKED
                if error.protection or error.rate_limited
                else ProviderLoginVerificationOutcome.FAILED
            )
        return ProviderLoginVerification(ProviderLoginVerificationOutcome(result.outcome))

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
        if not self.enabled or target.provider is not Provider.KORAIL:
            return ReservationConfirmationResult(
                provider=Provider.KORAIL,
                outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
                source="korail-same-session-detail",
                observed_at=datetime.now(UTC),
            )
        try:
            result = await self._transport.confirm_reservation(
                KorailReservationConfirmationRequest(
                    attempt_id=target.attempt_id,
                    candidate_id=target.candidate_id,
                    train_number=_normalize_train_number(target.train_number),
                    origin=target.origin,
                    destination=target.destination,
                    departure_at=target.departure_at,
                    arrival_at=target.arrival_at,
                    seat_class=target.seat_class.value,
                    passenger_count=target.passenger_count,
                    credential_version=target.credential_version,
                )
            )
        except _AdapterFailure as error:
            return ReservationConfirmationResult(
                provider=Provider.KORAIL,
                outcome=(
                    ReservationConfirmationOutcome.PROVIDER_BLOCKED
                    if error.protection or error.rate_limited
                    else ReservationConfirmationOutcome.INCONCLUSIVE
                ),
                source="korail-same-session-detail",
                observed_at=datetime.now(UTC),
            )
        except (ValueError, ValidationError):
            return ReservationConfirmationResult(
                provider=Provider.KORAIL,
                outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
                source="korail-same-session-detail",
                observed_at=datetime.now(UTC),
            )
        try:
            return ReservationConfirmationResult(
                provider=Provider.KORAIL,
                outcome=ReservationConfirmationOutcome(result.outcome),
                source=result.source,
                observed_at=result.observed_at,
                payment_deadline=result.payment_deadline,
                official_handoff_url=result.official_handoff_url,
            )
        except ValueError:
            return ReservationConfirmationResult(
                provider=Provider.KORAIL,
                outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
                source="korail-same-session-detail",
                observed_at=datetime.now(UTC),
            )

    async def drain_pending_calls(self) -> None:
        """Drain shielded searches before their event loop and transport are closed."""
        while True:
            async with self._state_lock:
                tasks = tuple(self._inflight.values())
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)

    async def observation_deferred_until(self) -> datetime | None:
        """Expose the shared Redis hold without issuing a sidecar request."""
        if not self.enabled:
            return None
        cooldown = await self._cooldown_store.get("korail-browser")
        if cooldown is None or cooldown.reason != "provider_access_restricted":
            return None
        return datetime.now(UTC) + timedelta(seconds=max(1, cooldown.retry_after_seconds))

    async def observe(
        self,
        request: SeatObservationRequest,
        *,
        origin: str,
        destination: str,
    ) -> list[SeatObservationResult]:
        """Observe one exact KORAIL candidate from a shared service-day browser query."""
        if not self.enabled:
            return self._observation_error(request, "provider_unavailable")
        if request.provider != Provider.KORAIL or request.passenger_count != 1:
            return self._observation_error(request, "provider_unavailable")
        if request.seat_class not in {SeatClass.STANDARD, SeatClass.FIRST}:
            return self._observation_error(request, "provider_unavailable")

        local_departure = request.departure_at.astimezone(KOREA)
        departure_from = self._browser_departure_from(
            local_departure.replace(minute=0, second=0, microsecond=0),
            local_departure.replace(hour=23, minute=59, second=59, microsecond=0),
        )
        if departure_from is None:
            return self._observation_error(request, "provider_unavailable")
        browser_request = BrowserSeatSearchRequest(
            origin=origin,
            destination=destination,
            travel_date=local_departure.date(),
            # Future service days start at midnight to preserve coupled identities.
            # Today's picker cannot select elapsed KST hours, so use the bounded
            # current/request hour selected above and exact-match afterward.
            departure_from=departure_from,
            departure_to=clock_time(23, 59, 59),
            passenger_count=request.passenger_count,
        )
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

        identity = (
            _normalize_train_number(request.train_number),
            local_departure.strftime("%Y%m%d%H%M%S"),
        )
        snapshot = next(
            (
                item
                for item in result.trains
                if (
                    _normalize_train_number(item.train_number),
                    item.departure_at.astimezone(KOREA).strftime("%Y%m%d%H%M%S"),
                )
                == identity
            ),
            None,
        )
        if snapshot is None:
            return self._observation_error(request, "provider_unavailable")
        status = (
            snapshot.standard
            if request.seat_class == SeatClass.STANDARD
            else snapshot.first
        )
        freshness_seconds = max(0, min(self.cache_ttl_seconds, 30))
        return [
            SeatObservationResult(
                seat_class=request.seat_class,
                status=status,
                source=SOURCE_NAME,
                observed_at=result.observed_at,
                fresh_until=result.observed_at + timedelta(seconds=freshness_seconds),
                delay_minutes=snapshot.expected_delay_minutes,
            )
        ]

    async def reserve_once(
        self,
        request: ReservationRequest,
        credentials: ProviderCredentials,
    ) -> ReservationResult:
        """Send one exact reservation command to the same managed browser sidecar."""
        observed_at = datetime.now(UTC)
        if (
            not self.enabled
            or request.provider != Provider.KORAIL
            or request.arrival_at is None
            or request.passenger_count != 1
            or request.seat_class not in {SeatClass.STANDARD, SeatClass.FIRST}
        ):
            return ReservationResult(
                outcome=ReservationOutcome.FAILED,
                source="korail-pydoll-reservation",
                observed_at=observed_at,
            )
        departure = request.departure_at.astimezone(KOREA)
        arrival = request.arrival_at.astimezone(KOREA)
        internal_request = KorailReserveOnceRequest(
            origin=request.origin,
            destination=request.destination,
            travel_date=departure.date(),
            train_number=_normalize_train_number(request.train_number),
            train_type=None,
            departure_time=departure.time().replace(tzinfo=None),
            arrival_time=arrival.time().replace(tzinfo=None),
            seat_class=(
                "general" if request.seat_class == SeatClass.STANDARD else "special"
            ),
            credential=KorailCredentialRequest(
                login_method=credentials.login_method,
                login_id=credentials.login_id,
                password=credentials.password,
                version=str(credentials.credential_version),
            ),
        )
        try:
            result = await self._transport.reserve(internal_request)
        except _AdapterFailure as error:
            if error.protection or error.rate_limited:
                return ReservationResult(
                    outcome=ReservationOutcome.PROVIDER_BLOCKED,
                    source="korail-pydoll-reservation",
                    observed_at=observed_at,
                )
            return ReservationResult(
                outcome=ReservationOutcome.FAILED,
                source="korail-pydoll-reservation",
                observed_at=observed_at,
            )

        observed_at = datetime.now(UTC)
        progress_stages = tuple(
            ReservationProgressStage(stage=stage, occurred_at=occurred_at)
            for stage, occurred_at in (
                ("authenticated_session_ready", result.session_ready_at),
                ("target_rechecked", result.target_rechecked_at),
                ("seat_selected", result.seat_selected_at),
                ("reservation_requested", result.reservation_requested_at),
            )
            if occurred_at is not None
        )

        if result.outcome == "payment_required":
            return ReservationResult(
                outcome=ReservationOutcome.PAYMENT_REQUIRED,
                source="korail-pydoll-reservation",
                observed_at=observed_at,
                official_handoff_url="https://www.korail.com/ticket/mypage/mykorail",
                progress_stages=progress_stages,
            )
        if result.outcome == "auth_required":
            outcome = ReservationOutcome.AUTH_REQUIRED
        elif result.outcome in {"consent_required", "action_required"}:
            # These outcomes confirm that the authenticated flow reached an official
            # manual-intervention boundary. They do not prove that the saved
            # credentials or provider session are invalid.
            outcome = ReservationOutcome.UNKNOWN
        elif result.outcome == "provider_blocked":
            outcome = ReservationOutcome.PROVIDER_BLOCKED
        elif result.outcome == "unavailable":
            outcome = ReservationOutcome.NOT_AVAILABLE
        elif result.reservation_clicked:
            # A final click with no authoritative terminal state is never replayed.
            outcome = ReservationOutcome.UNKNOWN
        else:
            outcome = ReservationOutcome.FAILED
        return ReservationResult(
            outcome=outcome,
            source="korail-pydoll-reservation",
            observed_at=observed_at,
            progress_stages=progress_stages,
        )

    @staticmethod
    def _observation_error(
        request: SeatObservationRequest,
        error_category: ObservationErrorCategory,
    ) -> list[SeatObservationResult]:
        observed_at = datetime.now(UTC)
        return [
            SeatObservationResult(
                seat_class=request.seat_class,
                status="error",
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
            await self._open_cooldown(
                _AdapterFailure("source_unavailable"), request.cache_key()
            )
            return self._mark_not_observed(items, "source_unavailable")

        by_identity = {
            (
                _normalize_train_number(snapshot.train_number),
                snapshot.departure_at.astimezone(KOREA).strftime("%Y%m%d%H%M%S"),
            ): snapshot
            for snapshot in result.trains
        }
        overlaid: list[TimetableItem] = []
        for item in items:
            local_departure = item.departure_at.astimezone(KOREA)
            snapshot = by_identity.get(
                (
                    _normalize_train_number(item.train_number),
                    local_departure.strftime("%Y%m%d%H%M%S"),
                )
            )
            if snapshot is None:
                overlaid.extend(self._mark_not_observed([item], "no_exact_match"))
                continue
            overlaid.append(
                self._overlay_item(
                    item,
                    snapshot,
                    result.observed_at,
                    official_search_url=result.official_search_url,
                )
            )
        return overlaid

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
            await self._open_cooldown(
                _AdapterFailure("source_unavailable"), request.cache_key()
            )
            raise KorailBrowserTimetableUnavailable("source_unavailable") from None

        official_url = OFFICIAL_KORAIL_SEARCH_URL
        items: list[TimetableItem] = []
        for snapshot in result.trains:
            local_departure = snapshot.departure_at.astimezone(KOREA)
            if local_departure < local_from or local_departure > local_to:
                continue
            seats = [
                _seat_class(
                    "standard",
                    snapshot.standard,
                    result.observed_at,
                    official_url,
                    fare=snapshot.adult_fare,
                ),
                _seat_class(
                    "first",
                    snapshot.first,
                    result.observed_at,
                    official_url,
                ),
            ]
            items.append(
                TimetableItem(
                    provider=Provider.KORAIL,
                    train_number=_normalize_train_number(snapshot.train_number),
                    train_type=snapshot.train_type,
                    origin=result.origin,
                    destination=result.destination,
                    departure_at=snapshot.departure_at,
                    arrival_at=snapshot.arrival_at,
                    adult_fare=snapshot.adult_fare,
                    timetable_source="official_provider",
                    timetable_retrieved_at=result.observed_at,
                    availability=SeatAvailability(
                        status=snapshot.standard,
                        source=SOURCE_NAME,
                        observed_at=result.observed_at,
                    ),
                    seat_classes=seats,
                    official_booking_url=official_url,
                    official_search_url=result.official_search_url,
                )
            )
        return items

    def _browser_departure_from(
        self,
        local_from: datetime,
        local_to: datetime,
    ) -> clock_time | None:
        """Choose the earliest hour that KORAIL's current KST picker can select."""
        now = self._now().astimezone(KOREA)
        if local_from.date() > now.date():
            return clock_time(0, 0)
        if local_from.date() < now.date() or local_to < now:
            return None
        current_hour = now.replace(minute=0, second=0, microsecond=0)
        effective_from = max(local_from, current_hour)
        if effective_from > local_to:
            return None
        return effective_from.time().replace(tzinfo=None)

    async def _search(self, request: BrowserSeatSearchRequest) -> BrowserSeatSearchResult:
        key = request.cache_key()
        now = self._monotonic()
        async with self._state_lock:
            expired_query_keys = [
                query_key
                for query_key, cooldown in self._query_cooldowns.items()
                if cooldown.expires_at <= now
            ]
            for query_key in expired_query_keys:
                self._query_cooldowns.pop(query_key, None)
                self._query_failure_counts.pop(query_key, None)
            cached = self._cache.get(key)
            if cached is not None and cached.expires_at > now:
                return cached.result
            query_cooldown = self._query_cooldowns.get(key)
            if query_cooldown is not None:
                raise _ProviderCooldown(query_cooldown.reason)
            cooldown = await self._cooldown_store.get("korail-browser")
            if cooldown is not None and cooldown.reason == "provider_access_restricted":
                raise _ProviderCooldown(cooldown.reason)
            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(self._load(key, request))
                self._inflight[key] = task
        return await asyncio.shield(task)

    async def _load(
        self,
        key: tuple[str, str, str, str, str, int],
        request: BrowserSeatSearchRequest,
    ) -> BrowserSeatSearchResult:
        current_task = asyncio.current_task()
        try:
            async with self._provider_gate:
                result = await self._transport.search(request)
            if (
                result.origin != request.origin
                or result.destination != request.destination
                or result.travel_date != request.travel_date
                or result.passenger_count != request.passenger_count
            ):
                raise _AdapterFailure("source_unavailable")
            async with self._state_lock:
                self._failure_count = 0
                self._query_failure_counts.pop(key, None)
                self._query_cooldowns.pop(key, None)
                self._cache[key] = _CacheEntry(
                    expires_at=self._monotonic() + self.cache_ttl_seconds,
                    result=result,
                )
            return result
        finally:
            async with self._state_lock:
                if self._inflight.get(key) is current_task:
                    self._inflight.pop(key, None)

    async def _open_cooldown(
        self,
        error: _AdapterFailure,
        key: tuple[str, str, str, str, str, int],
    ) -> None:
        if error.protection:
            self._failure_count += 1
            duration = self.protection_cooldown_seconds
        elif error.rate_limited:
            self._failure_count += 1
            duration = self.rate_limit_cooldown_seconds
        else:
            # A valid-but-empty late-night query and a transient DOM/source failure
            # are scoped to the exact route/date/window. They must not poison a
            # subsequent query for another service day through the provider-wide
            # Redis hold reserved for explicit 403/423/429 signals.
            async with self._state_lock:
                failures = self._query_failure_counts.get(key, 0) + 1
                self._query_failure_counts[key] = failures
                duration = min(
                    30 * (2 ** (failures - 1)),
                    SOURCE_FAILURE_COOLDOWN_MAX_SECONDS,
                )
                self._query_cooldowns[key] = _QueryCooldown(
                    expires_at=self._monotonic() + duration,
                    reason=error.reason,
                )
            return
        # Access protection and rate limiting are provider-wide evidence, so all
        # query keys share the configured Redis-backed hold.
        await self._cooldown_store.set("korail-browser", error.reason, duration)

    @staticmethod
    def _overlay_item(
        item: TimetableItem,
        snapshot: BrowserTrainSnapshot,
        observed_at: datetime,
        *,
        official_search_url: str | None = None,
    ) -> TimetableItem:
        official_url = str(item.official_booking_url)
        seats = [
            _seat_class("standard", snapshot.standard, observed_at, official_url),
            _seat_class("first", snapshot.first, observed_at, official_url),
        ]
        return item.model_copy(
            update={"seat_classes": seats, "official_search_url": official_search_url}
        )

    @staticmethod
    def _mark_not_observed(
        items: list[TimetableItem], reason: SeatAvailabilityNotObservedReason
    ) -> list[TimetableItem]:
        marked: list[TimetableItem] = []
        for item in items:
            seats = []
            for seat in item.seat_classes:
                if seat.status == "unknown" and seat.provenance.kind == "not_observed":
                    seats.append(
                        seat.model_copy(
                            update={
                                "provenance": SeatAvailabilityProvenance(
                                    kind="not_observed", reason=reason
                                ),
                                "actions": [],
                            }
                        )
                    )
                else:
                    seats.append(seat)
            marked.append(item.model_copy(update={"seat_classes": seats}))
        return marked


def _normalize_train_number(value: object) -> str:
    digits = "".join(character for character in str(value) if character.isdigit())
    return digits.lstrip("0") or "0"


def _seat_class(
    seat_class: str,
    status: str,
    observed_at: datetime,
    official_url: str,
    *,
    fare: int | None = None,
) -> SeatClassAvailability:
    actions: list[SeatAvailabilityAction] = []
    if status in {"available", "limited", "standing_plus_seat"}:
        actions.extend(
            [
                SeatAvailabilityAction(kind="official_check", url=official_url),
                SeatAvailabilityAction(kind="add_to_watch"),
            ]
        )
    elif status == "waitlist_available":
        actions.extend(
            [
                SeatAvailabilityAction(kind="official_waitlist", url=official_url),
                SeatAvailabilityAction(kind="add_to_watch"),
            ]
        )
    elif status == "sold_out":
        actions.append(SeatAvailabilityAction(kind="add_to_watch"))
    return SeatClassAvailability(
        seat_class=seat_class,
        status=status,
        provenance=SeatAvailabilityProvenance(
            kind="official_provider",
            source=SOURCE_NAME,
            observed_at=observed_at.astimezone(UTC),
        ),
        fare=fare,
        actions=actions,
    )
