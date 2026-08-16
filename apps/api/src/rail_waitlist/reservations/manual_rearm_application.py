from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import Provider, ReservationOutcome, ReservationPolicy, WatchStatus
from ..watch_management.models import ReservationAttempt, Watch, WatchCandidate
from .attempt_policy import (
    active_unresolved_unknown_attempt_ids,
    exact_paid_reservation_attempt_id,
)
from .manual_rearm_contracts import ManualReservationRearmReason


class ManualReservationRearmNotFound(LookupError):
    """Raised when the watch no longer exists at the locked command boundary."""


class ManualReservationRearmRejected(RuntimeError):
    """Raised when an explicit retry would widen the one-attempt safety fence."""


class ReservationDispatchCredentialVersion(Protocol):
    async def __call__(self, session: AsyncSession, watch: Watch) -> int | None: ...


class PaymentHoldEnded(Protocol):
    def __call__(self, attempt: ReservationAttempt) -> bool: ...


class UnresolvedUnknownManualRearmSource(Protocol):
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
    reservation_dispatch_credential_version: ReservationDispatchCredentialVersion
    is_payment_hold_ended: PaymentHoldEnded
    is_unresolved_unknown_manual_rearm_source: UnresolvedUnknownManualRearmSource
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
    reason: ManualReservationRearmReason | None = None,
    official_reservation_state_confirmed: bool = False,
    dependencies: ManualReservationRearmDependencies,
) -> ManualReservationRearmResult:
    """Persist one user-approved retry marker without calling the rail provider."""

    watch = await session.scalar(select(Watch).where(Watch.id == watch_id).with_for_update())
    if watch is None:
        raise ManualReservationRearmNotFound("watch not found")

    attempt_rows = (
        await session.execute(
            select(ReservationAttempt, WatchCandidate)
            .join(WatchCandidate, WatchCandidate.id == ReservationAttempt.candidate_id)
            .where(WatchCandidate.watch_id == watch.id)
            .order_by(
                ReservationAttempt.started_at.desc(),
                ReservationAttempt.attempt_sequence.desc(),
                ReservationAttempt.id.desc(),
            )
        )
    ).all()
    if not attempt_rows:
        raise ManualReservationRearmRejected("다시 확인할 예매 기록이 없습니다.")
    attempts = [attempt for attempt, _candidate in attempt_rows]
    if exact_paid_reservation_attempt_id(attempts) is not None:
        raise ManualReservationRearmRejected(
            "공식 결제 완료가 확인된 예매는 다시 시작할 수 없습니다."
        )
    unresolved_unknown_ids = active_unresolved_unknown_attempt_ids(attempts)
    if len(unresolved_unknown_ids) > 1:
        raise ManualReservationRearmRejected(
            "확인되지 않은 예매 결과가 여러 건이라 다시 시작할 수 없습니다."
        )
    overall_latest_attempt = attempts[0]
    if overall_latest_attempt.outcome is ReservationOutcome.PENDING:
        raise ManualReservationRearmRejected("예매가 이미 진행 중입니다.")
    selected_attempt_id = (
        next(iter(unresolved_unknown_ids)) if unresolved_unknown_ids else overall_latest_attempt.id
    )
    latest_attempt, candidate = next(
        row for row in attempt_rows if row[0].id == selected_attempt_id
    )

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
    now = _as_utc(dependencies.now())
    if _candidate_departure(candidate) <= now:
        raise ManualReservationRearmRejected(
            "이미 출발한 열차의 자동 예매는 다시 시작할 수 없습니다."
        )
    credential_version = await dependencies.reservation_dispatch_credential_version(session, watch)
    if credential_version is None:
        raise ManualReservationRearmRejected(
            "철도사 계정 인증 또는 자동 예매 기능을 확인한 뒤 다시 시도해 주세요."
        )

    authorization_kind: ManualReservationRearmReason
    if dependencies.is_payment_hold_ended(latest_attempt):
        if reason not in {None, ManualReservationRearmReason.PAYMENT_HOLD_ENDED}:
            raise ManualReservationRearmRejected(
                "현재 예매 기록과 다시 확인하는 이유가 일치하지 않습니다."
            )
        authorization_kind = ManualReservationRearmReason.PAYMENT_HOLD_ENDED
    elif dependencies.is_unresolved_unknown_manual_rearm_source(latest_attempt):
        if (
            reason is not ManualReservationRearmReason.UNKNOWN_RESULT_UNRESOLVED
            or not official_reservation_state_confirmed
        ):
            raise ManualReservationRearmRejected(
                "공식 앱에서 예약이 없음을 확인한 뒤 다시 시도해 주세요."
            )
        if (
            latest_attempt.credential_version is None
            or latest_attempt.credential_version != credential_version
        ):
            raise ManualReservationRearmRejected(
                "예매 시도 이후 철도사 계정이 변경되어 다시 시작할 수 없습니다."
            )
        authorization_kind = ManualReservationRearmReason.UNKNOWN_RESULT_UNRESOLVED
    else:
        raise ManualReservationRearmRejected(
            "현재 공식 확인 상태에서는 자동 예매를 다시 시작할 수 없습니다."
        )

    marker_matches_latest = candidate.manual_rearm_source_attempt_id == latest_attempt.id
    if marker_matches_latest:
        if authorization_kind is ManualReservationRearmReason.UNKNOWN_RESULT_UNRESOLVED:
            return ManualReservationRearmResult(watch=watch, created=False)
        authorized_at = candidate.manual_rearm_authorized_at
        hold_ended_at = latest_attempt.post_deadline_reconciled_at
        if (
            authorized_at is not None
            and hold_ended_at is not None
            and _as_utc(authorized_at) >= _as_utc(hold_ended_at)
        ):
            return ManualReservationRearmResult(watch=watch, created=False)

    candidate.manual_rearm_source_attempt_id = latest_attempt.id
    candidate.manual_rearm_authorized_at = now
    watch.next_check_at = now
    dedupe_generation = "current"
    if authorization_kind is ManualReservationRearmReason.PAYMENT_HOLD_ENDED:
        hold_ended_at = latest_attempt.post_deadline_reconciled_at
        if hold_ended_at is None:
            raise RuntimeError("ended payment hold must persist its reconciliation time")
        dedupe_generation = str(int(_as_utc(hold_ended_at).timestamp() * 1_000_000))
    await dependencies.add_outbox_event(
        session,
        aggregate_type="watch",
        aggregate_id=watch.id,
        event_type="watch.manual_reservation_rearmed",
        payload={
            "watch_id": watch.id,
            "candidate_id": candidate.id,
            "source_attempt_id": latest_attempt.id,
            "authorization_kind": authorization_kind.value,
            "status": watch.status.value,
            "message": "사용자 확인으로 다음 공식 좌석 관측의 자동 예매를 한 번 다시 허용했습니다.",
        },
        dedupe_key=(
            "manual-reservation-rearm:"
            f"{authorization_kind.value}:{latest_attempt.id}:{dedupe_generation}"
        ),
    )
    await session.commit()
    await session.refresh(watch)
    return ManualReservationRearmResult(watch=watch, created=True)
