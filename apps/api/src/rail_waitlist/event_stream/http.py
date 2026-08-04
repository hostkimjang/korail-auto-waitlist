from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from ..auth import require_admin
from ..config import get_settings
from ..database import SessionFactory
from ..models import OutboxEvent
from ..schemas import EventRead

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


async def _stream_events(request: Request, last_event_id: str | None):
    cursor_time = None
    cursor_id = last_event_id
    if cursor_id:
        async with SessionFactory() as session:
            previous = await session.get(OutboxEvent, cursor_id)
            cursor_time = previous.created_at if previous else None
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
