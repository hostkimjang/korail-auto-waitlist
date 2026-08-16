from __future__ import annotations

from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import WatchStatus
from ..watch_management.models import ReservationAttempt, Watch, WatchCandidate


class ApplyWatchTransition(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        watch: Watch,
        target: WatchStatus,
        idempotency_key: str | None = None,
        *,
        reason: str | None = None,
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


async def apply_exact_paid_resolution(
    session: AsyncSession,
    watch: Watch,
    candidate: WatchCandidate,
    attempt: ReservationAttempt,
    *,
    apply_watch_transition: ApplyWatchTransition,
    add_outbox_event: AddOutboxEvent,
) -> bool:
    """Persist exact paid evidence as an absolute watch-wide reservation fence."""

    completed_in_this_call = False
    completion_from_status = watch.status
    attempt.next_reconcile_at = None
    attempt.reconciliation_resolution = None
    candidate.state = "expired"
    candidate.suppressed_by_candidate_id = None
    candidate.manual_rearm_source_attempt_id = None
    candidate.manual_rearm_authorized_at = None
    watch_candidates = list(
        (
            await session.scalars(select(WatchCandidate).where(WatchCandidate.watch_id == watch.id))
        ).all()
    )
    for watch_candidate in watch_candidates:
        watch_candidate.state = "expired"
        watch_candidate.suppressed_by_candidate_id = None
        watch_candidate.manual_rearm_source_attempt_id = None
        watch_candidate.manual_rearm_authorized_at = None
    watch.payment_deadline = None
    watch.official_booking_url = None
    watch.next_check_at = None
    watch.observation_in_flight_until = None
    watch.cooldown_until = None
    if watch.status in {
        WatchStatus.SCHEDULED,
        WatchStatus.OFFICIAL_WAITLIST,
        WatchStatus.SEAT_FOUND,
        WatchStatus.RESERVING,
    }:
        await apply_watch_transition(
            session,
            watch,
            WatchStatus.WATCHING,
            reason="reservation_result_unknown_confirmed_paid",
        )
        completion_from_status = watch.status
    if watch.status in {WatchStatus.WATCHING, WatchStatus.PAYMENT_REQUIRED}:
        completion_from_status = watch.status
        await apply_watch_transition(
            session,
            watch,
            WatchStatus.COMPLETED,
            reason="reservation_reconciliation_confirmed_paid",
        )
        completed_in_this_call = True
    if completed_in_this_call:
        await add_outbox_event(
            session,
            aggregate_type="watch",
            aggregate_id=watch.id,
            event_type="watch.payment_completed",
            payload={
                "watch_id": watch.id,
                "candidate_id": candidate.id,
                "terminal": True,
                "status": watch.status.value,
                "from": completion_from_status.value,
                "to": watch.status.value,
                "reason": "confirmed_paid",
                "message": "공식 예약 내역에서 결제 완료를 확인했습니다.",
                "automatic_reservation_retry": False,
            },
            dedupe_key=f"payment-completed:{attempt.id}",
        )
    return completed_in_this_call
