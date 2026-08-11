from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import ReservationOutcome, SeatObservationStatus, WatchStatus
from ..watch_management.models import ReservationAttempt, SeatObservation, Watch, WatchCandidate
from .attempt_policy import (
    CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX,
    RESERVATION_RETRY_EDGE_OBSERVATIONS,
    official_seat_observation_source,
    parse_manual_payment_hold_rearm_episode_key,
    parse_payment_hold_retry_episode_key,
)
from .attempt_timing_application import latest_candidate_seat_detected_at
from .payment_hold_retry_application import (
    active_watch_payment_hold_fence,
    conclusive_unavailable_after_hold,
    watch_attempt_by_id,
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
    payment_hold_episode = parse_payment_hold_retry_episode_key(normalized_episode_key)
    payment_hold_fence = (
        await active_watch_payment_hold_fence(
            session,
            watch.id,
            is_payment_hold_ended=dependencies.is_payment_hold_ended,
        )
        if retry_authorized
        else None
    )
    payment_hold_retry_authorized = False
    manual_payment_hold_retry_authorized = False
    referenced_payment_hold: ReservationAttempt | None = None
    if payment_hold_episode is not None:
        hold_attempt_id, unavailable_observation_id = payment_hold_episode
        referenced_payment_hold = await watch_attempt_by_id(session, watch.id, hold_attempt_id)
        if (
            retry_authorized
            and payment_hold_fence is not None
            and payment_hold_fence.attempt.id == hold_attempt_id
        ):
            retry_edge = await conclusive_unavailable_after_hold(
                session,
                payment_hold_fence,
                candidate.id,
                observation_id=unavailable_observation_id,
            )
            if retry_edge is not None:
                actionable_after_edge = await session.scalar(
                    select(SeatObservation.id)
                    .where(
                        SeatObservation.candidate_id == candidate.id,
                        SeatObservation.observed_at > retry_edge.observed_at,
                        SeatObservation.status.in_(dependencies.actionable_seat_statuses),
                    )
                    .order_by(SeatObservation.observed_at, SeatObservation.id)
                    .limit(1)
                )
                payment_hold_retry_authorized = actionable_after_edge is not None
    manual_payment_hold_episode = parse_manual_payment_hold_rearm_episode_key(
        normalized_episode_key
    )
    if manual_payment_hold_episode is not None:
        hold_attempt_id, candidate_id, observation_id = manual_payment_hold_episode
        manual_rearm_authorized_at = candidate.manual_rearm_authorized_at
        if (
            retry_authorized
            and payment_hold_fence is not None
            and payment_hold_fence.attempt.id == hold_attempt_id
            and candidate_id == candidate.id
            and candidate.manual_rearm_source_attempt_id == hold_attempt_id
            and manual_rearm_authorized_at is not None
        ):
            official_source = official_seat_observation_source(watch.provider)
            actionable_after_authorization = await session.scalar(
                select(SeatObservation.id)
                .where(
                    SeatObservation.candidate_id == candidate.id,
                    SeatObservation.id == observation_id,
                    SeatObservation.observed_at > manual_rearm_authorized_at,
                    SeatObservation.source == official_source,
                    SeatObservation.status.in_(dependencies.actionable_seat_statuses),
                )
                .order_by(SeatObservation.observed_at, SeatObservation.id)
                .limit(1)
            )
            manual_payment_hold_retry_authorized = actionable_after_authorization is not None
    if payment_hold_fence is not None and not (
        payment_hold_retry_authorized or manual_payment_hold_retry_authorized
    ):
        # A watch-scoped hold may belong to another candidate. Returning that durable
        # blocker with created=False keeps the execution caller side-effect free even
        # when this candidate has no local attempt to replay.
        return latest_attempt or payment_hold_fence.attempt, False
    if payment_hold_episode is not None and not payment_hold_retry_authorized:
        blocking_attempt = latest_attempt or referenced_payment_hold
        if blocking_attempt is not None:
            return blocking_attempt, False
        raise ValueError("payment-hold retry episode does not reference this watch")
    if manual_payment_hold_episode is not None and not manual_payment_hold_retry_authorized:
        blocking_attempt = latest_attempt or (
            payment_hold_fence.attempt if payment_hold_fence else None
        )
        if blocking_attempt is not None:
            return blocking_attempt, False
        raise ValueError("manual payment-hold retry episode does not reference this watch")
    not_available_retry_authorized = False
    if (
        latest_attempt is not None
        and latest_attempt.outcome is ReservationOutcome.NOT_AVAILABLE
        and normalized_episode_key.startswith("availability-after:")
    ):
        retry_edge_id = normalized_episode_key.removeprefix("availability-after:")
        retry_edge = await session.scalar(
            select(SeatObservation.id).where(
                SeatObservation.id == retry_edge_id,
                SeatObservation.candidate_id == candidate.id,
                SeatObservation.observed_at
                > (latest_attempt.finished_at or latest_attempt.started_at),
                SeatObservation.status.in_(RESERVATION_RETRY_EDGE_OBSERVATIONS),
            )
        )
        not_available_retry_authorized = retry_edge is not None
    confirmed_absent_retry_authorized = False
    if (
        latest_attempt is not None
        and latest_attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
        and normalized_episode_key.startswith(CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX)
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
    if latest_attempt is not None:
        provider_auth_retry_authorized = latest_attempt.outcome in {
            ReservationOutcome.AUTH_REQUIRED,
            ReservationOutcome.PROVIDER_BLOCKED,
        }
        retry_permitted = retry_authorized and any(
            (
                not_available_retry_authorized,
                provider_auth_retry_authorized,
                payment_hold_retry_authorized,
                manual_payment_hold_retry_authorized,
                confirmed_absent_retry_authorized,
            )
        )
        if not retry_permitted:
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

    seat_detected_at = await latest_candidate_seat_detected_at(
        session,
        candidate.id,
        attempt_started_at=attempt.started_at,
    )
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
            "seat_detected_at": (
                seat_detected_at.isoformat() if seat_detected_at is not None else None
            ),
            "attempt_started_at": attempt.started_at.isoformat(),
            "episode_key": attempt.episode_key,
            "outcome": ReservationOutcome.PENDING.value,
        },
        dedupe_key=f"reservation-attempt:{attempt.id}",
    )
    return attempt, True
