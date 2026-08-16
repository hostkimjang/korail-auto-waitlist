from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import (
    Provider,
    ProviderCircuitState,
    ReservationOutcome,
    ReservationPolicy,
    ReservationResultReasonCode,
    SeatClass,
    WatchStatus,
)
from ..outbox_management.models import OutboxEvent
from ..provider_account_management.models import RailProviderAccount
from ..provider_account_management.schemas import RailProviderAuthStatus
from ..provider_call_context import bind_request_id
from ..provider_circuit.models import ProviderCircuit
from ..provider_contracts import ReservationExecutionProvider
from ..watch_management.models import ReservationAttempt, Watch, WatchCandidate
from .contracts import (
    POST_REQUEST_UNKNOWN_CORRELATION_REASON_CODES,
    ReservationProgressStage,
    ReservationRequest,
    ReservationResult,
)
from .exact_paid_application import apply_exact_paid_resolution
from .progress_application import cumulative_progress_with, record_reservation_progress
from .provider_confirmation.contracts import (
    ReservationConfirmationDiagnosticCode,
    ReservationConfirmationOutcome,
    ReservationConfirmationPurpose,
    ReservationConfirmationResult,
    ReservationConfirmationSeat,
    ReservationConfirmationTarget,
)
from .provider_confirmation.safety_policy import enforce_confirmation_target_safety

EXTERNAL_RESERVATION_PROVIDERS = frozenset({Provider.KORAIL, Provider.SRT})
logger = logging.getLogger(__name__)


class AsyncSessionFactory(Protocol):
    def __call__(self) -> AsyncSession: ...


ReservationProgressCallback = Callable[[ReservationProgressStage], Awaitable[None]]


@runtime_checkable
class ReservationProgressProvider(Protocol):
    async def reserve_once_with_progress(
        self,
        request: ReservationRequest,
        on_progress: ReservationProgressCallback,
    ) -> ReservationResult: ...


@dataclass(frozen=True)
class ReservationExecutionTarget:
    watch_id: str
    candidate_id: str
    provider: Provider
    origin: str
    destination: str
    origin_node_id: str
    destination_node_id: str
    train_number: str
    departure_at: datetime
    arrival_at: datetime | None
    seat_class: str
    passenger_count: int
    reservation_episode_key: str | None

    def reservation_request(
        self,
        idempotency_key: str,
        *,
        expected_credential_version: int | None = None,
    ) -> ReservationRequest:
        return ReservationRequest(
            provider=self.provider,
            origin=self.origin,
            destination=self.destination,
            origin_node_id=self.origin_node_id,
            destination_node_id=self.destination_node_id,
            train_number=self.train_number,
            departure_at=self.departure_at,
            seat_class=self.seat_class,
            passenger_count=self.passenger_count,
            candidate_id=self.candidate_id,
            idempotency_key=idempotency_key,
            expected_credential_version=expected_credential_version,
            arrival_at=self.arrival_at,
        )


class GetOrCreateProviderCircuit(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        provider: Provider,
        *,
        lock: bool = False,
    ) -> ProviderCircuit: ...


class ApplyWatchTransition(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        watch: Watch,
        target: WatchStatus,
        *,
        reason: str | None = None,
    ) -> Watch: ...


class BeginReservationAttempt(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        watch: Watch,
        candidate: WatchCandidate,
        idempotency_key: str,
        *,
        episode_key: str | None = None,
        retry_authorized: bool = False,
        credential_version: int | None = None,
    ) -> tuple[ReservationAttempt, bool]: ...


class AddOutboxEvent(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        *,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, object],
        dedupe_key: str,
    ) -> OutboxEvent: ...


class CompleteReservationAttempt(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        watch: Watch,
        candidate: WatchCandidate,
        attempt: ReservationAttempt,
        result: ReservationResult,
        confirmation: ReservationConfirmationResult | None = None,
    ) -> None: ...


class RecordReservationConfirmation(Protocol):
    def __call__(
        self,
        attempt: ReservationAttempt,
        confirmation: ReservationConfirmationResult,
    ) -> None: ...


class UpdateProviderAuthStatusInTransaction(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        provider: Provider,
        status: RailProviderAuthStatus,
        *,
        expected_credential_version: int,
    ) -> None: ...


