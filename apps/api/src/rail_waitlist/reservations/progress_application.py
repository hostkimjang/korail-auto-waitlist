from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import Provider, ReservationOutcome, WatchStatus
from ..outbox_management.models import OutboxEvent
from ..provider_account_management.models import RailProviderAccount
from ..watch_management.models import ReservationAttempt, Watch, WatchCandidate
from .attempt_timing_application import latest_candidate_seat_detected_at
from .contracts import ReservationProgressStage


class AsyncSessionFactory(Protocol):
    def __call__(self) -> AsyncSession: ...


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


PROGRESS_STAGE_ORDER = {
    "authenticated_session_ready": 0,
    "target_rechecked": 1,
    "seat_selected": 2,
    "reservation_requested": 3,
}


def cumulative_progress_with(
    current: Sequence[ReservationProgressStage],
    progress: ReservationProgressStage,
) -> tuple[ReservationProgressStage, ...] | None:
    """Append only a new, forward-moving stage with a timezone-aware timestamp."""

    if progress.occurred_at.tzinfo is None or progress.occurred_at.utcoffset() is None:
        return None
    if any(item.stage == progress.stage for item in current):
        return None
    if current:
        previous = current[-1]
        if PROGRESS_STAGE_ORDER[progress.stage] <= PROGRESS_STAGE_ORDER[previous.stage]:
            return None
        if progress.occurred_at < previous.occurred_at:
            return None
    return (*current, progress)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _locked_provider_credential_version_query(provider: Provider):
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


def _serialized_progress(
    stages: Sequence[ReservationProgressStage],
) -> list[dict[str, str]]:
    return [
        {"stage": stage.stage, "occurred_at": stage.occurred_at.isoformat()} for stage in stages
    ]


def _valid_cumulative_progress(
    stages: Sequence[ReservationProgressStage],
    *,
    attempt_started_at: datetime,
) -> bool:
    if not stages:
        return False
    accumulated: tuple[ReservationProgressStage, ...] = ()
    for stage in stages:
        next_stages = cumulative_progress_with(accumulated, stage)
        if next_stages is None:
            return False
        accumulated = next_stages
    return _aware_utc(stages[0].occurred_at) >= _aware_utc(attempt_started_at)


async def record_reservation_progress(
    *,
    session_factory: AsyncSessionFactory,
    add_outbox_event: AddOutboxEvent,
    watch_id: str,
    candidate_id: str,
    attempt_id: str,
    expected_credential_version: int | None,
    cumulative_progress: Sequence[ReservationProgressStage],
) -> bool:
    """Persist one fenced progress snapshot without holding locks during provider I/O."""

    if expected_credential_version is None or not cumulative_progress:
        return False
    current_progress = cumulative_progress[-1]
    dedupe_key = f"reservation-progress:{attempt_id}:{current_progress.stage}"

    async with session_factory() as session:
        try:
            # Keep the same lock order as the terminal result transaction. A result that
            # wins this race makes the pending-attempt fence below reject late progress.
            credential_version = await session.scalar(
                _locked_provider_credential_version_query(Provider.KORAIL)
            )
            if credential_version != expected_credential_version:
                return False
            watch = await session.scalar(_locked_watch_query(watch_id))
            candidate = await session.scalar(_locked_candidate_query(candidate_id))
            attempt = await session.scalar(_locked_attempt_query(attempt_id))
            if watch is None or candidate is None or attempt is None:
                return False
            if (
                watch.provider is not Provider.KORAIL
                or watch.status is not WatchStatus.RESERVING
                or candidate.watch_id != watch.id
                or candidate.state != "reservation_attempted"
                or attempt.candidate_id != candidate.id
                or attempt.outcome is not ReservationOutcome.PENDING
                or attempt.credential_version != expected_credential_version
                or not _valid_cumulative_progress(
                    cumulative_progress,
                    attempt_started_at=attempt.started_at,
                )
            ):
                return False

            existing = await session.scalar(
                select(OutboxEvent).where(OutboxEvent.dedupe_key == dedupe_key).with_for_update()
            )
            if existing is not None:
                return False

            previous_events = list(
                (
                    await session.scalars(
                        select(OutboxEvent)
                        .where(
                            OutboxEvent.event_type == "watch.reservation_progressed",
                            OutboxEvent.dedupe_key.like(
                                f"reservation-progress:{attempt_id}:%"
                            ),
                        )
                        .order_by(OutboxEvent.created_at, OutboxEvent.id)
                        .with_for_update()
                    )
                ).all()
            )
            serialized = _serialized_progress(cumulative_progress)
            if previous_events:
                previous = previous_events[-1].payload.get("progress_stages")
                if not isinstance(previous, list) or serialized[: len(previous)] != previous:
                    return False

            attempt.progress_stages = serialized

            seat_detected_at = await latest_candidate_seat_detected_at(
                session,
                candidate.id,
                attempt_started_at=attempt.started_at,
            )
            await add_outbox_event(
                session,
                aggregate_type="watch",
                aggregate_id=watch.id,
                event_type="watch.reservation_progressed",
                payload={
                    "watch_id": watch.id,
                    "candidate_id": candidate.id,
                    "attempt_id": attempt.id,
                    "attempt_sequence": attempt.attempt_sequence,
                    "seat_detected_at": (
                        seat_detected_at.isoformat() if seat_detected_at is not None else None
                    ),
                    "attempt_started_at": attempt.started_at.isoformat(),
                    "stage": current_progress.stage,
                    "occurred_at": current_progress.occurred_at.isoformat(),
                    "progress_stages": serialized,
                },
                dedupe_key=dedupe_key,
            )
            await session.commit()
            return True
        except Exception:
            await session.rollback()
            raise
