from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from ..database import SessionFactory
from ..domain import OutboxStatus
from ..metrics import OUTBOX_DELIVERIES, OUTBOX_PENDING
from ..notifications import NotificationDeliveryError, deliver_notification
from ..outbox_management.models import OutboxEvent
from ..security import secret_box
from .models import NotificationChannel

DELIVERABLE_EVENT_TYPES = (
    "notification.test_requested",
    "notification.dispatch_requested",
)
DELIVERY_BATCH_SIZE = 50
MAX_DELIVERY_ATTEMPTS = 5
MAX_RETRY_DELAY_SECONDS = 1_800


def _pending_notification_events(now: datetime):
    return (
        select(OutboxEvent)
        .where(
            OutboxEvent.status == OutboxStatus.PENDING,
            OutboxEvent.available_at <= now,
            OutboxEvent.event_type.in_(DELIVERABLE_EVENT_TYPES),
        )
        .order_by(OutboxEvent.created_at)
        .limit(DELIVERY_BATCH_SIZE)
        .with_for_update(skip_locked=True)
    )


async def deliver_pending_notifications() -> int:
    """Deliver one locked notification outbox batch and persist each result atomically."""

    now = datetime.now(timezone.utc)
    async with SessionFactory() as session:
        events = list((await session.scalars(_pending_notification_events(now))).all())
        delivered = 0
        for event in events:
            channel = await session.get(NotificationChannel, event.aggregate_id)
            if channel is None or not channel.enabled:
                event.status = OutboxStatus.FAILED
                event.processed_at = now
                event.last_error = "channel_missing_or_disabled"
                OUTBOX_DELIVERIES.labels("failed").inc()
                continue
            event.attempts += 1
            try:
                try:
                    channel_config = secret_box.decrypt_dict(channel.config_ciphertext)
                except RuntimeError as error:
                    raise NotificationDeliveryError("config_decrypt_failed") from error
                await deliver_notification(channel.kind, channel_config, event.payload)
            except NotificationDeliveryError as error:
                # Never persist provider responses, URLs, tokens, or message bodies.
                event.last_error = str(error)[:80]
                if error.disable_channel:
                    channel.enabled = False
                if error.permanent or event.attempts >= MAX_DELIVERY_ATTEMPTS:
                    event.status = OutboxStatus.FAILED
                    event.processed_at = now
                    OUTBOX_DELIVERIES.labels("failed").inc()
                else:
                    event.available_at = now + timedelta(
                        seconds=min(
                            30 * (2 ** (event.attempts - 1)),
                            MAX_RETRY_DELAY_SECONDS,
                        )
                    )
            else:
                event.status = OutboxStatus.SENT
                event.processed_at = now
                event.last_error = None
                delivered += 1
                OUTBOX_DELIVERIES.labels("sent").inc()
        await session.commit()
        pending = await session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.status == OutboxStatus.PENDING)
        )
        OUTBOX_PENDING.set(int(pending or 0))
        return delivered