class UtcNow(Protocol):
    def __call__(self) -> datetime: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ReservationExecutionDependencies:
    session_factory: AsyncSessionFactory
    get_or_create_provider_circuit: GetOrCreateProviderCircuit
    apply_watch_transition: ApplyWatchTransition
    begin_reservation_attempt: BeginReservationAttempt
    add_outbox_event: AddOutboxEvent
    complete_reservation_attempt: CompleteReservationAttempt
    record_reservation_confirmation: RecordReservationConfirmation
    update_provider_auth_status: UpdateProviderAuthStatusInTransaction
    provider_call_errors: tuple[type[Exception], ...]
    srt_exact_reservation_source: str
    now: UtcNow = _utc_now


@dataclass(frozen=True)
class ReservationConfirmationEvaluation:
    result: ReservationResult
    confirmation: ReservationConfirmationResult | None
    request_id: str | None = None
    purpose: ReservationConfirmationPurpose | None = None

    def __iter__(self):
        yield self.result
        yield self.confirmation

    @property
    def outcome(self) -> ReservationOutcome:
        return self.result.outcome

    @property
    def source(self) -> str:
        return self.result.source

    @property
    def payment_deadline(self) -> datetime | None:
        return self.result.payment_deadline

    @property
    def official_handoff_url(self):
        return self.result.official_handoff_url


def _log_initial_confirmation_persisted(
    *,
    provider: Provider,
    confirmation: ReservationConfirmationResult,
    attempt_id: str,
    request_id: str,
    purpose: ReservationConfirmationPurpose,
    reconciliation_attempt_count: int,
    next_reconcile_at: datetime | None,
) -> None:
    logger.info(
        "Reservation confirmation persisted "
        "event=reservation_confirmation_persisted phase=initial_confirmation "
        "provider=%s purpose=%s outcome=%s confirmation_diagnostic_code=%s source=%s "
        "attempt_id=%s request_id=%s reconciliation_attempt=0 "
        "reconciliation_attempt_count=%s next_reconcile_at=%s",
        provider.value,
        purpose.value,
        confirmation.outcome.value,
        (
            confirmation.diagnostic_code.value
            if confirmation.diagnostic_code is not None
            else "none"
        ),
        confirmation.source,
        attempt_id,
        request_id,
        reconciliation_attempt_count,
        next_reconcile_at.isoformat() if next_reconcile_at is not None else "none",
    )


def provider_auth_status_for_reservation_outcome(
    outcome: ReservationOutcome,
) -> RailProviderAuthStatus | None:
    """Return only authentication metadata supported by a conclusive outcome."""
    if outcome in {
        ReservationOutcome.PAYMENT_REQUIRED,
        ReservationOutcome.RESERVED,
        ReservationOutcome.NOT_AVAILABLE,
    }:
        return "authenticated"
    if outcome is ReservationOutcome.AUTH_REQUIRED:
        return "auth_required"
    if outcome is ReservationOutcome.PROVIDER_BLOCKED:
        return "provider_blocked"
    # UNKNOWN and FAILED do not prove that the separately verified account is invalid.
    return None


def _request_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _has_reservation_requested_progress(result: ReservationResult) -> bool:
    return any(progress.stage == "reservation_requested" for progress in result.progress_stages)


def _unconfirmed_command_correlation_seats(
    result: ReservationResult,
    *,
    passenger_count: int,
):
    if not _has_reservation_requested_progress(result):
        return ()
    if result.outcome is ReservationOutcome.UNKNOWN:
        if result.result_reason_code not in POST_REQUEST_UNKNOWN_CORRELATION_REASON_CODES:
            return ()
        seats = result.confirmation_correlation_seats
    elif result.outcome in {ReservationOutcome.PAYMENT_REQUIRED, ReservationOutcome.RESERVED}:
        seats = result.reserved_seats
    else:
        return ()
    return seats if len(seats) == passenger_count else ()


def _post_request_auth_signal(
    result: ReservationResult,
) -> ReservationConfirmationOutcome | None:
    if result.outcome is not ReservationOutcome.UNKNOWN or not _has_reservation_requested_progress(
        result
    ):
        return None
    if result.result_reason_code is ReservationResultReasonCode.AUTHENTICATION_REQUIRED:
        return ReservationConfirmationOutcome.AUTH_REQUIRED
    if result.result_reason_code is ReservationResultReasonCode.PROVIDER_BLOCKED:
        return ReservationConfirmationOutcome.PROVIDER_BLOCKED
    return None


def _locked_authenticated_credential_version_query(provider: Provider):
    return (
        select(RailProviderAccount.credential_version)
        .where(
            RailProviderAccount.provider == provider,
            RailProviderAccount.enabled.is_(True),
            RailProviderAccount.last_auth_status == "authenticated",
        )
        .with_for_update()
    )


