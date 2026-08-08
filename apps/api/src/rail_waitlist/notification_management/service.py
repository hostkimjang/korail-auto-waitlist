from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import NotificationKind
from ..notifications import (
    NotificationDeliveryError,
    normalize_webpush_subscription,
    validate_webhook_url_syntax,
)
from ..outbox import add_outbox_event
from ..security import secret_box
from .models import NotificationChannel
from .schemas import NotificationChannelCreate, NotificationChannelUpdate, QueuedResponse

TEST_NOTIFICATION_MESSAGE = "KORAIL·SRT 알림 테스트입니다."
TEST_NOTIFICATION_TITLE = "레일웨잇 시험 알림"
RETIRED_NATIVE_NOTIFICATION_KINDS = frozenset(
    {NotificationKind.ANDROID_FCM, NotificationKind.IOS_APNS}
)
USER_CONFIGURABLE_NOTIFICATION_KINDS = (
    frozenset(NotificationKind) - RETIRED_NATIVE_NOTIFICATION_KINDS
)


class NotificationConfigError(ValueError):
    """The persisted delivery configuration would be incomplete or unsafe."""


class NotificationChannelDisabledError(RuntimeError):
    """A disabled channel must never produce a delivery request."""


class NotificationConfigConflictError(RuntimeError):
    """A browser subscription already belongs to another channel row."""


