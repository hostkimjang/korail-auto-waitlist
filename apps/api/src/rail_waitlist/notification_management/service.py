from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import NotificationChannel
from ..notifications import validate_webhook_url_syntax
from ..outbox import add_outbox_event
from ..security import secret_box
from .schemas import NotificationChannelCreate, NotificationChannelUpdate, QueuedResponse

TEST_NOTIFICATION_MESSAGE = "KORAIL·SRT 알림 테스트입니다."


class NotificationConfigError(ValueError):
    """The persisted delivery configuration would be incomplete or unsafe."""


class NotificationChannelDisabledError(RuntimeError):
    """A disabled channel must never produce a delivery request."""


def validate_notification_config(data: NotificationChannelCreate) -> None:
    required = {
        "web_push": {"subscription_info"},
        "telegram": {"bot_token", "chat_id"},
        "discord_webhook": {"url"},
        "generic_webhook": {"url"},
    }[data.kind.value]
    missing = required - data.config.keys()
    if missing:
        raise NotificationConfigError(f"missing channel fields: {', '.join(sorted(missing))}")
    if data.kind.value in {"discord_webhook", "generic_webhook"}:
        try:
            validate_webhook_url_syntax(data.config["url"])
        except ValueError as error:
            raise NotificationConfigError(str(error)) from None


async def create_notification_channel(
    session: AsyncSession, data: NotificationChannelCreate
) -> NotificationChannel:
    validate_notification_config(data)
    channel = NotificationChannel(
        kind=data.kind,
        name=data.name,
        config_ciphertext=secret_box.encrypt_dict(data.config),
        enabled=data.enabled,
    )
    session.add(channel)
    await session.commit()
    await session.refresh(channel)
    return channel


async def update_notification_channel(
    session: AsyncSession, channel: NotificationChannel, data: NotificationChannelUpdate
) -> NotificationChannel:
    if data.name is not None:
        channel.name = data.name
    if data.enabled is not None:
        channel.enabled = data.enabled
    if data.config is not None:
        validate_notification_config(
            NotificationChannelCreate(
                kind=channel.kind, name=channel.name, config=data.config, enabled=channel.enabled
            )
        )
        channel.config_ciphertext = secret_box.encrypt_dict(data.config)
    await session.commit()
    await session.refresh(channel)
    return channel


async def queue_test_notification(
    session: AsyncSession,
    channel: NotificationChannel,
    *,
    requested_at: datetime | None = None,
) -> QueuedResponse:
    if not channel.enabled:
        raise NotificationChannelDisabledError("notification channel is disabled")
    event = await add_outbox_event(
        session,
        aggregate_type="notification_channel",
        aggregate_id=channel.id,
        event_type="notification.test_requested",
        payload={"channel_id": channel.id, "message": TEST_NOTIFICATION_MESSAGE},
        dedupe_key=(
            f"notification:{channel.id}:test:{(requested_at or datetime.now()).isoformat()}"
        ),
    )
    await session.commit()
    return QueuedResponse(queued=True, event_id=event.id)
