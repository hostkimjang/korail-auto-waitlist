from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import ReservationPolicy, WatchStatus
from ..outbox import add_outbox_event
from ..reservations.domain import reservation_attempt_result_policy
from ..reservations.payment_hold_application import is_payment_hold_ended
from ..watch_management.models import ReservationAttempt, SeatObservation, Watch, WatchCandidate
from .models import NotificationChannel

KOREA_TIMEZONE = ZoneInfo("Asia/Seoul")
SEAT_CLASS_LABELS = {
    "standard": "일반실",
    "first": "특실",
    "infant": "유아석",
    "free": "자유석",
    "waitlist": "예약대기",
    "any": "좌석 등급 무관",
}
KOREAN_WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _iso(value: datetime | None) -> str | None:
    return _aware(value).isoformat() if value is not None else None


def _date_label(value: date) -> str:
    return f"{value.year}년 {value.month}월 {value.day}일 ({KOREAN_WEEKDAYS[value.weekday()]})"


def _candidate_time_label(value: datetime) -> str:
    return _aware(value).astimezone(KOREA_TIMEZONE).strftime("%H:%M")


def _seat_class_label(value: str) -> str:
    return SEAT_CLASS_LABELS.get(value, value)


async def _latest_attempt_for_candidate(
    session: AsyncSession,
    candidate_id: str,
) -> ReservationAttempt | None:
    attempt: ReservationAttempt | None = await session.scalar(
        select(ReservationAttempt)
        .where(ReservationAttempt.candidate_id == candidate_id)
        .order_by(
            ReservationAttempt.started_at.desc(),
            ReservationAttempt.attempt_sequence.desc(),
            ReservationAttempt.id.desc(),
        )
        .limit(1)
    )
    return attempt


async def _notification_candidate_and_attempt(
    session: AsyncSession,
    watch: Watch,
    observation: SeatObservation | None,
) -> tuple[WatchCandidate | None, ReservationAttempt | None]:
    if observation is not None:
        candidate = await session.scalar(
            select(WatchCandidate).where(
                WatchCandidate.id == observation.candidate_id,
                WatchCandidate.watch_id == watch.id,
            )
        )
        if candidate is not None:
            return candidate, await _latest_attempt_for_candidate(session, candidate.id)

    row = (
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
    ).first()
    if row is None:
        return None, None
    attempt, candidate = row
    return candidate, attempt


def _retry_condition(
    watch: Watch,
    attempt: ReservationAttempt | None,
) -> str | None:
    if attempt is None:
        return None
    if is_payment_hold_ended(attempt):
        return (
            "new_availability_episode"
            if watch.reservation_policy is ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT
            else None
        )
    return reservation_attempt_result_policy(attempt.outcome).retry_condition


def _workflow_stage(
    target: WatchStatus,
    reason: str | None,
    attempt: ReservationAttempt | None,
) -> str | None:
    if attempt is None:
        return None
    if reason in {
        "confirmed_payment_hold_no_longer_actionable_monitoring_resumed",
        "confirmed_payment_hold_no_longer_actionable_one_off_expired",
    }:
        return "payment_hold_ended"
    if reason in {
        "reservation_unknown",
        "reservation_result_deadline_already_elapsed",
        "stale_reservation_attempt_requires_manual_check",
    }:
        return "manual_check_required"
    if target is WatchStatus.WATCHING:
        return "monitoring_resumed"
    return target.value


def _candidate_summary(watch: Watch, candidate: WatchCandidate) -> str:
    departure = _candidate_time_label(candidate.departure_at)
    arrival = (
        _candidate_time_label(candidate.arrival_at)
        if candidate.arrival_at is not None
        else "도착시각 미확인"
    )
    provider = watch.provider.value.upper()
    return (
        f"{provider} · {candidate.train_number} · {_date_label(watch.travel_date)} · "
        f"{watch.origin} {departure} → {watch.destination} {arrival} · "
        f"{_seat_class_label(candidate.seat_class)} · {watch.passenger_count}명"
    )


