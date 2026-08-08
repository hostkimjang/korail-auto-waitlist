from __future__ import annotations

from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import WatchStatus
from ..outbox import add_outbox_event
from ..watch_management.models import Watch
from .models import NotificationChannel


async def add_watch_notifications(
    session: AsyncSession,
    watch: Watch,
    target: WatchStatus,
    transition_token: str,
    *,
    reason: str | None = None,
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
    if target == WatchStatus.RESERVING:
        message = (
            f"{watch.origin} → {watch.destination} 좌석을 발견해 결제 직전까지 "
            "이번 좌석 가용성에 대한 예매를 진행하고 있습니다."
        )
    elif target == WatchStatus.PAYMENT_REQUIRED:
        if watch.payment_deadline is not None:
            local_deadline = watch.payment_deadline.astimezone(ZoneInfo("Asia/Seoul"))
            message = (
                f"{watch.origin} → {watch.destination} 임시 예약이 완료되었습니다. "
                f"{local_deadline:%m월 %d일 %H:%M}까지 공식 플랫폼에서 결제해 주세요."
            )
        else:
            message = (
                f"{watch.origin} → {watch.destination} 임시 예약이 완료되었습니다. "
                "공식 플랫폼에서 결제기한을 확인하고 결제해 주세요."
            )
    elif target == WatchStatus.AUTH_REQUIRED:
        message = (
            f"{watch.origin} → {watch.destination} 예매 진행에 로그인 또는 사용자 확인이 "
            "필요합니다. 철도 계정 확인이 완료되면 감시와 예매 준비를 재개합니다."
        )
    elif seat_disappeared:
        message = (
            f"{watch.origin} → {watch.destination} 좌석이 다시 판매 불가 상태로 바뀌어 "
            "감시를 계속합니다."
        )
    elif reservation_failed_monitoring_resumed:
        message = (
            f"{watch.origin} → {watch.destination} 예매 결과를 확정하지 못해 감시를 "
            "다시 시작했습니다. 같은 가용성 구간에서는 다시 예매하지 않습니다."
        )
    elif reservation_result_requires_manual_check:
        message = (
            f"{watch.origin} → {watch.destination} 예매 결과를 확정하지 못했습니다. "
            "공식 플랫폼의 예약 내역을 확인해 주세요. 좌석 감시는 계속하지만 "
            "같은 좌석 가용 상태에서는 자동 예매를 다시 시도하지 않습니다."
        )
    elif reservation_not_available:
        message = (
            f"{watch.origin} → {watch.destination} 예매 시점에 좌석을 확보하지 못해 "
            "감시를 계속합니다. 판매 불가 상태를 확인한 뒤 좌석이 다시 가용해지는 "
            "경우에만 다음 자동 예매를 시도합니다."
        )
    elif payment_hold_ended:
        follow_up = (
            "좌석 감시를 다시 시작합니다. 같은 가용성 구간에서는 바로 다시 예매하지 않습니다."
            if target == WatchStatus.WATCHING
            else "해당 1회성 작업을 종료합니다."
        )
        message = (
            f"{watch.origin} → {watch.destination} 임시 예약이 결제기한 안에 "
            f"결제되지 않아 취소되었습니다. {follow_up}"
        )
    else:
        message = f"{watch.origin} → {watch.destination} 작업 상태: {target.value}"
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
                "official_booking_url": watch.official_booking_url,
                "payment_deadline": (
                    watch.payment_deadline.isoformat()
                    if watch.payment_deadline is not None
                    else None
                ),
            },
            dedupe_key=f"notification:{channel_id}:watch:{watch.id}:{transition_token}",
        )