def _locked_provider_account_credential_version_query(provider: Provider):
    return (
        select(RailProviderAccount.credential_version)
        .where(RailProviderAccount.provider == provider)
        .with_for_update()
    )


def _locked_watch_query(watch_id: str):
    return select(Watch).where(Watch.id == watch_id).with_for_update()


def _locked_candidate_query(candidate_id: str):
    return (
        select(WatchCandidate)
        .where(WatchCandidate.id == candidate_id)
        .with_for_update(of=WatchCandidate)
    )


def _candidate_reservation_departure(candidate: WatchCandidate) -> datetime:
    departure = (
        candidate.actual_departure_at
        or candidate.estimated_departure_at
        or candidate.scheduled_departure_at
        or candidate.departure_at
    )
    if departure.tzinfo is None or departure.utcoffset() is None:
        return departure.replace(tzinfo=UTC)
    return departure.astimezone(UTC)


async def _expire_elapsed_reservation_target(
    session: AsyncSession,
    watch: Watch,
    candidate: WatchCandidate,
    *,
    now: datetime,
    dependencies: ReservationExecutionDependencies,
) -> bool:
    normalized_now = (
        now.replace(tzinfo=UTC)
        if now.tzinfo is None or now.utcoffset() is None
        else now.astimezone(UTC)
    )
    if _candidate_reservation_departure(candidate) > normalized_now:
        return False
    candidate.state = "expired"
    candidate.suppressed_by_candidate_id = None
    candidate.manual_rearm_source_attempt_id = None
    candidate.manual_rearm_authorized_at = None
    remaining_candidate_id = await session.scalar(
        select(WatchCandidate.id)
        .where(
            WatchCandidate.watch_id == watch.id,
            WatchCandidate.id != candidate.id,
            WatchCandidate.state.in_(["active", "observed", "seat_found"]),
        )
        .order_by(WatchCandidate.priority, WatchCandidate.id)
        .limit(1)
    )
    watch.observation_in_flight_until = None
    if remaining_candidate_id is None:
        await dependencies.apply_watch_transition(
            session,
            watch,
            WatchStatus.EXPIRED,
            reason="all_candidates_departed_before_reservation",
        )
    else:
        await dependencies.apply_watch_transition(
            session,
            watch,
            WatchStatus.WATCHING,
            reason="reservation_target_departure_elapsed_before_provider_call",
        )
        watch.next_check_at = normalized_now
    return True


def _locked_attempt_query(attempt_id: str):
    return select(ReservationAttempt).where(ReservationAttempt.id == attempt_id).with_for_update()


