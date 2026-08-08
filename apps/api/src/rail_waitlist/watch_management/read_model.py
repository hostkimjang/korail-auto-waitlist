from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import ReservationPolicy
from ..reservations.domain import reservation_attempt_result_policy
from ..reservations.payment_hold_application import payment_hold_end_reason
from .models import ReservationAttempt, SeatObservation, Watch, WatchCandidate
from .schemas import WatchRead


def reservation_attempt_projection(
    reservation_policy: ReservationPolicy,
    attempt: ReservationAttempt,
) -> dict[str, object]:
    """Project an attempt without broadening the persisted retry policy."""
    result_policy = reservation_attempt_result_policy(attempt.outcome)
    hold_end_reason = payment_hold_end_reason(attempt)
    payment_hold_ended = hold_end_reason is not None
    automatic_hold_retry = (
        payment_hold_ended and reservation_policy is ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT
    )
    return {
        "outcome": attempt.outcome,
        "confirmation_outcome": attempt.confirmation_outcome,
        "started_at": attempt.started_at,
        "finished_at": attempt.finished_at,
        "post_deadline_reconciled_at": attempt.post_deadline_reconciled_at,
        "payment_hold_end_reason": hold_end_reason,
        "retryable": automatic_hold_retry or (not payment_hold_ended and result_policy.retryable),
        "manual_check_required": (
            False if payment_hold_ended else result_policy.manual_check_required
        ),
        "retry_condition": (
            "new_availability_episode"
            if automatic_hold_retry
            else None
            if payment_hold_ended
            else result_policy.retry_condition
        ),
    }


async def _latest_observations_by_watch(
    session: AsyncSession, watch_ids: list[str]
) -> tuple[dict[str, SeatObservation], dict[str, datetime]]:
    if not watch_ids:
        return {}, {}
    ranked_observations = (
        select(
            SeatObservation.id.label("observation_id"),
            func.row_number()
            .over(
                partition_by=SeatObservation.candidate_id,
                order_by=(SeatObservation.observed_at.desc(), SeatObservation.id.desc()),
            )
            .label("observation_rank"),
        )
        .join(WatchCandidate, WatchCandidate.id == SeatObservation.candidate_id)
        .where(WatchCandidate.watch_id.in_(watch_ids))
        .subquery()
    )
    rows = (
        await session.execute(
            select(WatchCandidate.watch_id, SeatObservation)
            .join(SeatObservation, SeatObservation.candidate_id == WatchCandidate.id)
            .join(
                ranked_observations,
                ranked_observations.c.observation_id == SeatObservation.id,
            )
            .where(ranked_observations.c.observation_rank == 1)
        )
    ).all()
    latest_by_candidate: dict[str, SeatObservation] = {}
    latest_by_watch: dict[str, datetime] = {}
    for watch_id, observation in rows:
        latest_by_candidate[observation.candidate_id] = observation
        current_latest = latest_by_watch.get(watch_id)
        if current_latest is None or observation.observed_at > current_latest:
            latest_by_watch[watch_id] = observation.observed_at
    return latest_by_candidate, latest_by_watch


async def _latest_reservation_attempts_by_watch(
    session: AsyncSession, watch_ids: list[str]
) -> dict[str, ReservationAttempt]:
    if not watch_ids:
        return {}
    ranked_attempts = (
        select(
            ReservationAttempt.id.label("attempt_id"),
            func.row_number()
            .over(
                partition_by=ReservationAttempt.candidate_id,
                order_by=(
                    ReservationAttempt.attempt_sequence.desc(),
                    ReservationAttempt.started_at.desc(),
                    ReservationAttempt.id.desc(),
                ),
            )
            .label("attempt_rank"),
        )
        .join(WatchCandidate, WatchCandidate.id == ReservationAttempt.candidate_id)
        .where(WatchCandidate.watch_id.in_(watch_ids))
        .subquery()
    )
    attempts = (
        await session.scalars(
            select(ReservationAttempt)
            .join(ranked_attempts, ranked_attempts.c.attempt_id == ReservationAttempt.id)
            .where(ranked_attempts.c.attempt_rank == 1)
        )
    ).all()
    return {attempt.candidate_id: attempt for attempt in attempts}


async def watch_read(
    session: AsyncSession,
    watch: Watch,
    *,
    last_checked_at: datetime | None = None,
    latest_observations: dict[str, SeatObservation] | None = None,
    latest_reservation_attempts: dict[str, ReservationAttempt] | None = None,
) -> WatchRead:
    if latest_observations is None:
        latest_observations, latest_by_watch = await _latest_observations_by_watch(
            session, [watch.id]
        )
        last_checked_at = latest_by_watch.get(watch.id)
    if latest_reservation_attempts is None:
        latest_reservation_attempts = await _latest_reservation_attempts_by_watch(
            session, [watch.id]
        )
    payload = WatchRead.model_validate(watch).model_dump()
    for candidate in payload["candidates"]:
        latest = latest_observations.get(candidate["id"])
        if latest is not None:
            candidate["latest_observation"] = {
                "status": latest.status,
                "source": latest.source,
                "observed_at": latest.observed_at,
                "fresh_until": latest.fresh_until,
                "error_category": latest.error_category,
            }
        latest_attempt = latest_reservation_attempts.get(candidate["id"])
        if latest_attempt is not None:
            candidate["latest_reservation_attempt"] = reservation_attempt_projection(
                watch.reservation_policy,
                latest_attempt,
            )
    return WatchRead.model_validate({**payload, "last_checked_at": last_checked_at})


async def watch_reads(session: AsyncSession, watches: list[Watch]) -> list[WatchRead]:
    if not watches:
        return []
    latest_observations, latest_by_watch = await _latest_observations_by_watch(
        session, [watch.id for watch in watches]
    )
    latest_reservation_attempts = await _latest_reservation_attempts_by_watch(
        session, [watch.id for watch in watches]
    )
    return [
        await watch_read(
            session,
            watch,
            last_checked_at=latest_by_watch.get(watch.id),
            latest_observations=latest_observations,
            latest_reservation_attempts=latest_reservation_attempts,
        )
        for watch in watches
    ]
