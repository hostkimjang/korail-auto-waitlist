from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import SeatObservationStatus, WatchStatus
from ..watch_management.models import SeatObservation, Watch, WatchCandidate
from .contracts import SeatObservationResult
from .operational_projection_application import OperationalProjectionCandidate
from .status_policy import (
    ACTIONABLE_SEAT_STATUSES,
    SEAT_FOUND_STATUSES,
)


class ApplyOperationalProjection(Protocol):
    def __call__(
        self,
        candidate: OperationalProjectionCandidate,
        result: SeatObservationResult,
    ) -> None: ...


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


@dataclass(frozen=True, slots=True)
class ObservationRecordingDependencies:
    apply_operational_projection: ApplyOperationalProjection
    add_outbox_event: AddOutboxEvent
    apply_watch_transition: ApplyWatchTransition


async def record_seat_observation(
    session: AsyncSession,
    watch: Watch,
    candidate: WatchCandidate,
    result: SeatObservationResult,
    *,
    apply_status_transition: bool = True,
    dependencies: ObservationRecordingDependencies,
) -> SeatObservation:
    """Persist one normalized result inside the caller-owned observation transaction."""
    dependencies.apply_operational_projection(candidate, result)
    observation = SeatObservation(
        candidate=candidate,
        status=result.status,
        source=result.source,
        observed_at=result.observed_at,
        fresh_until=result.fresh_until,
        error_category=result.error_category,
    )
    session.add(observation)
    await session.flush()

    is_actionable = result.status in ACTIONABLE_SEAT_STATUSES
    candidate.state = "seat_found" if is_actionable else "observed"
    await dependencies.add_outbox_event(
        session,
        aggregate_type="watch",
        aggregate_id=watch.id,
        event_type="watch.seat_observed",
        payload={
            "watch_id": watch.id,
            "candidate_id": candidate.id,
            "status": result.status.value,
            "source": result.source,
            "observed_at": result.observed_at.isoformat(),
            "fresh_until": result.fresh_until.isoformat(),
        },
        dedupe_key=f"seat-observation:{observation.id}",
    )
    if (
        apply_status_transition
        and result.status == SeatObservationStatus.WAITLIST_AVAILABLE
        and watch.status == WatchStatus.WATCHING
    ):
        await dependencies.apply_watch_transition(
            session,
            watch,
            WatchStatus.OFFICIAL_WAITLIST,
            reason="authorized_seat_observation_waitlist_available",
            observation=observation,
        )
    elif (
        apply_status_transition
        and result.status in SEAT_FOUND_STATUSES
        and watch.status == WatchStatus.WATCHING
    ):
        await dependencies.apply_watch_transition(
            session,
            watch,
            WatchStatus.SEAT_FOUND,
            reason="authorized_seat_observation_actionable",
            observation=observation,
        )
    return observation