async def confirm_provider_reservation_result(
    adapter: ReservationExecutionProvider,
    target: ReservationExecutionTarget,
    attempt_id: str,
    result: ReservationResult,
    *,
    dependencies: ReservationExecutionDependencies,
) -> ReservationConfirmationEvaluation:
    """Require exact official hold evidence before exposing a payment-required state."""
    if target.provider not in EXTERNAL_RESERVATION_PROVIDERS or result.outcome not in {
        ReservationOutcome.PAYMENT_REQUIRED,
        ReservationOutcome.RESERVED,
        ReservationOutcome.UNKNOWN,
    }:
        return ReservationConfirmationEvaluation(result, None)
    if result.credential_version is None:
        return ReservationConfirmationEvaluation(
            ReservationResult(
                outcome=ReservationOutcome.UNKNOWN,
                result_reason_code=ReservationResultReasonCode.PROVIDER_RESPONSE_INVALID,
                source=result.source,
                observed_at=result.observed_at,
                progress_stages=result.progress_stages,
            ),
            None,
        )
    if (
        target.provider is Provider.SRT
        and result.outcome is ReservationOutcome.PAYMENT_REQUIRED
        and result.source == dependencies.srt_exact_reservation_source
        and result.official_handoff_url is not None
    ):
        # The SRT executor already exact-matched the returned official reservation;
        # no separate confirmation read is made, so no confirmation request_id exists.
        confirmation = ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
            source=result.source,
            observed_at=result.observed_at,
            payment_deadline=result.payment_deadline,
            official_handoff_url=str(result.official_handoff_url),
        )
        return ReservationConfirmationEvaluation(result, confirmation)
    private_correlation_seats = _unconfirmed_command_correlation_seats(
        result,
        passenger_count=target.passenger_count,
    )
    confirmation_purpose = (
        ReservationConfirmationPurpose.UNKNOWN_RESULT_FOLLOW_UP
        if result.outcome is ReservationOutcome.UNKNOWN
        else ReservationConfirmationPurpose.INITIAL
    )
    confirmation_target = ReservationConfirmationTarget(
        attempt_id=attempt_id,
        candidate_id=target.candidate_id,
        provider=target.provider,
        train_number=target.train_number,
        origin=target.origin,
        destination=target.destination,
        departure_at=target.departure_at,
        arrival_at=target.arrival_at,
        seat_class=SeatClass(target.seat_class),
        passenger_count=target.passenger_count,
        credential_version=result.credential_version,
        purpose=confirmation_purpose,
        confirmation_correlation_seats=tuple(
            ReservationConfirmationSeat(
                car_number=seat.car_number,
                seat_number=seat.seat_number,
            )
            for seat in (
                private_correlation_seats
                if confirmation_purpose is ReservationConfirmationPurpose.UNKNOWN_RESULT_FOLLOW_UP
                else ()
            )
        ),
    )
    with bind_request_id() as request_id:
        logger.info(
            "Reservation confirmation started event=reservation_confirmation_started "
            "phase=initial_confirmation provider=%s purpose=%s attempt_id=%s "
            "request_id=%s reconciliation_attempt=0",
            target.provider.value,
            confirmation_target.purpose.value,
            attempt_id,
            request_id,
        )
        try:
            confirmation = await adapter.confirm_reservation(confirmation_target)
        except (*dependencies.provider_call_errors, RuntimeError, TypeError, ValueError):
            confirmation = ReservationConfirmationResult(
                provider=target.provider,
                outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
                source="worker-initial-confirmation",
                observed_at=dependencies.now(),
                diagnostic_code=(ReservationConfirmationDiagnosticCode.OFFICIAL_READ_UNAVAILABLE),
            )
        confirmation = enforce_confirmation_target_safety(confirmation_target, confirmation)
        post_request_auth_signal = _post_request_auth_signal(result)
        if post_request_auth_signal is not None and confirmation.outcome not in {
            ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
            ReservationConfirmationOutcome.CONFIRMED_PAID,
        }:
            # The side-effecting command already crossed the official request
            # boundary. Preserve its exact AUTH/BLOCK signal unless a fresh
            # official hold proves the command succeeded.
            confirmation = ReservationConfirmationResult(
                provider=target.provider,
                outcome=post_request_auth_signal,
                source=result.source,
                observed_at=result.observed_at,
            )
        logger.info(
            "Reservation confirmation classified event=reservation_confirmation_classified "
            "phase=initial_confirmation provider=%s purpose=%s outcome=%s "
            "confirmation_diagnostic_code=%s source=%s attempt_id=%s request_id=%s "
            "reconciliation_attempt=0",
            target.provider.value,
            confirmation_target.purpose.value,
            confirmation.outcome.value,
            (
                confirmation.diagnostic_code.value
                if confirmation.diagnostic_code is not None
                else "none"
            ),
            confirmation.source,
            attempt_id,
            request_id,
        )
    if confirmation.outcome is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED:
        confirmed_reserved_seats = result.reserved_seats
        if (
            result.outcome is ReservationOutcome.UNKNOWN
            and len(result.confirmation_correlation_seats) == target.passenger_count
        ):
            # A strict post-request UNKNOWN can carry private seat identity.  A
            # fresh official hold read makes that identity confirmed, so promote
            # it for the subsequent PAYMENT_FOLLOW_UP and stop treating it as
            # uncertain correlation data.
            confirmed_reserved_seats = result.confirmation_correlation_seats
        return ReservationConfirmationEvaluation(
            ReservationResult(
                outcome=ReservationOutcome.PAYMENT_REQUIRED,
                result_reason_code=ReservationResultReasonCode.PAYMENT_HOLD_CREATED,
                source=confirmation.source,
                observed_at=confirmation.observed_at,
                credential_version=result.credential_version,
                payment_deadline=confirmation.payment_deadline,
                official_handoff_url=confirmation.official_handoff_url,
                progress_stages=result.progress_stages,
                reserved_seats=confirmed_reserved_seats,
            ),
            confirmation,
            request_id,
            confirmation_target.purpose,
        )
    command_may_have_been_issued = result.outcome in {
        ReservationOutcome.UNKNOWN,
        ReservationOutcome.PAYMENT_REQUIRED,
        ReservationOutcome.RESERVED,
    } or _has_reservation_requested_progress(result)
    auth_confirmation_outcome = {
        ReservationConfirmationOutcome.AUTH_REQUIRED: ReservationOutcome.AUTH_REQUIRED,
        ReservationConfirmationOutcome.PROVIDER_BLOCKED: ReservationOutcome.PROVIDER_BLOCKED,
    }.get(confirmation.outcome)
    mapped_outcome = (
        ReservationOutcome.UNKNOWN
        if auth_confirmation_outcome is not None and command_may_have_been_issued
        else auth_confirmation_outcome or ReservationOutcome.UNKNOWN
    )
    mapped_reason_code = {
        ReservationOutcome.AUTH_REQUIRED: ReservationResultReasonCode.AUTHENTICATION_REQUIRED,
        ReservationOutcome.PROVIDER_BLOCKED: ReservationResultReasonCode.PROVIDER_BLOCKED,
    }.get(
        mapped_outcome,
        (
            result.result_reason_code
            if result.outcome is ReservationOutcome.UNKNOWN
            else ReservationResultReasonCode.RESERVATION_REQUEST_RESULT_UNKNOWN
        ),
    )
    return ReservationConfirmationEvaluation(
        ReservationResult(
            outcome=mapped_outcome,
            result_reason_code=mapped_reason_code,
            source=confirmation.source,
            observed_at=confirmation.observed_at,
            credential_version=result.credential_version,
            progress_stages=result.progress_stages,
            confirmation_correlation_seats=(
                _unconfirmed_command_correlation_seats(
                    result,
                    passenger_count=target.passenger_count,
                )
                if mapped_outcome is ReservationOutcome.UNKNOWN
                else ()
            ),
        ),
        confirmation,
        request_id,
        confirmation_target.purpose,
    )


