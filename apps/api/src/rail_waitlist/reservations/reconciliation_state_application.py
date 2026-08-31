from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import (
    Provider,
    ReservationOutcome,
    ReservationPolicy,
    ReservationResultReasonCode,
    WatchStatus,
    reservation_result_reason_code_for_outcome,
)
from ..provider_account_management.schemas import RailProviderAuthStatus
from ..watch_management.models import ReservationAttempt, SeatObservation, Watch, WatchCandidate
from .attempt_policy import automatic_reservation_retry_fence_reason
from .domain import reservation_attempt_manual_check_required
from .exact_paid_application import apply_exact_paid_resolution
from .provider_confirmation.contracts import (
    ReservationConfirmationOutcome,
    ReservationConfirmationResult,
    ReservationConfirmationSeat,
    effective_reservation_confirmation_diagnostic_code,
)
from .reconciliation_policy import (
    PAYMENT_HOLD_RECONCILIATION_MAX_ATTEMPTS,
    RESERVATION_RECONCILIATION_INTERVAL,
    RESERVATION_RECONCILIATION_MAX_ATTEMPTS,
    UNKNOWN_RECONCILIATION_MAX_ATTEMPTS,
    ReservationReconciliationResolution,
    payment_hold_reconciliation_retry_interval,
    unknown_reconciliation_retry_interval,
)


class ReservationReconciliationNotEligible(Exception):
    """Raised when a terminal attempt receives a reconciliation result."""


def _validated_correlation_seat_payload(
    value: object,
    *,
    passenger_count: int,
) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) != passenger_count:
        return []
    seats: list[ReservationConfirmationSeat] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"car_number", "seat_number"}:
            return []
        car_number = item.get("car_number")
        seat_number = item.get("seat_number")
        if not isinstance(car_number, str) or not isinstance(seat_number, str):
            return []
        try:
            seats.append(
                ReservationConfirmationSeat(
                    car_number=car_number,
                    seat_number=seat_number,
                )
            )
        except ValueError:
            return []
    keys = {(seat.car_number, seat.seat_number) for seat in seats}
    if len(keys) != len(seats):
        return []
    return [{"car_number": seat.car_number, "seat_number": seat.seat_number} for seat in seats]


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


class UpdateProviderAuthStatusInTransaction(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        provider: Provider,
        status: RailProviderAuthStatus,
        *,
        expected_credential_version: int,
    ) -> bool: ...


class UtcInstant(Protocol):
    def __call__(self, value: datetime) -> datetime: ...


@dataclass(frozen=True, slots=True)
class ReservationReconciliationStateDependencies:
    apply_watch_transition: ApplyWatchTransition
    add_outbox_event: AddOutboxEvent
    record_reservation_confirmation: RecordReservationConfirmation
    update_provider_auth_status: UpdateProviderAuthStatusInTransaction
    utc_instant: UtcInstant


