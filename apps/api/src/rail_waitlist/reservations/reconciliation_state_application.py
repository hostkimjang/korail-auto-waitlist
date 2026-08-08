from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import ReservationOutcome, ReservationPolicy, WatchStatus
from ..watch_management.models import ReservationAttempt, SeatObservation, Watch, WatchCandidate
from .provider_confirmation.contracts import (
    ReservationConfirmationOutcome,
    ReservationConfirmationResult,
)
from .reconciliation_policy import (
    RESERVATION_RECONCILIATION_INTERVAL,
    RESERVATION_RECONCILIATION_MAX_ATTEMPTS,
    UNKNOWN_RECONCILIATION_MAX_ATTEMPTS,
    unknown_reconciliation_retry_interval,
)


class ReservationReconciliationNotEligible(Exception):
    """Raised when a terminal attempt receives a reconciliation result."""


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


class RecordReservationConfirmation(Protocol):
    def __call__(
        self,
        attempt: ReservationAttempt,
        confirmation: ReservationConfirmationResult,
        *,
        reconciled_at: datetime | None = None,
    ) -> None: ...


class UtcInstant(Protocol):
    def __call__(self, value: datetime) -> datetime: ...


@dataclass(frozen=True, slots=True)
class ReservationReconciliationStateDependencies:
    apply_watch_transition: ApplyWatchTransition
    add_outbox_event: AddOutboxEvent
    record_reservation_confirmation: RecordReservationConfirmation
    utc_instant: UtcInstant


