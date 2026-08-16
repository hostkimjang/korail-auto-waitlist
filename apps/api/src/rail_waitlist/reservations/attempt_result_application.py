from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import ReservationOutcome, ReservationResultReasonCode, WatchStatus
from ..watch_management.models import ReservationAttempt, SeatObservation, Watch, WatchCandidate
from .attempt_timing_application import latest_candidate_seat_detected_at
from .contracts import ReservationResult
from .domain import ReservationAttemptResultPolicy
from .exact_paid_application import apply_exact_paid_resolution
from .progress_timing_policy import (
    normalize_reservation_terminal_time,
    persisted_reservation_progress_times,
)
from .provider_confirmation.contracts import (
    ReservationConfirmationOutcome,
    ReservationConfirmationResult,
    effective_reservation_confirmation_diagnostic_code,
)
from .reconciliation_policy import RESERVATION_RECONCILIATION_INTERVAL


class ReservationAttemptAlreadyCompleted(Exception):
    """Raised when a terminal reservation attempt receives another result."""


class ApplyWatchTransition(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        watch: Watch,
        target: WatchStatus,
        idempotency_key: str | None = None,
        *,
        reason: str | None = None,
        observation: SeatObservation | None = None,
    ) -> Watch: ...


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
    ) -> object: ...


class Clock(Protocol):
    def __call__(self) -> datetime: ...


class ReservationResultPolicy(Protocol):
    def __call__(self, outcome: ReservationOutcome) -> ReservationAttemptResultPolicy: ...


