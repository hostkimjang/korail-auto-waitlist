"""Orchestrate one fail-closed KORAIL Pydoll reservation attempt."""

from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import date, datetime
from datetime import time as clock_time
from typing import Protocol

from ..browser_contracts import (
    BrowserProtectionDetected,
    BrowserRateLimited,
    BrowserSourceUnavailable,
)
from .auth_actor import (
    KorailSessionActorState,
    PydollAuthenticationSessionLease,
)
from .auth_actor import PydollAuthenticationSession as _PydollAuthenticationSession
from .auth_contracts import KorailCredentialInput
from .page_contracts import (
    KORAIL_ROUTE_HEADING,
    PydollPageSnapshot,
    normalize_korail_station,
    normalize_korail_train_number,
)
from .reservation_contracts import (
    KorailReservationOutcome,
    KorailReservationProgressCallback,
    KorailReservationRequest,
    KorailReservationResult,
    KorailReservationSeatClass,
)
from .reservation_contracts import (
    KorailReservationProgress as _KorailReservationProgress,
)
from .reservation_contracts import (
    KorailReservedSeat as _KorailReservedSeat,
)

__all__ = (
    "KorailReservationOutcome",
    "KorailReservationProgressCallback",
    "KorailReservationRequest",
    "KorailReservationResult",
    "KorailReservationSeatClass",
    "PydollReservationActor",
    "PydollReservationSession",
    "assert_reservation_identity",
    "has_unique_reservation_target",
)


class PydollReservationSession(_PydollAuthenticationSession, Protocol):
    async def open(self) -> PydollPageSnapshot: ...

    async def navigate(self, url: str) -> PydollPageSnapshot: ...

    async def navigate_fresh(self, url: str) -> PydollPageSnapshot: ...

    async def choose_station(self, kind: str, station: str) -> None: ...

    async def choose_schedule(self, travel_date: date, departure_hour: int) -> None: ...

    async def current_station(self, kind: str) -> str: ...

    async def current_schedule(self) -> tuple[date, int]: ...

    async def current_passenger(self) -> str: ...

    async def ensure_authenticated(self, credential: KorailCredentialInput) -> bool: ...

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

    async def confirmation_correlation_seats_from_fresh_state(
        self,
        request: KorailReservationRequest,
    ) -> tuple[_KorailReservedSeat, ...]: ...


class AcquireReservationSession[Session: PydollReservationSession](Protocol):
    async def __call__(
        self,
        *,
        credential_version: str | None = None,
    ) -> PydollAuthenticationSessionLease[Session]: ...


class EnsureAuthenticatedSession[Session: PydollReservationSession](Protocol):
    async def __call__(
        self,
        session: Session,
        credential: KorailCredentialInput,
    ) -> bool: ...


type DirectSearchUrl = Callable[
    [str, str, date, clock_time],
    Awaitable[str | None],
]
type DiscardIfCredentialChanged = Callable[[KorailCredentialInput], Awaitable[None]]
type DiscardWithState = Callable[[KorailSessionActorState], Awaitable[None]]
type ResponseSafetyGuard = Callable[[PydollPageSnapshot, str], None]
type ReservationIdentityGuard[Session: PydollReservationSession] = Callable[
    [Session, KorailReservationRequest, str],
    Awaitable[None],
]
type UniqueReservationTarget = Callable[
    [PydollPageSnapshot, KorailReservationRequest],
    bool,
]


async def assert_reservation_identity(
    session: PydollReservationSession,
    request: KorailReservationRequest,
    stage: str,
) -> None:
    origin = normalize_korail_station(await session.current_station("departure"))
    destination = normalize_korail_station(await session.current_station("arrival"))
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


def has_unique_reservation_target(
    snapshot: PydollPageSnapshot,
    request: KorailReservationRequest,
) -> bool:
    """Skip expansion only when the initial DOM proves one exact target train."""

    try:
        requested_number = normalize_korail_train_number(request.train_number)
    except ValueError as error:
        raise BrowserSourceUnavailable("read_result") from error
    matches = 0
    for row in snapshot.rows:
        try:
            row_number = normalize_korail_train_number(row.train_number)
        except ValueError:
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
        route_match = KORAIL_ROUTE_HEADING.fullmatch(" ".join(row.route_text.split()))
        if route_match is None:
            continue
        origin, destination, departure, arrival = route_match.groups()
        if not (
            normalize_korail_station(origin) == request.origin
            and normalize_korail_station(destination) == request.destination
            and departure == request.departure_time.strftime("%H:%M")
            and arrival == request.arrival_time.strftime("%H:%M")
        ):
            continue
        matches += 1
        if matches > 1:
            return False
    return matches == 1


