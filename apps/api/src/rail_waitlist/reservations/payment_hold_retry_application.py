from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..watch_management.models import ReservationAttempt, SeatObservation, WatchCandidate
from .attempt_policy import RESERVATION_RETRY_EDGE_OBSERVATIONS


class PaymentHoldEndedPredicate(Protocol):
    def __call__(self, attempt: ReservationAttempt) -> bool: ...


@dataclass(frozen=True, slots=True)
class WatchPaymentHoldFence:
    attempt: ReservationAttempt
    ended_at: datetime


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def active_watch_payment_hold_fence(
    session: AsyncSession,
    watch_id: str,
    *,
    is_payment_hold_ended: PaymentHoldEndedPredicate,
) -> WatchPaymentHoldFence | None:
    """Return the latest ended hold until any watch attempt starts after its reconciliation."""
    hold_candidates = await session.scalars(
        select(ReservationAttempt)
        .join(WatchCandidate, WatchCandidate.id == ReservationAttempt.candidate_id)
        .where(
            WatchCandidate.watch_id == watch_id,
            ReservationAttempt.post_deadline_reconciled_at.is_not(None),
        )
        .order_by(
            ReservationAttempt.post_deadline_reconciled_at.desc(),
            ReservationAttempt.started_at.desc(),
            ReservationAttempt.id.desc(),
        )
    )
    hold_attempt = next(
        (attempt for attempt in hold_candidates if is_payment_hold_ended(attempt)),
        None,
    )
    if hold_attempt is None or hold_attempt.post_deadline_reconciled_at is None:
        return None
    fence = WatchPaymentHoldFence(
        attempt=hold_attempt,
        ended_at=_as_utc(hold_attempt.post_deadline_reconciled_at),
    )
    post_hold_attempt_id = await session.scalar(
        select(ReservationAttempt.id)
        .join(WatchCandidate, WatchCandidate.id == ReservationAttempt.candidate_id)
        .where(
            WatchCandidate.watch_id == watch_id,
            ReservationAttempt.id != fence.attempt.id,
            ReservationAttempt.started_at >= fence.ended_at,
        )
        .order_by(ReservationAttempt.started_at, ReservationAttempt.id)
        .limit(1)
    )
    return None if post_hold_attempt_id is not None else fence


async def conclusive_unavailable_after_hold(
    session: AsyncSession,
    fence: WatchPaymentHoldFence,
    candidate_id: str,
    *,
    observation_id: str | None = None,
    before: datetime | None = None,
) -> SeatObservation | None:
    conditions = [
        SeatObservation.candidate_id == candidate_id,
        SeatObservation.observed_at > fence.ended_at,
        SeatObservation.status.in_(RESERVATION_RETRY_EDGE_OBSERVATIONS),
    ]
    if observation_id is not None:
        conditions.append(SeatObservation.id == observation_id)
    if before is not None:
        conditions.append(SeatObservation.observed_at < before)
    observation: SeatObservation | None = await session.scalar(
        select(SeatObservation)
        .where(*conditions)
        .order_by(SeatObservation.observed_at, SeatObservation.id)
        .limit(1)
    )
    return observation


async def watch_attempt_by_id(
    session: AsyncSession,
    watch_id: str,
    attempt_id: str,
) -> ReservationAttempt | None:
    attempt: ReservationAttempt | None = await session.scalar(
        select(ReservationAttempt)
        .join(WatchCandidate, WatchCandidate.id == ReservationAttempt.candidate_id)
        .where(
            WatchCandidate.watch_id == watch_id,
            ReservationAttempt.id == attempt_id,
        )
        .limit(1)
    )
    return attempt
