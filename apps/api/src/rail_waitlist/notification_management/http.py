from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..config import get_settings
from ..database import get_session
from ..models import NotificationChannel
from .schemas import (
    NotificationChannelCreate,
    NotificationChannelRead,
    NotificationChannelUpdate,
    QueuedResponse,
)
from .service import (
    NotificationChannelDisabledError,
    NotificationConfigError,
    create_notification_channel,
    queue_test_notification,
    update_notification_channel,
)

router = APIRouter(prefix="/api/v1/notifications", dependencies=[Depends(require_admin)])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.post("/channels", response_model=NotificationChannelRead, status_code=201)
async def channels_create(data: NotificationChannelCreate, session: Session) -> NotificationChannel:
    try:
        return await create_notification_channel(session, data)
    except NotificationConfigError as error:
        raise HTTPException(422, str(error)) from None


@router.get("/web-push/public-key")
async def webpush_public_key() -> dict[str, str]:
    public_key = get_settings().webpush_public_key()
    if not public_key:
        raise HTTPException(503, "Web Push VAPID public key is not configured")
    return {"public_key": public_key}


@router.get("/channels", response_model=list[NotificationChannelRead])
async def channels_list(session: Session) -> list[NotificationChannel]:
    rows = await session.scalars(
        select(NotificationChannel).order_by(NotificationChannel.created_at)
    )
    return list(rows.all())


async def find_channel(session: AsyncSession, channel_id: str) -> NotificationChannel:
    channel = await session.get(NotificationChannel, channel_id)
    if channel is None:
        raise HTTPException(404, "notification channel not found")
    return channel


@router.get("/channels/{channel_id}", response_model=NotificationChannelRead)
async def channels_get(channel_id: str, session: Session) -> NotificationChannel:
    return await find_channel(session, channel_id)


@router.patch("/channels/{channel_id}", response_model=NotificationChannelRead)
async def channels_update(
    channel_id: str, data: NotificationChannelUpdate, session: Session
) -> NotificationChannel:
    try:
        return await update_notification_channel(
            session, await find_channel(session, channel_id), data
        )
    except NotificationConfigError as error:
        raise HTTPException(422, str(error)) from None


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