def web_push_device_key(config: dict[str, object]) -> str:
    """Return a stable, non-secret identifier without exposing the push endpoint."""

    try:
        subscription = normalize_webpush_subscription(config)
    except NotificationDeliveryError as error:
        raise NotificationConfigError("invalid Web Push subscription") from error
    endpoint = cast(str, subscription["endpoint"])
    digest = hashlib.sha256(endpoint.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def validate_notification_config(data: NotificationChannelCreate) -> dict[str, object]:
    if data.kind in RETIRED_NATIVE_NOTIFICATION_KINDS:
        raise NotificationConfigError("native push notification channels are no longer supported")
    required = {
        "web_push": {"subscription_info"},
        "telegram": {"bot_token", "chat_id"},
        "discord_webhook": {"url"},
        "generic_webhook": {"url"},
    }[data.kind.value]
    missing = required - data.config.keys()
    if missing:
        raise NotificationConfigError(f"missing channel fields: {', '.join(sorted(missing))}")
    invalid = sorted(
        field
        for field in required
        if not isinstance(data.config[field], str) or not data.config[field].strip()
    )
    if invalid:
        raise NotificationConfigError(f"empty or invalid channel fields: {', '.join(invalid)}")
    normalized = dict(data.config)
    for field in required | {"authorization"}:
        value = normalized.get(field)
        if isinstance(value, str):
            normalized[field] = value.strip()
    if data.kind.value in {"discord_webhook", "generic_webhook"}:
        try:
            validate_webhook_url_syntax(normalized["url"])
        except ValueError as error:
            raise NotificationConfigError(str(error)) from None
    if data.kind is NotificationKind.WEB_PUSH:
        try:
            subscription = normalize_webpush_subscription(normalized)
        except NotificationDeliveryError as error:
            raise NotificationConfigError("invalid Web Push subscription") from error
        normalized["subscription_info"] = json.dumps(
            subscription,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    return normalized


def _apply_web_push_channel(
    channel: NotificationChannel,
    data: NotificationChannelCreate,
    config: dict[str, object],
    device_key: str,
) -> None:
    channel.name = data.name
    channel.config_ciphertext = secret_box.encrypt_dict(config)
    channel.web_push_device_key = device_key
    channel.enabled = data.enabled


async def _find_legacy_web_push_channel(
    session: AsyncSession, device_key: str
) -> NotificationChannel | None:
    rows = await session.scalars(
        select(NotificationChannel).where(
            NotificationChannel.kind == NotificationKind.WEB_PUSH,
            NotificationChannel.web_push_device_key.is_(None),
        )
    )
    for channel in rows.all():
        try:
            config = secret_box.decrypt_dict(channel.config_ciphertext)
            legacy_device_key = web_push_device_key(config)
        except (RuntimeError, NotificationConfigError):
            continue
        if legacy_device_key == device_key:
            return channel
    return None


async def _find_web_push_channel(
    session: AsyncSession, device_key: str
) -> NotificationChannel | None:
    channel = await session.scalar(
        select(NotificationChannel).where(
            NotificationChannel.kind == NotificationKind.WEB_PUSH,
            NotificationChannel.web_push_device_key == device_key,
        )
    )
    return channel or await _find_legacy_web_push_channel(session, device_key)


async def backfill_web_push_device_keys(session: AsyncSession) -> dict[str, str]:
    """Lazily identify encrypted legacy subscriptions after the schema migration.

    Alembic cannot decrypt channel configuration. The first authenticated read locks
    the Web Push rows, computes their endpoint digests in the application boundary,
    and collapses duplicate legacy endpoints to one enabled canonical row. Invalid
    legacy rows are disabled and omitted from the list response instead of exposing
    a misleading device identity.
    """

    channels = list(
        (
            await session.scalars(
                select(NotificationChannel)
                .where(NotificationChannel.kind == NotificationKind.WEB_PUSH)
                .order_by(NotificationChannel.created_at, NotificationChannel.id)
                .with_for_update()
            )
        ).all()
    )
    canonical_by_key = {
        channel.web_push_device_key: channel
        for channel in channels
        if channel.web_push_device_key is not None
    }
    response_keys = {
        channel.id: channel.web_push_device_key
        for channel in channels
        if channel.web_push_device_key is not None
    }
    changed = False
    for channel in channels:
        if channel.web_push_device_key is not None:
            continue
        try:
            config = secret_box.decrypt_dict(channel.config_ciphertext)
            device_key = web_push_device_key(config)
        except (RuntimeError, NotificationConfigError):
            if channel.enabled:
                channel.enabled = False
                changed = True
            continue

        canonical = canonical_by_key.get(device_key)
        if canonical is None:
            channel.web_push_device_key = device_key
            canonical_by_key[device_key] = channel
            response_keys[channel.id] = device_key
            changed = True
            continue

        if channel.enabled and not canonical.enabled:
            canonical.enabled = True
            changed = True
        if channel.enabled:
            channel.enabled = False
            changed = True

    if changed:
        await session.commit()
    return response_keys


async def create_notification_channel(
    session: AsyncSession, data: NotificationChannelCreate
) -> NotificationChannel:
    config = validate_notification_config(data)
    if data.kind is NotificationKind.WEB_PUSH:
        device_key = web_push_device_key(config)
        existing = await _find_web_push_channel(session, device_key)
        if existing is not None:
            _apply_web_push_channel(existing, data, config, device_key)
            await session.commit()
            await session.refresh(existing)
            return existing
    channel = NotificationChannel(
        kind=data.kind,
        name=data.name,
        config_ciphertext=secret_box.encrypt_dict(config),
        web_push_device_key=(
            web_push_device_key(config) if data.kind is NotificationKind.WEB_PUSH else None
        ),
        enabled=data.enabled,
    )
    session.add(channel)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        if data.kind is not NotificationKind.WEB_PUSH:
            raise
        existing = await _find_web_push_channel(session, web_push_device_key(config))
        if existing is None:
            raise
        _apply_web_push_channel(existing, data, config, web_push_device_key(config))
        await session.commit()
        channel = existing
    await session.refresh(channel)
    return channel


async def update_notification_channel(
    session: AsyncSession, channel: NotificationChannel, data: NotificationChannelUpdate
) -> NotificationChannel:
    if channel.kind in RETIRED_NATIVE_NOTIFICATION_KINDS:
        raise NotificationConfigError("native push notification channels are no longer supported")
    if data.name is not None:
        channel.name = data.name
    if data.enabled is not None:
        channel.enabled = data.enabled
    if data.config is not None:
        config = validate_notification_config(
            NotificationChannelCreate(
                kind=channel.kind, name=channel.name, config=data.config, enabled=channel.enabled
            )
        )
        if channel.kind is NotificationKind.WEB_PUSH:
            device_key = web_push_device_key(config)
            duplicate = await session.scalar(
                select(NotificationChannel.id).where(
                    NotificationChannel.kind == NotificationKind.WEB_PUSH,
                    NotificationChannel.web_push_device_key == device_key,
                    NotificationChannel.id != channel.id,
                )
            )
            if duplicate is not None:
                raise NotificationConfigConflictError("Web Push subscription is already registered")
            channel.web_push_device_key = device_key
        channel.config_ciphertext = secret_box.encrypt_dict(config)
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
        payload={
            "channel_id": channel.id,
            "title": TEST_NOTIFICATION_TITLE,
            "body": TEST_NOTIFICATION_MESSAGE,
            "message": TEST_NOTIFICATION_MESSAGE,
            "status": "seat_found",
        },
        dedupe_key=(
            f"notification:{channel.id}:test:{(requested_at or datetime.now(UTC)).isoformat()}"
        ),
    )
    await session.commit()
    return QueuedResponse(queued=True, event_id=event.id)