def _message_detail(
    watch: Watch,
    target: WatchStatus,
    *,
    reason: str | None,
    transition_token: str,
    payment_deadline: datetime | None,
) -> str:
    seat_disappeared = target == WatchStatus.WATCHING and transition_token.startswith(
        ("seat_found:watching", "official_waitlist:watching")
    )
    reservation_failed_monitoring_resumed = (
        target == WatchStatus.WATCHING and reason == "reservation_failed_monitoring_resumed"
    )
    reservation_result_requires_manual_check = target == WatchStatus.WATCHING and reason in {
        "reservation_unknown",
        "reservation_result_deadline_already_elapsed",
        "stale_reservation_attempt_requires_manual_check",
    }
    reservation_not_available = (
        target == WatchStatus.WATCHING and reason == "reservation_not_available"
    )
    payment_hold_ended = reason in {
        "confirmed_payment_hold_no_longer_actionable_monitoring_resumed",
        "confirmed_payment_hold_no_longer_actionable_one_off_expired",
    }
    if target == WatchStatus.RESERVING:
        return "좌석을 발견해 결제 직전까지 이번 좌석 가용성에 대한 예매를 진행하고 있습니다."
    if target == WatchStatus.PAYMENT_REQUIRED:
        if payment_deadline is not None:
            local_deadline = _aware(payment_deadline).astimezone(KOREA_TIMEZONE)
            return (
                "임시 예약이 완료되었습니다. "
                f"{local_deadline:%m월 %d일 %H:%M}까지 공식 플랫폼에서 결제해 주세요."
            )
        return "임시 예약이 완료되었습니다. 공식 플랫폼에서 결제기한을 확인하고 결제해 주세요."
    if target == WatchStatus.AUTH_REQUIRED:
        return (
            "예매 진행에 로그인 또는 사용자 확인이 필요합니다. "
            "철도 계정 확인이 완료되면 감시와 예매 준비를 재개합니다."
        )
    if target == WatchStatus.SEAT_FOUND:
        return "예매 가능한 좌석을 확인했습니다. 공식 플랫폼에서 최종 상태를 확인해 주세요."
    if target == WatchStatus.OFFICIAL_WAITLIST:
        return "공식 예약대기 가능 상태를 확인했습니다. 공식 플랫폼에서 최종 상태를 확인해 주세요."
    if seat_disappeared:
        return "좌석이 다시 판매 불가 상태로 바뀌어 감시를 계속합니다."
    if reservation_failed_monitoring_resumed:
        return (
            "예매 결과를 확정하지 못해 감시를 다시 시작했습니다. "
            "같은 가용성 구간에서는 다시 예매하지 않습니다."
        )
    if reservation_result_requires_manual_check:
        return (
            "예매 결과를 확정하지 못했습니다. 공식 플랫폼의 예약 내역을 확인해 주세요. "
            "좌석 감시는 계속하지만 같은 좌석 가용 상태에서는 자동 예매를 다시 시도하지 않습니다."
        )
    if reservation_not_available:
        return (
            "예매 시점에 좌석을 확보하지 못해 감시를 계속합니다. 판매 불가 상태를 확인한 뒤 "
            "좌석이 다시 가용해지는 경우에만 다음 자동 예매를 시도합니다."
        )
    if payment_hold_ended:
        follow_up = (
            "좌석 감시를 다시 시작합니다. 같은 가용성 구간에서는 바로 다시 예매하지 않습니다."
            if target == WatchStatus.WATCHING
            else "해당 1회성 작업을 종료합니다."
        )
        return f"임시 예약이 결제기한 안에 결제되지 않아 취소되었습니다. {follow_up}"
    return f"작업 상태: {target.value}"


