from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import ReservationOutcome, SeatObservationStatus, WatchStatus
from ..models import ReservationAttempt, SeatObservation, Watch, WatchCandidate
from .attempt_policy import (
    CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX,
    RESERVATION_RETRY_EDGE_OBSERVATIONS,
)


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


class ReservationAttemptPredicate(Protocol):
    def __call__(self, attempt: ReservationAttempt) -> bool: ...


@dataclass(frozen=True, slots=True)
class ReservationAttemptClaimDependencies:
    apply_watch_transition: ApplyWatchTransition
    add_outbox_event: AddOutboxEvent
    is_payment_hold_ended: ReservationAttemptPredicate
    is_confirmed_absent_retry_source: ReservationAttemptPredicate
    actionable_seat_statuses: frozenset[SeatObservationStatus]


async def begin_reservation_attempt(
    session: AsyncSession,
    watch: Watch,
    candidate: WatchCandidate,
    idempotency_key: str,
    *,
    episode_key: str | None = None,
    retry_authorized: bool = False,
    credential_version: int | None = None,
    dependencies: ReservationAttemptClaimDependencies,
) -> tuple[ReservationAttempt, bool]:
    """Claim one durable reservation attempt inside the caller-owned unit of work."""
    normalized_episode_key = episode_key or f"manual:{idempotency_key}"
    existing = await session.scalar(
        select(ReservationAttempt).where(
            ReservationAttempt.candidate_id == candidate.id,
            ReservationAttempt.episode_key == normalized_episode_key,
        )
    )
    if existing is not None:
        return existing, False

    latest_attempt = await session.scalar(
        select(ReservationAttempt)
        .where(ReservationAttempt.candidate_id == candidate.id)
        .order_by(ReservationAttempt.attempt_sequence.desc())
        .limit(1)
    )
    confirmed_absent_retry_authorized = False
    if latest_attempt is not None and normalized_episode_key.startswith(
        CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX
    ):
        expected_episode_key = f"{CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX}{latest_attempt.id}"
        actionable_after_confirmation = None
        if (
            retry_authorized
            and normalized_episode_key == expected_episode_key
            and dependencies.is_confirmed_absent_retry_source(latest_attempt)
        ):
            actionable_after_confirmation = await session.scalar(
                select(SeatObservation.id)
                .where(
                    SeatObservation.candidate_id == candidate.id,
                    SeatObservation.observed_at > latest_attempt.confirmation_observed_at,
                    SeatObservation.status.in_(dependencies.actionable_seat_statuses),
                )
                .order_by(SeatObservation.observed_at, SeatObservation.id)
                .limit(1)
            )
        confirmed_absent_retry_authorized = actionable_after_confirmation is not None
    payment_hold_ended = latest_attempt is not None and dependencies.is_payment_hold_ended(
        latest_attempt
    )
    payment_hold_retry_edge_observed = False
    if payment_hold_ended and latest_attempt is not None:
        retry_edge = await session.scalar(
            select(SeatObservation.id)
            .where(
                SeatObservation.candidate_id == candidate.id,
                SeatObservation.observed_at > latest_attempt.post_deadline_reconciled_at,
                SeatObservation.status.in_(RESERVATION_RETRY_EDGE_OBSERVATIONS),
            )
            .order_by(SeatObservation.observed_at, SeatObservation.id)
            .limit(1)
        )
        payment_hold_retry_edge_observed = retry_edge is not None
    if latest_attempt is not None and (
        not retry_authorized
        or latest_attempt.outcome
        not in {
            ReservationOutcome.NOT_AVAILABLE,
            ReservationOutcome.AUTH_REQUIRED,
            ReservationOutcome.PROVIDER_BLOCKED,
        }
        and not payment_hold_retry_edge_observed
        and not confirmed_absent_retry_authorized
    ):
        return latest_attempt, False

    latest_sequence = latest_attempt.attempt_sequence if latest_attempt is not None else 0
    attempt = ReservationAttempt(
        candidate_id=candidate.id,
        attempt_sequence=(latest_sequence or 0) + 1,
        episode_key=normalized_episode_key,
        idempotency_key=idempotency_key,
        outcome=ReservationOutcome.PENDING,
        credential_version=credential_version,
    )
    try:
        async with session.begin_nested():
            session.add(attempt)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(
            select(ReservationAttempt).where(
                ReservationAttempt.candidate_id == candidate.id,
                ReservationAttempt.episode_key == normalized_episode_key,
            )
        )
        if existing is None:
            raise
        return existing, False

    candidate.state = "reservation_attempted"
    watch.reservation_attempted = True
    if watch.status == WatchStatus.SEAT_FOUND:
        await dependencies.apply_watch_transition(
            session,
            watch,
            WatchStatus.RESERVING,
            reason="reservation_attempt_claimed",
        )
    await dependencies.add_outbox_event(
        session,
        aggregate_type="watch",
        aggregate_id=watch.id,
        event_type="watch.reservation_attempted",
        payload={
            "watch_id": watch.id,
            "candidate_id": candidate.id,
            "attempt_sequence": attempt.attempt_sequence,
            "episode_key": attempt.episode_key,
            "outcome": ReservationOutcome.PENDING.value,
        },
        dedupe_key=f"reservation-attempt:{attempt.id}",
    )
    return attempt, True