class RecordReservationConfirmation(Protocol):
    def __call__(
        self,
        attempt: ReservationAttempt,
        confirmation: ReservationConfirmationResult,
        *,
        reconciled_at: datetime | None = None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class ReservationAttemptResultDependencies:
    apply_watch_transition: ApplyWatchTransition
    add_outbox_event: AddOutboxEvent
    now: Clock
    result_policy: ReservationResultPolicy
    record_reservation_confirmation: RecordReservationConfirmation


def record_reservation_confirmation(
    attempt: ReservationAttempt,
    confirmation: ReservationConfirmationResult,
    *,
    reconciled_at: datetime | None = None,
) -> None:
    """Persist normalized confirmation evidence without provider transport material."""

    attempt.confirmation_outcome = confirmation.outcome
    attempt.confirmation_diagnostic_code = confirmation.diagnostic_code
    attempt.confirmation_source = confirmation.source
    attempt.confirmation_observed_at = confirmation.observed_at
    if reconciled_at is not None:
        if reconciled_at.tzinfo is None or reconciled_at.utcoffset() is None:
            raise ValueError("reconciled_at must include a timezone")
        attempt.last_reconciled_at = max(reconciled_at, confirmation.observed_at)


async def complete_reservation_attempt(
    session: AsyncSession,
    watch: Watch,
    candidate: WatchCandidate,
    attempt: ReservationAttempt,
    result: ReservationResult,
    confirmation: ReservationConfirmationResult | None = None,
    *,
    dependencies: ReservationAttemptResultDependencies,
) -> None:
    """Apply a provider result inside the caller-owned persistence unit of work."""
    if attempt.outcome != ReservationOutcome.PENDING:
        raise ReservationAttemptAlreadyCompleted
    if attempt.reconciliation_attempt_count is None:
        attempt.reconciliation_attempt_count = 0
    attempt.outcome = result.outcome
    attempt.result_reason_code = result.result_reason_code
    if result.credential_version is not None:
        attempt.credential_version = result.credential_version
    if confirmation is not None:
        if confirmation.provider != watch.provider:
            raise ValueError("reservation confirmation provider does not match watch")
        dependencies.record_reservation_confirmation(attempt, confirmation)
    completed_at = dependencies.now()
    attempt.finished_at = normalize_reservation_terminal_time(
        max(result.observed_at, completed_at),
        (
            *(progress.occurred_at for progress in result.progress_stages),
            *persisted_reservation_progress_times(attempt.progress_stages),
        ),
    )
    if (
        result.outcome is ReservationOutcome.UNKNOWN
        and confirmation is not None
        and confirmation.outcome
        in {
            ReservationConfirmationOutcome.INCONCLUSIVE,
            ReservationConfirmationOutcome.NOT_FOUND,
        }
    ):
        # An immediate official-list miss can race the provider's list propagation.
        # Require one delayed read before exact absence may unlock the bounded
        # confirmed-absent recovery episode.
        attempt.next_reconcile_at = (
            max(completed_at, confirmation.observed_at) + RESERVATION_RECONCILIATION_INTERVAL
        )
    attempt.payment_deadline = result.payment_deadline
    attempt.official_handoff_url = (
        str(result.official_handoff_url) if result.official_handoff_url is not None else None
    )
    attempt.reserved_seats = [seat.model_dump() for seat in result.reserved_seats]
    attempt.confirmation_correlation_seats = [
        seat.model_dump() for seat in result.confirmation_correlation_seats
    ]
    if result.progress_stages:
        attempt.progress_stages = [
            {
                "stage": progress.stage,
                "occurred_at": progress.occurred_at.isoformat(),
            }
            for progress in result.progress_stages
        ]
    persisted_progress = attempt.progress_stages or []

    unknown_confirmation_auth_failure = (
        result.outcome is ReservationOutcome.UNKNOWN
        and confirmation is not None
        and confirmation.outcome
        in {
            ReservationConfirmationOutcome.AUTH_REQUIRED,
            ReservationConfirmationOutcome.PROVIDER_BLOCKED,
        }
    )
    confirmed_paid = (
        result.outcome is ReservationOutcome.UNKNOWN
        and confirmation is not None
        and confirmation.outcome is ReservationConfirmationOutcome.CONFIRMED_PAID
    )
    if confirmed_paid:
        await apply_exact_paid_resolution(
            session,
            watch,
            candidate,
            attempt,
            apply_watch_transition=dependencies.apply_watch_transition,
            add_outbox_event=dependencies.add_outbox_event,
        )
        return
    successful_hold = result.outcome in {
        ReservationOutcome.PAYMENT_REQUIRED,
        ReservationOutcome.RESERVED,
    }
    if (
        successful_hold
        and result.payment_deadline is not None
        and result.payment_deadline <= completed_at
    ):
        attempt.outcome = ReservationOutcome.UNKNOWN
        attempt.result_reason_code = ReservationResultReasonCode.RESERVATION_REQUEST_RESULT_UNKNOWN
        attempt.payment_deadline = None
        attempt.official_handoff_url = None
        attempt.confirmation_correlation_seats = list(attempt.reserved_seats)
        attempt.reserved_seats = []
        # An already unusable hold is ambiguous rather than authentication failure.
        # The UNKNOWN attempt remains the durable no-retry fence.
        candidate.state = "observed"
        if watch.status == WatchStatus.RESERVING:
            await dependencies.apply_watch_transition(
                session,
                watch,
                WatchStatus.WATCHING,
                reason="reservation_result_deadline_already_elapsed",
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
                "result_reason_code": attempt.result_reason_code.value,
                "confirmation_outcome": (
                    attempt.confirmation_outcome.value
                    if attempt.confirmation_outcome is not None
                    else None
                ),
                "confirmation_diagnostic_code": (
                    diagnostic.value
                    if (
                        diagnostic := effective_reservation_confirmation_diagnostic_code(
                            attempt.confirmation_outcome,
                            attempt.confirmation_diagnostic_code,
                        )
                    )
                    is not None
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
                "reason": "payment_deadline_already_elapsed",
            },
            dedupe_key=f"reservation-result-expired-deadline:{attempt.id}",
        )
        return
    if successful_hold:
        candidate.state = "payment_required"
        watch.payment_deadline = result.payment_deadline
        if result.official_handoff_url is None:
            raise RuntimeError("successful reservation result requires an official handoff URL")
        watch.official_booking_url = str(result.official_handoff_url)
        if watch.status == WatchStatus.RESERVING:
            await dependencies.apply_watch_transition(
                session,
                watch,
                WatchStatus.PAYMENT_REQUIRED,
                reason="reservation_requires_user_payment",
            )
        lower_candidates = list(
            (
                await session.scalars(
                    select(WatchCandidate).where(
                        WatchCandidate.watch_id == watch.id,
                        WatchCandidate.priority > candidate.priority,
                        WatchCandidate.state.in_(["active", "observed", "seat_found"]),
                    )
                )
            ).all()
        )
        for lower in lower_candidates:
            lower.state = "suppressed_by_priority"
            lower.suppressed_by_candidate_id = candidate.id
            await dependencies.add_outbox_event(
                session,
                aggregate_type="watch",
                aggregate_id=watch.id,
                event_type="watch.candidate_suppressed",
                payload={
                    "watch_id": watch.id,
                    "candidate_id": lower.id,
                    "suppressed_by_candidate_id": candidate.id,
                    "reason": "higher_priority_payment_required",
                },
                dedupe_key=f"candidate-suppressed:{lower.id}:{candidate.id}",
            )
    else:
        monitoring_resumed = (
            result.outcome
            in {
                ReservationOutcome.NOT_AVAILABLE,
                ReservationOutcome.UNKNOWN,
                ReservationOutcome.FAILED,
            }
            and not unknown_confirmation_auth_failure
        )
        if monitoring_resumed:
            # These outcomes do not prove monitoring itself is unsafe.
            candidate.state = "observed"
            target = WatchStatus.WATCHING
        elif unknown_confirmation_auth_failure:
            candidate.state = "observed"
            target = WatchStatus.AUTH_REQUIRED
        else:
            candidate.state = "failed"
            target = (
                WatchStatus.AUTH_REQUIRED
                if result.outcome
                in {
                    ReservationOutcome.AUTH_REQUIRED,
                    ReservationOutcome.PROVIDER_BLOCKED,
                }
                else WatchStatus.FAILED
            )
        if watch.status == WatchStatus.RESERVING:
            if unknown_confirmation_auth_failure:
                transition_reason = (
                    "reservation_reconciliation_auth_required"
                    if confirmation is not None
                    and confirmation.outcome is ReservationConfirmationOutcome.AUTH_REQUIRED
                    else "reservation_reconciliation_provider_blocked"
                )
            else:
                transition_reason = (
                    "reservation_failed_monitoring_resumed"
                    if result.outcome is ReservationOutcome.FAILED
                    else f"reservation_{result.outcome.value}"
                )
            await dependencies.apply_watch_transition(
                session,
                watch,
                target,
                reason=transition_reason,
            )

        if unknown_confirmation_auth_failure:
            watch.next_check_at = None
            watch.observation_in_flight_until = None
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

        if result.outcome is ReservationOutcome.FAILED:
            await dependencies.add_outbox_event(
                session,
                aggregate_type="watch",
                aggregate_id=watch.id,
                event_type="watch.reservation_failed_monitoring_resumed",
                payload={
                    "watch_id": watch.id,
                    "candidate_id": candidate.id,
                    "outcome": result.outcome.value,
                    "reason": "reservation_failed_monitoring_resumed",
                    "monitoring_resumed": True,
                },
                dedupe_key=f"reservation-failed-monitoring-resumed:{attempt.id}",
            )

    result_policy = dependencies.result_policy(result.outcome)
    seat_detected_at = await latest_candidate_seat_detected_at(
        session,
        candidate.id,
        attempt_started_at=attempt.started_at,
    )
    await dependencies.add_outbox_event(
        session,
        aggregate_type="watch",
        aggregate_id=watch.id,
        event_type="watch.reservation_result",
        payload={
            "watch_id": watch.id,
            "candidate_id": candidate.id,
            "attempt_sequence": attempt.attempt_sequence,
            "seat_detected_at": (
                seat_detected_at.isoformat() if seat_detected_at is not None else None
            ),
            "attempt_started_at": attempt.started_at.isoformat(),
            "attempt_finished_at": (
                attempt.finished_at.isoformat() if attempt.finished_at is not None else None
            ),
            "outcome": result.outcome.value,
            "result_reason_code": attempt.result_reason_code.value,
            "confirmation_outcome": (
                attempt.confirmation_outcome.value
                if attempt.confirmation_outcome is not None
                else None
            ),
            "confirmation_diagnostic_code": (
                diagnostic.value
                if (
                    diagnostic := effective_reservation_confirmation_diagnostic_code(
                        attempt.confirmation_outcome,
                        attempt.confirmation_diagnostic_code,
                    )
                )
                is not None
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
            "payment_deadline": (
                result.payment_deadline.isoformat() if result.payment_deadline is not None else None
            ),
            "monitoring_resumed": monitoring_resumed if not successful_hold else False,
            "retryable": result_policy.retryable,
            "manual_check_required": (
                result_policy.manual_check_required and not unknown_confirmation_auth_failure
            ),
            "retry_condition": result_policy.retry_condition,
            "reserved_seats": [seat.model_dump() for seat in result.reserved_seats],
            **({"progress_stages": persisted_progress} if persisted_progress else {}),
        },
        dedupe_key=f"reservation-result:{attempt.id}",
    )