async def add_watch_notifications(
    session: AsyncSession,
    watch: Watch,
    target: WatchStatus,
    transition_token: str,
    *,
    reason: str | None = None,
    observation: SeatObservation | None = None,
) -> None:
    seat_disappeared = target == WatchStatus.WATCHING and transition_token.startswith(
        ("seat_found:watching", "official_waitlist:watching")
    )
    reservation_failed_monitoring_resumed = (
        target == WatchStatus.WATCHING and reason == "reservation_failed_monitoring_resumed"
    )
    reservation_result_requires_manual_check = target == WatchStatus.WATCHING and reason in {
        "reservation_unknown",
        "reservation_result_deadline_already_elapsed",
        "stale_reservation_attempt_requires_manual_check",
    }
    reservation_not_available = (
        target == WatchStatus.WATCHING and reason == "reservation_not_available"
    )
    payment_hold_ended = reason in {
        "confirmed_payment_hold_no_longer_actionable_monitoring_resumed",
        "confirmed_payment_hold_no_longer_actionable_one_off_expired",
    }
    if target not in {
        WatchStatus.OFFICIAL_WAITLIST,
        WatchStatus.SEAT_FOUND,
        WatchStatus.RESERVING,
        WatchStatus.PAYMENT_REQUIRED,
        WatchStatus.COMPLETED,
        WatchStatus.COOLDOWN,
        WatchStatus.AUTH_REQUIRED,
        WatchStatus.EXPIRED,
        WatchStatus.FAILED,
    } and not any(
        (
            seat_disappeared,
            reservation_failed_monitoring_resumed,
            reservation_result_requires_manual_check,
            reservation_not_available,
            payment_hold_ended,
        )
    ):
        return
    # This is a single-admin service: notification channels are global settings, not
    # immutable per-watch recipients. Resolve them at the state edge so a channel
    # connected after a watch was created is used immediately, while disabling it
    # stops new dispatch requests. The watch field remains only as a persisted draft
    # configuration/legacy snapshot and is deliberately not a dispatch authority.
    channel_ids = list(
        (
            await session.scalars(
                select(NotificationChannel.id)
                .where(NotificationChannel.enabled.is_(True))
                .order_by(NotificationChannel.created_at, NotificationChannel.id)
            )
        ).all()
    )
    if not channel_ids:
        return
    candidate, attempt = await _notification_candidate_and_attempt(session, watch, observation)
    payment_deadline = watch.payment_deadline or (
        attempt.payment_deadline if attempt is not None else None
    )
    detail = _message_detail(
        watch,
        target,
        reason=reason,
        transition_token=transition_token,
        payment_deadline=payment_deadline,
    )
    message = (
        f"{_candidate_summary(watch, candidate)}\n{detail}"
        if candidate is not None
        else f"{watch.origin} → {watch.destination} {detail}"
    )
    retry_condition = _retry_condition(watch, attempt)
    workflow_stage = _workflow_stage(target, reason, attempt)
    for channel_id in channel_ids:
        await add_outbox_event(
            session,
            aggregate_type="notification_channel",
            aggregate_id=channel_id,
            event_type="notification.dispatch_requested",
            payload={
                "channel_id": channel_id,
                "watch_id": watch.id,
                "status": target.value,
                "message": message,
                "provider": watch.provider.value,
                "candidate_id": candidate.id if candidate is not None else None,
                "train_number": candidate.train_number if candidate is not None else None,
                "travel_date": watch.travel_date.isoformat(),
                "origin": watch.origin,
                "destination": watch.destination,
                "departure_at": _iso(candidate.departure_at) if candidate is not None else None,
                "arrival_at": _iso(candidate.arrival_at) if candidate is not None else None,
                "seat_class": candidate.seat_class if candidate is not None else watch.seat_class,
                "passenger_count": watch.passenger_count,
                "attempt_sequence": attempt.attempt_sequence if attempt is not None else None,
                "attempt_started_at": _iso(attempt.started_at) if attempt is not None else None,
                "attempt_finished_at": _iso(attempt.finished_at) if attempt is not None else None,
                "workflow_stage": workflow_stage,
                "retry_condition": retry_condition,
                "official_booking_url": watch.official_booking_url,
                "payment_deadline": _iso(payment_deadline),
            },
            dedupe_key=f"notification:{channel_id}:watch:{watch.id}:{transition_token}",
        )
