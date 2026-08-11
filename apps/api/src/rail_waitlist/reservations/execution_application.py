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
    SeatClass,
    WatchStatus,
)
from ..outbox_management.models import OutboxEvent
from ..provider_account_management.models import RailProviderAccount
from ..provider_account_management.schemas import RailProviderAuthStatus
from ..provider_circuit.models import ProviderCircuit
from ..provider_contracts import ReservationExecutionProvider
from ..watch_management.models import ReservationAttempt, Watch, WatchCandidate
from .contracts import ReservationProgressStage, ReservationRequest, ReservationResult
from .progress_application import cumulative_progress_with, record_reservation_progress
from .provider_confirmation.contracts import (
    ReservationConfirmationOutcome,
    ReservationConfirmationResult,
    ReservationConfirmationTarget,
)

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
        # The SRT executor already exact-matched the returned official reservation.
        confirmation = ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
            source=result.source,
            observed_at=result.observed_at,
            payment_deadline=result.payment_deadline,
            official_handoff_url=str(result.official_handoff_url),
        )
        return ReservationConfirmationEvaluation(result, confirmation)
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
    )
    try:
        confirmation = await adapter.confirm_reservation(confirmation_target)
    except (*dependencies.provider_call_errors, TypeError):
        return ReservationConfirmationEvaluation(
            ReservationResult(
                outcome=ReservationOutcome.UNKNOWN,
                source=result.source,
                observed_at=dependencies.now(),
                credential_version=result.credential_version,
                progress_stages=result.progress_stages,
            ),
            None,
        )
    if confirmation.outcome is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED:
        return ReservationConfirmationEvaluation(
            ReservationResult(
                outcome=ReservationOutcome.PAYMENT_REQUIRED,
                source=confirmation.source,
                observed_at=confirmation.observed_at,
                credential_version=result.credential_version,
                payment_deadline=confirmation.payment_deadline,
                official_handoff_url=confirmation.official_handoff_url,
                progress_stages=result.progress_stages,
                reserved_seats=result.reserved_seats,
            ),
            confirmation,
        )
    mapped_outcome = {
        ReservationConfirmationOutcome.AUTH_REQUIRED: ReservationOutcome.AUTH_REQUIRED,
        ReservationConfirmationOutcome.PROVIDER_BLOCKED: ReservationOutcome.PROVIDER_BLOCKED,
    }.get(confirmation.outcome, ReservationOutcome.UNKNOWN)
    return ReservationConfirmationEvaluation(
        ReservationResult(
            outcome=mapped_outcome,
            source=confirmation.source,
            observed_at=confirmation.observed_at,
            credential_version=result.credential_version,
            progress_stages=result.progress_stages,
        ),
        confirmation,
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
    try:
        if target.provider is Provider.KORAIL and isinstance(adapter, ReservationProgressProvider):
            cumulative_progress: tuple[ReservationProgressStage, ...] = ()

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
        result = ReservationResult(
            outcome=ReservationOutcome.FAILED,
            source=("mock" if target.provider is Provider.MOCK else "authorized-provider"),
            observed_at=dependencies.now(),
            credential_version=provider_credential_version,
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
    auth_status = (
        provider_auth_status_for_reservation_outcome(result.outcome)
        if target.provider in EXTERNAL_RESERVATION_PROVIDERS
        else None
    )

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
            watch = await session.scalar(_locked_watch_query(target.watch_id))
            candidate = await session.scalar(_locked_candidate_query(target.candidate_id))
            attempt = await session.scalar(_locked_attempt_query(attempt_id))
            if watch is None or candidate is None or attempt is None:
                return
            if (
                watch.status != WatchStatus.RESERVING
                or attempt.outcome != ReservationOutcome.PENDING
                or candidate.state != "reservation_attempted"
            ):
                if attempt.outcome == ReservationOutcome.PENDING:
                    attempt.outcome = ReservationOutcome.UNKNOWN
                    attempt.finished_at = dependencies.now()
                    attempt.credential_version = result.credential_version
                    if confirmation is not None:
                        dependencies.record_reservation_confirmation(attempt, confirmation)
                if watch.status == WatchStatus.EXPIRED:
                    candidate.state = "expired"
                elif candidate.state == "reservation_attempted":
                    candidate.state = "failed"
                await dependencies.add_outbox_event(
                    session,
                    aggregate_type="watch",
                    aggregate_id=watch.id,
                    event_type="watch.reservation_result_requires_manual_check",
                    payload={
                        "watch_id": watch.id,
                        "candidate_id": candidate.id,
                        "reason": "watch_state_changed_during_provider_call",
                    },
                    dedupe_key=f"reservation-result-fenced:{attempt.id}",
                )
                await session.commit()
                return
            if auth_status is not None and result.credential_version is not None:
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
        except Exception:
            # The pre-I/O claim remains durable while partial result writes roll back.
            await session.rollback()
            raise
