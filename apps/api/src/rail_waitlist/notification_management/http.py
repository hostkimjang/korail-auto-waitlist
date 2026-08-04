from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..config import get_settings
from ..database import get_session
from ..models import NotificationChannel
from ..services import (
    add_outbox_event,
    create_notification_channel,
    update_notification_channel,
)
from .schemas import (
    NotificationChannelCreate,
    NotificationChannelRead,
    NotificationChannelUpdate,
    QueuedResponse,
)

router = APIRouter(prefix="/api/v1/notifications", dependencies=[Depends(require_admin)])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.post("/channels", response_model=NotificationChannelRead, status_code=201)
async def channels_create(data: NotificationChannelCreate, session: Session) -> NotificationChannel:
    return await create_notification_channel(session, data)


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
    return await update_notification_channel(session, await find_channel(session, channel_id), data)


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
    if not channel.enabled:
        raise HTTPException(409, "notification channel is disabled")
    event = await add_outbox_event(
        session,
        aggregate_type="notification_channel",
        aggregate_id=channel.id,
        event_type="notification.test_requested",
        payload={"channel_id": channel.id, "message": "KORAIL·SRT 알림 테스트입니다."},
        dedupe_key=f"notification:{channel.id}:test:{datetime.now().isoformat()}",
    )
    await session.commit()
    return QueuedResponse(queued=True, event_id=event.id)
