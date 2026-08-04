from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_admin
from .config import get_settings
from .database import SessionFactory, get_session
from .domain import Provider
from .models import KorailBrowserSnapshotBatch, OutboxEvent
from .official_page_confirmations import upsert_official_page_confirmations
from .providers import (
    OfficialTimetableAdapter,
    ProviderUnavailable,
    get_timetable_provider,
    list_capabilities,
)
from .schemas import (
    EventRead,
    KorailBrowserSnapshotRevision,
    OfficialPageSeatConfirmationCreate,
    OfficialPageSeatConfirmationRead,
    ProviderCapabilities,
    SeatStatusSourceStatus,
    StationCatalog,
)
from .seat_status_cooldown import ProviderCooldown

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_admin)])
Session = Annotated[AsyncSession, Depends(get_session)]
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key", max_length=100)]


@router.get(
    "/korail-browser-snapshot-revision",
    response_model=KorailBrowserSnapshotRevision,
)
async def korail_browser_snapshot_revision(
    response: Response, session: Session
) -> KorailBrowserSnapshotRevision:
    response.headers["Cache-Control"] = "no-store"
    revision = await session.scalar(
        select(func.max(KorailBrowserSnapshotBatch.observed_at)).where(
            KorailBrowserSnapshotBatch.fresh_until > datetime.now(timezone.utc)
        )
    )
    if revision is not None:
        revision = (
            revision.replace(tzinfo=timezone.utc)
            if revision.tzinfo is None or revision.utcoffset() is None
            else revision.astimezone(timezone.utc)
        )
    return KorailBrowserSnapshotRevision(revision=revision)


@router.get("/providers", response_model=list[ProviderCapabilities])
async def providers() -> list[ProviderCapabilities]:
    return list_capabilities()


@router.get("/stations", response_model=StationCatalog)
async def stations(provider: Provider, request: Request) -> StationCatalog:
    try:
        adapter = get_timetable_provider(provider)
        if isinstance(adapter, OfficialTimetableAdapter):
            return await request.app.state.station_catalog_service.get_catalog(provider)
        return await adapter.stations()
    except ProviderUnavailable as error:
        raise HTTPException(503, str(error)) from None


@router.get("/seat-status/status", response_model=list[SeatStatusSourceStatus])
async def seat_status_sources(
    request: Request,
    response: Response,
) -> list[SeatStatusSourceStatus]:
    """Expose only the active seat-source cooldowns, not worker provider circuits."""
    response.headers["Cache-Control"] = "no-store"
    cooldown_store = request.app.state.seat_status_cooldown_store
    korail_cooldown, srt_cooldown = await asyncio.gather(
        cooldown_store.get("korail-browser"),
        cooldown_store.get("srt"),
    )
    return [
        _seat_status_source_status("korail", "korail_browser", korail_cooldown),
        _seat_status_source_status("srt", "srt_live", srt_cooldown),
    ]


def _seat_status_source_status(
    provider: Literal["korail", "srt"],
    source: Literal["korail_browser", "srt_live"],
    cooldown: ProviderCooldown | None,
) -> SeatStatusSourceStatus:
    if cooldown is None:
        return SeatStatusSourceStatus(
            provider=provider,
            source=source,
            state="ready",
        )
    return SeatStatusSourceStatus(
        provider=provider,
        source=source,
        state="cooldown",
        cause=cooldown.reason,
        retry_after_seconds=cooldown.retry_after_seconds,
    )


@router.post(
    "/seat-observations/official-page-confirmations",
    response_model=OfficialPageSeatConfirmationRead,
    status_code=201,
)
async def official_page_confirmation_create(
    data: OfficialPageSeatConfirmationCreate,
    response: Response,
    session: Session,
    idempotency_key: IdempotencyKey = None,
) -> OfficialPageSeatConfirmationRead:
    response.headers["Cache-Control"] = "no-store"
    try:
        confirmations, created_count, replayed = await upsert_official_page_confirmations(
            session,
            data,
            idempotency_key=idempotency_key,
        )
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    await session.commit()
    for confirmation in confirmations:
        await session.refresh(confirmation)
    first = confirmations[0]
    return OfficialPageSeatConfirmationRead(
        provider=first.provider,
        origin_node_id=first.origin_node_id,
        destination_node_id=first.destination_node_id,
        train_number=first.train_number,
        departure_at=first.departure_at,
        passenger_count=first.passenger_count,
        seat_classes=[
            {
                "id": confirmation.id,
                "seat_class": confirmation.seat_class,
                "status": confirmation.status.value,
            }
            for confirmation in confirmations
        ],
        source=first.source,
        observed_at=first.observed_at,
        fresh_until=first.fresh_until,
        created_count=created_count,
        replayed=replayed,
    )


def event_wire(event: OutboxEvent) -> str:
    data = EventRead(
        id=event.id,
        event_type=event.event_type,
        aggregate_id=event.aggregate_id,
        payload=event.payload,
        created_at=event.created_at,
    ).model_dump_json()
    return f"id: {event.id}\nevent: {event.event_type}\ndata: {data}\n\n"


@router.get("/events")
async def events(
    request: Request,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    async def stream():
        cursor_time = None
        cursor_id = last_event_id
        if cursor_id:
            async with SessionFactory() as session:
                previous = await session.get(OutboxEvent, cursor_id)
                cursor_time = previous.created_at if previous else None
        while not await request.is_disconnected():
            async with SessionFactory() as session:
                query = (
                    select(OutboxEvent).order_by(OutboxEvent.created_at, OutboxEvent.id).limit(100)
                )
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

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
