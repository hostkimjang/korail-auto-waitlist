from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import Provider, ReservationOutcome, ReservationPolicy, WatchStatus
from ..watch_management.models import ReservationAttempt, Watch, WatchCandidate


class ManualReservationRearmNotFound(LookupError):
    """Raised when the watch no longer exists at the locked command boundary."""


class ManualReservationRearmRejected(RuntimeError):
    """Raised when an explicit retry would widen the one-attempt safety fence."""


class ReservationDispatchReady(Protocol):
    async def __call__(self, session: AsyncSession, watch: Watch) -> bool: ...


class PaymentHoldEnded(Protocol):
    def __call__(self, attempt: ReservationAttempt) -> bool: ...


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
class ManualReservationRearmDependencies:
    reservation_dispatch_ready: ReservationDispatchReady
    is_payment_hold_ended: PaymentHoldEnded
    add_outbox_event: AddOutboxEvent
    now: Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class ManualReservationRearmResult:
    watch: Watch
    created: bool


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _candidate_departure(candidate: WatchCandidate) -> datetime:
    return _as_utc(
        candidate.actual_departure_at
        or candidate.estimated_departure_at
        or candidate.scheduled_departure_at
        or candidate.departure_at
    )


async def authorize_manual_reservation_rearm(
    session: AsyncSession,
    watch_id: str,
    *,
    dependencies: ManualReservationRearmDependencies,
) -> ManualReservationRearmResult:
    """Persist one user-approved retry marker without calling the rail provider."""

    watch = await session.scalar(select(Watch).where(Watch.id == watch_id).with_for_update())
    if watch is None:
        raise ManualReservationRearmNotFound("watch not found")

    latest_row = (
        await session.execute(
            select(ReservationAttempt, WatchCandidate)
            .join(WatchCandidate, WatchCandidate.id == ReservationAttempt.candidate_id)
            .where(WatchCandidate.watch_id == watch.id)
            .order_by(
                ReservationAttempt.started_at.desc(),
                ReservationAttempt.attempt_sequence.desc(),
                ReservationAttempt.id.desc(),
            )
            .limit(1)
        )
    ).one_or_none()
    if latest_row is None:
        raise ManualReservationRearmRejected("결제 보류가 종료된 예매 기록이 없습니다.")
    latest_attempt, candidate = latest_row

    if candidate.manual_rearm_source_attempt_id == latest_attempt.id:
        return ManualReservationRearmResult(watch=watch, created=False)

    if watch.reservation_policy is not ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT:
        raise ManualReservationRearmRejected(
            "자동 예매 정책을 사용하는 대기만 다시 시작할 수 있습니다."
        )
    if watch.status is not WatchStatus.WATCHING:
        raise ManualReservationRearmRejected(
            "감시 중인 대기에서만 자동 예매를 다시 시작할 수 있습니다."
        )
    if watch.provider not in {Provider.KORAIL, Provider.SRT}:
        raise ManualReservationRearmRejected(
            "공식 철도사 대기에서만 자동 예매를 다시 시작할 수 있습니다."
        )
    if latest_attempt.outcome is ReservationOutcome.PENDING:
        raise ManualReservationRearmRejected("예매가 이미 진행 중입니다.")
    if not dependencies.is_payment_hold_ended(latest_attempt):
        raise ManualReservationRearmRejected(
            "공식 확인으로 종료된 결제 보류만 다시 시작할 수 있습니다."
        )

    now = _as_utc(dependencies.now())
    if _candidate_departure(candidate) <= now:
        raise ManualReservationRearmRejected(
            "이미 출발한 열차의 자동 예매는 다시 시작할 수 없습니다."
        )
    if not await dependencies.reservation_dispatch_ready(session, watch):
        raise ManualReservationRearmRejected(
            "철도사 계정 인증 또는 자동 예매 기능을 확인한 뒤 다시 시도해 주세요."
        )

    candidate.manual_rearm_source_attempt_id = latest_attempt.id
    candidate.manual_rearm_authorized_at = now
    watch.next_check_at = now
    await dependencies.add_outbox_event(
        session,
        aggregate_type="watch",
        aggregate_id=watch.id,
        event_type="watch.manual_reservation_rearmed",
        payload={
            "watch_id": watch.id,
            "candidate_id": candidate.id,
            "source_attempt_id": latest_attempt.id,
            "status": watch.status.value,
            "message": "사용자 확인으로 다음 공식 좌석 관측의 자동 예매를 한 번 다시 허용했습니다.",
        },
        dedupe_key=f"manual-reservation-rearm:{latest_attempt.id}",
    )
    await session.commit()
    await session.refresh(watch)
    return ManualReservationRearmResult(watch=watch, created=True)