async def _add_late_result_evidence_event(
    session: AsyncSession,
    watch: Watch,
    candidate: WatchCandidate,
    attempt: ReservationAttempt,
    *,
    dependencies: ReservationExecutionDependencies,
) -> None:
    reason_code = (
        attempt.result_reason_code or ReservationResultReasonCode.RESERVATION_REQUEST_RESULT_UNKNOWN
    )
    await dependencies.add_outbox_event(
        session,
        aggregate_type="watch",
        aggregate_id=watch.id,
        event_type="watch.reservation_result_requires_manual_check",
        payload={
            "watch_id": watch.id,
            "candidate_id": candidate.id,
            "outcome": attempt.outcome.value,
            "result_reason_code": reason_code.value,
            "confirmation_outcome": (
                attempt.confirmation_outcome.value
                if attempt.confirmation_outcome is not None
                else None
            ),
            "confirmation_diagnostic_code": (
                attempt.confirmation_diagnostic_code.value
                if attempt.confirmation_diagnostic_code is not None
                else None
            ),
            "confirmation_observed_at": (
                attempt.confirmation_observed_at.isoformat()
                if attempt.confirmation_observed_at is not None
                else None
            ),
            "reconciliation_attempt_count": attempt.reconciliation_attempt_count,
            "next_reconcile_at": (
                attempt.next_reconcile_at.isoformat()
                if attempt.next_reconcile_at is not None
                else None
            ),
            "reason": "watch_state_changed_during_provider_call",
        },
        dedupe_key=f"reservation-result-fenced:{attempt.id}",
    )


async def _add_late_exact_paid_evidence_event(
    session: AsyncSession,
    watch: Watch,
    candidate: WatchCandidate,
    attempt: ReservationAttempt,
    confirmation: ReservationConfirmationResult,
    *,
    dependencies: ReservationExecutionDependencies,
) -> None:
    reason_code = (
        attempt.result_reason_code or ReservationResultReasonCode.RESERVATION_REQUEST_RESULT_UNKNOWN
    )
    await dependencies.add_outbox_event(
        session,
        aggregate_type="watch",
        aggregate_id=watch.id,
        event_type="watch.reservation_reconciled",
        payload={
            "watch_id": watch.id,
            "candidate_id": candidate.id,
            "attempt_sequence": attempt.attempt_sequence,
            "attempt_started_at": attempt.started_at.isoformat(),
            "attempt_finished_at": (
                attempt.finished_at.isoformat() if attempt.finished_at is not None else None
            ),
            "outcome": attempt.outcome.value,
            "result_reason_code": reason_code.value,
            "payment_actionable": False,
            "confirmation_outcome": confirmation.outcome.value,
            "confirmation_diagnostic_code": (
                attempt.confirmation_diagnostic_code.value
                if attempt.confirmation_diagnostic_code is not None
                else None
            ),
            "confirmation_observed_at": confirmation.observed_at.isoformat(),
            "reconciliation_attempt_count": attempt.reconciliation_attempt_count,
            "reconciliation_resolution": None,
            "next_reconcile_at": None,
            "payment_deadline": None,
            "progress_stages": attempt.progress_stages or [],
            "reserved_seats": attempt.reserved_seats or [],
            "retryable": False,
        },
        dedupe_key=f"reservation-reconciled:{attempt.id}:{confirmation.observed_at.isoformat()}",
    )