class PydollReservationActor[Session: PydollReservationSession]:
    """Own one-shot reservation sequencing while browser DOM actions stay in the session."""

    def __init__(
        self,
        *,
        auth_lock: asyncio.Lock,
        direct_search_url: DirectSearchUrl,
        discard_if_credential_changed: DiscardIfCredentialChanged,
        acquire_session: AcquireReservationSession[Session],
        ensure_authenticated_session: EnsureAuthenticatedSession[Session],
        probe_reused_authenticated_session: Callable[
            [Session, KorailCredentialInput], Awaitable[bool]
        ],
        discard_with_state: DiscardWithState,
        response_safety_guard: ResponseSafetyGuard,
        reservation_identity_guard: ReservationIdentityGuard[Session],
        has_unique_reservation_target: UniqueReservationTarget,
        max_more_result_actions: int,
        utc_now: Callable[[], datetime],
    ) -> None:
        self._auth_lock = auth_lock
        self._direct_search_url = direct_search_url
        self._discard_if_credential_changed = discard_if_credential_changed
        self._acquire_session = acquire_session
        self._ensure_authenticated_session = ensure_authenticated_session
        self._probe_reused_authenticated_session = probe_reused_authenticated_session
        self._discard_with_state = discard_with_state
        self._response_safety_guard = response_safety_guard
        self._reservation_identity_guard = reservation_identity_guard
        self._has_unique_reservation_target = has_unique_reservation_target
        self._max_more_result_actions = max_more_result_actions
        self._utc_now = utc_now

    async def reserve_once(
        self,
        request: KorailReservationRequest,
        *,
        on_progress: KorailReservationProgressCallback | None = None,
    ) -> KorailReservationResult:
        direct_url = await self._direct_search_url(
            request.origin,
            request.destination,
            request.travel_date,
            request.departure_time,
        )
        async with self._auth_lock:
            # Timetable replay is isolated from this auth lock and is used only to
            # derive a public direct URL before the authenticated attempt starts.
            await self._discard_if_credential_changed(request.credential)

            lease: PydollAuthenticationSessionLease[Session] | None = None
            stage = "browser_launch"
            seat_clicked = False
            reservation_clicked = False
            session_ready_at: datetime | None = None
            target_rechecked_at: datetime | None = None
            seat_selected_at: datetime | None = None
            reservation_requested_at: datetime | None = None

            def track_progress(progress: _KorailReservationProgress) -> None:
                nonlocal target_rechecked_at
                nonlocal seat_clicked, seat_selected_at
                nonlocal reservation_clicked, reservation_requested_at

                if progress.stage == "target_rechecked":
                    target_rechecked_at = progress.occurred_at
                elif progress.stage == "seat_selected":
                    seat_clicked = True
                    seat_selected_at = progress.occurred_at
                elif progress.stage == "reservation_requested":
                    # Reaching the reservation request proves both preceding clicks.
                    seat_clicked = True
                    reservation_clicked = True
                    reservation_requested_at = progress.occurred_at
                if on_progress is not None:
                    on_progress(progress)

            async def uncertain_result_correlation_seats() -> tuple[_KorailReservedSeat, ...]:
                if lease is None or not reservation_clicked or reservation_requested_at is None:
                    return ()
                try:
                    return await lease.session.confirmation_correlation_seats_from_fresh_state(
                        request
                    )
                except Exception:  # noqa: BLE001 -- correlation evidence is optional and fail-closed.
                    return ()

            async def correlate_then_discard(
                state: KorailSessionActorState,
            ) -> tuple[_KorailReservedSeat, ...]:
                try:
                    return await uncertain_result_correlation_seats()
                finally:
                    await self._discard_with_state(state)

            try:
                lease = await self._acquire_session(
                    credential_version=request.credential.version,
                )
                session = lease.session
                if lease.authenticated:
                    stage = "reservation_session_probe"
                    try:
                        reused_session_authenticated = (
                            await self._probe_reused_authenticated_session(
                                session,
                                request.credential,
                            )
                        )
                    except BrowserSourceUnavailable:
                        # A reused browser generation can outlive KORAIL's official
                        # login-check surface. No reservation control has been reached,
                        # so the probe owner retires that generation and this actor
                        # allows one fresh authentication. Protection, rate-limit, and
                        # cancellation signals deliberately bypass this recovery path.
                        reused_session_authenticated = False
                    if not reused_session_authenticated:
                        lease = await self._acquire_session(
                            credential_version=request.credential.version,
                        )
                        session = lease.session
                warm_direct_navigation = direct_url is not None and lease.authenticated
                if not warm_direct_navigation:
                    stage = "load_page"
                    self._response_safety_guard(await session.open(), stage)
                stage = "authenticate"
                if not await self._ensure_authenticated_session(session, request.credential):
                    return KorailReservationResult(
                        outcome=KorailReservationOutcome.AUTH_REQUIRED,
                        reason="authentication_required",
                    )
                session_ready_at = self._utc_now()
                track_progress(
                    _KorailReservationProgress(
                        stage="authenticated_session_ready",
                        occurred_at=session_ready_at,
                    )
                )
                if direct_url is not None and lease.authenticated:
                    stage = "direct_navigation"
                    self._response_safety_guard(
                        await session.navigate_fresh(direct_url),
                        stage,
                    )
                elif direct_url is None:
                    stage = "choose_origin"
                    await session.choose_station("departure", request.origin)
                    stage = "choose_destination"
                    await session.choose_station("arrival", request.destination)
                    stage = "choose_departure"
                    await session.choose_schedule(request.travel_date, request.departure_time.hour)
                    stage = "pre_submit_identity_check"
                    await self._reservation_identity_guard(session, request, stage)
                    stage = "submit_search"
                    await session.submit_once()
                else:
                    stage = "direct_navigation"
                    self._response_safety_guard(await session.navigate(direct_url), stage)
                stage = "wait_result"
                snapshot = await session.wait_for_result()
                self._response_safety_guard(snapshot, stage)
                if not self._has_unique_reservation_target(snapshot, request):
                    stage = "expand_results"
                    snapshot = await session.expand_results(
                        snapshot,
                        self._max_more_result_actions,
                    )
                    self._response_safety_guard(snapshot, stage)
                stage = "reserve_once"
                result = await session.reserve_once(request, on_progress=track_progress)
                seat_clicked = seat_clicked or result.seat_clicked
                reservation_clicked = reservation_clicked or result.reservation_clicked
                merged_result = replace(
                    result,
                    seat_clicked=seat_clicked,
                    reservation_clicked=reservation_clicked,
                    session_ready_at=session_ready_at,
                    target_rechecked_at=result.target_rechecked_at or target_rechecked_at,
                    seat_selected_at=result.seat_selected_at or seat_selected_at,
                    reservation_requested_at=(
                        result.reservation_requested_at or reservation_requested_at
                    ),
                )
                if result.outcome in {
                    KorailReservationOutcome.AUTH_REQUIRED,
                    KorailReservationOutcome.PROVIDER_BLOCKED,
                }:
                    await self._discard_with_state(
                        KorailSessionActorState.AUTH_REQUIRED
                        if result.outcome is KorailReservationOutcome.AUTH_REQUIRED
                        else KorailSessionActorState.BLOCKED,
                    )
                elif result.outcome is not KorailReservationOutcome.PAYMENT_REQUIRED and (
                    (
                        result.outcome is KorailReservationOutcome.FAILED
                        and merged_result.seat_clicked
                    )
                    or merged_result.reservation_clicked
                    or merged_result.reservation_requested_at is not None
                    or result.reason.startswith("reservation_result_unknown")
                ):
                    await self._discard_with_state(KorailSessionActorState.STALE)
                return merged_result
            except asyncio.CancelledError:
                await self._discard_with_state(KorailSessionActorState.STALE)
                raise
            except (BrowserProtectionDetected, BrowserRateLimited):
                correlation_seats = await correlate_then_discard(KorailSessionActorState.BLOCKED)
                return KorailReservationResult(
                    outcome=KorailReservationOutcome.PROVIDER_BLOCKED,
                    reason="provider_access_restricted",
                    seat_clicked=seat_clicked,
                    reservation_clicked=reservation_clicked,
                    session_ready_at=session_ready_at,
                    target_rechecked_at=target_rechecked_at,
                    seat_selected_at=seat_selected_at,
                    reservation_requested_at=reservation_requested_at,
                    confirmation_correlation_seats=correlation_seats,
                )
            except BrowserSourceUnavailable as error:
                # An uncertain result after the reservation button is never retried.
                source_stage = (
                    error.stage
                    if error.stage != "unspecified"
                    and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error.stage) is not None
                    else stage
                )
                correlation_seats = await correlate_then_discard(KorailSessionActorState.STALE)
                return KorailReservationResult(
                    outcome=KorailReservationOutcome.FAILED,
                    reason=f"source_unavailable:{source_stage}",
                    seat_clicked=seat_clicked,
                    reservation_clicked=reservation_clicked,
                    session_ready_at=session_ready_at,
                    target_rechecked_at=target_rechecked_at,
                    seat_selected_at=seat_selected_at,
                    reservation_requested_at=reservation_requested_at,
                    confirmation_correlation_seats=correlation_seats,
                )
            except Exception:  # noqa: BLE001 -- browser backend errors are intentionally opaque.
                if seat_clicked or reservation_clicked or reservation_requested_at is not None:
                    correlation_seats = await correlate_then_discard(KorailSessionActorState.STALE)
                else:
                    correlation_seats = await uncertain_result_correlation_seats()
                return KorailReservationResult(
                    outcome=KorailReservationOutcome.FAILED,
                    reason=f"browser_error:{stage}",
                    seat_clicked=seat_clicked,
                    reservation_clicked=reservation_clicked,
                    session_ready_at=session_ready_at,
                    target_rechecked_at=target_rechecked_at,
                    seat_selected_at=seat_selected_at,
                    reservation_requested_at=reservation_requested_at,
                    confirmation_correlation_seats=correlation_seats,
                )
            finally:
                if lease is not None and not lease.persistent:
                    await lease.context.__aexit__(*sys.exc_info())