async def apply_reservation_reconciliation(
    session: AsyncSession,
    watch: Watch,
    candidate: WatchCandidate,
    attempt: ReservationAttempt,
    confirmation: ReservationConfirmationResult,
    *,
    reconciled_at: datetime,
    dependencies: ReservationReconciliationStateDependencies,
) -> None:
    """Apply one bounded confirmation inside the caller-owned unit of work."""

    if attempt.outcome not in {
        ReservationOutcome.PAYMENT_REQUIRED,
        ReservationOutcome.UNKNOWN,
    }:
        raise ReservationReconciliationNotEligible
    if confirmation.provider != watch.provider:
        raise ValueError("reservation confirmation provider does not match watch")
    payment_deadline = watch.payment_deadline
    if payment_deadline is not None and (
        payment_deadline.tzinfo is None or payment_deadline.utcoffset() is None
    ):
        payment_deadline = payment_deadline.replace(tzinfo=UTC)
    legacy_expired_hold_cleanup_read = (
        attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
        and attempt.reconciliation_attempt_count == RESERVATION_RECONCILIATION_MAX_ATTEMPTS
        and attempt.post_deadline_reconciled_at is not None
        and attempt.confirmation_outcome
        is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
        and attempt.payment_deadline is not None
        and dependencies.utc_instant(attempt.payment_deadline)
        <= dependencies.utc_instant(attempt.post_deadline_reconciled_at)
    )
    post_deadline_final_read = (
        attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
        and watch.status is WatchStatus.PAYMENT_REQUIRED
        and payment_deadline is not None
        and payment_deadline <= reconciled_at
        and attempt.reconciliation_attempt_count >= RESERVATION_RECONCILIATION_MAX_ATTEMPTS
        and (attempt.post_deadline_reconciled_at is None or legacy_expired_hold_cleanup_read)
    )
    dependencies.record_reservation_confirmation(
        attempt,
        confirmation,
        reconciled_at=reconciled_at,
    )
    confirmed_hold_has_usable_deadline = (
        confirmation.outcome is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
        and confirmation.payment_deadline is not None
        and confirmation.payment_deadline > reconciled_at
    )
    reconciliation_attempt_limit = (
        UNKNOWN_RECONCILIATION_MAX_ATTEMPTS
        if attempt.outcome is ReservationOutcome.UNKNOWN
        else RESERVATION_RECONCILIATION_MAX_ATTEMPTS
    )
    if post_deadline_final_read:
        if confirmed_hold_has_usable_deadline:
            attempt.post_deadline_reconciled_at = None
        else:
            attempt.post_deadline_reconciled_at = reconciled_at
            if legacy_expired_hold_cleanup_read:
                attempt.reconciliation_attempt_count += 1
    else:
        attempt.reconciliation_attempt_count += 1
        if attempt.reconciliation_attempt_count > reconciliation_attempt_limit:
            raise RuntimeError("reservation reconciliation attempt limit exceeded")
    confirmed_absent_unknown = (
        attempt.outcome is ReservationOutcome.UNKNOWN
        and confirmation.outcome is ReservationConfirmationOutcome.NOT_FOUND
    )
    terminal_confirmation = (
        confirmed_hold_has_usable_deadline
        or confirmed_absent_unknown
        or confirmation.outcome
        in {
            ReservationConfirmationOutcome.AUTH_REQUIRED,
            ReservationConfirmationOutcome.PROVIDER_BLOCKED,
        }
    )
    if (
        attempt.outcome is ReservationOutcome.UNKNOWN
        and confirmation.outcome is ReservationConfirmationOutcome.INCONCLUSIVE
    ):
        retry_interval = unknown_reconciliation_retry_interval(attempt.reconciliation_attempt_count)
        if retry_interval is None:
            attempt.next_reconcile_at = None
        else:
            reconciliation_anchor = attempt.last_reconciled_at
            if reconciliation_anchor is None:
                raise RuntimeError("reconciliation must persist a reconciliation timestamp")
            attempt.next_reconcile_at = reconciliation_anchor + retry_interval
    elif (
        not terminal_confirmation
        and attempt.reconciliation_attempt_count < RESERVATION_RECONCILIATION_MAX_ATTEMPTS
    ):
        reconciliation_anchor = attempt.last_reconciled_at
        if reconciliation_anchor is None:
            raise RuntimeError("reconciliation must persist a reconciliation timestamp")
        attempt.next_reconcile_at = reconciliation_anchor + RESERVATION_RECONCILIATION_INTERVAL
    elif (
        confirmation.outcome is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
        and confirmation.payment_deadline is not None
        and confirmation.payment_deadline <= reconciled_at
        and attempt.reconciliation_attempt_count >= RESERVATION_RECONCILIATION_MAX_ATTEMPTS
        and attempt.post_deadline_reconciled_at is None
    ):
        attempt.next_reconcile_at = reconciled_at + RESERVATION_RECONCILIATION_INTERVAL
    else:
        attempt.next_reconcile_at = None
    expired_confirmed_hold = (
        confirmation.outcome is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
        and confirmation.payment_deadline is not None
        and confirmation.payment_deadline <= reconciled_at
    )
    payment_hold_ended_confirmation = (
        post_deadline_final_read
        and attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
        and watch.status is WatchStatus.PAYMENT_REQUIRED
        and (
            confirmation.outcome is ReservationConfirmationOutcome.NOT_FOUND
            or expired_confirmed_hold
        )
    )
    if payment_hold_ended_confirmation:
        if expired_confirmed_hold:
            attempt.payment_deadline = confirmation.payment_deadline
        candidate.state = (
            "expired" if watch.reservation_policy is ReservationPolicy.NOTIFY_ONLY else "observed"
        )
        candidate.suppressed_by_candidate_id = None
        suppressed_candidates = list(
            (
                await session.scalars(
                    select(WatchCandidate).where(
                        WatchCandidate.watch_id == watch.id,
                        WatchCandidate.state == "suppressed_by_priority",
                        WatchCandidate.suppressed_by_candidate_id == candidate.id,
                    )
                )
            ).all()
        )
        for suppressed in suppressed_candidates:
            suppressed.state = (
                "expired"
                if watch.reservation_policy is ReservationPolicy.NOTIFY_ONLY
                else "observed"
            )
            suppressed.suppressed_by_candidate_id = None
        watch.payment_deadline = None
        watch.official_booking_url = None
        watch.next_check_at = reconciled_at
        terminal_one_off = watch.reservation_policy is ReservationPolicy.NOTIFY_ONLY
        await dependencies.apply_watch_transition(
            session,
            watch,
            WatchStatus.EXPIRED if terminal_one_off else WatchStatus.WATCHING,
            reason=(
                "confirmed_payment_hold_no_longer_actionable_one_off_expired"
                if terminal_one_off
                else "confirmed_payment_hold_no_longer_actionable_monitoring_resumed"
            ),
        )
        await dependencies.add_outbox_event(
            session,
            aggregate_type="watch",
            aggregate_id=watch.id,
            event_type=(
                "watch.payment_hold_ended_one_off_expired"
                if terminal_one_off
                else "watch.payment_hold_ended_monitoring_resumed"
            ),
            payload={
                "watch_id": watch.id,
                "candidate_id": candidate.id,
                "terminal": True,
                "status": (
                    WatchStatus.EXPIRED.value if terminal_one_off else WatchStatus.WATCHING.value
                ),
                "from": WatchStatus.PAYMENT_REQUIRED.value,
                "to": (
                    WatchStatus.EXPIRED.value if terminal_one_off else WatchStatus.WATCHING.value
                ),
                "reason": (
                    "confirmed_payment_deadline_elapsed"
                    if expired_confirmed_hold
                    else "confirmed_payment_hold_no_longer_present"
                ),
                "message": (
                    "임시 예약이 결제기한 안에 결제되지 않아 취소되었습니다."
                    if expired_confirmed_hold
                    else "공식 예약 목록에서 미결제 보류가 종료된 것을 확인했습니다."
                ),
                "payment_deadline": (
                    confirmation.payment_deadline.isoformat()
                    if expired_confirmed_hold and confirmation.payment_deadline is not None
                    else None
                ),
                "automatic_reservation_retry": not terminal_one_off,
                "retry_condition": "new_availability_episode" if not terminal_one_off else None,
            },
            dedupe_key=f"payment-hold-ended:{attempt.id}",
        )
        return
    if confirmation.outcome is not ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED:
        return
    if confirmation.official_handoff_url is None:
        raise RuntimeError("confirmed reservation requires an official handoff URL")
    if confirmation.payment_deadline is not None and confirmation.payment_deadline <= reconciled_at:
        return

    attempt.outcome = ReservationOutcome.PAYMENT_REQUIRED
    attempt.payment_deadline = confirmation.payment_deadline
    attempt.official_handoff_url = confirmation.official_handoff_url
    if (
        watch.status
        in {
            WatchStatus.WATCHING,
            WatchStatus.OFFICIAL_WAITLIST,
            WatchStatus.SEAT_FOUND,
            WatchStatus.RESERVING,
            WatchStatus.PAYMENT_REQUIRED,
        }
        and candidate.state != "expired"
    ):
        candidate.state = "payment_required"
        watch.payment_deadline = confirmation.payment_deadline
        watch.official_booking_url = confirmation.official_handoff_url
        if watch.status != WatchStatus.PAYMENT_REQUIRED:
            await dependencies.apply_watch_transition(
                session,
                watch,
                WatchStatus.PAYMENT_REQUIRED,
                reason="reservation_reconciliation_confirmed_payment_required",
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
        event_type="watch.reservation_reconciled",
        payload={
            "watch_id": watch.id,
            "candidate_id": candidate.id,
            "attempt_sequence": attempt.attempt_sequence,
            "confirmation_outcome": confirmation.outcome.value,
            "payment_deadline": (
                confirmation.payment_deadline.isoformat()
                if confirmation.payment_deadline is not None
                else None
            ),
            "retryable": False,
        },
        dedupe_key=f"reservation-reconciled:{attempt.id}:{confirmation.observed_at.isoformat()}",
    )