async def execute_reservation(
    adapter: ReservationExecutionProvider,
    target: ReservationExecutionTarget,
    *,
    dependencies: ReservationExecutionDependencies,
) -> None:
    """Claim one episode before I/O, invoke the provider once, then persist its result."""
    provider_credential_version: int | None = None
    async with dependencies.session_factory() as session:
        try:
            if target.provider in EXTERNAL_RESERVATION_PROVIDERS:
                # Keep the global account -> watch -> candidate lock order.
                provider_credential_version = await session.scalar(
                    _locked_authenticated_credential_version_query(target.provider)
                )
            watch = await session.scalar(_locked_watch_query(target.watch_id))
            candidate = await session.scalar(_locked_candidate_query(target.candidate_id))
            if (
                watch is None
                or candidate is None
                or watch.status != WatchStatus.SEAT_FOUND
                or watch.reservation_policy != ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT
            ):
                return
            reservation_now = dependencies.now()
            if await _expire_elapsed_reservation_target(
                session,
                watch,
                candidate,
                now=reservation_now,
                dependencies=dependencies,
            ):
                await session.commit()
                return
            circuit = await dependencies.get_or_create_provider_circuit(
                session, watch.provider, lock=True
            )
            if circuit.state != ProviderCircuitState.CLOSED:
                if (
                    circuit.state == ProviderCircuitState.OPEN
                    and not circuit.manual_resume_required
                ):
                    watch.cooldown_until = circuit.cooldown_until
                    await dependencies.apply_watch_transition(
                        session,
                        watch,
                        WatchStatus.COOLDOWN,
                        reason="provider_circuit_open_before_reservation",
                    )
                    watch.next_check_at = circuit.cooldown_until
                else:
                    await dependencies.apply_watch_transition(
                        session,
                        watch,
                        WatchStatus.AUTH_REQUIRED,
                        reason="provider_circuit_manual_hold_before_reservation",
                    )
                    watch.next_check_at = None
                await session.commit()
                return
            if (
                watch.provider in EXTERNAL_RESERVATION_PROVIDERS
                and provider_credential_version is None
            ):
                await dependencies.apply_watch_transition(
                    session,
                    watch,
                    WatchStatus.AUTH_REQUIRED,
                    reason="provider_account_not_authenticated_before_reservation",
                )
                watch.next_check_at = None
                await session.commit()
                return
            if target.reservation_episode_key is None:
                return
            idempotency_key = (
                f"reserve:{target.candidate_id}:"
                f"{_request_hash(target.reservation_episode_key)[:32]}"
            )
            attempt, created = await dependencies.begin_reservation_attempt(
                session,
                watch,
                candidate,
                idempotency_key,
                episode_key=target.reservation_episode_key,
                retry_authorized=True,
                credential_version=provider_credential_version,
            )
            attempt_id = attempt.id
            # This durable claim is intentionally committed before provider I/O.
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    if not created:
        return

    reservation_request = target.reservation_request(
        idempotency_key,
        expected_credential_version=provider_credential_version,
    )
    cumulative_progress: tuple[ReservationProgressStage, ...] = ()
    try:
        if target.provider is Provider.KORAIL and isinstance(adapter, ReservationProgressProvider):

            async def on_progress(progress: ReservationProgressStage) -> None:
                nonlocal cumulative_progress
                next_progress = cumulative_progress_with(cumulative_progress, progress)
                if next_progress is None:
                    return
                cumulative_progress = next_progress
                try:
                    await record_reservation_progress(
                        session_factory=dependencies.session_factory,
                        add_outbox_event=dependencies.add_outbox_event,
                        watch_id=target.watch_id,
                        candidate_id=target.candidate_id,
                        attempt_id=attempt_id,
                        expected_credential_version=provider_credential_version,
                        cumulative_progress=cumulative_progress,
                    )
                except Exception:  # noqa: BLE001 -- progress must not abort the provider command.
                    logger.warning(
                        "Reservation progress persistence failed "
                        "watch_id=%s attempt_id=%s stage=%s",
                        target.watch_id,
                        attempt_id,
                        progress.stage,
                    )

            result = await adapter.reserve_once_with_progress(reservation_request, on_progress)
        else:
            result = await adapter.reserve_once(reservation_request)
    except dependencies.provider_call_errors:
        # A provider adapter that returns FAILED has proved that its reservation
        # command never crossed the dispatch boundary. An exception escaping an
        # external adapter carries no such proof: the sidecar may have accepted
        # the command before its response was lost. Preserve the no-retry/manual
        # confirmation fence by classifying that boundary failure as UNKNOWN.
        command_status_unknown = target.provider in EXTERNAL_RESERVATION_PROVIDERS
        result = ReservationResult(
            outcome=(
                ReservationOutcome.UNKNOWN if command_status_unknown else ReservationOutcome.FAILED
            ),
            result_reason_code=ReservationResultReasonCode.PROVIDER_UNAVAILABLE,
            source=("mock" if target.provider is Provider.MOCK else "authorized-provider"),
            observed_at=dependencies.now(),
            credential_version=provider_credential_version,
            progress_stages=cumulative_progress if command_status_unknown else (),
        )
    evaluation = await confirm_provider_reservation_result(
        adapter,
        target,
        attempt_id,
        result,
        dependencies=dependencies,
    )
    result = evaluation.result
    confirmation = evaluation.confirmation
    auth_status = None
    if target.provider in EXTERNAL_RESERVATION_PROVIDERS:
        if confirmation is not None and confirmation.outcome in {
            ReservationConfirmationOutcome.AUTH_REQUIRED,
            ReservationConfirmationOutcome.PROVIDER_BLOCKED,
        }:
            auth_status = (
                "auth_required"
                if confirmation.outcome is ReservationConfirmationOutcome.AUTH_REQUIRED
                else "provider_blocked"
            )
        else:
            auth_status = provider_auth_status_for_reservation_outcome(result.outcome)

    async with dependencies.session_factory() as session:
        try:
            current_credential_version: int | None = None
            if target.provider in EXTERNAL_RESERVATION_PROVIDERS:
                current_credential_version = await session.scalar(
                    _locked_provider_account_credential_version_query(target.provider)
                )
                if (
                    result.credential_version is None
                    or current_credential_version != result.credential_version
                ):
                    # A provider result belongs to the credential generation that made
                    # the external call. A newer verified login fences the entire late
                    # result transaction, including watch/attempt/outbox/payment state.
                    return
                if auth_status in {"auth_required", "provider_blocked"}:
                    await dependencies.update_provider_auth_status(
                        session,
                        target.provider,
                        auth_status,
                        expected_credential_version=result.credential_version,
                    )
            watch = await session.scalar(_locked_watch_query(target.watch_id))
            candidate = await session.scalar(_locked_candidate_query(target.candidate_id))
            attempt = await session.scalar(_locked_attempt_query(attempt_id))
            if watch is None or candidate is None or attempt is None:
                if auth_status in {"auth_required", "provider_blocked"}:
                    # Provider-global authentication evidence belongs to the locked
                    # credential generation even when the watch was deleted during I/O.
                    await session.commit()
                return
            if (
                watch.status != WatchStatus.RESERVING
                or attempt.outcome != ReservationOutcome.PENDING
                or candidate.state != "reservation_attempted"
            ):
                if attempt.outcome == ReservationOutcome.PENDING:
                    attempt.outcome = ReservationOutcome.UNKNOWN
                    attempt.result_reason_code = (
                        result.result_reason_code
                        if result.outcome is ReservationOutcome.UNKNOWN
                        else ReservationResultReasonCode.RESERVATION_REQUEST_RESULT_UNKNOWN
                    )
                    attempt.finished_at = dependencies.now()
                    attempt.credential_version = result.credential_version
                    attempt.progress_stages = [
                        {
                            "stage": progress.stage,
                            "occurred_at": progress.occurred_at.isoformat(),
                        }
                        for progress in result.progress_stages
                    ]
                    attempt.reserved_seats = []
                    attempt.confirmation_correlation_seats = [
                        seat.model_dump()
                        for seat in _unconfirmed_command_correlation_seats(
                            result,
                            passenger_count=target.passenger_count,
                        )
                    ]
                    if confirmation is not None:
                        dependencies.record_reservation_confirmation(attempt, confirmation)
                if (
                    confirmation is not None
                    and confirmation.outcome is ReservationConfirmationOutcome.CONFIRMED_PAID
                ):
                    if attempt.confirmation_outcome is not (
                        ReservationConfirmationOutcome.CONFIRMED_PAID
                    ):
                        dependencies.record_reservation_confirmation(attempt, confirmation)
                    payment_completed_emitted = await apply_exact_paid_resolution(
                        session,
                        watch,
                        candidate,
                        attempt,
                        apply_watch_transition=dependencies.apply_watch_transition,
                        add_outbox_event=dependencies.add_outbox_event,
                    )
                    if not payment_completed_emitted:
                        await _add_late_exact_paid_evidence_event(
                            session,
                            watch,
                            candidate,
                            attempt,
                            confirmation,
                            dependencies=dependencies,
                        )
                    await session.commit()
                    if evaluation.request_id is not None:
                        _log_initial_confirmation_persisted(
                            provider=target.provider,
                            confirmation=confirmation,
                            attempt_id=attempt_id,
                            request_id=evaluation.request_id,
                            purpose=(evaluation.purpose or ReservationConfirmationPurpose.INITIAL),
                            reconciliation_attempt_count=attempt.reconciliation_attempt_count,
                            next_reconcile_at=attempt.next_reconcile_at,
                        )
                    return
                if auth_status in {"auth_required", "provider_blocked"}:
                    watch_candidates = list(
                        (
                            await session.scalars(
                                select(WatchCandidate).where(WatchCandidate.watch_id == watch.id)
                            )
                        ).all()
                    )
                    for watch_candidate in watch_candidates:
                        watch_candidate.manual_rearm_source_attempt_id = None
                        watch_candidate.manual_rearm_authorized_at = None
                    if watch.status in {
                        WatchStatus.WATCHING,
                        WatchStatus.OFFICIAL_WAITLIST,
                        WatchStatus.SEAT_FOUND,
                        WatchStatus.RESERVING,
                    }:
                        watch.next_check_at = None
                        watch.observation_in_flight_until = None
                        await dependencies.apply_watch_transition(
                            session,
                            watch,
                            WatchStatus.AUTH_REQUIRED,
                            reason=(
                                "reservation_reconciliation_auth_required"
                                if auth_status == "auth_required"
                                else "reservation_reconciliation_provider_blocked"
                            ),
                        )
                if watch.status == WatchStatus.EXPIRED:
                    candidate.state = "expired"
                elif candidate.state == "reservation_attempted":
                    candidate.state = "observed"
                await _add_late_result_evidence_event(
                    session,
                    watch,
                    candidate,
                    attempt,
                    dependencies=dependencies,
                )
                await session.commit()
                if confirmation is not None and evaluation.request_id is not None:
                    _log_initial_confirmation_persisted(
                        provider=target.provider,
                        confirmation=confirmation,
                        attempt_id=attempt_id,
                        request_id=evaluation.request_id,
                        purpose=evaluation.purpose or ReservationConfirmationPurpose.INITIAL,
                        reconciliation_attempt_count=attempt.reconciliation_attempt_count,
                        next_reconcile_at=attempt.next_reconcile_at,
                    )
                return
            if (
                auth_status is not None
                and auth_status not in {"auth_required", "provider_blocked"}
                and result.credential_version is not None
            ):
                await dependencies.update_provider_auth_status(
                    session,
                    watch.provider,
                    auth_status,
                    expected_credential_version=result.credential_version,
                )
            await dependencies.complete_reservation_attempt(
                session,
                watch,
                candidate,
                attempt,
                result,
                confirmation=confirmation,
            )
            await session.commit()
            if confirmation is not None and evaluation.request_id is not None:
                _log_initial_confirmation_persisted(
                    provider=target.provider,
                    confirmation=confirmation,
                    attempt_id=attempt_id,
                    request_id=evaluation.request_id,
                    purpose=evaluation.purpose or ReservationConfirmationPurpose.INITIAL,
                    reconciliation_attempt_count=attempt.reconciliation_attempt_count,
                    next_reconcile_at=attempt.next_reconcile_at,
                )
        except Exception:
            # The pre-I/O claim remains durable while partial result writes roll back.
            await session.rollback()
            raise
