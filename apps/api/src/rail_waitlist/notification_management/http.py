from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..config import get_settings
from ..database import get_session
from ..domain import NotificationKind
from .models import NotificationChannel
from .schemas import (
    NotificationChannelCreate,
    NotificationChannelRead,
    NotificationChannelUpdate,
    QueuedResponse,
)
from .service import (
    RETIRED_NATIVE_NOTIFICATION_KINDS,
    USER_CONFIGURABLE_NOTIFICATION_KINDS,
    NotificationChannelDisabledError,
    NotificationConfigConflictError,
    NotificationConfigError,
    backfill_web_push_device_keys,
    create_notification_channel,
    queue_test_notification,
    update_notification_channel,
)

router = APIRouter(prefix="/api/v1/notifications", dependencies=[Depends(require_admin)])
Session = Annotated[AsyncSession, Depends(get_session)]


async def _active_web_push_device_count(session: AsyncSession) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(NotificationChannel)
        .where(
            NotificationChannel.kind == NotificationKind.WEB_PUSH,
            NotificationChannel.enabled.is_(True),
        )
    )
    return int(count or 0)


async def _read_channel(
    session: AsyncSession, channel: NotificationChannel
) -> NotificationChannelRead:
    is_web_push = channel.kind is NotificationKind.WEB_PUSH
    device_keys = await backfill_web_push_device_keys(session) if is_web_push else {}
    return NotificationChannelRead.model_validate(channel).model_copy(
        update={
            "device_key": device_keys.get(channel.id) if is_web_push else None,
            "active_device_count": (
                await _active_web_push_device_count(session) if is_web_push else None
            ),
        }
    )


@router.post("/channels", response_model=NotificationChannelRead, status_code=201)
async def channels_create(
    data: NotificationChannelCreate, session: Session
) -> NotificationChannelRead:
    try:
        channel = await create_notification_channel(session, data)
        return await _read_channel(session, channel)
    except NotificationConfigError as error:
        raise HTTPException(422, str(error)) from None


@router.get("/web-push/public-key")
async def webpush_public_key() -> dict[str, str]:
    public_key = get_settings().webpush_public_key()
    if not public_key:
        raise HTTPException(503, "Web Push VAPID public key is not configured")
    return {"public_key": public_key}


@router.get("/channels", response_model=list[NotificationChannelRead])
async def channels_list(session: Session) -> list[NotificationChannelRead]:
    rows = await session.scalars(
        select(NotificationChannel)
        .where(NotificationChannel.kind.in_(USER_CONFIGURABLE_NOTIFICATION_KINDS))
        .order_by(NotificationChannel.created_at)
    )
    channels = list(rows.all())
    web_push_device_keys = await backfill_web_push_device_keys(session)
    active_web_push_count = await _active_web_push_device_count(session)
    return [
        NotificationChannelRead.model_validate(channel).model_copy(
            update={
                "device_key": (
                    web_push_device_keys.get(channel.id)
                    if channel.kind is NotificationKind.WEB_PUSH
                    else None
                ),
                "active_device_count": (
                    active_web_push_count if channel.kind is NotificationKind.WEB_PUSH else None
                ),
            }
        )
        for channel in channels
        if channel.kind is not NotificationKind.WEB_PUSH or channel.id in web_push_device_keys
    ]


async def find_channel(session: AsyncSession, channel_id: str) -> NotificationChannel:
    channel = await session.get(NotificationChannel, channel_id)
    if channel is None or channel.kind in RETIRED_NATIVE_NOTIFICATION_KINDS:
        raise HTTPException(404, "notification channel not found")
    return channel


@router.get("/channels/{channel_id}", response_model=NotificationChannelRead)
async def channels_get(channel_id: str, session: Session) -> NotificationChannelRead:
    return await _read_channel(session, await find_channel(session, channel_id))


@router.patch("/channels/{channel_id}", response_model=NotificationChannelRead)
async def channels_update(
    channel_id: str, data: NotificationChannelUpdate, session: Session
) -> NotificationChannelRead:
    channel = await find_channel(session, channel_id)
    try:
        updated = await update_notification_channel(session, channel, data)
        return await _read_channel(session, updated)
    except NotificationConfigError as error:
        raise HTTPException(422, str(error)) from None
    except NotificationConfigConflictError as error:
        raise HTTPException(409, str(error)) from None


@router.delete("/channels/{channel_id}", status_code=204)
async def channels_delete(channel_id: str, session: Session) -> Response:
    await find_channel(session, channel_id)
    await session.execute(delete(NotificationChannel).where(NotificationChannel.id == channel_id))
    await session.commit()
    return Response(status_code=204)


@router.post(
    "/channels/{channel_id}/test-send",
    response_model=QueuedResponse,
    status_code=202,
)
async def channels_test_send(channel_id: str, session: Session) -> QueuedResponse:
    channel = await find_channel(session, channel_id)
    try:
        return await queue_test_notification(session, channel)
    except NotificationChannelDisabledError as error:
        raise HTTPException(409, str(error)) from None
