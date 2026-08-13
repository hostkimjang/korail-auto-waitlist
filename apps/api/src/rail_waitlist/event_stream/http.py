from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from ..auth import require_admin
from ..config import get_settings
from ..database import SessionFactory
from ..outbox_management.models import OutboxEvent
from .schemas import EventRead

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_admin)])


def event_wire(event: OutboxEvent) -> str:
    data = EventRead(
        id=event.id,
        event_type=event.event_type,
        aggregate_id=event.aggregate_id,
        payload=event.payload,
        created_at=event.created_at,
    ).model_dump_json()
    return f"id: {event.id}\nevent: {event.event_type}\ndata: {data}\n\n"


async def _initial_event_cursor(last_event_id: str | None) -> tuple[datetime | None, str | None]:
    async with SessionFactory() as session:
        if last_event_id is not None:
            previous = await session.get(OutboxEvent, last_event_id)
            if previous is not None:
                return previous.created_at, last_event_id
        latest = await session.scalar(
            select(OutboxEvent)
            .order_by(OutboxEvent.created_at.desc(), OutboxEvent.id.desc())
            .limit(1)
        )
    if latest is None:
        return None, None
    return latest.created_at, latest.id


async def _stream_events(
    request: Request,
    last_event_id: str | None,
) -> AsyncIterator[str]:
    cursor_time, cursor_id = await _initial_event_cursor(last_event_id)
    while not await request.is_disconnected():
        async with SessionFactory() as session:
            query = select(OutboxEvent).order_by(OutboxEvent.created_at, OutboxEvent.id).limit(100)
            if cursor_time is not None:
                query = query.where(
                    (OutboxEvent.created_at > cursor_time)
                    | (
                        (OutboxEvent.created_at == cursor_time)
                        & (OutboxEvent.id > (cursor_id or ""))
                    )
                )
            rows = list((await session.scalars(query)).all())
        if rows:
            for event in rows:
                cursor_time, cursor_id = event.created_at, event.id
                yield event_wire(event)
        else:
            yield ": keepalive\n\n"
        await asyncio.sleep(get_settings().sse_poll_seconds)


@router.get("/events")
async def events(
    request: Request,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    return StreamingResponse(
        _stream_events(request, last_event_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
