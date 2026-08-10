from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from ..domain import ReservationOutcome, WatchStatus
from ..watch_management.models import ReservationAttempt, SeatObservation, Watch, WatchCandidate

RESERVATION_ATTEMPT_STALE_AFTER = timedelta(minutes=5)


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


@dataclass(frozen=True, slots=True)
class StaleReservationAttemptRecoveryDependencies:
    apply_watch_transition: ApplyWatchTransition
    add_outbox_event: AddOutboxEvent


def build_stale_reservation_attempts_query(
    now: datetime,
    *,
    stale_after: timedelta = RESERVATION_ATTEMPT_STALE_AFTER,
) -> Select[tuple[ReservationAttempt, WatchCandidate, Watch]]:
    """Build the PostgreSQL-safe lock query for abandoned provider calls."""
    return (
        select(ReservationAttempt, WatchCandidate, Watch)
        .join(
            WatchCandidate,
            WatchCandidate.id == ReservationAttempt.candidate_id,
        )
        .join(Watch, Watch.id == WatchCandidate.watch_id)
        .where(
            ReservationAttempt.outcome == ReservationOutcome.PENDING,
            ReservationAttempt.started_at <= now - stale_after,
        )
        # registration_evidence is a nullable joined relationship. Lock only
        # the required rows so PostgreSQL does not lock that LEFT OUTER JOIN.
        .with_for_update(
            of=(ReservationAttempt, WatchCandidate, Watch),
            skip_locked=True,
        )
    )


async def recover_stale_reservation_attempts(
    session: AsyncSession,
    now: datetime,
    *,
    stale_after: timedelta = RESERVATION_ATTEMPT_STALE_AFTER,
    dependencies: StaleReservationAttemptRecoveryDependencies,
) -> int:
    """Fence abandoned provider calls whose hold result can no longer be proven."""
    rows = list(
        (
            await session.execute(
                build_stale_reservation_attempts_query(now, stale_after=stale_after)
            )
        ).all()
    )
    for attempt, candidate, watch in rows:
        attempt.outcome = ReservationOutcome.UNKNOWN
        attempt.finished_at = now
        if watch.status == WatchStatus.RESERVING:
            # UNKNOWN remains a durable ambiguous-result fence. Observation may
            # resume, but this candidate is not armed for another reservation.
            candidate.state = "observed"
            await dependencies.apply_watch_transition(
                session,
                watch,
                WatchStatus.WATCHING,
                reason="stale_reservation_attempt_requires_manual_check",
            )
            if watch.next_check_at is None:
                watch.next_check_at = now
        elif watch.status == WatchStatus.EXPIRED:
            candidate.state = "expired"
        elif candidate.state == "reservation_attempted":
            candidate.state = "observed"
        await dependencies.add_outbox_event(
            session,
            aggregate_type="watch",
            aggregate_id=watch.id,
            event_type="watch.reservation_result_requires_manual_check",
            payload={
                "watch_id": watch.id,
                "candidate_id": candidate.id,
                "attempt_id": attempt.id,
                "attempt_sequence": attempt.attempt_sequence,
                "attempt_started_at": attempt.started_at.isoformat(),
                "attempt_finished_at": now.isoformat(),
                "outcome": ReservationOutcome.UNKNOWN.value,
                "retryable": False,
                "manual_check_required": True,
                "retry_condition": None,
                "monitoring_resumed": watch.status == WatchStatus.WATCHING,
                "progress_stages": attempt.progress_stages or [],
                "reason": "reservation_attempt_result_unknown_after_restart",
            },
            dedupe_key=f"reservation-attempt-recovery:{attempt.id}",
        )
    if rows:
        await session.commit()
    return len(rows)