async def _add_reconciliation_outbox_event(
    session: AsyncSession,
    watch: Watch,
    candidate: WatchCandidate,
    attempt: ReservationAttempt,
    confirmation: ReservationConfirmationResult,
    *,
    reconciled_at: datetime,
    dependencies: ReservationReconciliationStateDependencies,
) -> None:
    fresh_confirmed_payment_deadline = (
        confirmation.payment_deadline
        if confirmation.outcome is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
        else None
    )
    effective_payment_deadline = (
        fresh_confirmed_payment_deadline or watch.payment_deadline or attempt.payment_deadline
    )
    payment_actionable = (
        watch.status is WatchStatus.PAYMENT_REQUIRED
        and candidate.state == "payment_required"
        and attempt.outcome
        in {
            ReservationOutcome.PAYMENT_REQUIRED,
            ReservationOutcome.RESERVED,
        }
        and attempt.post_deadline_reconciled_at is None
        and (
            effective_payment_deadline is None
            or dependencies.utc_instant(effective_payment_deadline)
            > dependencies.utc_instant(reconciled_at)
        )
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
            "attempt_started_at": dependencies.utc_instant(attempt.started_at).isoformat(),
            "attempt_finished_at": (
                dependencies.utc_instant(attempt.finished_at).isoformat()
                if attempt.finished_at is not None
                else None
            ),
            "outcome": attempt.outcome.value,
            "result_reason_code": attempt.result_reason_code.value,
            "payment_actionable": payment_actionable,
            "confirmation_outcome": confirmation.outcome.value,
            "confirmation_diagnostic_code": (
                diagnostic.value
                if (
                    diagnostic := effective_reservation_confirmation_diagnostic_code(
                        confirmation.outcome,
                        confirmation.diagnostic_code,
                    )
                )
                is not None
                else None
            ),
            "confirmation_observed_at": confirmation.observed_at.isoformat(),
            "reconciliation_attempt_count": attempt.reconciliation_attempt_count,
            "reconciliation_resolution": (
                attempt.reconciliation_resolution.value
                if attempt.reconciliation_resolution is not None
                else None
            ),
            "automatic_reservation_retry_fence_reason": (
                retry_fence_reason.value
                if (retry_fence_reason := automatic_reservation_retry_fence_reason(attempt))
                is not None
                else None
            ),
            "next_reconcile_at": (
                dependencies.utc_instant(attempt.next_reconcile_at).isoformat()
                if attempt.next_reconcile_at is not None
                else None
            ),
            "payment_deadline": (
                dependencies.utc_instant(effective_payment_deadline).isoformat()
                if effective_payment_deadline is not None
                else None
            ),
            "progress_stages": attempt.progress_stages or [],
            "reserved_seats": attempt.reserved_seats or [],
            "retryable": False,
            "manual_check_required": reservation_attempt_manual_check_required(
                attempt.outcome,
                confirmation_outcome=attempt.confirmation_outcome,
                reconciliation_resolution=attempt.reconciliation_resolution,
            ),
        },
        dedupe_key=f"reservation-reconciled:{attempt.id}:{confirmation.observed_at.isoformat()}",
    )


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
    reconciliation_auth_status: RailProviderAuthStatus | None
    if confirmation.outcome is ReservationConfirmationOutcome.AUTH_REQUIRED:
        reconciliation_auth_status = "auth_required"
    elif confirmation.outcome is ReservationConfirmationOutcome.PROVIDER_BLOCKED:
        reconciliation_auth_status = "provider_blocked"
    else:
        reconciliation_auth_status = None
    reconciliation_auth_failure = (
        attempt.outcome
        in {
            ReservationOutcome.UNKNOWN,
            ReservationOutcome.PAYMENT_REQUIRED,
        }
        and reconciliation_auth_status is not None
    )
    if reconciliation_auth_failure:
        assert reconciliation_auth_status is not None
        if attempt.credential_version is None:
            return
        account_updated = await dependencies.update_provider_auth_status(
            session,
            watch.provider,
            reconciliation_auth_status,
            expected_credential_version=attempt.credential_version,
        )
        if not account_updated:
            return
        if attempt.result_reason_code is None:
            attempt.result_reason_code = reservation_result_reason_code_for_outcome(attempt.outcome)
        dependencies.record_reservation_confirmation(
            attempt,
            confirmation,
            reconciled_at=reconciled_at,
        )
        attempt.reconciliation_resolution = None
        attempt.next_reconcile_at = None
        is_unknown_attempt = attempt.outcome is ReservationOutcome.UNKNOWN
        is_latest_watch_attempt = False
        if is_unknown_attempt:
            latest_watch_attempt_id = await session.scalar(
                select(ReservationAttempt.id)
                .join(WatchCandidate, WatchCandidate.id == ReservationAttempt.candidate_id)
                .where(WatchCandidate.watch_id == watch.id)
                .order_by(
                    ReservationAttempt.started_at.desc(),
                    ReservationAttempt.attempt_sequence.desc(),
                    ReservationAttempt.id.desc(),
                )
                .limit(1)
                .with_for_update(of=ReservationAttempt)
            )
            is_latest_watch_attempt = latest_watch_attempt_id == attempt.id
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
        if is_latest_watch_attempt and watch.status in {
            WatchStatus.SCHEDULED,
            WatchStatus.WATCHING,
        }:
            if watch.status is WatchStatus.SCHEDULED:
                await dependencies.apply_watch_transition(
                    session,
                    watch,
                    WatchStatus.WATCHING,
                    reason="worker_claimed_reconciliation",
                )
            watch.next_check_at = None
            watch.observation_in_flight_until = None
            await dependencies.apply_watch_transition(
                session,
                watch,
                WatchStatus.AUTH_REQUIRED,
                reason=(
                    "reservation_reconciliation_auth_required"
                    if reconciliation_auth_status == "auth_required"
                    else "reservation_reconciliation_provider_blocked"
                ),
            )
        await _add_reconciliation_outbox_event(
            session,
            watch,
            candidate,
            attempt,
            confirmation,
            reconciled_at=reconciled_at,
            dependencies=dependencies,
        )
        return
    if attempt.outcome is ReservationOutcome.UNKNOWN and watch.status is WatchStatus.SCHEDULED:
        await dependencies.apply_watch_transition(
            session,
            watch,
            WatchStatus.WATCHING,
            reason="worker_claimed_reconciliation",
        )
    if attempt.result_reason_code is None:
        attempt.result_reason_code = reservation_result_reason_code_for_outcome(attempt.outcome)
    payment_deadline = watch.payment_deadline
    if payment_deadline is not None and (
        payment_deadline.tzinfo is None or payment_deadline.utcoffset() is None
    ):
        payment_deadline = payment_deadline.replace(tzinfo=UTC)
    known_active_payment_hold = (
        attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
        and watch.status is WatchStatus.PAYMENT_REQUIRED
        and (payment_deadline is None or payment_deadline > reconciled_at)
    )
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
    previous_confirmation_outcome = attempt.confirmation_outcome
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
    confirmed_hold_without_deadline = (
        confirmation.outcome is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
        and confirmation.payment_deadline is None
        and payment_deadline is None
    )
    reconciliation_attempt_limit = (
        UNKNOWN_RECONCILIATION_MAX_ATTEMPTS
        if attempt.outcome is ReservationOutcome.UNKNOWN
        else PAYMENT_HOLD_RECONCILIATION_MAX_ATTEMPTS
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
        and previous_confirmation_outcome is ReservationConfirmationOutcome.NOT_FOUND
    )
    exhausted_unresolved_unknown = (
        attempt.outcome is ReservationOutcome.UNKNOWN
        and attempt.reconciliation_attempt_count >= UNKNOWN_RECONCILIATION_MAX_ATTEMPTS
        and (
            confirmation.outcome is ReservationConfirmationOutcome.INCONCLUSIVE
            or (
                confirmation.outcome is ReservationConfirmationOutcome.NOT_FOUND
                and not confirmed_absent_unknown
            )
        )
    )
    if confirmed_absent_unknown:
        attempt.reconciliation_resolution = ReservationReconciliationResolution.CONFIRMED_ABSENT
    elif exhausted_unresolved_unknown:
        attempt.reconciliation_resolution = ReservationReconciliationResolution.EXHAUSTED_UNRESOLVED
    confirmed_paid = confirmation.outcome is ReservationConfirmationOutcome.CONFIRMED_PAID
    if confirmed_paid:
        attempt.reconciliation_resolution = None
    terminal_confirmation = (
        confirmed_hold_has_usable_deadline
        or confirmed_hold_without_deadline
        or confirmed_absent_unknown
        or confirmed_paid
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
        attempt.outcome is ReservationOutcome.UNKNOWN
        and confirmation.outcome is ReservationConfirmationOutcome.NOT_FOUND
        and not confirmed_absent_unknown
    ):
        if exhausted_unresolved_unknown:
            attempt.next_reconcile_at = None
        else:
            reconciliation_anchor = attempt.last_reconciled_at
            if reconciliation_anchor is None:
                raise RuntimeError("reconciliation must persist a reconciliation timestamp")
            attempt.next_reconcile_at = reconciliation_anchor + RESERVATION_RECONCILIATION_INTERVAL
    elif confirmed_hold_has_usable_deadline or confirmed_hold_without_deadline or (
        known_active_payment_hold
        and confirmation.outcome
        in {
            ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
            ReservationConfirmationOutcome.INCONCLUSIVE,
            ReservationConfirmationOutcome.NOT_FOUND,
        }
    ):
        retry_interval = payment_hold_reconciliation_retry_interval(
            attempt.reconciliation_attempt_count
        )
        if retry_interval is None:
            attempt.next_reconcile_at = None
        else:
            reconciliation_anchor = attempt.last_reconciled_at
            if reconciliation_anchor is None:
                raise RuntimeError("reconciliation must persist a reconciliation timestamp")
            effective_payment_deadline = (
                confirmation.payment_deadline
                if confirmed_hold_has_usable_deadline
                else payment_deadline
            )
            next_reconcile_at = reconciliation_anchor + retry_interval
            attempt.next_reconcile_at = (
                min(next_reconcile_at, effective_payment_deadline)
                if effective_payment_deadline is not None
                else next_reconcile_at
            )
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
    if confirmed_paid:
        await apply_exact_paid_resolution(
            session,
            watch,
            candidate,
            attempt,
            apply_watch_transition=dependencies.apply_watch_transition,
            add_outbox_event=dependencies.add_outbox_event,
        )
        await _add_reconciliation_outbox_event(
            session,
            watch,
            candidate,
            attempt,
            confirmation,
            reconciled_at=reconciled_at,
            dependencies=dependencies,
        )
        return
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
                    "공식 확인에서 결제 가능 기한이 지난 임시 예약을 확인했습니다."
                    if expired_confirmed_hold
                    else "공식 예약 목록에서 대상 임시 예약을 더 이상 찾지 못했습니다."
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
        await _add_reconciliation_outbox_event(
            session,
            watch,
            candidate,
            attempt,
            confirmation,
            reconciled_at=reconciled_at,
            dependencies=dependencies,
        )
        return
    if confirmation.outcome is not ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED:
        await _add_reconciliation_outbox_event(
            session,
            watch,
            candidate,
            attempt,
            confirmation,
            reconciled_at=reconciled_at,
            dependencies=dependencies,
        )
        return
    if confirmation.official_handoff_url is None:
        raise RuntimeError("confirmed reservation requires an official handoff URL")
    if confirmation.payment_deadline is not None and confirmation.payment_deadline <= reconciled_at:
        await _add_reconciliation_outbox_event(
            session,
            watch,
            candidate,
            attempt,
            confirmation,
            reconciled_at=reconciled_at,
            dependencies=dependencies,
        )
        return

    correlation_seats = _validated_correlation_seat_payload(
        attempt.confirmation_correlation_seats,
        passenger_count=watch.passenger_count,
    )
    if attempt.outcome is ReservationOutcome.UNKNOWN and correlation_seats:
        attempt.reserved_seats = correlation_seats
    attempt.confirmation_correlation_seats = []
    attempt.outcome = ReservationOutcome.PAYMENT_REQUIRED
    attempt.result_reason_code = ReservationResultReasonCode.PAYMENT_HOLD_CREATED
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

    await _add_reconciliation_outbox_event(
        session,
        watch,
        candidate,
        attempt,
        confirmation,
        reconciled_at=reconciled_at,
        dependencies=dependencies,
    )
